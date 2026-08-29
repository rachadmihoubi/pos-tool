"""
Tests for poslib/etl.py's cross-process cache-rebuild lock (ETL._locked).

Regression coverage for a real bug reproduced live 2026-08-29: two ETL
instances started around the same time (the auto-started watcher and an
interactively launched dashboard, both hitting the same real database right
after install) raced on the same cache.building temp file - one held it open
via sqlite3.connect while the other's unlink() call hit a PermissionError
([WinError 32], file in use), crashing outright. See ETL._locked's own
docstring in poslib/etl.py.

These tests exercise the real OS-level msvcrt lock (not a mock) using two
threads, since Windows file-range locks are held per open file handle
regardless of which thread opened it - a second thread opening its own
handle to the same lock file genuinely blocks, the same as a second process
would.
"""
from __future__ import annotations

import threading
import time

from poslib.etl import ETL


class _FakeConfig:
    """Just enough of Config's surface for ETL.__init__ / ETL._locked."""

    def __init__(self, tmp_path):
        self.source_db = tmp_path / "source.dblx"
        self.cache_db = tmp_path / "cache.db"


def test_locked_creates_a_lock_file_next_to_the_cache(tmp_path):
    etl = ETL(_FakeConfig(tmp_path))
    with etl._locked():
        assert (tmp_path / "cache.lock").is_file()


def test_locked_serializes_two_concurrent_acquirers(tmp_path):
    etl = ETL(_FakeConfig(tmp_path))

    second_acquired_at = []
    first_released_at = []

    def hold_then_release():
        with etl._locked():
            first_released_at.append(time.monotonic())
            time.sleep(0.3)
        first_released_at.append(time.monotonic())

    def try_to_acquire():
        # Give the first thread a head start so it holds the lock first.
        time.sleep(0.05)
        with etl._locked():
            second_acquired_at.append(time.monotonic())

    t1 = threading.Thread(target=hold_then_release)
    t2 = threading.Thread(target=try_to_acquire)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert not t1.is_alive() and not t2.is_alive()
    # The second thread must not have acquired the lock until the first
    # thread actually released it - this is the whole point of the fix.
    assert second_acquired_at[0] >= first_released_at[-1]


def test_refresh_acquires_the_lock(tmp_path, monkeypatch):
    etl = ETL(_FakeConfig(tmp_path))

    calls = []
    monkeypatch.setattr(etl, "_refresh_locked", lambda force: calls.append(force) or "result")

    result = etl.refresh(force=True)

    assert result == "result"
    assert calls == [True]
    # The lock file should exist and be free again once refresh() returns.
    assert (tmp_path / "cache.lock").is_file()
    with etl._locked():
        pass  # would hang/raise if refresh() had left the lock held
