"""SSH deployment of the official core, controller, and verified rule sets."""

import json
from pathlib import Path
import shlex
import tarfile
import tempfile

from .autostart import hook_install_script, hook_preflight_script, render_autostart_config
from .config_ops import _template
from .errors import SetupError
from .io_utils import digest, json_bytes
from .profiles import load_profile, profile_host, profile_remote
from .release import fetch
from .rules import fetch_rules
from .settings import ROOT
from .transport import remote_idle, ssh


def deploy(args):
    profile = load_profile(getattr(args, "profile", None))
    # Device profiles use the official Android arm64 artifact.
    cache = fetch(args.version)
    rules = fetch_rules()
    rendered = _template(profile)
    with tempfile.TemporaryDirectory(prefix=".deploy-config-") as work:
        rendered_config = Path(work) / "config.example.json"
        rendered_config.write_bytes(json_bytes(rendered))
        rendered_boot = Path(work) / "autostart.conf"
        rendered_boot.write_text(render_autostart_config(profile), encoding="utf-8")
        sources = {
            "sing-box": cache / "sing-box",
            "f50-proxy": ROOT / "f50-proxy",
            "boot-start.sh": ROOT / "boot-start.sh",
            "autostart.conf": rendered_boot,
            "profile.conf": Path(profile["shell_config"]),
            "config.example.json": rendered_config,
            "manifest.json": cache / "manifest.json",
            "rule-set/geosite-cn.srs": rules / "geosite-cn.srs",
            "rule-set/geoip-cn.srs": rules / "geoip-cn.srs",
            "rule-set/manifest.json": rules / "manifest.json",
        }
        for path in sources.values():
            if not path.is_file():
                raise SetupError(f"Deployment input is missing: {path}")
        with tempfile.TemporaryFile() as package:
            with tarfile.open(fileobj=package, mode="w") as archive:
                for name, path in sources.items():
                    archive.add(path, arcname=name, recursive=False)
            package.seek(0)
            data = package.read()
        expected_core = digest(sources["sing-box"])
        rule_checks = "\n".join(
            f'[ "$(sha256sum "$stage/{name}" | cut -d " " -f 1)" = {shlex.quote(digest(path))} ]'
            " || { echo 'Transferred rule-set SHA256 mismatch' >&2; exit 1; }"
            for name, path in sources.items() if name.startswith("rule-set/")
        )
    host = profile_host(profile, getattr(args, "host", None))
    base = profile_remote(profile)
    script = """set -eu
umask 077
base={base}
""".format(base=shlex.quote(base)) + remote_idle() + hook_preflight_script(profile) + f"""
mkdir -p "$base"
chmod 700 "$base"
stage="$base/.deploy.$$"
mkdir "$stage"
mkdir "$stage/rule-set"
cleanup() {{
  rm -f "$stage/sing-box" "$stage/f50-proxy" "$stage/boot-start.sh" "$stage/autostart.conf" "$stage/profile.conf" "$stage/config.example.json" "$stage/manifest.json"
  rm -f "$stage/rule-set/geosite-cn.srs" "$stage/rule-set/geoip-cn.srs" "$stage/rule-set/manifest.json"
  rmdir "$stage/rule-set" 2>/dev/null || :
  rmdir "$stage" 2>/dev/null || :
}}
trap cleanup EXIT HUP INT TERM
tar -xf - -C "$stage"
actual=$(sha256sum "$stage/sing-box")
actual=${{actual%% *}}
[ "$actual" = {shlex.quote(expected_core)} ] || {{ echo 'Transferred binary SHA256 mismatch' >&2; exit 1; }}
{rule_checks}
chmod 700 "$stage/sing-box" "$stage/f50-proxy" "$stage/boot-start.sh"
chmod 600 "$stage/profile.conf" "$stage/autostart.conf" "$stage/config.example.json" "$stage/manifest.json"
chmod 600 "$stage/rule-set/geosite-cn.srs" "$stage/rule-set/geoip-cn.srs" "$stage/rule-set/manifest.json"
"$stage/sing-box" version >/dev/null
mkdir -p "$base/rule-set"
chmod 700 "$base/rule-set"
for name in sing-box f50-proxy boot-start.sh autostart.conf profile.conf config.example.json manifest.json rule-set/geosite-cn.srs rule-set/geoip-cn.srs rule-set/manifest.json; do
  mv "$stage/$name" "$base/$name"
done
""" + hook_install_script(profile) + """
echo 'Core and gateway controller installed.'
echo 'Use f50-proxy autostart status to view boot startup settings.'
"""
    ssh(host, script, data)
