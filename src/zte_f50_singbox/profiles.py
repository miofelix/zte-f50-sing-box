"""Load, validate, and apply device profiles.

Profiles are deliberately plain JSON plus a tiny shell ``profile.conf``.  The
JSON is the portable schema consumed by Python; the shell file records the
operational values used by a device-side controller and is validated before a
profile is accepted.  No shell code is executed while loading a profile.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import shlex

from .errors import SetupError
from .settings import DEFAULT_PROFILE, ROOT, REMOTE


_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@-]*\Z")
_IFACE_RE = re.compile(r"[A-Za-z0-9_.:-]+\Z")
_MARK_RE = re.compile(r"0x[0-9a-fA-F]+/0x[0-9a-fA-F]+\Z")
_CHAIN_RE = re.compile(r"[A-Za-z0-9_]{1,20}\Z")
_SHELL_KEY_RE = re.compile(r"[A-Z][A-Z0-9_]*\Z")

REQUIRED_JSON = {
    "schema_version", "name", "display_name", "ssh_host", "remote_root", "platform",
    "ports", "network", "shell_config",
}
REQUIRED_PORTS = {"tproxy", "local_test"}
REQUIRED_NETWORK = {
    "lan_interface", "mark", "route_table", "rule_priority",
    "chain_prefix",
}
REQUIRED_SHELL = {
    "PROFILE_NAME", "REMOTE_ROOT", "LAN_INTERFACE", "TPROXY_PORT",
    "LOCAL_TEST_PORT", "MARK", "ROUTE_TABLE", "RULE_PRIORITY",
    "CHAIN_PREFIX",
}


def _integer(value, field, minimum=1, maximum=65535):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise SetupError(f"Profile field {field} must be an integer in {minimum}..{maximum}")
    return value


def _profile_file(value) -> Path:
    path = Path(value)
    if path.suffix.lower() == ".json" or path.is_file():
        return path.expanduser()
    if not _NAME_RE.fullmatch(str(value)):
        raise SetupError("Profile name contains unsupported characters")
    return ROOT / "profiles" / f"{value}.json"


def _parse_shell(path: Path):
    if not path.is_file():
        raise SetupError(f"Profile shell configuration is missing: {path}")
    if path.stat().st_size > 64 * 1024:
        raise SetupError("Profile shell configuration exceeds 64 KiB")
    values = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise SetupError(f"Invalid profile shell assignment at line {line_number}")
        key, raw = stripped.split("=", 1)
        key = key.strip()
        if not _SHELL_KEY_RE.fullmatch(key) or key in values:
            raise SetupError(f"Invalid or duplicate profile shell key at line {line_number}")
        if key not in REQUIRED_SHELL:
            raise SetupError(f"Unsupported profile shell key at line {line_number}")
        try:
            parsed, rest = shlex.split(raw, comments=False, posix=True), ""
        except ValueError:
            raise SetupError(f"Invalid profile shell value at line {line_number}") from None
        if len(parsed) != 1:
            raise SetupError(f"Profile shell values must be scalar at line {line_number}")
        # The file is later sourced by the POSIX controller.  Restrict values
        # to the small literal alphabet used by device parameters so command
        # substitutions, redirects, and other shell syntax can never enter it.
        if not re.fullmatch(r"[A-Za-z0-9_./:+@-]+", parsed[0]):
            raise SetupError(f"Unsafe profile shell value at line {line_number}")
        values[key] = parsed[0]
    missing = REQUIRED_SHELL - set(values)
    if missing:
        raise SetupError("Profile shell configuration is missing: " + ", ".join(sorted(missing)))
    return values


def validate_profile(profile: dict, source: Path | None = None) -> dict:
    """Validate and return a normalized profile mapping."""
    from .autostart import validate_autostart

    if not isinstance(profile, dict):
        raise SetupError("Profile must be a JSON object")
    if profile.get("schema_version") != 1:
        raise SetupError("Unsupported profile schema_version")
    missing = REQUIRED_JSON - set(profile)
    if missing:
        raise SetupError("Profile is missing fields: " + ", ".join(sorted(missing)))
    name = profile.get("name")
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise SetupError("Profile name is invalid")
    for field in ("display_name", "ssh_host", "remote_root", "platform"):
        if not isinstance(profile.get(field), str) or not profile[field]:
            raise SetupError(f"Profile field {field} must be a non-empty string")
    if not _HOST_RE.fullmatch(profile["ssh_host"]):
        raise SetupError("Profile ssh_host is invalid")
    remote_parts = profile["remote_root"].split("/")
    if (not profile["remote_root"].startswith("/") or not profile["remote_root"].rstrip("/")
            or any(part in (".", "..") for part in remote_parts)):
        raise SetupError("Profile remote_root must be an absolute safe path")
    ports = profile.get("ports")
    if not isinstance(ports, dict) or not REQUIRED_PORTS <= set(ports):
        raise SetupError("Profile ports must contain tproxy and local_test")
    for key in REQUIRED_PORTS:
        _integer(ports[key], f"ports.{key}")
    if ports["tproxy"] == ports["local_test"]:
        raise SetupError("Profile tproxy and local_test ports must differ")
    network = profile.get("network")
    if not isinstance(network, dict) or not REQUIRED_NETWORK <= set(network):
        raise SetupError("Profile network is missing required fields")
    if not isinstance(network["lan_interface"], str) or not _IFACE_RE.fullmatch(network["lan_interface"]):
        raise SetupError("Profile network.lan_interface is invalid")
    if not isinstance(network["mark"], str) or not _MARK_RE.fullmatch(network["mark"]):
        raise SetupError("Profile network.mark is invalid")
    for key in ("route_table", "rule_priority"):
        _integer(network[key], f"network.{key}", 1, 2**31 - 1)
    if not isinstance(network["chain_prefix"], str) or not _CHAIN_RE.fullmatch(network["chain_prefix"]):
        raise SetupError("Profile network.chain_prefix is invalid")
    shell_path = profile.get("shell_config")
    if not isinstance(shell_path, str) or not shell_path:
        raise SetupError("Profile shell_config must be a path")
    if source is not None and not Path(shell_path).is_absolute():
        shell_path = str((source.parent / shell_path).resolve())
    shell = _parse_shell(Path(shell_path))
    expected = {
        "PROFILE_NAME": name,
        "REMOTE_ROOT": profile["remote_root"],
        "LAN_INTERFACE": network["lan_interface"],
        "TPROXY_PORT": str(ports["tproxy"]),
        "LOCAL_TEST_PORT": str(ports["local_test"]),
        "MARK": network["mark"],
        "ROUTE_TABLE": str(network["route_table"]),
        "RULE_PRIORITY": str(network["rule_priority"]),
        "CHAIN_PREFIX": network["chain_prefix"],
    }
    if any(shell[key] != value for key, value in expected.items()):
        raise SetupError("Profile shell configuration does not match its JSON values")
    normalized = dict(profile)
    normalized["ports"] = dict(ports)
    normalized["network"] = dict(network)
    normalized["shell_values"] = shell
    normalized["shell_config"] = str(Path(shell_path))
    normalized["autostart"] = validate_autostart(profile.get("autostart"))
    return normalized


def load_profile(profile=None) -> dict:
    """Load the named profile or a profile JSON path."""
    if isinstance(profile, dict):
        source = None
        candidate = profile
    else:
        requested = DEFAULT_PROFILE if profile is None else profile
        source = _profile_file(requested)
        try:
            candidate = json.loads(source.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise SetupError(f"Profile not found: {source}") from None
        except (OSError, ValueError):
            raise SetupError(f"Invalid profile JSON: {source}") from None
    return validate_profile(candidate, source)


def profile_remote(profile) -> str:
    return profile.get("remote_root", REMOTE)


def profile_host(profile, supplied=None) -> str:
    return supplied or profile["ssh_host"]


def render_config(template: dict, profile: dict) -> dict:
    """Apply profile ports to a native sing-box template."""
    import copy

    rendered = copy.deepcopy(template)
    inbounds = rendered.get("inbounds", [])
    if not isinstance(inbounds, list):
        raise SetupError("Native configuration inbounds must be an array")
    expected = {"gateway": profile["ports"]["tproxy"], "local-test": profile["ports"]["local_test"]}
    for tag, port in expected.items():
        matches = [item for item in inbounds if isinstance(item, dict) and item.get("tag") == tag]
        if len(matches) != 1:
            raise SetupError(f"Native configuration must contain exactly one {tag} inbound")
        matches[0]["listen_port"] = port
    return rendered

