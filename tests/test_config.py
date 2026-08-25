"""
Tests for the frozen-vs-dev path wiring in poslib/config.py.

Everything else about Config (get/require/secret/_validate/...) is already
covered indirectly by every other test module using the real config.yaml -
this file is scoped to the one thing this plan changes: where Config reads
its files from, and the first-run bootstrap for a packaged build.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from poslib import paths
from poslib.config import Config, ConfigError


MINIMAL_CONFIG = """
database:
  path: "C:/CHANGE-ME/database.dblx"
interface:
  default_language: "fr"
"""


class TestPathResolutionDevMode:

    def test_relative_database_path_resolves_against_project_root(self, tmp_path, monkeypatch):
        monkeypatch.delattr(paths.sys, "frozen", raising=False)
        config_file = tmp_path / "config.yaml"
        config_file.write_text(MINIMAL_CONFIG, encoding="utf-8")
        cfg = Config(config_path=config_file)
        # cache defaults to "cache.db" (relative) -> resolved against
        # user_data_dir(), which in dev mode is the project root.
        assert cfg.cache_db == paths.user_data_dir() / "cache.db"


class TestFirstRunBootstrap:

    def test_frozen_with_no_existing_config_copies_the_template(self, tmp_path, monkeypatch):
        bundle_dir = tmp_path / "bundle"
        bundle_dir.mkdir()
        (bundle_dir / "config.template.yaml").write_text(MINIMAL_CONFIG, encoding="utf-8")
        (bundle_dir / ".env.example").write_text("SMTP_PASSWORD=\n", encoding="utf-8")

        data_dir = tmp_path / "userdata"

        monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
        monkeypatch.setattr(paths, "app_root", lambda: bundle_dir)
        monkeypatch.setattr(paths, "user_data_dir", lambda: data_dir)

        cfg = Config()

        assert (data_dir / "config.yaml").is_file()
        assert (data_dir / ".env").is_file()
        assert cfg.get("database.path") == "C:/CHANGE-ME/database.dblx"

    def test_frozen_with_existing_config_does_not_overwrite_it(self, tmp_path, monkeypatch):
        bundle_dir = tmp_path / "bundle"
        bundle_dir.mkdir()
        (bundle_dir / "config.template.yaml").write_text(MINIMAL_CONFIG, encoding="utf-8")
        (bundle_dir / ".env.example").write_text("SMTP_PASSWORD=\n", encoding="utf-8")

        data_dir = tmp_path / "userdata"
        data_dir.mkdir()
        already_there = data_dir / "config.yaml"
        already_there.write_text(
            'database:\n  path: "D:/real/shop.dblx"\ninterface:\n  default_language: "fr"\n',
            encoding="utf-8",
        )

        monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
        monkeypatch.setattr(paths, "app_root", lambda: bundle_dir)
        monkeypatch.setattr(paths, "user_data_dir", lambda: data_dir)

        cfg = Config()
        assert cfg.get("database.path") == "D:/real/shop.dblx"

    def test_not_frozen_never_bootstraps(self, tmp_path, monkeypatch):
        monkeypatch.delattr(paths.sys, "frozen", raising=False)
        with pytest.raises(ConfigError):
            Config(config_path=tmp_path / "does-not-exist.yaml")
