"""
Tests for poslib/remote.py - the Cloudflare Pages push.

Entirely mocked - never actually calls Cloudflare or the network. The one
thing these tests cannot verify from a dev machine with no live Cloudflare
project is whether _cf_hash actually matches what Cloudflare's asset store
expects; that was checked by hand against a throwaway Pages project (fetch
a real deployed asset by its URL and confirm it serves 200, not 404) before
this replaced the old wrangler-CLI push - see the hash algorithm's own
docstring for why a wrong hash is otherwise undetectable from responses
alone.
"""

from __future__ import annotations

import base64

import pytest
import requests

from poslib import remote


class FakeConfig:
    def __init__(self, project="my-shop", export_dir=None,
                 api_token="fake-api-token", account_id="fake-account-id"):
        self._project = project
        self._export_dir = export_dir
        self._api_token = api_token
        self._account_id = account_id

    def get(self, key, default=None):
        if key == "remote.cloudflare_project_name":
            return self._project
        return default

    def path(self, key, default=""):
        if key == "remote.export_dir":
            return self._export_dir
        return default

    def secret(self, name, default=""):
        if name == "CLOUDFLARE_API_TOKEN":
            return self._api_token
        if name == "CLOUDFLARE_ACCOUNT_ID":
            return self._account_id
        return default


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._json_data


def _ok(result=None):
    return FakeResponse({"success": True, "errors": [], "result": result or {}})


def _fail(errors=None):
    return FakeResponse({"success": False, "errors": errors or ["boom"]})


class FakeSession:
    """
    Records every get/post call and answers from queued responses, in the
    same call order push_remote makes them: upload-token (GET), upload
    (POST, one per batch), upsert-hashes (POST), deployments (POST).
    """

    def __init__(self, jwt_response=None, upload_responses=None,
                 upsert_response=None, deploy_response=None):
        self.headers = {}
        self.calls = []
        self._jwt_response = jwt_response or _ok({"jwt": "fake-jwt"})
        self._upload_responses = list(upload_responses or [_ok()])
        self._upsert_response = upsert_response or _ok()
        self._deploy_response = deploy_response or _ok(
            {"url": "https://my-shop.pages.dev"})

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._jwt_response

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if url.endswith("/pages/assets/upload"):
            return self._upload_responses.pop(0)
        if url.endswith("/pages/assets/upsert-hashes"):
            return self._upsert_response
        return self._deploy_response


def _patch_session(monkeypatch, fake_session):
    monkeypatch.setattr(remote.requests, "Session", lambda: fake_session)


def _make_export_dir(tmp_path, files):
    export_dir = tmp_path / "remote-site"
    export_dir.mkdir()
    for rel, content in files.items():
        p = export_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content if isinstance(content, bytes) else content.encode())
    return export_dir


class TestPushRemoteGuardClauses:

    def test_no_project_configured_fails_gracefully(self, tmp_path):
        cfg = FakeConfig(project="", export_dir=_make_export_dir(tmp_path, {"index.html": "hi"}))
        assert remote.push_remote(cfg) is False

    def test_missing_export_dir_fails_gracefully(self, tmp_path):
        cfg = FakeConfig(export_dir=tmp_path / "does-not-exist")
        assert remote.push_remote(cfg) is False

    def test_missing_api_token_fails_gracefully(self, tmp_path):
        cfg = FakeConfig(export_dir=_make_export_dir(tmp_path, {"index.html": "hi"}),
                          api_token="")
        assert remote.push_remote(cfg) is False

    def test_missing_account_id_fails_gracefully(self, tmp_path):
        cfg = FakeConfig(export_dir=_make_export_dir(tmp_path, {"index.html": "hi"}),
                          account_id="")
        assert remote.push_remote(cfg) is False

    def test_empty_export_dir_fails_gracefully(self, tmp_path):
        export_dir = tmp_path / "remote-site"
        export_dir.mkdir()
        cfg = FakeConfig(export_dir=export_dir)
        assert remote.push_remote(cfg) is False


class TestPushRemoteHappyPath:

    def test_success_returns_true_and_logs_url(self, tmp_path, monkeypatch, caplog):
        export_dir = _make_export_dir(tmp_path, {"index.html": "<h1>hi</h1>", "daily.json": "{}"})
        cfg = FakeConfig(export_dir=export_dir)
        session = FakeSession()
        _patch_session(monkeypatch, session)

        with caplog.at_level("INFO"):
            assert remote.push_remote(cfg) is True
        assert "https://my-shop.pages.dev" in caplog.text

    def test_upload_token_request_uses_account_prefixed_url_and_api_token(self, tmp_path, monkeypatch):
        export_dir = _make_export_dir(tmp_path, {"index.html": "hi"})
        cfg = FakeConfig(export_dir=export_dir)
        session = FakeSession()
        _patch_session(monkeypatch, session)

        remote.push_remote(cfg)

        method, url, kwargs = session.calls[0]
        assert method == "GET"
        assert url == "https://api.cloudflare.com/client/v4/accounts/fake-account-id/pages/projects/my-shop/upload-token"
        assert session.headers["Authorization"] == "Bearer fake-api-token"

    def test_upload_and_upsert_hashes_use_jwt_and_no_account_prefix(self, tmp_path, monkeypatch):
        export_dir = _make_export_dir(tmp_path, {"index.html": "hi"})
        cfg = FakeConfig(export_dir=export_dir)
        session = FakeSession()
        _patch_session(monkeypatch, session)

        remote.push_remote(cfg)

        upload_call = next(c for c in session.calls if c[1].endswith("/pages/assets/upload"))
        upsert_call = next(c for c in session.calls if c[1].endswith("/pages/assets/upsert-hashes"))
        _method, upload_url, upload_kwargs = upload_call
        assert upload_url == "https://api.cloudflare.com/client/v4/pages/assets/upload"
        assert upload_kwargs["headers"]["Authorization"] == "Bearer fake-jwt"
        _method, upsert_url, upsert_kwargs = upsert_call
        assert upsert_url == "https://api.cloudflare.com/client/v4/pages/assets/upsert-hashes"
        assert upsert_kwargs["headers"]["Authorization"] == "Bearer fake-jwt"

    def test_deployment_request_goes_back_to_account_prefixed_url_and_api_token(self, tmp_path, monkeypatch):
        export_dir = _make_export_dir(tmp_path, {"index.html": "hi"})
        cfg = FakeConfig(export_dir=export_dir)
        session = FakeSession()
        _patch_session(monkeypatch, session)

        remote.push_remote(cfg)

        deploy_call = next(
            c for c in session.calls if c[1].endswith("/pages/projects/my-shop/deployments"))
        _method, url, kwargs = deploy_call
        assert url == "https://api.cloudflare.com/client/v4/accounts/fake-account-id/pages/projects/my-shop/deployments"
        # session-level Authorization header carries the API token here -
        # no jwt override was set for this call.
        assert "headers" not in kwargs or "Authorization" not in kwargs.get("headers", {})

    def test_manifest_keys_have_leading_slash_and_match_uploaded_hashes(self, tmp_path, monkeypatch):
        export_dir = _make_export_dir(tmp_path, {"index.html": "<h1>hi</h1>",
                                                  "sub/daily.json": "{}"})
        cfg = FakeConfig(export_dir=export_dir)
        session = FakeSession()
        _patch_session(monkeypatch, session)

        remote.push_remote(cfg)

        deploy_call = next(
            c for c in session.calls if c[1].endswith("/pages/projects/my-shop/deployments"))
        _method, _url, kwargs = deploy_call
        import json as _json
        manifest = _json.loads(kwargs["files"]["manifest"][1])
        assert set(manifest.keys()) == {"/index.html", "/sub/daily.json"}
        assert all(len(v) == 32 for v in manifest.values())

    def test_batches_uploads_when_more_files_than_batch_size(self, tmp_path, monkeypatch):
        files = {f"file{i}.html": f"content {i}" for i in range(5)}
        export_dir = _make_export_dir(tmp_path, files)
        cfg = FakeConfig(export_dir=export_dir)
        monkeypatch.setattr(remote, "_MAX_FILES_PER_UPLOAD_BATCH", 2)
        session = FakeSession(upload_responses=[_ok(), _ok(), _ok()])
        _patch_session(monkeypatch, session)

        assert remote.push_remote(cfg) is True
        upload_calls = [c for c in session.calls if c[1].endswith("/pages/assets/upload")]
        assert len(upload_calls) == 3

    def test_skips_files_over_the_size_limit(self, tmp_path, monkeypatch):
        export_dir = _make_export_dir(tmp_path, {"small.html": "ok", "huge.html": "x" * 1000})
        cfg = FakeConfig(export_dir=export_dir)
        monkeypatch.setattr(remote, "_MAX_FILE_SIZE_BYTES", 100)
        session = FakeSession()
        _patch_session(monkeypatch, session)

        remote.push_remote(cfg)

        deploy_call = next(
            c for c in session.calls if c[1].endswith("/pages/projects/my-shop/deployments"))
        import json as _json
        manifest = _json.loads(deploy_call[2]["files"]["manifest"][1])
        assert "/small.html" in manifest
        assert "/huge.html" not in manifest

    def test_ignores_wrangler_reserved_names(self, tmp_path, monkeypatch):
        files = {
            "index.html": "hi",
            "_worker.js": "ignored",
            "_redirects": "ignored",
            "_headers": "ignored",
            "_routes.json": "ignored",
            "functions/handler.js": "ignored",
        }
        export_dir = _make_export_dir(tmp_path, files)
        cfg = FakeConfig(export_dir=export_dir)
        session = FakeSession()
        _patch_session(monkeypatch, session)

        remote.push_remote(cfg)

        deploy_call = next(
            c for c in session.calls if c[1].endswith("/pages/projects/my-shop/deployments"))
        import json as _json
        manifest = _json.loads(deploy_call[2]["files"]["manifest"][1])
        assert set(manifest.keys()) == {"/index.html"}


class TestPushRemoteFailureModes:

    def test_upload_token_rejected_returns_false(self, tmp_path, monkeypatch):
        export_dir = _make_export_dir(tmp_path, {"index.html": "hi"})
        cfg = FakeConfig(export_dir=export_dir)
        session = FakeSession(jwt_response=_fail())
        _patch_session(monkeypatch, session)
        assert remote.push_remote(cfg) is False

    def test_upload_batch_rejected_returns_false(self, tmp_path, monkeypatch):
        export_dir = _make_export_dir(tmp_path, {"index.html": "hi"})
        cfg = FakeConfig(export_dir=export_dir)
        session = FakeSession(upload_responses=[_fail()])
        _patch_session(monkeypatch, session)
        assert remote.push_remote(cfg) is False

    def test_upsert_hashes_rejected_returns_false(self, tmp_path, monkeypatch):
        export_dir = _make_export_dir(tmp_path, {"index.html": "hi"})
        cfg = FakeConfig(export_dir=export_dir)
        session = FakeSession(upsert_response=_fail())
        _patch_session(monkeypatch, session)
        assert remote.push_remote(cfg) is False

    def test_deployment_rejected_returns_false(self, tmp_path, monkeypatch):
        export_dir = _make_export_dir(tmp_path, {"index.html": "hi"})
        cfg = FakeConfig(export_dir=export_dir)
        session = FakeSession(deploy_response=_fail())
        _patch_session(monkeypatch, session)
        assert remote.push_remote(cfg) is False

    def test_network_error_returns_false_not_raises(self, tmp_path, monkeypatch):
        export_dir = _make_export_dir(tmp_path, {"index.html": "hi"})
        cfg = FakeConfig(export_dir=export_dir)

        class BrokenSession:
            headers = {}

            def get(self, *a, **k):
                raise requests.ConnectionError("no internet")

        monkeypatch.setattr(remote.requests, "Session", lambda: BrokenSession())
        assert remote.push_remote(cfg) is False

    def test_http_error_status_returns_false_not_raises(self, tmp_path, monkeypatch):
        export_dir = _make_export_dir(tmp_path, {"index.html": "hi"})
        cfg = FakeConfig(export_dir=export_dir)
        session = FakeSession(jwt_response=FakeResponse({}, status_code=500))
        _patch_session(monkeypatch, session)
        assert remote.push_remote(cfg) is False

    def test_malformed_success_response_returns_false_not_raises(self, tmp_path, monkeypatch):
        """result.jwt missing entirely - a KeyError must not escape push_remote."""
        export_dir = _make_export_dir(tmp_path, {"index.html": "hi"})
        cfg = FakeConfig(export_dir=export_dir)
        session = FakeSession(jwt_response=FakeResponse({"success": True, "result": {}}))
        _patch_session(monkeypatch, session)
        assert remote.push_remote(cfg) is False


class TestCfHash:
    """
    Regression-locks the hash formula against wrangler's own documented
    algorithm: blake3(base64(bytes) + ext_without_dot).hex()[:32].
    """

    def test_matches_the_documented_formula(self):
        from blake3 import blake3 as blake3_direct

        data = b"<h1>hello</h1>"
        expected = blake3_direct(base64.b64encode(data) + b"html").hexdigest()[:32]
        assert remote._cf_hash(data, "index.html") == expected

    def test_is_32_hex_characters(self):
        result = remote._cf_hash(b"anything", "file.json")
        assert len(result) == 32
        int(result, 16)  # raises if not valid hex

    def test_different_content_different_hash(self):
        assert remote._cf_hash(b"a", "x.html") != remote._cf_hash(b"b", "x.html")

    def test_different_extension_different_hash(self):
        assert remote._cf_hash(b"same", "x.html") != remote._cf_hash(b"same", "x.json")

    def test_extension_has_no_leading_dot(self):
        # A file with no extension hashes with an empty extension string,
        # not ".": confirms .suffix.lstrip(".") is doing its job.
        from blake3 import blake3 as blake3_direct

        data = b"no extension here"
        expected = blake3_direct(base64.b64encode(data) + b"").hexdigest()[:32]
        assert remote._cf_hash(data, "Makefile") == expected
