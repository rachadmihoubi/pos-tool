"""
Tests for poslib/updater.py - the silent auto-update check.

Entirely mocked - never makes a real network call, never spawns a real
process. See docs/superpowers/specs/2026-08-26-component3-auto-update-design.md
for the design this implements.
"""

from __future__ import annotations

import hashlib
import pytest
import requests

from poslib import updater


def test_current_version_reads_and_parses_the_version_file(monkeypatch, tmp_path):
    version_file = tmp_path / "VERSION"
    version_file.write_text("2.5.11\n", encoding="utf-8")
    monkeypatch.setattr(updater, "app_root", lambda: tmp_path)

    assert updater.current_version() == (2, 5, 11)


def test_current_version_falls_back_to_zero_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "app_root", lambda: tmp_path)

    assert updater.current_version() == (0, 0, 0)


@pytest.mark.parametrize("text,expected", [
    ("1.2.3", (1, 2, 3)),
    ("v1.2.3", (1, 2, 3)),
    ("V1.2.3", (1, 2, 3)),
    (" 1.2.3 \n", (1, 2, 3)),
    ("1.2", None),
    ("1.2.3.4", None),
    ("not-a-version", None),
    ("", None),
])
def test_parse_version(text, expected):
    assert updater._parse_version(text) == expected


def test_current_version_catches_read_failures(monkeypatch, tmp_path):
    # Create VERSION as a directory instead of a file to trigger IsADirectoryError
    # when read_text() is called (IsADirectoryError is a subclass of OSError)
    version_dir = tmp_path / "VERSION"
    version_dir.mkdir()
    monkeypatch.setattr(updater, "app_root", lambda: tmp_path)

    # Attempting to read a directory as text will raise OSError
    assert updater.current_version() == (0, 0, 0)


def test_current_version_catches_decode_failures(monkeypatch, tmp_path):
    version_file = tmp_path / "VERSION"
    version_file.write_bytes(b"\xff\xfe")  # Invalid UTF-8
    monkeypatch.setattr(updater, "app_root", lambda: tmp_path)

    # This should catch UnicodeDecodeError (a ValueError) and return (0, 0, 0)
    assert updater.current_version() == (0, 0, 0)


class FakeConfig:
    def __init__(self, enabled=True, repo="rachadmihoubi/pos-tool"):
        self._enabled = enabled
        self._repo = repo

    def get(self, key, default=None):
        if key == "update.enabled":
            return self._enabled
        if key == "update.github_repo":
            return self._repo
        return default


class FakeResponse:
    def __init__(self, json_data=None, status_code=200):
        self._json_data = json_data or {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._json_data


def _release_json(tag_name, with_assets=True):
    assets = []
    if with_assets:
        assets = [
            {"name": "Setup.exe", "browser_download_url": "https://example.test/Setup.exe"},
            {"name": "Setup.exe.sha256",
             "browser_download_url": "https://example.test/Setup.exe.sha256"},
        ]
    return {"tag_name": tag_name, "assets": assets}


class TestCheckForUpdate:

    def test_skips_when_not_frozen(self, monkeypatch):
        monkeypatch.setattr(updater, "is_frozen", lambda: False)

        def _fail_if_called(*a, **k):
            raise AssertionError("must not make a network call when not frozen")
        monkeypatch.setattr(updater.requests, "get", _fail_if_called)

        assert updater.check_for_update(FakeConfig()) is None

    def test_skips_when_disabled_in_config(self, monkeypatch):
        monkeypatch.setattr(updater, "is_frozen", lambda: True)

        def _fail_if_called(*a, **k):
            raise AssertionError("must not make a network call when disabled")
        monkeypatch.setattr(updater.requests, "get", _fail_if_called)

        assert updater.check_for_update(FakeConfig(enabled=False)) is None

    def test_returns_release_info_when_newer_version_available(self, monkeypatch):
        monkeypatch.setattr(updater, "is_frozen", lambda: True)
        monkeypatch.setattr(updater, "current_version", lambda: (1, 0, 0))
        monkeypatch.setattr(updater.requests, "get",
                            lambda url, **k: FakeResponse(_release_json("v1.2.0")))

        result = updater.check_for_update(FakeConfig())

        assert result == updater.ReleaseInfo(
            version=(1, 2, 0), tag_name="v1.2.0",
            installer_url="https://example.test/Setup.exe",
            checksum_url="https://example.test/Setup.exe.sha256")

    def test_returns_none_when_not_newer(self, monkeypatch):
        monkeypatch.setattr(updater, "is_frozen", lambda: True)
        monkeypatch.setattr(updater, "current_version", lambda: (1, 2, 0))
        monkeypatch.setattr(updater.requests, "get",
                            lambda url, **k: FakeResponse(_release_json("v1.2.0")))

        assert updater.check_for_update(FakeConfig()) is None

    def test_returns_none_when_equal_version(self, monkeypatch):
        monkeypatch.setattr(updater, "is_frozen", lambda: True)
        monkeypatch.setattr(updater, "current_version", lambda: (1, 2, 0))
        monkeypatch.setattr(updater.requests, "get",
                            lambda url, **k: FakeResponse(_release_json("v1.1.9")))

        assert updater.check_for_update(FakeConfig()) is None

    def test_returns_none_on_request_failure(self, monkeypatch):
        monkeypatch.setattr(updater, "is_frozen", lambda: True)

        def _raise(*a, **k):
            raise requests.ConnectionError("offline")
        monkeypatch.setattr(updater.requests, "get", _raise)

        assert updater.check_for_update(FakeConfig()) is None

    def test_returns_none_on_unparseable_tag(self, monkeypatch):
        monkeypatch.setattr(updater, "is_frozen", lambda: True)
        monkeypatch.setattr(updater, "current_version", lambda: (1, 0, 0))
        monkeypatch.setattr(updater.requests, "get",
                            lambda url, **k: FakeResponse(_release_json("not-a-version")))

        assert updater.check_for_update(FakeConfig()) is None

    def test_returns_none_when_assets_missing(self, monkeypatch):
        monkeypatch.setattr(updater, "is_frozen", lambda: True)
        monkeypatch.setattr(updater, "current_version", lambda: (1, 0, 0))
        monkeypatch.setattr(
            updater.requests, "get",
            lambda url, **k: FakeResponse(_release_json("v1.2.0", with_assets=False)))

        assert updater.check_for_update(FakeConfig()) is None


class TestDownloadAndVerify:

    def _release(self):
        return updater.ReleaseInfo(
            version=(1, 2, 0), tag_name="v1.2.0",
            installer_url="https://example.test/Setup.exe",
            checksum_url="https://example.test/Setup.exe.sha256")

    def test_downloads_and_returns_installer_path_on_matching_checksum(
            self, monkeypatch, tmp_path):
        installer_bytes = b"fake installer bytes"
        digest = hashlib.sha256(installer_bytes).hexdigest()

        def _fake_get(url, **kwargs):
            if url.endswith(".sha256"):
                return FakeResponse2(f"{digest}  Setup.exe\n".encode("ascii"))
            return FakeResponse2(installer_bytes)
        monkeypatch.setattr(updater.requests, "get", _fake_get)

        result = updater.download_and_verify(self._release(), tmp_path)

        assert result == tmp_path / "Setup.exe"
        assert result.read_bytes() == installer_bytes

    def test_returns_none_and_cleans_up_on_checksum_mismatch(self, monkeypatch, tmp_path):
        installer_bytes = b"fake installer bytes"
        wrong_digest = "0" * 64

        def _fake_get(url, **kwargs):
            if url.endswith(".sha256"):
                return FakeResponse2(f"{wrong_digest}  Setup.exe\n".encode("ascii"))
            return FakeResponse2(installer_bytes)
        monkeypatch.setattr(updater.requests, "get", _fake_get)

        result = updater.download_and_verify(self._release(), tmp_path)

        assert result is None
        assert not (tmp_path / "Setup.exe").exists()
        assert not (tmp_path / "Setup.exe.sha256").exists()

    def test_returns_none_when_checksum_download_fails(self, monkeypatch, tmp_path):
        def _fake_get(url, **kwargs):
            if url.endswith(".sha256"):
                raise requests.ConnectionError("offline")
            return FakeResponse2(b"unused")
        monkeypatch.setattr(updater.requests, "get", _fake_get)

        result = updater.download_and_verify(self._release(), tmp_path)

        assert result is None
        assert not (tmp_path / "Setup.exe").exists()
        assert not (tmp_path / "Setup.exe.sha256").exists()

    def test_returns_none_when_installer_download_fails(self, monkeypatch, tmp_path):
        digest = hashlib.sha256(b"x").hexdigest()

        def _fake_get(url, **kwargs):
            if url.endswith(".sha256"):
                return FakeResponse2(f"{digest}  Setup.exe\n".encode("ascii"))
            raise requests.ConnectionError("offline")
        monkeypatch.setattr(updater.requests, "get", _fake_get)

        result = updater.download_and_verify(self._release(), tmp_path)

        assert result is None
        assert not (tmp_path / "Setup.exe").exists()
        assert not (tmp_path / "Setup.exe.sha256").exists()

    def test_returns_none_and_cleans_up_on_empty_checksum_file(
            self, monkeypatch, tmp_path):
        installer_bytes = b"fake installer bytes"

        def _fake_get(url, **kwargs):
            if url.endswith(".sha256"):
                return FakeResponse2(b"")  # Empty checksum response
            return FakeResponse2(installer_bytes)
        monkeypatch.setattr(updater.requests, "get", _fake_get)

        result = updater.download_and_verify(self._release(), tmp_path)

        assert result is None
        assert not (tmp_path / "Setup.exe").exists()
        assert not (tmp_path / "Setup.exe.sha256").exists()

    def test_returns_none_and_cleans_up_on_corrupt_checksum_file(
            self, monkeypatch, tmp_path):
        installer_bytes = b"fake installer bytes"

        def _fake_get(url, **kwargs):
            if url.endswith(".sha256"):
                return FakeResponse2(b"\xff\xfe")  # Invalid UTF-8
            return FakeResponse2(installer_bytes)
        monkeypatch.setattr(updater.requests, "get", _fake_get)

        result = updater.download_and_verify(self._release(), tmp_path)

        assert result is None
        assert not (tmp_path / "Setup.exe").exists()
        assert not (tmp_path / "Setup.exe.sha256").exists()


class FakeResponse2:
    """Like FakeResponse, but carries raw bytes for downloads instead of JSON."""

    def __init__(self, content: bytes, status_code=200):
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


class TestLaunchSilentInstall:

    def test_launches_installer_with_silent_flags(self, monkeypatch, tmp_path):
        installer = tmp_path / "Setup.exe"
        installer.write_bytes(b"fake")
        seen = {}

        class FakePopen:
            def __init__(self, cmd, **kwargs):
                seen["cmd"] = cmd
                seen["kwargs"] = kwargs
        monkeypatch.setattr(updater.subprocess, "Popen", FakePopen)

        assert updater.launch_silent_install(installer) is True
        assert seen["cmd"][0] == str(installer)
        assert "/VERYSILENT" in seen["cmd"]
        assert "/NORESTART" in seen["cmd"]
        assert "/SUPPRESSMSGBOXES" in seen["cmd"]

    def test_returns_false_when_spawning_fails(self, monkeypatch, tmp_path):
        installer = tmp_path / "Setup.exe"
        installer.write_bytes(b"fake")

        def _raise(*a, **k):
            raise OSError("cannot launch")
        monkeypatch.setattr(updater.subprocess, "Popen", _raise)

        assert updater.launch_silent_install(installer) is False


class TestCheckAndApplyUpdate:

    def test_returns_false_when_no_update_available(self, monkeypatch):
        monkeypatch.setattr(updater, "check_for_update", lambda cfg: None)

        assert updater.check_and_apply_update(FakeConfig()) is False

    def test_returns_false_when_download_fails(self, monkeypatch):
        release = updater.ReleaseInfo(version=(9, 9, 9), tag_name="v9.9.9",
                                       installer_url="https://x/Setup.exe",
                                       checksum_url="https://x/Setup.exe.sha256")
        monkeypatch.setattr(updater, "check_for_update", lambda cfg: release)
        monkeypatch.setattr(updater, "download_and_verify", lambda release, dest_dir: None)

        assert updater.check_and_apply_update(FakeConfig()) is False

    def test_returns_true_and_launches_installer_on_success(self, monkeypatch, tmp_path):
        release = updater.ReleaseInfo(version=(9, 9, 9), tag_name="v9.9.9",
                                       installer_url="https://x/Setup.exe",
                                       checksum_url="https://x/Setup.exe.sha256")
        installer_path = tmp_path / "Setup.exe"
        installer_path.write_bytes(b"fake")
        launched = []

        monkeypatch.setattr(updater, "check_for_update", lambda cfg: release)
        monkeypatch.setattr(updater, "download_and_verify",
                            lambda release, dest_dir: installer_path)
        monkeypatch.setattr(updater, "launch_silent_install",
                            lambda path: launched.append(path) or True)

        assert updater.check_and_apply_update(FakeConfig()) is True
        assert launched == [installer_path]

    def test_returns_false_when_launch_fails(self, monkeypatch, tmp_path):
        release = updater.ReleaseInfo(version=(9, 9, 9), tag_name="v9.9.9",
                                       installer_url="https://x/Setup.exe",
                                       checksum_url="https://x/Setup.exe.sha256")
        installer_path = tmp_path / "Setup.exe"
        installer_path.write_bytes(b"fake")

        monkeypatch.setattr(updater, "check_for_update", lambda cfg: release)
        monkeypatch.setattr(updater, "download_and_verify",
                            lambda release, dest_dir: installer_path)
        monkeypatch.setattr(updater, "launch_silent_install", lambda path: False)

        assert updater.check_and_apply_update(FakeConfig()) is False

    def test_returns_false_when_tempdir_creation_fails(self, monkeypatch):
        release = updater.ReleaseInfo(version=(9, 9, 9), tag_name="v9.9.9",
                                       installer_url="https://x/Setup.exe",
                                       checksum_url="https://x/Setup.exe.sha256")

        def _raise(*a, **k):
            raise OSError("temp directory unavailable")
        monkeypatch.setattr(updater, "check_for_update", lambda cfg: release)
        monkeypatch.setattr(updater.tempfile, "mkdtemp", _raise)

        assert updater.check_and_apply_update(FakeConfig()) is False
