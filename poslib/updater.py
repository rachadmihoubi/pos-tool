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

import logging
from pathlib import Path

from .paths import app_root

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
    compares as newer than a real release.
    """
    version_file = app_root() / "VERSION"
    if not version_file.is_file():
        return (0, 0, 0)
    parsed = _parse_version(version_file.read_text(encoding="utf-8"))
    return parsed or (0, 0, 0)
