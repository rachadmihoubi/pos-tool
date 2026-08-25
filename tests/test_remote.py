"""
Tests for poslib/remote.py - the Cloudflare Pages push.

Entirely mocked - never actually calls wrangler or the network, and never
requires wrangler to be installed to run the test suite.
"""

from __future__ import annotations

import subprocess

import pytest

from poslib import remote


class FakeConfig:
    def __init__(self, project="", export_dir_exists=True, cloudflare_api_token=""):
        self._project = project
        self._export_dir_exists = export_dir_exists
        self._cloudflare_api_token = cloudflare_api_token

    def get(self, key, default=None):
        if key == "remote.cloudflare_project_name":
            return self._project
        return default

    def path(self, key, default=""):
        if key == "remote.export_dir":
            class FakeDir:
                def __init__(self, exists):
                    self._exists = exists

                def is_dir(self):
                    return self._exists

                def __str__(self):
                    return "remote-site"

            return FakeDir(self._export_dir_exists)
        return default

    def secret(self, name, default=""):
        if name == "CLOUDFLARE_API_TOKEN":
            return self._cloudflare_api_token
        return default


class TestPushRemoteGuardClauses:

    def test_no_project_configured_fails_gracefully(self):
        cfg = FakeConfig(project="")
        assert remote.push_remote(cfg) is False

    def test_missing_export_dir_fails_gracefully(self):
        cfg = FakeConfig(project="my-shop", export_dir_exists=False)
        assert remote.push_remote(cfg) is False

    def test_wrangler_not_installed_fails_gracefully(self, monkeypatch):
        cfg = FakeConfig(project="my-shop", export_dir_exists=True)
        monkeypatch.setattr(remote, "_wrangler_path", lambda: None)
        assert remote.push_remote(cfg) is False


class TestPushRemoteSubprocess:

    def test_success_returns_true(self, monkeypatch):
        cfg = FakeConfig(project="my-shop", export_dir_exists=True)
        monkeypatch.setattr(remote, "_wrangler_path", lambda: "C:/fake/wrangler.CMD")

        def fake_run(command, **kwargs):
            assert "my-shop" in command
            return subprocess.CompletedProcess(command, returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(remote.subprocess, "run", fake_run)
        assert remote.push_remote(cfg) is True

    def test_nonzero_exit_returns_false_not_raises(self, monkeypatch):
        cfg = FakeConfig(project="my-shop", export_dir_exists=True)
        monkeypatch.setattr(remote, "_wrangler_path", lambda: "C:/fake/wrangler.CMD")

        def fake_run(command, **kwargs):
            return subprocess.CompletedProcess(command, returncode=1, stdout="",
                                               stderr="not authenticated")

        monkeypatch.setattr(remote.subprocess, "run", fake_run)
        assert remote.push_remote(cfg) is False

    def test_timeout_returns_false_not_raises(self, monkeypatch):
        cfg = FakeConfig(project="my-shop", export_dir_exists=True)
        monkeypatch.setattr(remote, "_wrangler_path", lambda: "C:/fake/wrangler.CMD")

        def fake_run(command, **kwargs):
            raise subprocess.TimeoutExpired(cmd=command, timeout=120)

        monkeypatch.setattr(remote.subprocess, "run", fake_run)
        assert remote.push_remote(cfg) is False

    def test_output_is_decoded_as_utf8_not_the_console_codepage(self, monkeypatch):
        """
        Regression test: wrangler prints UTF-8 (including emoji) regardless
        of the Windows console's own codepage. Without an explicit
        encoding="utf-8" on subprocess.run(), decoding that output with the
        default codepage crashes subprocess's internal stdout-reader thread
        on real wrangler output containing e.g. the ⛅️ character.
        """
        cfg = FakeConfig(project="my-shop", export_dir_exists=True)
        monkeypatch.setattr(remote, "_wrangler_path", lambda: "C:/fake/wrangler.CMD")

        seen_kwargs = {}

        def fake_run(command, **kwargs):
            seen_kwargs.update(kwargs)
            return subprocess.CompletedProcess(command, returncode=0, stdout="⛅️ ok", stderr="")

        monkeypatch.setattr(remote.subprocess, "run", fake_run)
        assert remote.push_remote(cfg) is True
        assert seen_kwargs.get("encoding") == "utf-8"

    def test_missing_executable_returns_false_not_raises(self, monkeypatch):
        cfg = FakeConfig(project="my-shop", export_dir_exists=True)
        monkeypatch.setattr(remote, "_wrangler_path", lambda: "C:/fake/wrangler.CMD")

        def fake_run(command, **kwargs):
            raise OSError("file not found")

        monkeypatch.setattr(remote.subprocess, "run", fake_run)
        assert remote.push_remote(cfg) is False


class TestPushRemoteCredential:

    def test_passes_cloudflare_api_token_to_subprocess_env_when_configured(self, monkeypatch):
        cfg = FakeConfig(project="my-shop", export_dir_exists=True,
                          cloudflare_api_token="fake-scoped-token")
        monkeypatch.setattr(remote, "_wrangler_path", lambda: "C:/fake/wrangler.CMD")

        seen_kwargs = {}

        def fake_run(command, **kwargs):
            seen_kwargs.update(kwargs)
            return subprocess.CompletedProcess(command, returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(remote.subprocess, "run", fake_run)
        assert remote.push_remote(cfg) is True
        assert seen_kwargs["env"]["CLOUDFLARE_API_TOKEN"] == "fake-scoped-token"

    def test_does_not_set_cloudflare_api_token_when_not_configured(self, monkeypatch):
        cfg = FakeConfig(project="my-shop", export_dir_exists=True, cloudflare_api_token="")
        monkeypatch.setattr(remote, "_wrangler_path", lambda: "C:/fake/wrangler.CMD")

        seen_kwargs = {}

        def fake_run(command, **kwargs):
            seen_kwargs.update(kwargs)
            return subprocess.CompletedProcess(command, returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(remote.subprocess, "run", fake_run)
        assert remote.push_remote(cfg) is True
        assert "CLOUDFLARE_API_TOKEN" not in seen_kwargs["env"]

    def test_still_passes_rest_of_the_process_environment(self, monkeypatch):
        cfg = FakeConfig(project="my-shop", export_dir_exists=True,
                          cloudflare_api_token="fake-token")
        monkeypatch.setattr(remote, "_wrangler_path", lambda: "C:/fake/wrangler.CMD")
        monkeypatch.setenv("PATH_MARKER_FOR_TEST", "still-here")

        seen_kwargs = {}

        def fake_run(command, **kwargs):
            seen_kwargs.update(kwargs)
            return subprocess.CompletedProcess(command, returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(remote.subprocess, "run", fake_run)
        assert remote.push_remote(cfg) is True
        assert seen_kwargs["env"].get("PATH_MARKER_FOR_TEST") == "still-here"


class TestWranglerAvailable:

    def test_true_when_found(self, monkeypatch):
        monkeypatch.setattr(remote.shutil, "which", lambda name: "C:/fake/wrangler.CMD")
        assert remote.wrangler_available() is True

    def test_false_when_not_found(self, monkeypatch):
        monkeypatch.setattr(remote.shutil, "which", lambda name: None)
        assert remote.wrangler_available() is False
