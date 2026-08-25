"""
paths.py - where the app reads its own bundled files from, and where it
writes its data.

Two different questions, both frozen-mode-aware:

  app_root()       Read-only. Where templates/, static/, and the app's own
                    code live. In dev, the project folder. In a packaged
                    build, the folder next to ShopAnalysis.exe.

  user_data_dir()   Writable. config.yaml, .env, cache.db, logs, digests,
                    backups, the remote export, and the owner's own typed-in
                    data all live here. In dev, the same as app_root() - this
                    machine's layout is unchanged. In a packaged build,
                    %LOCALAPPDATA%\\Shop Analysis, since a normal Windows
                    install (Program Files, or a Task Scheduler entry set to
                    /rl limited) is not reliably writable by the app itself.

Every place in the codebase that used to compute PROJECT_ROOT by hand goes
through here instead, so there is exactly one frozen/dev branch, not one per
file.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """True inside a PyInstaller-built exe, false in every dev/test run."""
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """Read-only root: the folder templates/, static/ and poslib/ live in."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def user_data_dir() -> Path:
    """Writable root: config.yaml, .env, cache.db, logs, digests, backups."""
    if is_frozen():
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
        return base / "Shop Analysis"
    return app_root()
