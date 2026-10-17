#!/usr/bin/env python3
"""Command-line entry point for the ZTE F50 installer."""

import sys
from pathlib import Path

# Run directly from a checkout without installing the package.
_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from zte_f50_singbox.config_ops import import_vless, install_config
from zte_f50_singbox.errors import SetupError
from zte_f50_singbox.installer import deploy
from zte_f50_singbox.release import fetch
from zte_f50_singbox.rules import fetch_rules
from zte_f50_singbox.vless import parse_vless
from zte_f50_singbox.cli import main

__all__ = ["SetupError", "fetch", "fetch_rules", "parse_vless", "deploy",
           "import_vless", "install_config", "main"]


if __name__ == "__main__":
    sys.exit(main())
