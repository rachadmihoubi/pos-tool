"""
Tests for poslib/paths.py - where the app reads its own bundled files from,
and where it writes its data, in dev mode vs. a frozen PyInstaller build.
"""

from __future__ import annotations

from pathlib import Path

from poslib import paths


class TestIsFrozen:

    def test_false_when_sys_has_no_frozen_attribute(self, monkeypatch):
        monkeypatch.delattr(paths.sys, "frozen", raising=False)
        assert paths.is_frozen() is False

    def test_true_when_sys_frozen_is_set(self, monkeypatch):
        monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
        assert paths.is_frozen() is True


class TestAppRoot:

    def test_dev_mode_is_the_project_root(self, monkeypatch):
        monkeypatch.delattr(paths.sys, "frozen", raising=False)
        root = paths.app_root()
        # poslib/ lives directly under the project root.
        assert (root / "poslib" / "paths.py").resolve() == Path(__file__).resolve().parent.parent / "poslib" / "paths.py"

    def test_frozen_mode_is_the_exe_folder(self, monkeypatch, tmp_path):
        fake_exe = tmp_path / "ShopAnalysis.exe"
        fake_exe.touch()
        monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
        monkeypatch.setattr(paths.sys, "executable", str(fake_exe))
        assert paths.app_root() == tmp_path


class TestUserDataDir:

    def test_dev_mode_matches_app_root(self, monkeypatch):
        monkeypatch.delattr(paths.sys, "frozen", raising=False)
        assert paths.user_data_dir() == paths.app_root()

    def test_frozen_mode_is_under_localappdata(self, monkeypatch, tmp_path):
        monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        assert paths.user_data_dir() == tmp_path / "Shop Analysis"
