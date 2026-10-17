"""Strict VLESS URL importer for native sing-box JSON."""

import re
import urllib.parse
import uuid

from .errors import SetupError


def parse_vless(text: str) -> dict:
    """Parse the supported VLESS TCP URL subset without exposing secrets."""
    try:
        parsed = urllib.parse.urlsplit(text.strip())
        port = parsed.port
        server = parsed.hostname
        user_id = str(uuid.UUID(urllib.parse.unquote(parsed.username or "")))
    except (ValueError, TypeError):
        raise SetupError("Invalid VLESS URL, UUID, host or port") from None
    if parsed.scheme != "vless" or not server or not port or parsed.password or parsed.path not in ("", "/"):
        raise SetupError("Expected a VLESS URL with UUID, host and port")
    pairs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    allowed = {"encryption", "security", "flow", "type", "sni", "pbk", "fp", "sid", "alpn"}
    if set(pairs) - allowed or any(len(values) != 1 for values in pairs.values()):
        raise SetupError("Unsupported or duplicate VLESS URL parameters; edit native JSON for other transports")
    params = {key: values[0] for key, values in pairs.items()}
    if params.get("encryption", "none") != "none" or params.get("type", "tcp") != "tcp":
        raise SetupError("This importer supports VLESS TCP; use native JSON for other transports")
    security = params.get("security", "none")
    if security not in {"none", "tls", "reality"}:
        raise SetupError("Unsupported VLESS security type")
    flow = params.get("flow", "")
    if flow not in {"", "xtls-rprx-vision"}:
        raise SetupError("Unsupported VLESS flow")
    if flow and security == "none":
        raise SetupError("Vision requires TLS or Reality")
    outbound = {"type": "vless", "tag": "proxy", "server": server,
                "server_port": port, "uuid": user_id}
    if flow:
        outbound["flow"] = flow
    if security != "none":
        tls = {"enabled": True, "server_name": params.get("sni", server)}
        fingerprint = params.get("fp", "chrome" if security == "reality" else "")
        if fingerprint:
            tls["utls"] = {"enabled": True, "fingerprint": fingerprint}
        if params.get("alpn"):
            tls["alpn"] = params["alpn"].split(",")
        if security == "reality":
            if not re.fullmatch(r"[A-Za-z0-9_-]{43}", params.get("pbk", "")):
                raise SetupError("Reality requires a valid 32-byte public key")
            short_id = params.get("sid", "")
            if len(short_id) > 16 or len(short_id) % 2 or not re.fullmatch(r"[0-9a-fA-F]*", short_id):
                raise SetupError("Reality short_id must be an even-length hex string of at most 16 characters")
            tls["reality"] = {"enabled": True, "public_key": params["pbk"], "short_id": short_id}
        outbound["tls"] = tls
    elif any(params.get(key) for key in ("sni", "pbk", "fp", "sid", "alpn")):
        raise SetupError("TLS parameters require TLS or Reality security")
    return outbound

