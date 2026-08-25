"""
Tests for main.py - the single entry point the packaged build launches.

No flag -> the dashboard. --watcher -> the watcher, forwarding its own
flags. Both app.main() and watcher.main() are mocked here; they have their
own test coverage already (or are exercised directly by start.bat/
start-quiet.bat in the dev workflow) - this file only tests the dispatch.
"""

from __future__ import annotations

import main as main_module


def test_no_flags_runs_the_dashboard(monkeypatch):
    calls = []
    monkeypatch.setattr(main_module.app, "main", lambda: calls.append("dashboard") or 0)
    monkeypatch.setattr(main_module.watcher, "main", lambda: calls.append("watcher") or 0)
    assert main_module.main([]) == 0
    assert calls == ["dashboard"]


def test_watcher_flag_runs_the_watcher(monkeypatch):
    calls = []
    monkeypatch.setattr(main_module.app, "main", lambda: calls.append("dashboard") or 0)
    monkeypatch.setattr(main_module.watcher, "main", lambda: calls.append("watcher") or 0)
    assert main_module.main(["--watcher"]) == 0
    assert calls == ["watcher"]


def test_watcher_flags_are_forwarded_to_sys_argv(monkeypatch):
    seen_argv = []

    def fake_watcher_main():
        seen_argv.append(list(main_module.sys.argv))
        return 0

    monkeypatch.setattr(main_module.watcher, "main", fake_watcher_main)
    assert main_module.main(["--watcher", "--digest-now"]) == 0
    assert seen_argv == [["watcher.py", "--digest-now"]]


def test_dashboard_flags_are_forwarded_to_sys_argv(monkeypatch):
    seen_argv = []

    def fake_app_main():
        seen_argv.append(list(main_module.sys.argv))
        return 0

    monkeypatch.setattr(main_module.app, "main", fake_app_main)
    assert main_module.main(["--no-browser"]) == 0
    assert seen_argv == [["app.py", "--no-browser"]]
