"""Pinned native rule-set download and verification."""

import datetime
import hashlib
import json
import re
from pathlib import Path
import tempfile

from .errors import SetupError
from .io_utils import digest, json_bytes, private_dir, write_private
from .release import download
from .settings import MAX_RULE_DOWNLOAD, ROOT


PINNED_RULES = (
    {"name": "geosite-cn.srs", "repo": "SagerNet/sing-geosite",
     "commit": "4f2dea911f1dbc3fd7516dbc2c82e03a0add71e6",
     "sha256": "87f3206c4272d1b916beccf34cb30fadeae516264d0f422be518715af2e32980",
     "size": 56189},
    {"name": "geoip-cn.srs", "repo": "SagerNet/sing-geoip",
     "commit": "eb32cd6ed4a16fcb3bf9a36ccebf28e45716ae84",
     "sha256": "81ada5cafd9d831adc48776f29cdfe55c9713d9287caeef9de925ef30f66cce4",
     "size": 34268},
)


def rule_url(item):
    return f"https://raw.githubusercontent.com/{item['repo']}/{item['commit']}/{item['name']}"


def git_blob_digest(path):
    content = path.read_bytes()
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()


def verify_rule(path, item):
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_RULE_DOWNLOAD:
        raise SetupError(f"Rule-set is missing or oversized: {path.name}")
    if path.stat().st_size != item["size"] or digest(path) != item["sha256"]:
        raise SetupError(f"Rule-set SHA256/size mismatch: {path.name}; fetch-rules --refresh to replace it")
    with path.open("rb") as source:
        if source.read(3) != b"SRS":
            raise SetupError(f"Rule-set has no native SRS header: {path.name}")
    if "git_blob_sha1" in item and git_blob_digest(path) != item["git_blob_sha1"]:
        raise SetupError(f"Rule-set Git blob mismatch: {path.name}")


def cached_rules(cache):
    manifest_file = cache / "manifest.json"
    if manifest_file.stat().st_size > 1024 * 1024:
        raise SetupError("Rule-set cache manifest exceeds the size limit")
    manifest = json.loads(manifest_file.read_bytes())
    if not isinstance(manifest, dict):
        raise SetupError("Invalid rule-set cache manifest")
    entries = manifest.get("rule_sets", [])
    if manifest.get("format_version") != 1 or not isinstance(entries, list) or len(entries) != len(PINNED_RULES):
        raise SetupError("Invalid rule-set cache manifest")
    for item, pin in zip(entries, PINNED_RULES):
        if (not isinstance(item, dict) or any(item.get(key) != value for key, value in pin.items())
                or ("git_blob_sha1" in item and not re.fullmatch(r"[0-9a-f]{40}", str(item["git_blob_sha1"])))
                or item.get("url") != rule_url(pin)):
            raise SetupError("Invalid rule-set cache manifest entry")
        verify_rule(cache / item["name"], item)
    return cache


def fetch_rules(refresh=False):
    """Fetch native local rules independently of the core; reuse verified pins."""
    cache = ROOT / ".cache" / "rule-sets"
    private_dir(cache)
    if not refresh and (cache / "manifest.json").exists():
        cached_rules(cache)
        print(f"Verified cached native rule-sets: {cache}")
        return cache
    entries = []
    with tempfile.TemporaryDirectory(prefix=".rules-", dir=cache.parent) as stage_name:
        stage = Path(stage_name)
        for pin in PINNED_RULES:
            item = dict(pin)
            item["url"] = rule_url(item)
            path = stage / item["name"]
            download(item["url"], path, limit=MAX_RULE_DOWNLOAD)
            item["git_blob_sha1"] = git_blob_digest(path)
            verify_rule(path, item)
            entries.append(item)
        manifest = {"format_version": 1, "rule_sets": entries,
                    "verification": "Pinned official commit and SHA256",
                    "retrieved_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
        for item in entries:
            write_private(cache / item["name"], (stage / item["name"]).read_bytes())
        write_private(cache / "manifest.json", json_bytes(manifest))
    print(f"Verified official native rule-sets: {cache}")
    return cache

