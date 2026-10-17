"""Download and verify official sing-box release artifacts."""

import datetime
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import time
import urllib.parse
import urllib.request

from .errors import SetupError
from .io_utils import digest, json_bytes, private_dir, write_private
from .settings import (DEFAULT_VERSION, MAX_DOWNLOAD, PINNED_SHA256, ROOT,
                       VERSION_RE)


def request(url):
    if urllib.parse.urlsplit(url).scheme != "https":
        raise SetupError("Only HTTPS release downloads are accepted")
    response = urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "f50-sing-box-installer"}),
        timeout=30,
    )
    if urllib.parse.urlsplit(response.geturl()).scheme != "https":
        response.close()
        raise SetupError("Only HTTPS download redirects are accepted")
    return response


def download(url, target: Path, limit=MAX_DOWNLOAD):
    private_dir(target.parent)
    pending = target.with_name(target.name + ".partial")
    started = time.monotonic()
    try:
        with request(url) as response, pending.open("wb") as output:
            length = response.headers.get("Content-Length")
            if length and int(length) > limit:
                raise SetupError("Release download exceeds the size limit")
            count = 0
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                count += len(block)
                if count > limit or time.monotonic() - started > 240:
                    raise SetupError("Release download exceeded its size/time limit")
                output.write(block)
        import os
        os.replace(pending, target)
    finally:
        pending.unlink(missing_ok=True)


def fetch(version=DEFAULT_VERSION, profile=None):
    """Fetch and verify a stable Android arm64 official release."""
    if not VERSION_RE.fullmatch(version):
        raise SetupError("Use a stable version such as 1.14.3")
    if profile is not None and profile.get("platform", "android-arm64") != "android-arm64":
        raise SetupError("This release fetcher supports the Android arm64 platform only")
    cache = ROOT / ".cache" / version
    private_dir(cache)
    release_url = f"https://api.github.com/repos/SagerNet/sing-box/releases/tags/v{version}"
    release_file = cache / "release.json"
    if not release_file.exists():
        download(release_url, release_file, limit=8 * 1024 * 1024)
    release = json.loads(release_file.read_text())
    if release.get("tag_name") != f"v{version}" or release.get("draft") or release.get("prerelease"):
        raise SetupError("GitHub did not return the requested stable release")
    asset_name = f"sing-box-{version}-android-arm64.tar.gz"
    matches = [item for item in release.get("assets", []) if item.get("name") == asset_name]
    if len(matches) != 1:
        raise SetupError("Expected exactly one official Android arm64 archive")
    asset = matches[0]
    official_digest = asset.get("digest", "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", official_digest):
        raise SetupError("Official release metadata has no SHA256 digest; refusing an unverified binary")
    expected = official_digest.split(":", 1)[1]
    if version == DEFAULT_VERSION and expected != PINNED_SHA256:
        raise SetupError("Official default-release digest differs from the pinned SHA256")
    url = asset.get("browser_download_url", "")
    expected_url = f"https://github.com/SagerNet/sing-box/releases/download/v{version}/{asset_name}"
    if url != expected_url:
        raise SetupError("Unexpected official archive URL")
    archive = cache / asset_name
    if not archive.exists():
        print(f"Downloading official sing-box {version} Android arm64…", flush=True)
        download(url, archive)
    if digest(archive) != expected:
        raise SetupError(f"SHA256 mismatch: remove {archive} and fetch again")
    binary = cache / "sing-box"
    expected_member = f"sing-box-{version}-android-arm64/sing-box"
    with tarfile.open(archive, "r:gz") as package:
        members = package.getmembers()
        for member in members:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or member.issym() or member.islnk():
                raise SetupError("Unsafe path or link in release archive")
        matches = [member for member in members if member.name == expected_member]
        if len(matches) != 1 or not matches[0].isfile() or matches[0].size > MAX_DOWNLOAD:
            raise SetupError("Official archive has an unexpected binary layout")
        source = package.extractfile(matches[0])
        if source is None:
            raise SetupError("Could not read official core from archive")
        with source:
            content = source.read(MAX_DOWNLOAD + 1)
        if len(content) > MAX_DOWNLOAD or not content.startswith(b"\x7fELF"):
            raise SetupError("The Android core is not a bounded ELF binary")
        write_private(binary, content, 0o700)
    manifest = {
        "version": version,
        "platform": "android-arm64",
        "release_url": release["html_url"],
        "metadata_url": release_url,
        "archive_url": url,
        "archive_sha256": expected,
        "binary_sha256": digest(binary),
        "verification": "GitHub official release asset SHA256 digest",
        "retrieved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    write_private(cache / "manifest.json", json_bytes(manifest))
    print(f"Verified sing-box {version}: {expected}")
    return cache

