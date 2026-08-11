"""
Tests for poslib/backup.py - the daily backup job.

Entirely against tmp_path via a minimal stand-in for Config - never touches
the real backups/ folder or the live cache.db, so these are safe to run
anywhere, any time.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from poslib.backup import run_backup


class FakeConfig:
    """Just enough of Config's interface for run_backup() to work with."""

    def __init__(self, source_db: Path, cache_db: Path, owner_db: Path,
                 output_dir: Path, keep_days: int = 30):
        self.source_db = source_db
        self.cache_db = cache_db
        self._owner_db = owner_db
        self._output_dir = output_dir
        self._keep_days = keep_days

    def path(self, key: str, default: str = "") -> Path:
        if key == "catalog.owner_data_db":
            return self._owner_db
        if key == "backup.output_dir":
            return self._output_dir
        return Path(default)

    def get(self, key: str, default=None):
        if key == "backup.keep_days":
            return self._keep_days
        return default


@pytest.fixture
def sandbox(tmp_path):
    source_db = tmp_path / "source.dblx"
    source_db.write_bytes(b"fake access database bytes")
    cache_db = tmp_path / "cache.db"
    cache_db.write_bytes(b"fake sqlite cache bytes")
    owner_db = tmp_path / "owner.db"
    owner_db.write_bytes(b"fake owner data bytes")
    output_dir = tmp_path / "backups"
    return FakeConfig(source_db, cache_db, owner_db, output_dir)


class TestRunBackup:

    def test_creates_dated_folder_with_all_three_files(self, sandbox):
        result = run_backup(sandbox)
        assert result.ran
        today = datetime.date.today().isoformat()
        assert result.folder == sandbox._output_dir / today
        assert result.folder.is_dir()
        assert sorted(result.files) == sorted(
            [sandbox.source_db.name, sandbox.cache_db.name, sandbox._owner_db.name])
        for name in result.files:
            assert (result.folder / name).is_file()

    def test_running_twice_same_day_does_not_duplicate(self, sandbox):
        first = run_backup(sandbox)
        assert first.ran
        second = run_backup(sandbox)
        assert not second.ran
        assert "already" in second.reason.lower()

    def test_missing_files_are_skipped_not_errors(self, tmp_path):
        # Nothing exists yet - a fresh install with no cache or owner data.
        cfg = FakeConfig(
            source_db=tmp_path / "missing.dblx",
            cache_db=tmp_path / "missing-cache.db",
            owner_db=tmp_path / "missing-owner.db",
            output_dir=tmp_path / "backups")
        result = run_backup(cfg)
        assert result.ran
        assert result.files == []
        assert result.folder.is_dir()

    def test_old_backups_are_removed(self, sandbox):
        old_date = (datetime.date.today() - datetime.timedelta(days=45)).isoformat()
        old_folder = sandbox._output_dir / old_date
        old_folder.mkdir(parents=True)
        (old_folder / "old-cache.db").write_bytes(b"stale")

        result = run_backup(sandbox)
        assert old_date in result.removed_folders
        assert not old_folder.exists()

    def test_recent_backups_survive(self, sandbox):
        recent_date = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()
        recent_folder = sandbox._output_dir / recent_date
        recent_folder.mkdir(parents=True)
        (recent_folder / "recent-cache.db").write_bytes(b"still good")

        result = run_backup(sandbox)
        assert recent_date not in result.removed_folders
        assert recent_folder.is_dir()

    def test_non_date_folders_are_left_alone(self, sandbox):
        # Safety check: only ever touch folders that look like our own
        # YYYY-MM-DD dated backups - never something else in that directory.
        stray = sandbox._output_dir / "not-a-date-folder"
        stray.mkdir(parents=True)
        (stray / "something.txt").write_text("do not touch")

        result = run_backup(sandbox)
        assert "not-a-date-folder" not in result.removed_folders
        assert stray.is_dir()
        assert (stray / "something.txt").is_file()
