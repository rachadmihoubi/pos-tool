"""
updater.py - checks GitHub Releases for a newer version and, if found,
downloads, verifies, and silently installs it.

Only ever active in a packaged build (poslib.paths.is_frozen()) - this dev
PC runs watcher.py directly via Python and must never try to download and
run a Windows installer on itself. Every public function here follows
poslib/remote.py's rule: never raise. A failed check, download, or install
attempt logs and gives up until the next watcher startup - it must never
crash the watcher.

See docs/superpowers/specs/2026-08-26-component3-auto-update-design.md for
the full design.
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
from pathlib import Path

import requests

from .config import Config
from .paths import app_root, is_frozen

log = logging.getLogger(__name__)


def _parse_version(text: str) -> tuple[int, int, int] | None:
    text = text.strip()
    if text[:1] in ("v", "V"):
        text = text[1:]
    parts = text.split(".")
    if len(parts) != 3:
        return None
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def current_version() -> tuple[int, int, int]:
    """
    This build's own version, read from the bundled VERSION file. Returns
    (0, 0, 0) if the file is missing or unparseable, which safely never
    compares as newer than a real release. Never raises - all I/O failures
    are caught and treated the same as a missing file.
    """
    version_file = app_root() / "VERSION"
    try:
        if not version_file.is_file():
            return (0, 0, 0)
        text = version_file.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return (0, 0, 0)
    parsed = _parse_version(text)
    return parsed or (0, 0, 0)


_GITHUB_API_TIMEOUT_SECONDS = 15


@dataclasses.dataclass
class ReleaseInfo:
    version: tuple[int, int, int]
    tag_name: str
    installer_url: str
    checksum_url: str


def _fetch_latest_release(repo: str) -> dict | None:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        resp = requests.get(url, timeout=_GITHUB_API_TIMEOUT_SECONDS,
                            headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("Could not check for updates: %s", exc)
        return None


def _asset_url(release: dict, name: str) -> str | None:
    for asset in release.get("assets", []):
        if asset.get("name") == name:
            return asset.get("browser_download_url")
    return None


def check_for_update(cfg: Config) -> ReleaseInfo | None:
    """
    Returns a ReleaseInfo if GitHub Releases has a version newer than this
    build's own VERSION file, else None. A no-op (returns None without any
    network call) unless running from a frozen build - see this module's
    docstring for why. Never raises.
    """
    if not is_frozen():
        return None
    if not bool(cfg.get("update.enabled", True)):
        return None

    repo = str(cfg.get("update.github_repo", "")).strip()
    if not repo:
        return None

    release = _fetch_latest_release(repo)
    if release is None:
        return None

    tag_name = str(release.get("tag_name", ""))
    remote_version = _parse_version(tag_name)
    if remote_version is None:
        log.warning("Could not parse a version from release tag %r", tag_name)
        return None

    if remote_version <= current_version():
        return None

    installer_url = _asset_url(release, "Setup.exe")
    checksum_url = _asset_url(release, "Setup.exe.sha256")
    if not installer_url or not checksum_url:
        log.warning("Release %s is missing Setup.exe or Setup.exe.sha256 - skipping.", tag_name)
        return None

    return ReleaseInfo(version=remote_version, tag_name=tag_name,
                        installer_url=installer_url, checksum_url=checksum_url)


_DOWNLOAD_TIMEOUT_SECONDS = 120


def _download(url: str, dest: Path) -> bool:
    try:
        resp = requests.get(url, timeout=_DOWNLOAD_TIMEOUT_SECONDS)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        return True
    except (requests.RequestException, OSError) as exc:
        log.warning("Could not download %s: %s", url, exc)
        return False


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download_and_verify(release: ReleaseInfo, dest_dir: Path) -> Path | None:
    """
    Downloads Setup.exe and its .sha256 into dest_dir and verifies the
    hash. Returns the path to the verified installer, or None on any
    failure (download error or checksum mismatch) - the caller must not
    run an installer this returns None for. Never raises.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    installer_path = dest_dir / "Setup.exe"
    checksum_path = dest_dir / "Setup.exe.sha256"

    if not _download(release.checksum_url, checksum_path):
        return None
    if not _download(release.installer_url, installer_path):
        return None

    expected = checksum_path.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = _sha256_of(installer_path)
    if actual.lower() != expected:
        log.error("Checksum mismatch for %s: expected %s, got %s",
                   release.tag_name, expected, actual)
        installer_path.unlink(missing_ok=True)
        checksum_path.unlink(missing_ok=True)
        return None

    return installer_path
