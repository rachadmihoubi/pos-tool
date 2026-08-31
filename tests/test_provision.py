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


def test_try_reuse_existing_watcher_token_returns_value_when_env_matches(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("CLOUDFLARE_API_TOKEN=savedvalue123\n", encoding="utf-8")
    fake_session = FakeSession({("GET", "/user/tokens/verify"): FakeResponse(200, {
        "success": True, "result": {"id": "tok1", "status": "active"},
    })})
    monkeypatch.setattr(provision.requests, "Session", lambda: fake_session)
    result = provision.try_reuse_existing_watcher_token(env_path, "tok1")
    assert result == "savedvalue123"


def test_try_reuse_existing_watcher_token_returns_none_when_id_mismatches(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("CLOUDFLARE_API_TOKEN=savedvalue123\n", encoding="utf-8")
    fake_session = FakeSession({("GET", "/user/tokens/verify"): FakeResponse(200, {
        "success": True, "result": {"id": "some-other-token", "status": "active"},
    })})
    monkeypatch.setattr(provision.requests, "Session", lambda: fake_session)
    assert provision.try_reuse_existing_watcher_token(env_path, "tok1") is None


def test_try_reuse_existing_watcher_token_returns_none_when_env_blank(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("CLOUDFLARE_API_TOKEN=\n", encoding="utf-8")
    assert provision.try_reuse_existing_watcher_token(env_path, "tok1") is None


def test_try_reuse_existing_watcher_token_returns_none_when_verify_fails(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("CLOUDFLARE_API_TOKEN=savedvalue123\n", encoding="utf-8")
    fake_session = FakeSession({("GET", "/user/tokens/verify"): FakeResponse(200, {
        "success": False, "errors": ["Invalid token"],
    })})
    monkeypatch.setattr(provision.requests, "Session", lambda: fake_session)
    assert provision.try_reuse_existing_watcher_token(env_path, "tok1") is None


def test_atomic_write_text_replaces_file_and_leaves_no_tmp_behind(tmp_path):
    target = tmp_path / "config.yaml"
    target.write_text("old content", encoding="utf-8")
    provision._atomic_write_text(target, "new content")
    assert target.read_text(encoding="utf-8") == "new content"
    assert not (tmp_path / "config.yaml.tmp").exists()


def test_get_pages_edit_permission_group_id_matches_exact_name_only():
    """
    Regression test for a real bug found live 2026-08-29: a substring match
    on "pages" + ("edit" or "write") false-positived on several unrelated
    Access custom-error-page permission groups that happen to contain those
    words. Only an exact "Pages Write" (or "Pages Edit") name may match -
    these fixture names are the actual false positives seen on the real
    account, not hypothetical.
    """
    session = FakeSession({("GET", "/user/tokens/permission_groups"): FakeResponse(200, {
        "success": True,
        "result": [
            {"id": "g1", "name": "Pages Read"},
            {"id": "g2", "name": "Pages Write"},
            {"id": "g3", "name": "Access: Custom Pages Write"},
            {"id": "g4", "name": "Account Custom Pages Write"},
            {"id": "g5", "name": "Custom Pages Write"},
        ],
    })})
    assert provision.get_pages_edit_permission_group_id(session) == "g2"


def test_get_pages_edit_permission_group_id_raises_on_zero_matches():
    session = FakeSession({("GET", "/user/tokens/permission_groups"): FakeResponse(200, {
        "success": True, "result": [
            {"id": "g1", "name": "Zone Read"},
            {"id": "g2", "name": "Custom Pages Write"},
        ],
    })})
    with pytest.raises(provision.ProvisionError, match="Could not find"):
        provision.get_pages_edit_permission_group_id(session)


def test_get_pages_edit_permission_group_id_raises_on_multiple_matches():
    session = FakeSession({("GET", "/user/tokens/permission_groups"): FakeResponse(200, {
        "success": True,
        "result": [
            {"id": "g1", "name": "Pages Write"},
            {"id": "g2", "name": "Pages Edit"},
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


def test_verify_token_raises_clear_error_on_non_json_response():
    class _HtmlResponse:
        status_code = 502

        def json(self):
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

    class _HtmlSession:
        def get(self, url, **kw):
            return _HtmlResponse()

    with pytest.raises(provision.ProvisionError, match="wasn't valid JSON"):
        provision.verify_token(_HtmlSession())


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
            "destinations": [
                {"type": "public", "uri": "storeb.pages.dev"},
                {"type": "public", "uri": "*.storeb.pages.dev"},
            ],
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


def test_create_broad_access_app_raises_when_destinations_desynced_from_self_hosted_domains():
    # self_hosted_domains carries the wildcard but destinations[] does not -
    # the exact asymmetric-desync shape docs/superpowers/specs/2026-08-29-
    # store-access-app-shapes.md warns about (a PUT touching only one of
    # the two fields, per CLAUDE.md's own incident history). Checking only
    # self_hosted_domains would silently accept this as already-provisioned
    # even though destinations[] leaves the wildcard ungated.
    session = FakeSession({("GET", "/accounts/acct1/access/apps"): FakeResponse(200, {
        "success": True,
        "result": [{
            "id": "app1",
            "domain": "storeb.pages.dev",
            "self_hosted_domains": ["storeb.pages.dev", "*.storeb.pages.dev"],
            "destinations": [{"type": "public", "uri": "storeb.pages.dev"}],
        }],
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
                "destinations": [
                    {"type": "public", "uri": "storeb.pages.dev"},
                    {"type": "public", "uri": "*.storeb.pages.dev"},
                ],
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


class _SequencedPostSession:
    """
    Like FakeSession but POST can return a different response on each call -
    needed to simulate Cloudflare's real, live-reproduced eventual-
    consistency behavior (see _post_access_app's docstring): the bypass
    app's domain transiently 400s with "domain does not belong to zone"
    right after the broad app registers it, then succeeds moments later.
    GET always returns get_response (no existing app, so create is always
    attempted).
    """

    def __init__(self, post_responses: list, get_response=None):
        self._post_responses = list(post_responses)
        self._get_response = get_response or FakeResponse(200, {"success": True, "result": []})
        self.post_calls = 0
        self.sleep_calls = 0
        self.headers = {}

    def get(self, url, **kw):
        return self._get_response

    def post(self, url, **kw):
        resp = self._post_responses[self.post_calls]
        self.post_calls += 1
        return resp


_TRANSIENT_ZONE_ERROR = FakeResponse(400, {
    "success": False,
    "errors": [{"code": 12130, "message": "access.api.error.invalid_request: domain does not belong to zone"}],
})


def test_post_access_app_retries_transient_zone_error_then_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr(provision.time, "sleep", lambda s: sleeps.append(s))

    session = _SequencedPostSession([
        _TRANSIENT_ZONE_ERROR,
        _TRANSIENT_ZONE_ERROR,
        FakeResponse(201, {"success": True, "result": {"id": "appX"}}),
    ])
    resp = provision._post_access_app(session, "acct1", {"domain": "x"}, delay_seconds=0)
    assert resp.status_code == 201
    assert session.post_calls == 3
    assert len(sleeps) == 2  # slept between attempts 1->2 and 2->3, not after the final success


def test_post_access_app_gives_up_after_max_attempts_on_persistent_zone_error(monkeypatch):
    monkeypatch.setattr(provision.time, "sleep", lambda s: None)

    session = _SequencedPostSession([_TRANSIENT_ZONE_ERROR] * 5)
    resp = provision._post_access_app(session, "acct1", {"domain": "x"}, max_attempts=5, delay_seconds=0)
    assert resp.status_code == 400
    assert session.post_calls == 5


def test_post_access_app_does_not_retry_a_different_400(monkeypatch):
    monkeypatch.setattr(provision.time, "sleep", lambda s: None)

    other_error = FakeResponse(400, {
        "success": False,
        "errors": [{"code": 1000, "message": "some unrelated validation error"}],
    })
    session = _SequencedPostSession([other_error, FakeResponse(201, {"success": True, "result": {"id": "appX"}})])
    resp = provision._post_access_app(session, "acct1", {"domain": "x"})
    assert resp.status_code == 400
    assert session.post_calls == 1  # never reached the second, would-succeed response


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
# _push_placeholder_with_retry - regression coverage for a real live-install
# failure 2026-08-29: push_remote failed with "Connection aborted... The
# write operation timed out" (a genuine transient network stall on the till
# PC), which fails the whole provisioning run and orphans the just-minted
# watcher token. A few automatic retries removes that friction.
# ---------------------------------------------------------------------------

def test_push_placeholder_with_retry_succeeds_first_try(monkeypatch):
    monkeypatch.setattr(provision.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("should not sleep")))
    calls = []
    monkeypatch.setattr(provision._remote, "push_remote", lambda cfg, **kw: calls.append(1) or True)
    ok = provision._push_placeholder_with_retry(object(), "storeb", Path("remote-site"))
    assert ok is True
    assert len(calls) == 1


def test_push_placeholder_with_retry_retries_then_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr(provision.time, "sleep", lambda s: sleeps.append(s))
    results = iter([False, False, True])
    monkeypatch.setattr(provision._remote, "push_remote", lambda cfg, **kw: next(results))
    ok = provision._push_placeholder_with_retry(object(), "storeb", Path("remote-site"), delay_seconds=0)
    assert ok is True
    assert len(sleeps) == 2


def test_push_placeholder_with_retry_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr(provision.time, "sleep", lambda s: None)
    calls = []
    monkeypatch.setattr(provision._remote, "push_remote", lambda cfg, **kw: calls.append(1) or False)
    ok = provision._push_placeholder_with_retry(
        object(), "storeb", Path("remote-site"), max_attempts=3, delay_seconds=0
    )
    assert ok is False
    assert len(calls) == 3


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
            "success": True, "result": [{"id": "g2", "name": "Pages Write"}],
        }),
        ("POST", "/user/tokens"): FakeResponse(200, {
            "success": True, "result": {"id": "tok_abc", "value": "secretval"},
        }),
        ("GET", "/accounts/acct1/access/apps"): FakeResponse(200, {"success": True, "result": []}),
        # Same fixture serves both the broad and bypass app POSTs - the
        # bypass path never reads self_hosted_domains/destinations, so the
        # wildcard entries here are harmless for it. Both fields carry the
        # wildcard so the broad app's post-create verification (which now
        # checks self_hosted_domains AND destinations[]) passes.
        ("POST", "/accounts/acct1/access/apps"): FakeResponse(200, {
            "success": True,
            "result": {
                "id": "appX",
                "self_hosted_domains": ["storeb.pages.dev", "*.storeb.pages.dev"],
                "destinations": [
                    {"type": "public", "uri": "storeb.pages.dev"},
                    {"type": "public", "uri": "*.storeb.pages.dev"},
                ],
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


def test_provision_store_reuses_existing_stock_json_token(tmp_path, monkeypatch):
    # Task 4 fix round, Minor finding: a store with an already-generated
    # remote.stock_json_token (e.g. from a previous partial provisioning
    # run) must reuse it rather than mint a fresh one - a fresh token here
    # would orphan the bypass Access app's already-configured filename.
    config_path = tmp_path / "config.yaml"
    _write_minimal_config(config_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'stock_json_token: ""', 'stock_json_token: "existingtoken123"'
        ),
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    env_path.write_text(
        "SMTP_PASSWORD=\nCLOUDFLARE_API_TOKEN=\nCLOUDFLARE_ACCOUNT_ID=\n", encoding="utf-8"
    )
    export_dir = tmp_path / "remote-site"
    cfg = FakeCfg(config_path, env_path, export_dir, stock_json_token="existingtoken123")

    fake_session = FakeSession({
        ("GET", "/user/tokens/verify"): FakeResponse(200, {
            "success": True, "result": {"id": "tok0", "status": "active"},
        }),
        ("GET", "/pages/projects/storeb"): FakeResponse(404, {"success": False}),
        ("POST", "/pages/projects"): FakeResponse(200, {"success": True, "result": {"name": "storeb"}}),
        ("GET", "/user/tokens"): FakeResponse(200, {"success": True, "result": []}),
        ("GET", "/user/tokens/permission_groups"): FakeResponse(200, {
            "success": True, "result": [{"id": "g2", "name": "Pages Write"}],
        }),
        ("POST", "/user/tokens"): FakeResponse(200, {
            "success": True, "result": {"id": "tok_abc", "value": "secretval"},
        }),
        ("GET", "/accounts/acct1/access/apps"): FakeResponse(200, {"success": True, "result": []}),
        ("POST", "/accounts/acct1/access/apps"): FakeResponse(200, {
            "success": True,
            "result": {
                "id": "appX",
                "self_hosted_domains": ["storeb.pages.dev", "*.storeb.pages.dev"],
                "destinations": [
                    {"type": "public", "uri": "storeb.pages.dev"},
                    {"type": "public", "uri": "*.storeb.pages.dev"},
                ],
            },
        }),
    })
    monkeypatch.setattr(provision.requests, "Session", lambda: fake_session)
    monkeypatch.setattr(provision._remote, "push_remote", lambda fresh_cfg, **kw: True)
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

    # The pre-existing token is still the one on disk - not overwritten
    # with a freshly generated value.
    config_text = config_path.read_text(encoding="utf-8")
    assert 'stock_json_token: "existingtoken123"' in config_text

    # The bypass Access app must be created for the EXISTING token's
    # filename, not a freshly generated one.
    access_post_calls = [
        c for c in fake_session.calls
        if c[0] == "POST" and c[1].endswith("/access/apps")
    ]
    assert len(access_post_calls) == 2  # broad app, then bypass app
    bypass_payload = access_post_calls[1][2]["json"]
    assert bypass_payload["domain"] == "storeb.pages.dev/stock-existingtoken123.json"

    # The placeholder site was written under the existing token's filename.
    assert (export_dir / "stock-existingtoken123.json").is_file()


def test_provision_store_returns_ok_when_provision_record_write_fails(tmp_path, monkeypatch):
    # Task 4 fix round, Important finding #2: write_provision_record can
    # raise OSError (disk full, permission denied). By the time it's
    # called, remote.enabled has already been flipped True and the store
    # is correctly gated - provision_store must still return a
    # ProvisionResult (never raise), with ok=True since the store really
    # is provisioned; only the local record file failed to write.
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
            "success": True, "result": [{"id": "g2", "name": "Pages Write"}],
        }),
        ("POST", "/user/tokens"): FakeResponse(200, {
            "success": True, "result": {"id": "tok_abc", "value": "secretval"},
        }),
        ("GET", "/accounts/acct1/access/apps"): FakeResponse(200, {"success": True, "result": []}),
        ("POST", "/accounts/acct1/access/apps"): FakeResponse(200, {
            "success": True,
            "result": {
                "id": "appX",
                "self_hosted_domains": ["storeb.pages.dev", "*.storeb.pages.dev"],
                "destinations": [
                    {"type": "public", "uri": "storeb.pages.dev"},
                    {"type": "public", "uri": "*.storeb.pages.dev"},
                ],
            },
        }),
    })
    monkeypatch.setattr(provision.requests, "Session", lambda: fake_session)
    monkeypatch.setattr(provision._remote, "push_remote", lambda fresh_cfg, **kw: True)
    monkeypatch.setattr(
        provision, "verify_reachable",
        lambda url, *, expect_status, **kw: True,
    )

    def raise_oserror(path, record):
        raise OSError("disk full")

    monkeypatch.setattr(provision, "write_provision_record", raise_oserror)

    result = provision.provision_store(
        cfg,
        powerful_token="powerful123",
        account_id="acct1",
        project_slug="storeb",
        owner_email="owner@example.com",
    )

    # Must not raise - and must still report the store as provisioned,
    # since remote.enabled was already flipped True before this failure.
    assert result.ok is True
    assert "disk full" in result.message
    config_text = config_path.read_text(encoding="utf-8")
    assert "remote:\n  enabled: true" in config_text


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


def test_provision_store_reuses_watcher_token_when_env_already_has_it(tmp_path, monkeypatch):
    # Simulates a previous run that minted the watcher token, wrote it to
    # .env, then got killed before finishing (a crash, an elevated
    # taskkill, the future watchdog) - re-running must not refuse just
    # because find_watcher_token sees the token already exists; it must
    # notice the value in .env is that same token and continue.
    config_path = tmp_path / "config.yaml"
    _write_minimal_config(config_path)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "SMTP_PASSWORD=\nCLOUDFLARE_API_TOKEN=alreadyminted\nCLOUDFLARE_ACCOUNT_ID=\n",
        encoding="utf-8",
    )
    export_dir = tmp_path / "remote-site"
    cfg = FakeCfg(config_path, env_path, export_dir)

    fake_session = FakeSession({
        ("GET", "/user/tokens/verify"): FakeResponse(200, {
            "success": True, "result": {"id": "tok1", "status": "active"},
        }),
        ("GET", "/pages/projects/storeb"): FakeResponse(200, {"success": True}),
        ("GET", "/user/tokens"): FakeResponse(200, {
            "success": True,
            "result": [{"id": "tok1", "name": "pos-tool watcher - storeb"}],
        }),
        ("GET", "/accounts/acct1/access/apps"): FakeResponse(200, {"success": True, "result": []}),
        ("POST", "/accounts/acct1/access/apps"): FakeResponse(200, {
            "success": True,
            "result": {
                "id": "appX",
                "self_hosted_domains": ["storeb.pages.dev", "*.storeb.pages.dev"],
                "destinations": [
                    {"type": "public", "uri": "storeb.pages.dev"},
                    {"type": "public", "uri": "*.storeb.pages.dev"},
                ],
            },
        }),
    })
    monkeypatch.setattr(provision.requests, "Session", lambda: fake_session)
    monkeypatch.setattr(provision._remote, "push_remote", lambda fresh_cfg, **kw: True)
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
    # No POST /user/tokens call - a second token must never be minted.
    mint_calls = [c for c in fake_session.calls if c[0] == "POST" and c[1].endswith("/user/tokens")]
    assert mint_calls == []
    env_text = env_path.read_text(encoding="utf-8")
    assert "CLOUDFLARE_API_TOKEN=alreadyminted" in env_text


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


# ---------------------------------------------------------------------------
# Cross-store hub registration (register_store_with_hub, fetch_hub_registry).
# ---------------------------------------------------------------------------


def _write_hub_site_dir(parent: Path) -> Path:
    d = parent / "hub-site"
    d.mkdir(parents=True)
    (d / "index.html").write_text("<html></html>", encoding="utf-8")
    (d / "app.js").write_text("// app", encoding="utf-8")
    (d / "style.css").write_text("body {}", encoding="utf-8")
    return d


class _QueuedSession:
    """
    Like FakeSession, but POST responses are consumed in call order instead
    of matched by URL - needed because register_store_with_hub's own two
    Access-app creates POST to the exact same /access/apps endpoint as
    provision_store's store-level ones, with different response bodies each
    time (a different domain's wildcard in self_hosted_domains/destinations).
    GET is matched by suffix like FakeSession, but never consumed - the same
    empty access-apps list correctly serves every existence check regardless
    of how many times it's called.
    """

    def __init__(self, get_responses: dict, post_responses: list):
        self._get_responses = get_responses
        self._post_responses = list(post_responses)
        self.headers: dict = {}
        self.calls: list = []

    def get(self, url, **kw):
        self.calls.append(("GET", url))
        for suffix, resp in self._get_responses.items():
            if url.endswith(suffix):
                return resp
        raise AssertionError(f"Unexpected GET: {url}")

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return self._post_responses.pop(0)


def _wildcard_access_app_response(app_id: str, domain: str) -> FakeResponse:
    return FakeResponse(200, {
        "success": True,
        "result": {
            "id": app_id,
            "self_hosted_domains": [domain, f"*.{domain}"],
            "destinations": [
                {"type": "public", "uri": domain},
                {"type": "public", "uri": f"*.{domain}"},
            ],
        },
    })


def test_fetch_hub_registry_returns_empty_sentinel_on_404(monkeypatch):
    monkeypatch.setattr(provision.requests, "get", lambda url, **kw: FakeResponse(404))
    registry = provision.fetch_hub_registry("hub.pages.dev")
    assert registry == {"hub_version": 0, "stores": []}


def test_fetch_hub_registry_parses_existing_json(monkeypatch):
    body = {"hub_version": 3, "stores": [{"name": "A", "url": "https://a.pages.dev/x.json"}]}
    monkeypatch.setattr(provision.requests, "get", lambda url, **kw: FakeResponse(200, body))
    assert provision.fetch_hub_registry("hub.pages.dev") == body


def test_fetch_hub_registry_raises_on_unexpected_status(monkeypatch):
    monkeypatch.setattr(provision.requests, "get", lambda url, **kw: FakeResponse(302))
    with pytest.raises(provision.ProvisionError, match="Unexpected response"):
        provision.fetch_hub_registry("hub.pages.dev")


def test_fetch_hub_registry_raises_on_network_error(monkeypatch):
    import requests as real_requests

    def boom(url, **kw):
        raise real_requests.ConnectionError("no route")

    monkeypatch.setattr(provision.requests, "get", boom)
    with pytest.raises(provision.ProvisionError, match="Could not reach"):
        provision.fetch_hub_registry("hub.pages.dev")


def test_fetch_hub_registry_with_retry_succeeds_after_transient_failure(monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append(1)
        if len(calls) == 1:
            return FakeResponse(302)  # Access not propagated to the new bypass app yet
        return FakeResponse(200, {"hub_version": 1, "stores": []})

    monkeypatch.setattr(provision.requests, "get", fake_get)
    monkeypatch.setattr(provision.time, "sleep", lambda s: None)

    registry = provision._fetch_hub_registry_with_retry(
        "hub.pages.dev", max_attempts=3, delay_seconds=0
    )
    assert registry["hub_version"] == 1
    assert len(calls) == 2


def test_fetch_hub_registry_with_retry_retries_on_content_mismatch(monkeypatch):
    # Root cause of a real 2026-08-31 failure: the deployment succeeds and
    # Cloudflare returns 200 immediately, but the store's own production
    # domain can take a few seconds to start routing to the deployment that
    # was JUST created - the exact same propagation lag verify_reachable
    # already retries around elsewhere in this file. A 200 with valid JSON
    # that simply doesn't contain what was just pushed must be retried too,
    # not accepted as if it were final.
    stale = {"hub_version": 1, "stores": [{"name": "A", "url": "https://a.pages.dev/x.json"}]}
    fresh = {"hub_version": 1, "stores": [
        {"name": "A", "url": "https://a.pages.dev/x.json"},
        {"name": "B", "url": "https://b.pages.dev/y.json"},
    ]}
    calls = []

    def fake_get(url, **kw):
        calls.append(1)
        return FakeResponse(200, stale if len(calls) < 3 else fresh)

    monkeypatch.setattr(provision.requests, "get", fake_get)
    monkeypatch.setattr(provision.time, "sleep", lambda s: None)

    registry = provision._fetch_hub_registry_with_retry(
        "hub.pages.dev", max_attempts=5, delay_seconds=0,
        until=lambda r: any(e["url"] == "https://b.pages.dev/y.json" for e in r["stores"]),
    )
    assert registry == fresh
    assert len(calls) == 3


def test_fetch_hub_registry_with_retry_returns_last_result_when_predicate_never_satisfied(monkeypatch):
    # Exhausting every attempt without the predicate ever matching must not
    # raise - it's a successful fetch, just not the content the caller
    # wanted. The caller (register_store_with_hub) decides what a
    # still-missing entry means and raises its own, more specific error.
    stale = {"hub_version": 1, "stores": []}
    monkeypatch.setattr(provision.requests, "get", lambda url, **kw: FakeResponse(200, stale))
    monkeypatch.setattr(provision.time, "sleep", lambda s: None)

    registry = provision._fetch_hub_registry_with_retry(
        "hub.pages.dev", max_attempts=3, delay_seconds=0,
        until=lambda r: False,
    )
    assert registry == stale


def test_register_store_with_hub_refuses_when_live_version_is_newer(tmp_path, monkeypatch):
    # register_store_with_hub must raise before ever touching `session` or
    # hub_site_dir - see this section's header comment for why (never push
    # a bundled hub-site older than what's already live).
    monkeypatch.setattr(provision.requests, "get", lambda url, **kw: FakeResponse(200, {
        "hub_version": provision.HUB_VERSION + 1, "stores": [],
    }))
    # stores stays [] forever, so the pre-push read's until=bool(stores)
    # predicate never matches and retries the full max_attempts before
    # falling through to the version-gate check - mock sleep so that
    # doesn't cost real wall-clock time in the test.
    monkeypatch.setattr(provision.time, "sleep", lambda s: None)
    cfg = FakeCfg(tmp_path / "config.yaml", tmp_path / ".env", tmp_path / "remote-site")

    with pytest.raises(provision.ProvisionError, match="newer"):
        provision.register_store_with_hub(
            session=object(), account_id="acct1", owner_email="owner@example.com",
            store_name="Store B", store_domain="storeb.pages.dev",
            store_stock_filename="stock-bbb.json",
            hub_site_dir=Path("does-not-exist"), cfg=cfg, powerful_token="powerful123",
        )


def test_register_store_with_hub_appends_new_store_and_pushes(tmp_path, monkeypatch):
    hub_site_dir = _write_hub_site_dir(tmp_path)
    cfg = FakeCfg(tmp_path / "config.yaml", tmp_path / ".env", tmp_path / "remote-site")

    pre_push = FakeResponse(200, {
        "hub_version": provision.HUB_VERSION,
        "stores": [{"name": "Store A", "url": "https://storea.pages.dev/stock-aaa.json"}],
    })
    post_push = FakeResponse(200, {
        "hub_version": provision.HUB_VERSION,
        "stores": [
            {"name": "Store A", "url": "https://storea.pages.dev/stock-aaa.json"},
            {"name": "Store B", "url": "https://storeb.pages.dev/stock-bbb.json"},
        ],
    })
    get_responses = iter([pre_push, post_push])
    monkeypatch.setattr(provision.requests, "get", lambda url, **kw: next(get_responses))

    hub_domain = f"{provision.HUB_PROJECT_SLUG}.pages.dev"
    session = _QueuedSession(
        get_responses={
            f"/pages/projects/{provision.HUB_PROJECT_SLUG}": FakeResponse(200, {"success": True}),
            "/access/apps": FakeResponse(200, {"success": True, "result": []}),
        },
        post_responses=[
            _wildcard_access_app_response("appHubBroad", hub_domain),
            _wildcard_access_app_response("appHubBypass", hub_domain),
        ],
    )

    push_calls = []

    def fake_push_remote(cfg_arg, *, project=None, export_dir=None, api_token=None):
        push_calls.append((project, api_token))
        data = json.loads((export_dir / provision.HUB_REGISTRY_FILENAME).read_text(encoding="utf-8"))
        assert data["stores"][-1] == {
            "name": "Store B", "url": "https://storeb.pages.dev/stock-bbb.json"
        }
        assert (export_dir / "index.html").is_file()
        return True

    monkeypatch.setattr(provision._remote, "push_remote", fake_push_remote)

    provision.register_store_with_hub(
        session=session, account_id="acct1", owner_email="owner@example.com",
        store_name="Store B", store_domain="storeb.pages.dev",
        store_stock_filename="stock-bbb.json",
        hub_site_dir=hub_site_dir, cfg=cfg, powerful_token="powerful123",
    )

    assert push_calls == [(provision.HUB_PROJECT_SLUG, "powerful123")]


def test_register_store_with_hub_replaces_existing_entry_for_same_domain(tmp_path, monkeypatch):
    # Idempotency is keyed on the store's domain, not the full URL - a
    # re-provisioned store's stock token can change, and that must update
    # the existing entry in place rather than append a duplicate.
    hub_site_dir = _write_hub_site_dir(tmp_path)
    cfg = FakeCfg(tmp_path / "config.yaml", tmp_path / ".env", tmp_path / "remote-site")

    existing = {
        "hub_version": provision.HUB_VERSION,
        "stores": [{"name": "Store B (old)", "url": "https://storeb.pages.dev/stock-oldtoken.json"}],
    }
    # Post-push verification re-fetch must show the store's NEW url to
    # satisfy _entry_live - a stale (pre-push) response here would trigger
    # real retries (unmocked time.sleep) and eventually StopIteration once
    # this iterator's items run out.
    updated = {
        "hub_version": provision.HUB_VERSION,
        "stores": [{"name": "Store B", "url": "https://storeb.pages.dev/stock-newtoken.json"}],
    }
    get_responses = iter([FakeResponse(200, existing), FakeResponse(200, updated)])
    monkeypatch.setattr(provision.requests, "get", lambda url, **kw: next(get_responses))

    hub_domain = f"{provision.HUB_PROJECT_SLUG}.pages.dev"
    session = _QueuedSession(
        get_responses={
            f"/pages/projects/{provision.HUB_PROJECT_SLUG}": FakeResponse(200, {"success": True}),
            "/access/apps": FakeResponse(200, {"success": True, "result": [
                {
                    "id": "appHubBroad", "domain": hub_domain,
                    "self_hosted_domains": [hub_domain, f"*.{hub_domain}"],
                    "destinations": [
                        {"type": "public", "uri": hub_domain},
                        {"type": "public", "uri": f"*.{hub_domain}"},
                    ],
                },
                {"id": "appHubBypass", "domain": f"{hub_domain}/{provision.HUB_REGISTRY_FILENAME}"},
            ]}),
        },
        post_responses=[],  # both apps already exist - no POST expected
    )

    captured = {}

    def fake_push_remote(cfg_arg, *, project=None, export_dir=None, api_token=None):
        captured["stores"] = json.loads(
            (export_dir / provision.HUB_REGISTRY_FILENAME).read_text(encoding="utf-8")
        )["stores"]
        return True

    monkeypatch.setattr(provision._remote, "push_remote", fake_push_remote)

    provision.register_store_with_hub(
        session=session, account_id="acct1", owner_email="owner@example.com",
        store_name="Store B", store_domain="storeb.pages.dev",
        store_stock_filename="stock-newtoken.json",
        hub_site_dir=hub_site_dir, cfg=cfg, powerful_token="powerful123",
    )

    assert captured["stores"] == [
        {"name": "Store B", "url": "https://storeb.pages.dev/stock-newtoken.json"}
    ]
    assert session.calls == [c for c in session.calls if c[0] != "POST"]  # no creates needed


def test_register_store_with_hub_raises_when_push_fails(tmp_path, monkeypatch):
    hub_site_dir = _write_hub_site_dir(tmp_path)
    cfg = FakeCfg(tmp_path / "config.yaml", tmp_path / ".env", tmp_path / "remote-site")
    monkeypatch.setattr(provision.requests, "get", lambda url, **kw: FakeResponse(200, {
        "hub_version": provision.HUB_VERSION, "stores": [],
    }))
    # This registry is genuinely, permanently empty (stores: []), so the
    # pre-push read's until=bool(stores) predicate never matches and retries
    # the full max_attempts before falling through - mock sleep so that
    # doesn't cost real wall-clock time in the test.
    monkeypatch.setattr(provision.time, "sleep", lambda s: None)
    hub_domain = f"{provision.HUB_PROJECT_SLUG}.pages.dev"
    session = _QueuedSession(
        get_responses={
            f"/pages/projects/{provision.HUB_PROJECT_SLUG}": FakeResponse(200, {"success": True}),
            "/access/apps": FakeResponse(200, {"success": True, "result": [
                {
                    "id": "appHubBroad", "domain": hub_domain,
                    "self_hosted_domains": [hub_domain, f"*.{hub_domain}"],
                    "destinations": [
                        {"type": "public", "uri": hub_domain},
                        {"type": "public", "uri": f"*.{hub_domain}"},
                    ],
                },
                {"id": "appHubBypass", "domain": f"{hub_domain}/{provision.HUB_REGISTRY_FILENAME}"},
            ]}),
        },
        post_responses=[],
    )
    monkeypatch.setattr(provision._remote, "push_remote", lambda *a, **kw: False)

    with pytest.raises(provision.ProvisionError, match="Pushing the updated hub failed"):
        provision.register_store_with_hub(
            session=session, account_id="acct1", owner_email="owner@example.com",
            store_name="Store B", store_domain="storeb.pages.dev",
            store_stock_filename="stock-bbb.json",
            hub_site_dir=hub_site_dir, cfg=cfg, powerful_token="powerful123",
        )


def test_register_store_with_hub_raises_when_verification_missing_entry(tmp_path, monkeypatch):
    hub_site_dir = _write_hub_site_dir(tmp_path)
    cfg = FakeCfg(tmp_path / "config.yaml", tmp_path / ".env", tmp_path / "remote-site")
    empty = {"hub_version": provision.HUB_VERSION, "stores": []}
    # Up to 4 pre-push retry reads (until=bool(stores) never matches a
    # genuinely-empty registry either) + up to 4 post-push retry reads, all
    # still empty - the pushed store never shows up. Queue generously past
    # the 8-call worst case so a retry-count tweak doesn't reintroduce a
    # StopIteration here.
    get_responses = iter([FakeResponse(200, empty)] * 10)
    monkeypatch.setattr(provision.requests, "get", lambda url, **kw: next(get_responses))
    monkeypatch.setattr(provision.time, "sleep", lambda s: None)

    hub_domain = f"{provision.HUB_PROJECT_SLUG}.pages.dev"
    session = _QueuedSession(
        get_responses={
            f"/pages/projects/{provision.HUB_PROJECT_SLUG}": FakeResponse(200, {"success": True}),
            "/access/apps": FakeResponse(200, {"success": True, "result": [
                {
                    "id": "appHubBroad", "domain": hub_domain,
                    "self_hosted_domains": [hub_domain, f"*.{hub_domain}"],
                    "destinations": [
                        {"type": "public", "uri": hub_domain},
                        {"type": "public", "uri": f"*.{hub_domain}"},
                    ],
                },
                {"id": "appHubBypass", "domain": f"{hub_domain}/{provision.HUB_REGISTRY_FILENAME}"},
            ]}),
        },
        post_responses=[],
    )
    monkeypatch.setattr(provision._remote, "push_remote", lambda *a, **kw: True)

    with pytest.raises(provision.ProvisionError, match="still doesn't show up"):
        provision.register_store_with_hub(
            session=session, account_id="acct1", owner_email="owner@example.com",
            store_name="Store B", store_domain="storeb.pages.dev",
            store_stock_filename="stock-bbb.json",
            hub_site_dir=hub_site_dir, cfg=cfg, powerful_token="powerful123",
        )


def test_provision_store_hub_registration_failure_does_not_fail_overall_result(tmp_path, monkeypatch):
    # A hub-registration failure must never fail or roll back the store's
    # own already-successful provisioning - just a loud, hand-actionable
    # note in the returned message (setup.iss greps for the marker to show
    # its own MsgBox even though ResultCode stays 0).
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
            "success": True, "result": [{"id": "g2", "name": "Pages Write"}],
        }),
        ("POST", "/user/tokens"): FakeResponse(200, {
            "success": True, "result": {"id": "tok_abc", "value": "secretval"},
        }),
        ("GET", "/accounts/acct1/access/apps"): FakeResponse(200, {"success": True, "result": []}),
        ("POST", "/accounts/acct1/access/apps"): _wildcard_access_app_response(
            "appX", "storeb.pages.dev"
        ),
    })
    monkeypatch.setattr(provision.requests, "Session", lambda: fake_session)
    monkeypatch.setattr(provision._remote, "push_remote", lambda cfg_arg, **kw: True)
    monkeypatch.setattr(provision, "verify_reachable", lambda url, *, expect_status, **kw: True)
    # Triggers register_store_with_hub's version-gate refusal - raises
    # before ever touching `session`, so no hub-specific session mocking
    # is needed for this failure path.
    monkeypatch.setattr(provision.requests, "get", lambda url, **kw: FakeResponse(200, {
        "hub_version": provision.HUB_VERSION + 1, "stores": [],
    }))
    # stores stays [] forever, so the pre-push read's until=bool(stores)
    # predicate never matches and retries the full max_attempts before
    # falling through to the version-gate check - mock sleep so that
    # doesn't cost real wall-clock time in the test.
    monkeypatch.setattr(provision.time, "sleep", lambda s: None)

    result = provision.provision_store(
        cfg,
        powerful_token="powerful123",
        account_id="acct1",
        project_slug="storeb",
        owner_email="owner@example.com",
        hub_store_name="Store B",
    )

    assert result.ok is True
    assert provision._HUB_REGISTRATION_FAILED_MARKER in result.message
    assert '"name": "Store B"' in result.message


def test_provision_store_registers_with_hub_on_success(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    _write_minimal_config(config_path)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "SMTP_PASSWORD=\nCLOUDFLARE_API_TOKEN=\nCLOUDFLARE_ACCOUNT_ID=\n", encoding="utf-8"
    )
    export_dir = tmp_path / "remote-site"
    cfg = FakeCfg(config_path, env_path, export_dir)

    hub_app_root = tmp_path / "app-root"
    _write_hub_site_dir(hub_app_root)
    monkeypatch.setattr(provision, "app_root", lambda: hub_app_root)

    hub_domain = f"{provision.HUB_PROJECT_SLUG}.pages.dev"
    session = _QueuedSession(
        get_responses={
            "/user/tokens/verify": FakeResponse(200, {
                "success": True, "result": {"id": "tok0", "status": "active"},
            }),
            "/pages/projects/storeb": FakeResponse(404, {"success": False}),
            f"/pages/projects/{provision.HUB_PROJECT_SLUG}": FakeResponse(200, {"success": True}),
            "/user/tokens": FakeResponse(200, {"success": True, "result": []}),
            "/user/tokens/permission_groups": FakeResponse(200, {
                "success": True, "result": [{"id": "g2", "name": "Pages Write"}],
            }),
            "/access/apps": FakeResponse(200, {"success": True, "result": []}),
        },
        post_responses=[
            FakeResponse(200, {"success": True, "result": {"name": "storeb"}}),  # create store project
            FakeResponse(200, {"success": True, "result": {"id": "tok_abc", "value": "secretval"}}),  # mint watcher token
            _wildcard_access_app_response("appStoreBroad", "storeb.pages.dev"),
            _wildcard_access_app_response("appStoreBypass", "storeb.pages.dev"),
            _wildcard_access_app_response("appHubBroad", hub_domain),
            _wildcard_access_app_response("appHubBypass", hub_domain),
        ],
    )
    monkeypatch.setattr(provision.requests, "Session", lambda: session)
    monkeypatch.setattr(provision, "verify_reachable", lambda url, *, expect_status, **kw: True)

    hub_registry_empty = {"hub_version": provision.HUB_VERSION, "stores": []}
    # provision_store generates the store's stock-filename token itself, so
    # the exact URL register_store_with_hub writes can't be predicted here -
    # echo back whatever was actually pushed instead of guessing it. Before
    # the push, this is empty (a legitimate first-ever-store registry),
    # which never satisfies the pre-push read's until=bool(stores)
    # predicate, so it retries the full max_attempts before falling through
    # to use it anyway - that's fine, sleep is mocked below.
    pushed_registry: dict = {}

    def fake_get(url, **kw):
        if pushed_registry.get("stores"):
            return FakeResponse(200, pushed_registry)
        return FakeResponse(200, hub_registry_empty)

    monkeypatch.setattr(provision.requests, "get", fake_get)
    monkeypatch.setattr(provision.time, "sleep", lambda s: None)

    push_calls = []

    def fake_push_remote(cfg_arg, *, project=None, export_dir=None, api_token=None):
        push_calls.append(project)
        if project == provision.HUB_PROJECT_SLUG:
            data = json.loads(
                (export_dir / provision.HUB_REGISTRY_FILENAME).read_text(encoding="utf-8")
            )
            pushed_registry.update(data)
        return True

    monkeypatch.setattr(provision._remote, "push_remote", fake_push_remote)

    result = provision.provision_store(
        cfg,
        powerful_token="powerful123",
        account_id="acct1",
        project_slug="storeb",
        owner_email="owner@example.com",
        hub_store_name="Store B",
    )

    assert result.ok is True
    assert "Added to the cross-store hub as 'Store B'." in result.message
    assert provision._HUB_REGISTRATION_FAILED_MARKER not in result.message
    assert push_calls == ["storeb", provision.HUB_PROJECT_SLUG]
