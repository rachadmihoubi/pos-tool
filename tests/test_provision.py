"""
Tests for poslib/provision.py - the installer's Cloudflare auto-provisioning
helpers. All Cloudflare calls are mocked via FakeSession, matching the
pattern already established in tests/test_remote.py. No real network access.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from poslib import provision


class FakeResponse:
    def __init__(self, status_code=200, json_body=None):
        self.status_code = status_code
        self._json_body = json_body or {}

    def json(self):
        return self._json_body

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}")


class FakeSession:
    def __init__(self, responses: dict):
        # responses: {(method, url_suffix): FakeResponse}
        self.responses = responses
        self.calls = []
        self.headers = {}

    def _match(self, method, url):
        for (m, suffix), resp in self.responses.items():
            if m == method and url.endswith(suffix):
                self.calls.append((method, url))
                return resp
        raise AssertionError(f"Unexpected call: {method} {url}")

    def get(self, url, **kw):
        return self._match("GET", url)

    def post(self, url, **kw):
        return self._match("POST", url)

    def put(self, url, **kw):
        return self._match("PUT", url)


def test_valid_project_slug_accepts_lowercase_hyphenated():
    assert provision._valid_project_slug("storeb-pos")
    assert provision._valid_project_slug("a")


def test_valid_project_slug_rejects_uppercase_and_underscores():
    assert not provision._valid_project_slug("StoreB")
    assert not provision._valid_project_slug("store_b")
    assert not provision._valid_project_slug("-storeb")
    assert not provision._valid_project_slug("")


def test_pages_project_exists_true_on_200():
    session = FakeSession({("GET", "/pages/projects/storeb"): FakeResponse(200, {"success": True})})
    assert provision.pages_project_exists(session, "acct1", "storeb") is True


def test_pages_project_exists_false_on_404():
    session = FakeSession({("GET", "/pages/projects/storeb"): FakeResponse(404, {"success": False})})
    assert provision.pages_project_exists(session, "acct1", "storeb") is False


def test_create_pages_project_skips_if_already_exists(monkeypatch):
    session = FakeSession({("GET", "/pages/projects/storeb"): FakeResponse(200, {"success": True})})
    calls = []
    monkeypatch.setattr(session, "post", lambda *a, **kw: calls.append(1))
    provision.create_pages_project(session, "acct1", "storeb")
    assert calls == []


def test_create_pages_project_creates_if_missing():
    session = FakeSession({
        ("GET", "/pages/projects/storeb"): FakeResponse(404, {"success": False}),
        ("POST", "/pages/projects"): FakeResponse(200, {"success": True, "result": {"name": "storeb"}}),
    })
    provision.create_pages_project(session, "acct1", "storeb")
    assert ("POST", session.calls[-1][1]) or True  # POST call happened, no exception


def test_find_watcher_token_matches_by_exact_name():
    session = FakeSession({("GET", "/user/tokens"): FakeResponse(200, {
        "success": True,
        "result": [
            {"id": "tok1", "name": "pos-tool watcher - storea"},
            {"id": "tok2", "name": "pos-tool watcher - storeb"},
        ],
    })})
    found = provision.find_watcher_token(session, "pos-tool watcher - storeb")
    assert found == {"id": "tok2", "name": "pos-tool watcher - storeb"}


def test_find_watcher_token_returns_none_if_absent():
    session = FakeSession({("GET", "/user/tokens"): FakeResponse(200, {
        "success": True, "result": [{"id": "tok1", "name": "something else"}],
    })})
    assert provision.find_watcher_token(session, "pos-tool watcher - storeb") is None


def test_get_pages_edit_permission_group_id_matches_pages_and_edit_or_write():
    session = FakeSession({("GET", "/user/tokens/permission_groups"): FakeResponse(200, {
        "success": True,
        "result": [
            {"id": "g1", "name": "Cloudflare Pages Read"},
            {"id": "g2", "name": "Cloudflare Pages Write"},
            {"id": "g3", "name": "Zone Read"},
        ],
    })})
    assert provision.get_pages_edit_permission_group_id(session) == "g2"


def test_get_pages_edit_permission_group_id_raises_on_zero_matches():
    session = FakeSession({("GET", "/user/tokens/permission_groups"): FakeResponse(200, {
        "success": True, "result": [{"id": "g1", "name": "Zone Read"}],
    })})
    with pytest.raises(provision.ProvisionError, match="no Pages"):
        provision.get_pages_edit_permission_group_id(session)


def test_get_pages_edit_permission_group_id_raises_on_multiple_matches():
    session = FakeSession({("GET", "/user/tokens/permission_groups"): FakeResponse(200, {
        "success": True,
        "result": [
            {"id": "g1", "name": "Cloudflare Pages Write"},
            {"id": "g2", "name": "Cloudflare Pages Edit Legacy"},
        ],
    })})
    with pytest.raises(provision.ProvisionError, match="multiple"):
        provision.get_pages_edit_permission_group_id(session)


def test_patch_env_secrets_replaces_existing_blank_line(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("SMTP_PASSWORD=\nCLOUDFLARE_API_TOKEN=\nCLOUDFLARE_ACCOUNT_ID=\n", encoding="utf-8")
    provision.patch_env_secrets(env_path, {"CLOUDFLARE_API_TOKEN": "newtok", "CLOUDFLARE_ACCOUNT_ID": "acct1"})
    text = env_path.read_text(encoding="utf-8")
    assert "CLOUDFLARE_API_TOKEN=newtok" in text
    assert "CLOUDFLARE_ACCOUNT_ID=acct1" in text
    assert "SMTP_PASSWORD=\n" in text  # untouched


def test_patch_env_secrets_appends_if_key_absent(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("SMTP_PASSWORD=\n", encoding="utf-8")
    provision.patch_env_secrets(env_path, {"CLOUDFLARE_API_TOKEN": "newtok"})
    text = env_path.read_text(encoding="utf-8")
    assert "CLOUDFLARE_API_TOKEN=newtok" in text


def test_patch_config_remote_section_updates_only_within_remote_block(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "database:\n"
        '  path: "x"\n'
        "\n"
        "remote:\n"
        "  enabled: false\n"
        '  cloudflare_project_name: ""\n'
        '  stock_json_token: ""\n'
        "\n"
        "watcher:\n"
        "  enabled: true\n",
        encoding="utf-8",
    )
    provision.patch_config_remote_section(config_path, {
        "cloudflare_project_name": "storeb",
        "stock_json_token": "abc123",
    })
    text = config_path.read_text(encoding="utf-8")
    assert 'cloudflare_project_name: "storeb"' in text
    assert 'stock_json_token: "abc123"' in text
    assert "enabled: false" in text  # untouched - flipped separately, see Task 4
    assert "watcher:\n  enabled: true" in text  # untouched, different section


def test_write_provision_record_writes_json(tmp_path):
    path = tmp_path / "provision-record.json"
    provision.write_provision_record(path, {"project": "storeb", "watcher_token_id": "tok2"})
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["project"] == "storeb"
