"""Register a bounded, reversible entry in an existing user boot script.

The adapter does not install an Android boot loader. A device profile must name
an existing script that the device already executes as root during startup.
"""

import re
import shlex

from .errors import SetupError
from .profiles import load_profile, profile_host, profile_remote
from .transport import ssh


BEGIN_MARKER = "# BEGIN ZTE-F50-SINGBOX AUTOSTART"
END_MARKER = "# END ZTE-F50-SINGBOX AUTOSTART"
_HOOK_PATH = re.compile(r"/(?:data|sdcard)/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\Z")


def validate_autostart(value):
    """Return optional adapter metadata without interpreting any shell input."""
    if value is None:
        return None
    required = {"backend", "hook_path"}
    if (not isinstance(value, dict) or not required <= set(value)
            or set(value) - required - {"fallback_hook_path"}):
        raise SetupError("Profile autostart must contain backend and hook_path")
    if value["backend"] != "external-script":
        raise SetupError("Unsupported autostart backend; use external-script")
    for key in ("hook_path", "fallback_hook_path"):
        if key not in value:
            continue
        path = value[key]
        if (not isinstance(path, str) or not _HOOK_PATH.fullmatch(path)
                or any(part in (".", "..") for part in path.split("/"))):
            raise SetupError(f"Autostart {key} must be a safe file below /data or /sdcard")
    if value.get("fallback_hook_path") == value["hook_path"]:
        raise SetupError("Autostart fallback hook must differ from the primary hook")
    return dict(value)


def hook_block(base):
    """Render only our guarded, detached invocation; never source the hook."""
    entry = shlex.quote(base + "/boot-start.sh")
    disabled = shlex.quote(base + "/autostart.disabled")
    log_dir = shlex.quote(base + "/log")
    log_file = shlex.quote(base + "/log/boot.log")
    attempt = shlex.quote(base + "/run/boot.attempt")
    return f"""{BEGIN_MARKER}
if [ -f {entry} ] && [ ! -e {disabled} ] &&
   [ "$(cat /proc/sys/kernel/random/boot_id)" != "$(cat {attempt} 2>/dev/null)" ]; then
  if command -v setsid >/dev/null 2>&1 && mkdir -p {log_dir}; then
    setsid /system/bin/sh {entry} </dev/null >>{log_file} 2>&1 &
  fi
fi
{END_MARKER}
"""


def render_autostart_config(profile):
    """Literal controller metadata; no shell is executed while loading it."""
    adapter = validate_autostart(profile.get("autostart"))
    if adapter is None:
        return "AUTOSTART_BACKEND=none\nAUTOSTART_HOOK=none\n"
    metadata = ("AUTOSTART_BACKEND=external-script\n"
                f"AUTOSTART_HOOK={adapter['hook_path']}\n")
    if "fallback_hook_path" in adapter:
        metadata += f"AUTOSTART_FALLBACK_HOOK={adapter['fallback_hook_path']}\n"
    return metadata


def _hook_paths(adapter):
    paths = [adapter["hook_path"]]
    if "fallback_hook_path" in adapter:
        paths.append(adapter["fallback_hook_path"])
    return paths


def hook_preflight_script(profile):
    """Read-only adapter checks to perform before replacing deployed files."""
    adapter = validate_autostart(profile.get("autostart"))
    if adapter is None:
        return ""
    return "".join(_hook_preflight_script(path) for path in _hook_paths(adapter))


def _hook_preflight_script(path):
    return f"""(
hook={shlex.quote(path)}
test -f "$hook" && test ! -L "$hook" || {{
  echo 'Autostart requires an existing regular boot hook; no loader was installed' >&2; exit 1;
}}
test "$(wc -c < "$hook")" -le 262144 || {{ echo 'Boot hook exceeds 256 KiB' >&2; exit 1; }}
test "$(LC_ALL=C tr -d '\\000' < "$hook" | wc -c)" -eq "$(wc -c < "$hook")" || {{
  echo "Boot hook contains NUL bytes; restore a verified backup: $hook" >&2; exit 1;
}}
command -v setsid >/dev/null 2>&1 || {{ echo 'Boot hook requires setsid' >&2; exit 1; }}
command -v sync >/dev/null 2>&1 || {{ echo 'Boot hook requires sync' >&2; exit 1; }}
/system/bin/sh -n "$hook" || {{ echo 'Existing boot hook has invalid shell syntax' >&2; exit 1; }}
awk -v first={shlex.quote(BEGIN_MARKER)} -v last={shlex.quote(END_MARKER)} '
  index($0, first) {{ if ($0 != first || begin || end) bad=1; begin=NR; }}
  index($0, last) {{ if ($0 != last || !begin || end) bad=1; end=NR; }}
  END {{ if (bad || (!!begin != !!end)) exit 1; }}
' "$hook" || {{ echo 'Malformed autostart markers; boot hook was not changed' >&2; exit 1; }}
)
"""


def hook_install_script(profile):
    """Shell fragment executed with the controller lock held by the caller.

    Temporary files have their own subshell cleanup so this fragment can be
    included in deployment without replacing deployment's staging cleanup.
    The disabled marker is deliberately never modified here.
    """
    adapter = validate_autostart(profile.get("autostart"))
    if adapter is None:
        return "echo 'Profile has no boot adapter; autostart was not registered'\n"
    # Check every declared entry before changing either one.
    return hook_preflight_script(profile) + "".join(
        _hook_install_script(profile, path, index)
        for index, path in enumerate(_hook_paths(adapter))
    )


def _hook_install_script(profile, path, index):
    base = profile_remote(profile)
    block = hook_block(base)
    # This literal delimiter cannot occur in a validated base path.
    return f"""(
hook={shlex.quote(path)}
test -f "$hook" && test ! -L "$hook" || {{
  echo 'Autostart requires an existing regular boot hook; no loader was installed' >&2; exit 1;
}}
test "$(wc -c < "$hook")" -le 262144 || {{ echo 'Boot hook exceeds 256 KiB' >&2; exit 1; }}
test "$(LC_ALL=C tr -d '\\000' < "$hook" | wc -c)" -eq "$(wc -c < "$hook")" || {{
  echo "Boot hook contains NUL bytes; restore a verified backup: $hook" >&2; exit 1;
}}
command -v setsid >/dev/null 2>&1 || {{ echo 'Boot hook requires setsid' >&2; exit 1; }}
/system/bin/sh -n "$hook" || {{ echo 'Existing boot hook has invalid shell syntax' >&2; exit 1; }}
pending="$base/.boot-hook.$$"
original="$base/.boot-original.$$"
block="$base/.boot-block.$$"
backup=''
hook_writing=0
cleanup_hook() {{
  if [ "$hook_writing" = 1 ] && [ -n "$backup" ]; then
    cat "$backup" > "$hook" || echo "Restore failed; inspect $backup" >&2
    sync || echo "Sync failed; inspect $backup" >&2
  fi
  rm -f "$pending" "$original" "$block"
}}
trap cleanup_hook EXIT
trap 'exit 1' HUP INT TERM
cat "$hook" > "$original"
chmod 600 "$original"
test "$(LC_ALL=C tr -d '\\000' < "$original" | wc -c)" -eq "$(wc -c < "$original")" || {{
  echo 'Captured boot hook contains NUL bytes; nothing was installed' >&2; exit 1;
}}
# Reject partial, duplicate, reversed, or non-literal marker lines.
positions=$(awk -v first={shlex.quote(BEGIN_MARKER)} -v last={shlex.quote(END_MARKER)} '
  index($0, first) {{ if ($0 != first || begin || end) bad=1; begin=NR; }}
  index($0, last) {{ if ($0 != last || !begin || end) bad=1; end=NR; }}
  END {{ if (bad || (!!begin != !!end)) exit 1; print begin+0, end+0; }}
' "$original") || {{ echo 'Malformed autostart markers; boot hook was not changed' >&2; exit 1; }}
read -r first_line last_line <<EOF_BOOT_POSITIONS
$positions
EOF_BOOT_POSITIONS
cat > "$block" <<'EOF_ZTE_SINGBOX_BOOT_BLOCK'
{block}EOF_ZTE_SINGBOX_BOOT_BLOCK
if [ "$first_line" = 0 ]; then
  cat "$original" > "$pending"
  printf '\\n' >> "$pending"
  cat "$block" >> "$pending"
else
  head -n "$((first_line - 1))" "$original" > "$pending"
  cat "$block" >> "$pending"
  tail -n "+$((last_line + 1))" "$original" >> "$pending"
fi
chmod 600 "$pending"
/system/bin/sh -n "$pending" || {{ echo 'Boot hook failed shell syntax validation' >&2; exit 1; }}
if cmp -s "$original" "$pending"; then
  echo 'Boot hook registered.'
  exit 0
fi
mkdir -p "$base/backups"
chmod 700 "$base/backups"
backup="$base/backups/boot-hook.$(date +%Y%m%d-%H%M%S).$$.{index}.sh"
test ! -e "$backup" || {{ echo 'Boot-hook backup collision; nothing changed' >&2; exit 1; }}
cp "$original" "$backup"
chmod 600 "$backup"
# Persist the complete candidate and recovery copy before truncating the hook.
sync
cmp -s "$hook" "$original" || {{ echo 'Boot hook changed during registration; retry after review' >&2; exit 1; }}
# Keep the existing hook inode, ownership, and permissions (including sdcard).
hook_writing=1
if ! cat "$pending" > "$hook"; then
  echo 'Writing boot hook failed; restoring its backup' >&2
  exit 1
fi
cmp -s "$pending" "$hook" || {{
  echo 'Boot-hook verification failed; restoring its backup' >&2; exit 1;
}}
sync
hook_writing=0
echo "Registered boot hook; backup: $backup"
)
"""


def autostart(args):
    """Manage the persistent preference through the installed controller."""
    profile = load_profile(getattr(args, "profile", None))
    adapter = validate_autostart(profile.get("autostart"))
    if adapter is None and args.action == "on":
        raise SetupError("This device profile has no autostart adapter")
    base = profile_remote(profile)
    script = f"""set -eu
umask 077
base={shlex.quote(base)}
test "$(id -u)" = 0 || {{ echo 'Root SSH is required' >&2; exit 1; }}
test -x "$base/f50-proxy" || {{ echo 'Deploy the controller first' >&2; exit 1; }}
"""
    if args.action == "on":
        # Register while holding the same lock used by the device controller,
        # then release it before invoking that controller (no nested lock).
        script += """(
mkdir -p "$base/run"
chmod 700 "$base/run"
exec 9>"$base/run/control.lock"
flock -n 9 9>&9 || { echo 'Another proxy control operation is running' >&2; exit 1; }
test -f "$base/boot-start.sh" || { echo 'Deploy the boot controller first' >&2; exit 1; }
metadata="$base/.autostart.conf.$$"
trap 'rm -f "$metadata"' EXIT
trap 'exit 1' HUP INT TERM
cat > "$metadata" <<'EOF_ZTE_SINGBOX_BOOT_METADATA'
""" + render_autostart_config(profile) + """EOF_ZTE_SINGBOX_BOOT_METADATA
chmod 600 "$metadata"
""" + hook_install_script(profile) + """
mv "$metadata" "$base/autostart.conf"
)
"""
    script += f'"$base/f50-proxy" autostart {shlex.quote(args.action)}\n'
    ssh(profile_host(profile, getattr(args, "host", None)), script)
