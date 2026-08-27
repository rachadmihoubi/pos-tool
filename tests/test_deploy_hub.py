"""
Tests for tools/deploy_hub.py - the hub's manual, occasional deploy script.

push_remote itself is already fully tested in tests/test_remote.py; these
tests only cover this script's own thin argument-handling/wiring layer,
with push_remote mocked out.
"""

from __future__ import annotations

import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import deploy_hub  # noqa: E402


class FakeConfig:
    pass


def test_missing_dir_fails_without_calling_push_remote(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(deploy_hub, "get_config", lambda: FakeConfig())
    monkeypatch.setattr(deploy_hub, "setup_logging", lambda cfg: None)
    called = []
    monkeypatch.setattr(deploy_hub.remote, "push_remote",
                        lambda cfg, **kw: called.append(kw) or True)

    rc = deploy_hub.main(["--project", "hub-site", "--dir", str(tmp_path / "nope")])

    assert rc == 1
    assert called == []
    assert "does not exist" in capsys.readouterr().out


def test_success_passes_project_and_resolved_dir_to_push_remote(tmp_path, monkeypatch, capsys):
    hub_dir = tmp_path / "hub-site"
    hub_dir.mkdir()
    monkeypatch.setattr(deploy_hub, "get_config", lambda: FakeConfig())
    monkeypatch.setattr(deploy_hub, "setup_logging", lambda cfg: None)
    captured = {}

    def fake_push(cfg, *, project, export_dir):
        captured["project"] = project
        captured["export_dir"] = export_dir
        return True

    monkeypatch.setattr(deploy_hub.remote, "push_remote", fake_push)

    rc = deploy_hub.main(["--project", "my-hub", "--dir", str(hub_dir)])

    assert rc == 0
    assert captured["project"] == "my-hub"
    assert captured["export_dir"] == hub_dir.resolve()
    assert "Pushed" in capsys.readouterr().out


def test_push_failure_returns_1(tmp_path, monkeypatch, capsys):
    hub_dir = tmp_path / "hub-site"
    hub_dir.mkdir()
    monkeypatch.setattr(deploy_hub, "get_config", lambda: FakeConfig())
    monkeypatch.setattr(deploy_hub, "setup_logging", lambda cfg: None)
    monkeypatch.setattr(deploy_hub.remote, "push_remote", lambda cfg, **kw: False)

    rc = deploy_hub.main(["--project", "my-hub", "--dir", str(hub_dir)])

    assert rc == 1
    assert "failed" in capsys.readouterr().out.lower()
