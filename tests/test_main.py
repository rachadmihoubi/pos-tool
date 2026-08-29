"""
Tests for main.py - the single entry point the packaged build launches.

No flag -> the dashboard. --watcher -> the watcher, forwarding its own
flags. Both app.main() and watcher.main() are mocked here; they have their
own test coverage already (or are exercised directly by start.bat/
start-quiet.bat in the dev workflow) - this file only tests the dispatch.
"""

from __future__ import annotations

import os

import pytest

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


class TestRunProvisioningWithWatchdog:
    """
    _run_provisioning_with_watchdog is split out from _provision_cloudflare
    specifically so this timeout path can be tested without actually
    calling os._exit (which would kill the pytest process itself) - see its
    docstring in main.py.
    """

    def test_returns_result_when_provision_store_finishes_in_time(self, monkeypatch):
        import poslib.provision as provision_module

        def fake_provision_store(cfg, *, powerful_token, account_id, project_slug, owner_email):
            return provision_module.ProvisionResult(True, "done")

        monkeypatch.setattr(main_module, "provision_store", fake_provision_store)

        result = main_module._run_provisioning_with_watchdog(
            object(), token="tok", account_id="acct1",
            project_slug="storeb", owner_email="owner@x.com",
            timeout_seconds=5,
        )

        assert result is not None
        assert result.ok is True
        assert result.message == "done"

    def test_returns_none_when_provision_store_exceeds_timeout(self, monkeypatch):
        import threading

        def hanging_provision_store(cfg, *, powerful_token, account_id, project_slug, owner_email):
            # Blocks well past the short timeout below, standing in for a
            # genuinely stuck DNS resolution or network call - a real
            # provision_store never actually needs killing at the OS level
            # here because it's a daemon thread; the test process just
            # stops waiting on it and exits normally.
            threading.Event().wait(30)

        monkeypatch.setattr(main_module, "provision_store", hanging_provision_store)

        result = main_module._run_provisioning_with_watchdog(
            object(), token="tok", account_id="acct1",
            project_slug="storeb", owner_email="owner@x.com",
            timeout_seconds=0.2,
        )

        assert result is None

    def test_returns_a_failure_result_when_provision_store_raises(self, monkeypatch):
        # provision_store's own catch-all only covers Exception - a
        # log.exception() call inside it can itself raise (this project has
        # a documented, recurring log-rotation PermissionError when the
        # watcher holds the log file open) and BaseException subclasses
        # escape it entirely. Without _run's own try/except, the thread
        # would die having appended nothing to result_holder, and the read
        # in _run_provisioning_with_watchdog (result_holder[0]) would raise
        # IndexError instead of surfacing the real cause.
        def exploding_provision_store(cfg, *, powerful_token, account_id, project_slug, owner_email):
            raise RuntimeError("log rotation permission error")

        monkeypatch.setattr(main_module, "provision_store", exploding_provision_store)

        result = main_module._run_provisioning_with_watchdog(
            object(), token="tok", account_id="acct1",
            project_slug="storeb", owner_email="owner@x.com",
            timeout_seconds=5,
        )

        assert result is not None
        assert result.ok is False
        assert "log rotation permission error" in result.message

    def test_provision_cloudflare_exits_process_on_timeout(self, monkeypatch, capsys):
        calls = _patch_provision_cloudflare_deps(monkeypatch)

        monkeypatch.setattr(
            main_module, "_run_provisioning_with_watchdog",
            lambda cfg, **kw: None,
        )
        exit_calls = []

        class _FakeExit(BaseException):
            pass

        def fake_exit(code):
            # The real os._exit() never returns - it terminates the process
            # immediately. A plain no-op mock would let control fall through
            # into `result.message` with result still None and crash with
            # an unrelated AttributeError, so this raises to mimic that
            # "never returns" contract instead.
            exit_calls.append(code)
            raise _FakeExit(code)

        monkeypatch.setattr(main_module.os, "_exit", fake_exit)

        os.environ["POS_TOOL_PROVISION_TOKEN"] = "tok"
        try:
            with pytest.raises(_FakeExit):
                main_module._provision_cloudflare([
                    "--account-id", "acct1", "--project-slug", "storeb",
                    "--owner-email", "owner@x.com",
                ])
        finally:
            os.environ.pop("POS_TOOL_PROVISION_TOKEN", None)

        assert exit_calls == [1]
        assert "taking far longer than expected" in capsys.readouterr().out
        assert calls["provision_store"] == []  # never actually invoked directly
