"""
Regression test for a real bug found 2026-09-14: poslib.etl._tool_version()
read the hardcoded, never-updated poslib.__version__ ("1.0.0" since the
project's very first commit) instead of the real installed version - so the
cache-rebuild-after-tool-update trigger (ETL.needs_refresh/refresh comparing
stored tool_version against a fresh _tool_version() call) could never
actually detect a real version change, and the same stale value would have
been shown if ever surfaced in the UI (which it wasn't, until this fix).

Found while wiring the real app version into the remote dashboard's "Synced"
badge - poslib.updater.current_version() (which reads the real, bumped-every-
release VERSION file) is the one already-correct source of truth for this in
the codebase; _tool_version() now delegates to it instead of duplicating it.
"""
from __future__ import annotations

from poslib import etl, updater


def test_tool_version_matches_updater_current_version(monkeypatch):
    monkeypatch.setattr(updater, "current_version", lambda: (2, 5, 9))
    assert etl._tool_version() == "2.5.9"


def test_tool_version_is_not_the_stale_hardcoded_constant(monkeypatch):
    # Regression guard: before the fix, this always returned "1.0.0"
    # regardless of the real installed version.
    monkeypatch.setattr(updater, "current_version", lambda: (3, 1, 0))
    assert etl._tool_version() != "1.0.0"
    assert etl._tool_version() == "3.1.0"
