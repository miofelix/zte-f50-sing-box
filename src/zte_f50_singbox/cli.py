"""Command-line interface for the standalone installer."""

import argparse
import os
import subprocess
import sys
import tarfile
import urllib.error

from .autostart import autostart
from .config_ops import import_vless, install_config
from .errors import SetupError
from .installer import deploy
from .profiles import load_profile
from .release import fetch
from .rules import fetch_rules
from .settings import DEFAULT_PROFILE, DEFAULT_VERSION


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Dependency-free SSH installer for the official sing-box core.")
    commands = parser.add_subparsers(dest="command", required=True)
    fetching = commands.add_parser("fetch", help="download and SHA256-verify the official Android arm64 core")
    fetching.add_argument("--version", default=DEFAULT_VERSION)
    fetching.add_argument("--profile", default=DEFAULT_PROFILE)
    fetching.set_defaults(handler=lambda args: fetch(args.version, load_profile(args.profile)))
    rule_fetching = commands.add_parser("fetch-rules", help="download and verify official native CN rule-sets without the core")
    rule_fetching.add_argument("--refresh", action="store_true", help="redownload the reviewed pinned rules to repair the cache")
    rule_fetching.set_defaults(handler=lambda args: fetch_rules(args.refresh))
    deploying = commands.add_parser("deploy", help="install files over SSH without activation")
    deploying.add_argument("--host", default=None)
    deploying.add_argument("--profile", default=DEFAULT_PROFILE)
    deploying.add_argument("--version", default=DEFAULT_VERSION)
    deploying.set_defaults(handler=deploy)
    importing = commands.add_parser("import-vless", help="convert a VLESS TCP URL to private native JSON")
    importing.add_argument("url", nargs="?", help="prefer stdin or the hidden prompt to avoid shell history")
    importing.add_argument("--output", help="private output path (default: .local/config.json)")
    importing.add_argument("--profile", default=DEFAULT_PROFILE)
    importing.set_defaults(handler=import_vless)
    installing = commands.add_parser("install-config", help="validate with native remote core and atomically install JSON")
    installing.add_argument("--host", default=None)
    installing.add_argument("--profile", default=DEFAULT_PROFILE)
    installing.add_argument("--config", help="native JSON path (default: .local/config.json)")
    installing.set_defaults(handler=install_config)
    booting = commands.add_parser("autostart", help="register and enable boot startup, disable it, or show status")
    booting.add_argument("action", choices=("on", "off", "status"))
    booting.add_argument("--host", default=None)
    booting.add_argument("--profile", default=DEFAULT_PROFILE)
    booting.set_defaults(handler=autostart)
    args = parser.parse_args(argv)
    try:
        args.handler(args)
    except (SetupError, OSError, ValueError, urllib.error.URLError, tarfile.TarError, subprocess.SubprocessError) as error:
        message = str(error) if isinstance(error, SetupError) else type(error).__name__
        print(f"Error: {message}", file=sys.stderr)
        return 1
    return 0
