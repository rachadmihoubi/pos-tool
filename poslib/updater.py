"""
updater.py - checks GitHub Releases for a newer version and, if found,
downloads, verifies, and silently installs it.

Only ever active in a packaged build (poslib.paths.is_frozen()) - this dev
PC runs watcher.py directly via Python and must never try to download and
run a Windows installer on itself. Every public function here follows
poslib/remote.py's rule: never raise. A failed check, download, or install
attempt logs and gives up until the next watcher startup - it must never
crash the watcher.

Invoked by main.py's --apply-update, run from a separate always-elevated
scheduled task, not the (deliberately de-elevated) watcher itself - see
docs/superpowers/specs/2026-08-27-update-elevation-fix.md for why.

A small on-disk marker (update_attempted.txt in user_data_dir()) records
the tag of the last release an install was actually launched for, so a
mis-cut release (bundled VERSION not actually bumped) is detected and
skipped instead of looping download->install->relaunch forever - see
_read_last_attempted_tag/_write_attempted_tag below.

See docs/superpowers/specs/2026-08-26-component3-auto-update-design.md for
the full original design.
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import subprocess
import tempfile
from pathlib import Path

import requests

from .config import Config
from .paths import app_root, is_frozen, user_data_dir

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


_ATTEMPTED_MARKER_NAME = "update_attempted.txt"


def _attempted_marker_path() -> Path:
    return user_data_dir() / _ATTEMPTED_MARKER_NAME


def _read_last_attempted_tag() -> str | None:
    """
    The tag_name of the last release check_and_apply_update() actually
    launched an installer for, or None if there isn't one / it can't be
    read. Never raises - a missing or unreadable marker is treated the same
    as "nothing attempted yet", which just means one more retry, not a
    crash.
    """
    try:
        path = _attempted_marker_path()
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _write_attempted_tag(tag_name: str) -> None:
    """
    Records that an install for tag_name was just launched, so a mis-cut
    release (bundled VERSION not actually newer, so current_version() never
    advances) can be detected next check instead of silently
    download-install-relaunching forever. Never raises - if this write
    fails, the worst case is one possible extra retry, not a crash.
    """
    try:
        path = _attempted_marker_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tag_name, encoding="utf-8")
    except OSError as exc:
        log.warning("Could not record the update-attempt marker: %s", exc)


def check_for_update(cfg: Config) -> ReleaseInfo | None:
    """
    Returns a ReleaseInfo if GitHub Releases has a version newer than this
    build's own VERSION file, else None. A no-op (returns None without any
    network call) unless running from a frozen build - see this module's
    docstring for why. Never raises.
    """
    if not is_frozen():
        log.debug("Not a frozen build - skipping update check.")
        return None
    if not bool(cfg.get("update.enabled", False)):
        log.info("Auto-update disabled via config - skipping check.")
        return None

    repo = str(cfg.get("update.github_repo", "")).strip()
    if not repo:
        log.warning("update.github_repo is not set - cannot check for updates.")
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

    if _read_last_attempted_tag() == tag_name:
        log.error(
            "Release %s was already attempted but this build's version did "
            "not change afterwards - the release is likely mis-cut (bundled "
            "VERSION file not actually bumped). Not retrying automatically; "
            "publish a corrected release to resume.", tag_name)
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
        installer_path.unlink(missing_ok=True)
        checksum_path.unlink(missing_ok=True)
        return None
    if not _download(release.installer_url, installer_path):
        installer_path.unlink(missing_ok=True)
        checksum_path.unlink(missing_ok=True)
        return None

    try:
        expected = checksum_path.read_text(encoding="utf-8").strip().split()[0].lower()
        actual = _sha256_of(installer_path)
    except (OSError, UnicodeDecodeError, IndexError) as exc:
        log.error("Could not read or parse checksum for %s: %s",
                   release.tag_name, exc)
        installer_path.unlink(missing_ok=True)
        checksum_path.unlink(missing_ok=True)
        return None

    if actual.lower() != expected:
        log.error("Checksum mismatch for %s: expected %s, got %s",
                   release.tag_name, expected, actual)
        installer_path.unlink(missing_ok=True)
        checksum_path.unlink(missing_ok=True)
        return None

    return installer_path


def launch_silent_install(installer_path: Path) -> bool:
    """
    Spawns the installer detached and returns immediately without waiting
    for it to finish - the caller must stop and exit right after this so
    the installer can replace this process's own files once the OS
    releases the lock. Returns True if the process was launched, False if
    spawning itself failed. Never raises.
    """
    try:
        subprocess.Popen(
            [str(installer_path), "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"],
            close_fds=True,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        return True
    except OSError as exc:
        log.error("Could not launch the installer: %s", exc)
        return False


def check_and_apply_update(cfg: Config) -> bool:
    """
    The one entry point watcher.py calls. Checks for a newer release and,
    if everything checks out (found, downloaded, checksum verified,
    installer launched), returns True - the caller must stop and exit
    immediately so the installer can replace the running files. Returns
    False if there's no update or any step failed; the next attempt is the
    next watcher startup. Never raises.
    """
    try:
        release = check_for_update(cfg)
        if release is None:
            return False

        log.info("Update found: %s - downloading and verifying.", release.tag_name)
        dest_dir = Path(tempfile.mkdtemp(prefix="shop-analysis-update-"))
        installer_path = download_and_verify(release, dest_dir)
        if installer_path is None:
            log.warning("Update download/verification failed - will try again next login.")
            return False

        if not launch_silent_install(installer_path):
            return False

        _write_attempted_tag(release.tag_name)
        log.info("Installer launched for %s - stopping so it can replace running files.",
                  release.tag_name)
        return True

    except Exception as exc:
        log.warning("Could not apply update: %s", exc)
        return False
