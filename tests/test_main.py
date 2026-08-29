"""
Tests for main.py - the single entry point the packaged build launches.

No flag -> the dashboard. --watcher -> the watcher, forwarding its own
flags. Both app.main() and watcher.main() are mocked here; they have their
own test coverage already (or are exercised directly by start.bat/
start-quiet.bat in the dev workflow) - this file only tests the dispatch.
"""

from __future__ import annotations

import os

import main as main_module


def test_no_flags_runs_the_dashboard(monkeypatch):
    calls = []
    monkeypatch.setattr(main_module.app, "main", lambda: calls.append("dashboard") or 0)
    monkeypatch.setattr(main_module.watcher, "main", lambda: calls.append("watcher") or 0)
    assert main_module.main([]) == 0
    assert calls == ["dashboard"]


def test_watcher_flag_runs_the_watcher(monkeypatch):
    calls = []
    monkeypatch.setattr(main_module.app, "main", lambda: calls.append("dashboard") or 0)
    monkeypatch.setattr(main_module.watcher, "main", lambda: calls.append("watcher") or 0)
    assert main_module.main(["--watcher"]) == 0
    assert calls == ["watcher"]


def test_watcher_flags_are_forwarded_to_sys_argv(monkeypatch):
    seen_argv = []

    def fake_watcher_main():
        seen_argv.append(list(main_module.sys.argv))
        return 0

    monkeypatch.setattr(main_module.watcher, "main", fake_watcher_main)
    assert main_module.main(["--watcher", "--digest-now"]) == 0
    assert seen_argv == [["watcher.py", "--digest-now"]]


def test_dashboard_flags_are_forwarded_to_sys_argv(monkeypatch):
    seen_argv = []

    def fake_app_main():
        seen_argv.append(list(main_module.sys.argv))
        return 0

    monkeypatch.setattr(main_module.app, "main", fake_app_main)
    assert main_module.main(["--no-browser"]) == 0
    assert seen_argv == [["app.py", "--no-browser"]]


class FakeConfig:
    pass


def _patch_apply_update_deps(monkeypatch, *, config_error=None):
    """
    poslib.config/poslib.updater are imported lazily inside
    main._apply_update, so they must be patched on the real modules, not on
    main_module's own namespace.
    """
    import poslib.config as config_module
    import poslib.updater as updater_module

    calls = {"get_config": 0, "setup_logging": 0, "check_and_apply_update": []}

    def fake_get_config():
        calls["get_config"] += 1
        if config_error is not None:
            raise config_error
        return FakeConfig()

    monkeypatch.setattr(config_module, "get_config", fake_get_config)
    monkeypatch.setattr(config_module, "setup_logging",
                        lambda cfg: calls.__setitem__("setup_logging", calls["setup_logging"] + 1))
    monkeypatch.setattr(updater_module, "check_and_apply_update",
                        lambda cfg: calls["check_and_apply_update"].append(cfg) or True)
    return calls


class TestApplyUpdateDispatch:

    def test_apply_update_flag_calls_check_and_apply_update(self, monkeypatch):
        calls = _patch_apply_update_deps(monkeypatch)
        assert main_module.main(["--apply-update"]) == 0
        assert calls["get_config"] == 1
        assert calls["setup_logging"] == 1
        assert len(calls["check_and_apply_update"]) == 1

    def test_data_dir_sets_the_override_env_var_before_loading_config(self, monkeypatch):
        monkeypatch.delenv("SHOP_ANALYSIS_DATA_DIR", raising=False)
        seen = {}

        import poslib.config as config_module
        import poslib.updater as updater_module

        def fake_get_config():
            seen["env"] = main_module.os.environ.get("SHOP_ANALYSIS_DATA_DIR")
            return FakeConfig()

        monkeypatch.setattr(config_module, "get_config", fake_get_config)
        monkeypatch.setattr(config_module, "setup_logging", lambda cfg: None)
        monkeypatch.setattr(updater_module, "check_and_apply_update", lambda cfg: False)

        try:
            assert main_module.main(["--apply-update", "--data-dir", r"C:\Users\owner\AppData\Local\Shop Analysis"]) == 0
            assert seen["env"] == r"C:\Users\owner\AppData\Local\Shop Analysis"
        finally:
            # main._apply_update() sets this via plain os.environ[...] = ...
            # (correct in the real one-shot elevated process it's meant for -
            # it just exits right after). A monkeypatch.delenv() here would
            # NOT actually clean up: monkeypatch's own teardown restores
            # whatever value was present at the moment of ITS call - which
            # by then is this test's own leaked value - putting it right
            # back after the test "cleans up". Popping os.environ directly
            # bypasses that tracking entirely. Caught for real when the
            # leak made an unrelated test (test_photos.py) fail trying to
            # write to this bogus path during a full-suite run.
            os.environ.pop("SHOP_ANALYSIS_DATA_DIR", None)

    def test_no_data_dir_leaves_the_override_env_var_untouched(self, monkeypatch):
        monkeypatch.delenv("SHOP_ANALYSIS_DATA_DIR", raising=False)
        _patch_apply_update_deps(monkeypatch)
        assert main_module.main(["--apply-update"]) == 0
        assert "SHOP_ANALYSIS_DATA_DIR" not in main_module.os.environ

    def test_bad_config_is_reported_and_returns_1_without_crashing(self, monkeypatch, capsys):
        import poslib.config as config_module
        _patch_apply_update_deps(monkeypatch, config_error=config_module.ConfigError("bad config"))

        assert main_module.main(["--apply-update"]) == 1
        assert "bad config" in capsys.readouterr().out


def _patch_provision_cloudflare_deps(monkeypatch, *, config_error=None):
    """
    poslib.config is imported lazily inside main._provision_cloudflare.
    poslib.provision.provision_store is imported at module level in main,
    so must be patched on main_module itself.
    """
    import poslib.config as config_module
    import poslib.provision as provision_module

    calls = {"get_config": 0, "setup_logging": 0, "provision_store": []}

    def fake_get_config():
        calls["get_config"] += 1
        if config_error is not None:
            raise config_error
        return FakeConfig()

    def fake_provision_store(cfg, *, powerful_token, account_id, project_slug, owner_email):
        calls["provision_store"].append({
            "cfg": cfg,
            "powerful_token": powerful_token,
            "account_id": account_id,
            "project_slug": project_slug,
            "owner_email": owner_email,
        })
        return provision_module.ProvisionResult(True, "done")

    monkeypatch.setattr(config_module, "get_config", fake_get_config)
    monkeypatch.setattr(config_module, "setup_logging",
                        lambda cfg: calls.__setitem__("setup_logging", calls["setup_logging"] + 1))
    # provision_store is imported at module level in main, so patch it there
    monkeypatch.setattr(main_module, "provision_store", fake_provision_store)
    return calls


def test_provision_cloudflare_reads_token_from_env_not_argv(monkeypatch, capsys):
    monkeypatch.setenv("POS_TOOL_PROVISION_TOKEN", "secret-token")
    calls = _patch_provision_cloudflare_deps(monkeypatch)

    rc = main_module.main([
        "--provision-cloudflare",
        "--account-id", "acct1",
        "--project-slug", "storeb",
        "--owner-email", "owner@x.com",
    ])

    assert rc == 0
    assert len(calls["provision_store"]) == 1
    assert calls["provision_store"][0]["powerful_token"] == "secret-token"
    assert calls["provision_store"][0]["account_id"] == "acct1"
    assert calls["provision_store"][0]["project_slug"] == "storeb"
    assert calls["provision_store"][0]["owner_email"] == "owner@x.com"
    assert "POS_TOOL_PROVISION_TOKEN" not in os.environ  # discarded after use


def test_provision_cloudflare_fails_cleanly_with_no_token_set(monkeypatch, capsys):
    monkeypatch.delenv("POS_TOOL_PROVISION_TOKEN", raising=False)

    rc = main_module.main([
        "--provision-cloudflare",
        "--account-id", "acct1",
        "--project-slug", "storeb",
        "--owner-email", "owner@x.com",
    ])

    assert rc == 1
    assert "POS_TOOL_PROVISION_TOKEN" in capsys.readouterr().out
