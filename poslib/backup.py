"""
backup.py - a daily copy of everything that cannot be rebuilt if this
computer's disk fails: the source database, the fast local cache, and the
owner-entered data (competitor prices).

Cheap insurance, nothing more. Never touches the source file for anything
but reading - the existing read-only-copy helper is reused here, not
duplicated, so this follows the same sharing-aware retry logic as the
regular ETL refresh.
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
import shutil
import tempfile
from pathlib import Path

from .config import Config
from .etl import copy_database_readonly

log = logging.getLogger(__name__)


@dataclasses.dataclass
class BackupResult:
    ran: bool
    folder: Path
    files: list[str]
    removed_folders: list[str]
    reason: str


def _backup_root(cfg: Config) -> Path:
    return cfg.path("backup.output_dir", "backups")


def run_backup(cfg: Config) -> BackupResult:
    """
    Copy the source database, the cache, and the owner-entered data into
    backups/YYYY-MM-DD/, then delete folders older than `backup.keep_days`.

    Safe to call more than once a day - a folder that already exists for
    today is left alone, not overwritten or duplicated. Each file is
    optional: a fresh install with no cache yet, or no competitor prices
    logged yet, still backs up whatever does exist rather than failing.
    """
    root = _backup_root(cfg)
    today_folder = root / datetime.date.today().isoformat()

    if today_folder.is_dir():
        return BackupResult(ran=False, folder=today_folder, files=[],
                            removed_folders=[], reason="Already backed up today.")

    today_folder.mkdir(parents=True, exist_ok=True)
    files: list[str] = []

    if cfg.source_db.is_file():
        working_copy = copy_database_readonly(
            cfg.source_db, dst_dir=Path(tempfile.gettempdir()) / "pos-tool-backup-work")
        dest = today_folder / cfg.source_db.name
        shutil.copy2(working_copy, dest)
        files.append(dest.name)

    if cfg.cache_db.is_file():
        dest = today_folder / cfg.cache_db.name
        shutil.copy2(cfg.cache_db, dest)
        files.append(dest.name)

    owner_db = cfg.path("catalog.owner_data_db", "data/owner.db")
    if owner_db.is_file():
        dest = today_folder / owner_db.name
        shutil.copy2(owner_db, dest)
        files.append(dest.name)

    removed = _remove_old_backups(root, keep_days=int(cfg.get("backup.keep_days", 30)))

    return BackupResult(ran=True, folder=today_folder, files=files,
                        removed_folders=removed,
                        reason=f"Backed up {len(files)} file(s).")


def _remove_old_backups(root: Path, keep_days: int) -> list[str]:
    """Delete dated backup folders older than `keep_days`. Only ever
    touches folders that look like one of ours (YYYY-MM-DD)."""
    if not root.is_dir():
        return []

    cutoff = datetime.date.today() - datetime.timedelta(days=keep_days)
    removed: list[str] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        try:
            folder_date = datetime.date.fromisoformat(entry.name)
        except ValueError:
            continue  # not one of ours - leave it alone
        if folder_date < cutoff:
            shutil.rmtree(entry, ignore_errors=True)
            removed.append(entry.name)
    return removed
