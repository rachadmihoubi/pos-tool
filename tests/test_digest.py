"""
Tests for poslib/digest.py's remote-sync staleness warning, added
2026-09-14 per CLAUDE.md's "Bug #2" ("give the non-technical owner a
visible signal when sync goes stale"). Scoped to just this new piece -
digest.py's own content-building otherwise needs the real database (see
tests/conftest.py's session-scoped cfg/metrics fixtures used elsewhere in
this suite) and isn't covered here.
"""
from __future__ import annotations

from poslib import digest, remote


class FakeConfig:
    """Just enough of Config's interface for _remote_sync_stale_hours."""

    def __init__(self, **overrides):
        self._overrides = overrides

    def get(self, key, default=None):
        return self._overrides.get(key, default)


class TestRemoteSyncStaleHours:

    def test_none_when_remote_disabled(self, monkeypatch):
        monkeypatch.setattr(remote, "remote_push_success_age_seconds", lambda: 999999)
        assert digest._remote_sync_stale_hours(FakeConfig(**{"remote.enabled": False})) is None

    def test_none_when_never_synced_yet(self, monkeypatch):
        """A fresh install with remote just turned on shouldn't alarm on day one."""
        monkeypatch.setattr(remote, "remote_push_success_age_seconds", lambda: None)
        assert digest._remote_sync_stale_hours(FakeConfig(**{"remote.enabled": True})) is None

    def test_none_when_recent(self, monkeypatch):
        monkeypatch.setattr(remote, "remote_push_success_age_seconds", lambda: 3600)  # 1h
        cfg = FakeConfig(**{"remote.enabled": True})
        assert digest._remote_sync_stale_hours(cfg) is None

    def test_none_right_at_the_threshold(self, monkeypatch):
        """Exactly 24h is not yet "over 24h" (the locale text's own wording)."""
        monkeypatch.setattr(remote, "remote_push_success_age_seconds", lambda: 24 * 3600)
        cfg = FakeConfig(**{"remote.enabled": True})
        assert digest._remote_sync_stale_hours(cfg) is None

    def test_stale_just_past_the_threshold(self, monkeypatch):
        monkeypatch.setattr(remote, "remote_push_success_age_seconds", lambda: 24 * 3600 + 1)
        cfg = FakeConfig(**{"remote.enabled": True})
        assert digest._remote_sync_stale_hours(cfg) is not None

    def test_hours_when_stale(self, monkeypatch):
        monkeypatch.setattr(remote, "remote_push_success_age_seconds", lambda: 30 * 3600)  # 30h
        cfg = FakeConfig(**{"remote.enabled": True})
        assert digest._remote_sync_stale_hours(cfg) == 30.0

    def test_custom_threshold_from_config(self, monkeypatch):
        monkeypatch.setattr(remote, "remote_push_success_age_seconds", lambda: 5 * 3600)  # 5h
        cfg = FakeConfig(**{"remote.enabled": True, "remote.stale_warning_hours": 4})
        assert digest._remote_sync_stale_hours(cfg) == 5.0


class TestDigestRendersTheWarning:

    def _digest(self, **overrides):
        import datetime
        return digest.Digest(for_date=datetime.date(2026, 9, 13), **overrides)

    def test_text_includes_the_warning_when_stale(self):
        d = self._digest(remote_sync_stale_hours=30.0)
        text = d.text("en")
        assert "30" in text
        assert "not updated" in text.lower() or "hours" in text.lower()

    def test_text_omits_the_warning_when_not_stale(self):
        d = self._digest(remote_sync_stale_hours=None)
        text = d.text("en")
        assert "not updated" not in text.lower()

    def test_html_includes_the_warning_when_stale(self):
        # No "tickets" key -> html() takes its no-sales-yet branch, which
        # doesn't need a full numbers dict - this test is only about the
        # warning banner's own presence, not the sales tiles.
        d = self._digest(remote_sync_stale_hours=30.0)
        html = d.html("en")
        assert "30" in html

    def test_html_omits_the_warning_when_not_stale(self):
        d = self._digest(remote_sync_stale_hours=None)
        html = d.html("en")
        assert "not updated" not in html.lower()
