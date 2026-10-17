"""Native sing-box configuration import and installation operations."""

import getpass
import json
from pathlib import Path
import shlex
import sys

from .errors import SetupError
from .io_utils import json_bytes, write_private
from .profiles import load_profile, profile_host, profile_remote, render_config
from .settings import MAX_CONFIG, ROOT
from .transport import remote_idle, ssh
from .vless import parse_vless


def _template(profile):
    try:
        value = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SetupError("config.example.json is missing or invalid") from None
    return render_config(value, profile)


def import_vless(args):
    profile = load_profile(getattr(args, "profile", None))
    if args.url:
        value = args.url
    elif sys.stdin.isatty():
        value = getpass.getpass("VLESS URL (hidden): ")
    else:
        value = sys.stdin.read(65537)
    if not value or len(value) > 65536:
        raise SetupError("Expected one VLESS URL, up to 64 KiB")
    outbound = parse_vless(value)
    template = _template(profile)
    positions = [i for i, item in enumerate(template.get("outbounds", [])) if item.get("tag") == "proxy"]
    if len(positions) != 1:
        raise SetupError("config.example.json must contain exactly one outbound tagged proxy")
    template["outbounds"][positions[0]] = outbound
    output = Path(args.output).expanduser() if args.output else ROOT / ".local" / "config.json"
    write_private(output, json_bytes(template))
    print(f"Private native configuration written to {output} (0600); install-config checks it with sing-box")


def install_config(args):
    profile = load_profile(getattr(args, "profile", None))
    source = Path(args.config).expanduser() if args.config else ROOT / ".local" / "config.json"
    content = source.read_bytes()
    if len(content) > MAX_CONFIG:
        raise SetupError("Configuration exceeds 4 MiB")
    if not isinstance(json.loads(content), dict):
        raise SetupError("Native configuration must be a JSON object")
    host = profile_host(profile, getattr(args, "host", None))
    base = profile_remote(profile)
    script = """set -eu
umask 077
base={base}
""".format(base=shlex.quote(base)) + remote_idle() + """
test -x "$base/sing-box" || { echo 'Deploy the core first' >&2; exit 1; }
pending="$base/.config.$$"
trap 'rm -f "$pending"' EXIT HUP INT TERM
cat > "$pending"
chmod 600 "$pending"
if ! "$base/sing-box" check -D "$base" -c "$pending" >/dev/null 2>&1; then
  echo 'Native sing-box configuration check failed; current config is unchanged' >&2
  exit 1
fi
if [ -f "$base/config.json" ]; then
  cp "$base/config.json" "$base/config.previous.json"
  chmod 600 "$base/config.previous.json"
fi
mv "$pending" "$base/config.json"
echo 'Private config installed and checked by native sing-box; networking has not been activated'
"""
    ssh(host, script, content)

