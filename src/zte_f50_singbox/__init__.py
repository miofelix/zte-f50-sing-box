"""Download, deployment and configuration helpers for the official sing-box core.

Public entry points are re-exported from the repository-level :mod:`deploy`
entry point.
"""

from .errors import SetupError
from .config_ops import import_vless, install_config
from .installer import deploy
from .release import fetch
from .rules import fetch_rules
from .vless import parse_vless

__all__ = ["SetupError", "fetch", "fetch_rules", "parse_vless", "deploy",
           "import_vless", "install_config"]

