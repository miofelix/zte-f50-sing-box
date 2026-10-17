"""Project paths and immutable installer defaults."""

from pathlib import Path
import re


# ``src/zte_f50_singbox/settings.py`` -> repository root.
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VERSION = "1.14.3"
DEFAULT_PROFILE = "zte-f50"
REMOTE = "/data/local/f50-sing-box"
MAX_DOWNLOAD = 160 * 1024 * 1024
MAX_CONFIG = 4 * 1024 * 1024
MAX_RULE_DOWNLOAD = 32 * 1024 * 1024
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")

# Official GitHub release asset digest, checked 2026-10-17. An explicit
# version selects its own release metadata; the default additionally checks
# this pin.
PINNED_SHA256 = "149569b5dbf01958e6598beb3a13078d6fc0e353ef06f4aff85f5f28bc23593b"

