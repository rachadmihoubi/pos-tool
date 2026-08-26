"""
Tests for Watcher._check_for_update - the thin wrapper around
poslib.updater.check_and_apply_update. Constructs a bare Watcher instance
(bypassing __init__, which needs a real ETL/database) since this method
only touches self.cfg.
"""

from __future__ import annotations

import poslib.updater as updater_module
from watcher import Watcher


class FakeConfig:
    pass


def _bare_watcher(cfg) -> Watcher:
    watcher = Watcher.__new__(Watcher)
    watcher.cfg = cfg
    return watcher


def test_check_for_update_delegates_to_updater_and_returns_true(monkeypatch):
    seen = {}

    def _fake_check_and_apply_update(cfg):
        seen["cfg"] = cfg
        return True
    monkeypatch.setattr(updater_module, "check_and_apply_update", _fake_check_and_apply_update)

    cfg = FakeConfig()
    watcher = _bare_watcher(cfg)

    assert watcher._check_for_update() is True
    assert seen["cfg"] is cfg


def test_check_for_update_returns_false_when_no_update(monkeypatch):
    monkeypatch.setattr(updater_module, "check_and_apply_update", lambda cfg: False)

    assert _bare_watcher(FakeConfig())._check_for_update() is False


def test_check_for_update_never_raises_on_unexpected_error(monkeypatch):
    def _raise(cfg):
        raise RuntimeError("boom")
    monkeypatch.setattr(updater_module, "check_and_apply_update", _raise)

    assert _bare_watcher(FakeConfig())._check_for_update() is False
