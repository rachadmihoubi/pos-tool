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

    def _match(self, method, url, kw):
        for (m, suffix), resp in self.responses.items():
            if m == method and url.endswith(suffix):
                self.calls.append((method, url, kw))
                return resp
        raise AssertionError(f"Unexpected call: {method} {url}")

    def get(self, url, **kw):
        return self._match("GET", url, kw)

    def post(self, url, **kw):
        return self._match("POST", url, kw)

    def put(self, url, **kw):
        return self._match("PUT", url, kw)


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
    # Verify POST was called to /pages/projects endpoint
    post_calls = [call for call in session.calls if call[0] == "POST"]
    assert len(post_calls) > 0
    assert post_calls[0][1].endswith("/pages/projects")


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


def test_verify_token_succeeds_when_active():
    session = FakeSession({("GET", "/user/tokens/verify"): FakeResponse(200, {
        "success": True,
        "result": {"id": "tok1", "status": "active", "name": "provision token"},
    })})
    result = provision.verify_token(session)
    assert result["id"] == "tok1"
    assert result["status"] == "active"


def test_verify_token_raises_when_not_active():
    session = FakeSession({("GET", "/user/tokens/verify"): FakeResponse(200, {
        "success": True,
        "result": {"id": "tok1", "status": "disabled"},
    })})
    with pytest.raises(provision.ProvisionError, match="not valid or not active"):
        provision.verify_token(session)


def test_verify_token_raises_when_success_false():
    session = FakeSession({("GET", "/user/tokens/verify"): FakeResponse(200, {
        "success": False,
        "errors": ["Invalid token"],
    })})
    with pytest.raises(provision.ProvisionError, match="not valid or not active"):
        provision.verify_token(session)


def test_create_pages_project_raises_on_api_error():
    session = FakeSession({
        ("GET", "/pages/projects/storeb"): FakeResponse(404, {"success": False}),
        ("POST", "/pages/projects"): FakeResponse(200, {"success": False, "errors": ["Invalid project name"]}),
    })
    with pytest.raises(provision.ProvisionError, match="Could not create Pages project"):
        provision.create_pages_project(session, "acct1", "storeb")


def test_mint_watcher_token_succeeds_with_correct_scope():
    session = FakeSession({("POST", "/user/tokens"): FakeResponse(200, {
        "success": True,
        "result": {"id": "tok_abc", "value": "v1.secret123"},
    })})
    tok_id, tok_val = provision.mint_watcher_token(session, "acct123", "pos-tool watcher - storeb", "group_pages_edit")
    assert tok_id == "tok_abc"
    assert tok_val == "v1.secret123"


def test_mint_watcher_token_raises_on_api_error():
    session = FakeSession({("POST", "/user/tokens"): FakeResponse(200, {
        "success": False,
        "errors": ["Permission denied"],
    })})
    with pytest.raises(provision.ProvisionError, match="Could not mint the watcher token"):
        provision.mint_watcher_token(session, "acct123", "pos-tool watcher - storeb", "group_pages_edit")


def test_patch_config_remote_section_raises_when_key_missing_from_template(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "remote:\n"
        "  enabled: false\n"
        '  cloudflare_project_name: ""\n',
        encoding="utf-8",
    )
    # Try to patch a key that doesn't exist in the remote section
    with pytest.raises(provision.ProvisionError, match="no key"):
        provision.patch_config_remote_section(config_path, {
            "cloudflare_project_name": "storeb",
            "nonexistent_key": "value",
        })


def test_verify_reachable_succeeds_on_first_attempt():
    # Patch requests.get to return a successful response
    import poslib.provision as prov_module
    original_get = prov_module.requests.get

    def mock_get_success(url, **kw):
        response = FakeResponse(200, {})
        response.status_code = 200
        return response

    try:
        prov_module.requests.get = mock_get_success
        result = provision.verify_reachable("https://example.com", expect_status=200, max_attempts=1, delay_seconds=0)
        assert result is True
    finally:
        prov_module.requests.get = original_get


def test_verify_reachable_retries_and_eventually_succeeds():
    # Patch requests.get to fail first, then succeed
    import poslib.provision as prov_module
    original_get = prov_module.requests.get

    attempts = [0]

    def mock_get_eventually_succeeds(url, **kw):
        attempts[0] += 1
        response = FakeResponse(500 if attempts[0] < 2 else 200, {})
        response.status_code = 500 if attempts[0] < 2 else 200
        return response

    try:
        prov_module.requests.get = mock_get_eventually_succeeds
        result = provision.verify_reachable("https://example.com", expect_status=200, max_attempts=3, delay_seconds=0)
        assert result is True
        assert attempts[0] == 2
    finally:
        prov_module.requests.get = original_get


def test_verify_reachable_fails_after_max_attempts():
    # Patch requests.get to always fail
    import poslib.provision as prov_module
    original_get = prov_module.requests.get

    attempts = [0]

    def mock_get_always_fails(url, **kw):
        attempts[0] += 1
        response = FakeResponse(404, {})
        response.status_code = 404
        return response

    try:
        prov_module.requests.get = mock_get_always_fails
        result = provision.verify_reachable("https://example.com", expect_status=200, max_attempts=2, delay_seconds=0)
        assert result is False
        assert attempts[0] == 2
    finally:
        prov_module.requests.get = original_get


def test_verify_reachable_handles_network_errors():
    # Patch requests.get to raise an exception
    import poslib.provision as prov_module
    original_get = prov_module.requests.get

    def mock_get_raises(url, **kw):
        raise prov_module.requests.RequestException("Connection failed")

    try:
        prov_module.requests.get = mock_get_raises
        result = provision.verify_reachable("https://example.com", expect_status=200, max_attempts=2, delay_seconds=0)
        assert result is False
    finally:
        prov_module.requests.get = original_get


def test_write_provision_record_writes_json(tmp_path):
    path = tmp_path / "provision-record.json"
    provision.write_provision_record(path, {"project": "storeb", "watcher_token_id": "tok2"})
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["project"] == "storeb"


# ---------------------------------------------------------------------------
# Task 4, Step 1 - Access application creation.
#
# Payload shape here is not a guess - it reproduces the exact, live-verified
# shape recorded in docs/superpowers/specs/2026-08-29-store-access-app-shapes.md
# (Task 1's read-only investigation against the real account). The hub's own
# broad Access app is missing the wildcard in self_hosted_domains/destinations
# and that is a confirmed, live, ungated-preview-subdomain security gap - so
# the wildcard-presence checks below are regression tests for exactly that
# class of bug, not defensive padding.
# ---------------------------------------------------------------------------

def test_access_app_exists_matches_by_domain():
    session = FakeSession({("GET", "/accounts/acct1/access/apps"): FakeResponse(200, {
        "success": True,
        "result": [
            {"id": "app1", "domain": "storea.pages.dev"},
            {"id": "app2", "domain": "storeb.pages.dev"},
        ],
    })})
    assert provision.access_app_exists(session, "acct1", "storeb.pages.dev") is True
    assert provision.access_app_exists(session, "acct1", "storec.pages.dev") is False


def test_create_broad_access_app_returns_existing_id_when_correctly_scoped():
    session = FakeSession({("GET", "/accounts/acct1/access/apps"): FakeResponse(200, {
        "success": True,
        "result": [{
            "id": "app1",
            "domain": "storeb.pages.dev",
            "self_hosted_domains": ["storeb.pages.dev", "*.storeb.pages.dev"],
        }],
    })})
    app_id = provision.create_broad_access_app(session, "acct1", "storeb.pages.dev", "owner@example.com")
    assert app_id == "app1"
    # No POST should have happened - already correctly provisioned.
    assert all(call[0] != "POST" for call in session.calls)


def test_create_broad_access_app_raises_when_existing_app_under_scoped():
    # An app exists for this domain but its self_hosted_domains has no
    # wildcard - this is exactly the live hub gap documented in
    # docs/superpowers/specs/2026-08-29-store-access-app-shapes.md. A
    # previous partial run or a hand-created app must never be silently
    # accepted as "already provisioned" - that would ship the same gap
    # to every new store.
    session = FakeSession({("GET", "/accounts/acct1/access/apps"): FakeResponse(200, {
        "success": True,
        "result": [{"id": "app1", "domain": "storeb.pages.dev"}],
    })})
    with pytest.raises(provision.ProvisionError, match="under-scoped|wildcard"):
        provision.create_broad_access_app(session, "acct1", "storeb.pages.dev", "owner@example.com")


def test_create_broad_access_app_creates_with_correct_payload_shape():
    session = FakeSession({
        ("GET", "/accounts/acct1/access/apps"): FakeResponse(200, {"success": True, "result": []}),
        ("POST", "/accounts/acct1/access/apps"): FakeResponse(200, {
            "success": True,
            "result": {
                "id": "appNew",
                "self_hosted_domains": ["storeb.pages.dev", "*.storeb.pages.dev"],
            },
        }),
    })
    app_id = provision.create_broad_access_app(session, "acct1", "storeb.pages.dev", "owner@example.com")
    assert app_id == "appNew"

    post_calls = [c for c in session.calls if c[0] == "POST"]
    assert len(post_calls) == 1
    payload = post_calls[0][2]["json"]
    assert payload["domain"] == "storeb.pages.dev"
    assert payload["self_hosted_domains"] == ["storeb.pages.dev", "*.storeb.pages.dev"]
    assert payload["destinations"] == [
        {"type": "public", "uri": "storeb.pages.dev"},
        {"type": "public", "uri": "*.storeb.pages.dev"},
    ]
    assert payload["session_duration"] == "24h"
    policy = payload["policies"][0]
    assert policy["decision"] == "allow"
    assert policy["include"] == [{"email": {"email": "owner@example.com"}}]
    assert policy["reusable"] is True
    assert policy["name"]


def test_create_broad_access_app_raises_when_response_missing_wildcard():
    # Cloudflare accepted the create but the response doesn't confirm the
    # wildcard is actually covered - refuse rather than assume success.
    session = FakeSession({
        ("GET", "/accounts/acct1/access/apps"): FakeResponse(200, {"success": True, "result": []}),
        ("POST", "/accounts/acct1/access/apps"): FakeResponse(200, {
            "success": True,
            "result": {"id": "appNew", "self_hosted_domains": ["storeb.pages.dev"]},
        }),
    })
    with pytest.raises(provision.ProvisionError, match="wildcard|\\*\\."):
        provision.create_broad_access_app(session, "acct1", "storeb.pages.dev", "owner@example.com")


def test_create_bypass_access_app_skips_if_exists():
    path_domain = "storeb.pages.dev/stock-abc123.json"
    session = FakeSession({("GET", "/accounts/acct1/access/apps"): FakeResponse(200, {
        "success": True,
        "result": [{"id": "appBypass", "domain": path_domain}],
    })})
    app_id = provision.create_bypass_access_app(session, "acct1", path_domain)
    assert app_id == "appBypass"
    assert all(call[0] != "POST" for call in session.calls)


def test_create_bypass_access_app_creates_if_missing():
    path_domain = "storeb.pages.dev/stock-abc123.json"
    session = FakeSession({
        ("GET", "/accounts/acct1/access/apps"): FakeResponse(200, {"success": True, "result": []}),
        ("POST", "/accounts/acct1/access/apps"): FakeResponse(200, {
            "success": True, "result": {"id": "appBypass"},
        }),
    })
    app_id = provision.create_bypass_access_app(session, "acct1", path_domain)
    assert app_id == "appBypass"

    post_calls = [c for c in session.calls if c[0] == "POST"]
    assert len(post_calls) == 1
    payload = post_calls[0][2]["json"]
    assert payload["domain"] == path_domain
    assert payload["self_hosted_domains"] == [path_domain]
    assert payload["destinations"] == [{"type": "public", "uri": path_domain}]
    policy = payload["policies"][0]
    assert policy["decision"] == "bypass"
    assert policy["include"] == [{"everyone": {}}]
    assert policy["reusable"] is False
    assert policy["name"]
    # No wildcard anywhere - this app is deliberately scoped to one exact file.
    assert "*" not in str(payload)


# ---------------------------------------------------------------------------
# Task 4, Step 2 - placeholder site written before the first push, so the
# store's Pages project has *something* live before Access apps exist.
# ---------------------------------------------------------------------------

def test_write_placeholder_site_creates_index_and_stock_files(tmp_path):
    export_dir = tmp_path / "remote-site"
    provision.write_placeholder_site(export_dir, "stock-abc123.json")
    assert (export_dir / "index.html").is_file()
    stock_path = export_dir / "stock-abc123.json"
    assert stock_path.is_file()
    assert json.loads(stock_path.read_text(encoding="utf-8")) == []


# ---------------------------------------------------------------------------
# Task 4, Step 4 - flips remote.enabled to true, the very last step of a
# successful provisioning run. Separate from patch_config_remote_section
# because `enabled` is a bare boolean, not a quoted string.
# ---------------------------------------------------------------------------

def test_flip_remote_enabled_sets_true(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "database:\n"
        '  path: "x"\n'
        "\n"
        "remote:\n"
        "  enabled: false\n"
        '  cloudflare_project_name: "storeb"\n'
        "\n"
        "watcher:\n"
        "  enabled: true\n",
        encoding="utf-8",
    )
    provision._flip_remote_enabled(config_path, True)
    text = config_path.read_text(encoding="utf-8")
    assert "remote:\n  enabled: true" in text
    assert "watcher:\n  enabled: true" in text  # untouched, different section


# ---------------------------------------------------------------------------
# Task 4, Step 5 - the full provision_store orchestrator.
# ---------------------------------------------------------------------------

class FakeCfg:
    """
    Minimal stand-in for poslib.config.Config, matching only what
    provision_store actually calls on the cfg it's handed: config_path,
    env_path, .get(dotted, default), .path(dotted, default).
    """

    def __init__(self, config_path, env_path, export_dir, stock_json_token=""):
        self.config_path = config_path
        self.env_path = env_path
        self._export_dir = export_dir
        self._stock_json_token = stock_json_token

    def get(self, dotted, default=""):
        if dotted == "remote.stock_json_token":
            return self._stock_json_token
        return default

    def path(self, dotted, default=""):
        return self._export_dir


def _write_minimal_config(config_path):
    config_path.write_text(
        "database:\n"
        '  path: "C:/fake/db.dblx"\n'
        "\n"
        "remote:\n"
        "  enabled: false\n"
        '  cloudflare_project_name: ""\n'
        "  push_interval_seconds: 90\n"
        '  export_dir: "remote-site"\n'
        '  stock_json_token: ""\n',
        encoding="utf-8",
    )


def test_provision_store_happy_path_full_sequence(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    _write_minimal_config(config_path)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "SMTP_PASSWORD=\nCLOUDFLARE_API_TOKEN=\nCLOUDFLARE_ACCOUNT_ID=\n", encoding="utf-8"
    )
    export_dir = tmp_path / "remote-site"
    cfg = FakeCfg(config_path, env_path, export_dir)

    fake_session = FakeSession({
        ("GET", "/user/tokens/verify"): FakeResponse(200, {
            "success": True, "result": {"id": "tok0", "status": "active"},
        }),
        ("GET", "/pages/projects/storeb"): FakeResponse(404, {"success": False}),
        ("POST", "/pages/projects"): FakeResponse(200, {"success": True, "result": {"name": "storeb"}}),
        ("GET", "/user/tokens"): FakeResponse(200, {"success": True, "result": []}),
        ("GET", "/user/tokens/permission_groups"): FakeResponse(200, {
            "success": True, "result": [{"id": "g2", "name": "Cloudflare Pages Write"}],
        }),
        ("POST", "/user/tokens"): FakeResponse(200, {
            "success": True, "result": {"id": "tok_abc", "value": "secretval"},
        }),
        ("GET", "/accounts/acct1/access/apps"): FakeResponse(200, {"success": True, "result": []}),
        # Same fixture serves both the broad and bypass app POSTs - the
        # bypass path never reads self_hosted_domains, so the wildcard
        # entries here are harmless for it.
        ("POST", "/accounts/acct1/access/apps"): FakeResponse(200, {
            "success": True,
            "result": {
                "id": "appX",
                "self_hosted_domains": ["storeb.pages.dev", "*.storeb.pages.dev"],
            },
        }),
    })
    monkeypatch.setattr(provision.requests, "Session", lambda: fake_session)

    push_calls = []

    def fake_push_remote(fresh_cfg, *, project=None, export_dir=None):
        push_calls.append((project, export_dir))
        return True

    monkeypatch.setattr(provision._remote, "push_remote", fake_push_remote)
    monkeypatch.setattr(
        provision, "verify_reachable",
        lambda url, *, expect_status, **kw: True,
    )

    result = provision.provision_store(
        cfg,
        powerful_token="powerful123",
        account_id="acct1",
        project_slug="storeb",
        owner_email="owner@example.com",
    )

    assert result.ok is True
    assert "secretval" not in result.message

    config_text = config_path.read_text(encoding="utf-8")
    assert "remote:\n  enabled: true" in config_text
    assert 'cloudflare_project_name: "storeb"' in config_text

    env_text = env_path.read_text(encoding="utf-8")
    assert "CLOUDFLARE_API_TOKEN=secretval" in env_text
    assert "CLOUDFLARE_ACCOUNT_ID=acct1" in env_text

    assert push_calls == [("storeb", export_dir)]
    assert (export_dir / "index.html").is_file()


def test_provision_store_refuses_when_watcher_token_already_exists(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    _write_minimal_config(config_path)
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    cfg = FakeCfg(config_path, env_path, tmp_path / "remote-site")

    fake_session = FakeSession({
        ("GET", "/user/tokens/verify"): FakeResponse(200, {
            "success": True, "result": {"id": "tok0", "status": "active"},
        }),
        ("GET", "/pages/projects/storeb"): FakeResponse(200, {"success": True}),
        ("GET", "/user/tokens"): FakeResponse(200, {
            "success": True,
            "result": [{"id": "tok1", "name": "pos-tool watcher - storeb"}],
        }),
    })
    monkeypatch.setattr(provision.requests, "Session", lambda: fake_session)

    result = provision.provision_store(
        cfg,
        powerful_token="powerful123",
        account_id="acct1",
        project_slug="storeb",
        owner_email="owner@example.com",
    )

    assert result.ok is False
    assert "already exists" in result.message


def test_provision_store_rejects_invalid_slug(tmp_path):
    cfg = FakeCfg(tmp_path / "config.yaml", tmp_path / ".env", tmp_path / "remote-site")

    result = provision.provision_store(
        cfg,
        powerful_token="powerful123",
        account_id="acct1",
        project_slug="Store_B",
        owner_email="owner@example.com",
    )

    assert result.ok is False
    assert "not a valid" in result.message
