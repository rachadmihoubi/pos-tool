"""
Tests for watcher.py's self-healing pieces, added 2026-09-14 after
CLAUDE.md's "Bug #2" (a real incident, 2026-09-05): the watcher died from an
unhandled exception and stayed dead for ~17 hours, because its scheduled
task's only trigger is onlogon (one-shot, not recurring) and nothing else
was watching for it to come back. Three parts, tested here:

1. The main loop's try/except hardening (_safe_loop_iteration) - a single
   bad exception must log and keep the loop alive, not kill the process.
2. The heartbeat file + ensure_watcher_running - the "Shop Analysis -
   Watchdog" scheduled task's own entry point, which restarts the watcher
   via its scheduled task if the heartbeat has gone stale.

Note on _watcher_task_is_running: this originally parsed `schtasks
/query`'s own text output for an English "Status: Running"/"Ready" line -
an opus-reviewer pass caught, live on this exact store's till PC (which
runs French Windows), that schtasks localizes that VALUE too ("En
cours"/"Prêt"), making the whole self-healing mechanism a silent no-op on
the machine Bug #2 actually happened on. Fixed to use PowerShell's
Get-ScheduledTask, whose .State enum is culture-invariant - tests below
mock that mechanism, not the old one.

Entirely mocked/isolated: no real database, no real subprocess calls, no
real Observer/threading loop, and no dependency on this machine's own real
heartbeat/marker files (see _isolated_data_dir).
"""
from __future__ import annotations

import datetime

import pytest

import watcher


class _FakeConfig:
    """Just enough of Config's surface for Watcher.__init__."""

    def __init__(self, tmp_path, **overrides):
        self.source_db = tmp_path / "source.dblx"
        self.cache_db = tmp_path / "cache.db"
        self._overrides = overrides

    def get(self, key, default=None):
        return self._overrides.get(key, default)


@pytest.fixture(autouse=True)
def _isolated_data_dir(monkeypatch, tmp_path):
    """
    Every heartbeat/marker file lands under tmp_path, never a real
    machine's - both watcher.py's own user_data_dir and poslib.updater's
    (ensure_watcher_running checks poslib.updater.update_in_progress(),
    which reads its own marker via poslib.paths.user_data_dir()
    independently - patching only watcher.user_data_dir would leave that
    check reading this real machine's actual data dir).
    """
    monkeypatch.setattr(watcher, "user_data_dir", lambda: tmp_path)
    import poslib.updater as updater_module
    monkeypatch.setattr(updater_module, "user_data_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _no_update_in_progress(monkeypatch):
    """
    Default every test to "no update in progress" - TestUpdateInProgressGuard
    below overrides this explicitly to exercise the guard itself.
    """
    import poslib.updater as updater_module
    monkeypatch.setattr(updater_module, "update_in_progress", lambda: False)


class TestHeartbeat:

    def test_write_then_read_is_fresh(self):
        watcher._write_heartbeat()
        age = watcher._heartbeat_age_seconds()
        assert age is not None
        assert 0 <= age < 5

    def test_missing_heartbeat_returns_none(self):
        assert watcher._heartbeat_age_seconds() is None

    def test_corrupt_heartbeat_returns_none(self, _isolated_data_dir):
        watcher._heartbeat_path().write_text("not a timestamp", encoding="utf-8")
        assert watcher._heartbeat_age_seconds() is None

    def test_write_never_raises_when_the_directory_does_not_exist(self, monkeypatch, tmp_path):
        monkeypatch.setattr(watcher, "user_data_dir", lambda: tmp_path / "nonexistent" / "nested")
        watcher._write_heartbeat()  # must not raise


def _fake_powershell_state(state: str):
    """A fake subprocess.run matching _run_hidden's Get-ScheduledTask call shape."""
    def fake_run(cmd, **k):
        assert cmd[0] == "powershell.exe"
        joined = " ".join(cmd)
        assert "Get-ScheduledTask" in joined
        assert "Shop Analysis - Watcher" in joined
        return watcher.subprocess.CompletedProcess(cmd, 0, stdout=f"{state}\r\n", stderr="")
    return fake_run


class TestWatcherTaskIsRunning:
    """
    Deliberately does NOT test any English/French text-matching - the
    whole point of the Get-ScheduledTask fix is that .State.ToString() is
    culture-invariant, so there is no locale axis left to test against.
    """

    def test_true_when_state_is_running(self, monkeypatch):
        monkeypatch.setattr(watcher.subprocess, "run", _fake_powershell_state("Running"))
        assert watcher._watcher_task_is_running() is True

    def test_false_when_state_is_ready(self, monkeypatch):
        monkeypatch.setattr(watcher.subprocess, "run", _fake_powershell_state("Ready"))
        assert watcher._watcher_task_is_running() is False

    def test_fails_safe_true_on_nonzero_exit(self, monkeypatch):
        """A query failure (task missing, access denied) must never look like "not running" -
        that would trigger a restart on top of a task we couldn't actually confirm was idle."""
        def fake_run(cmd, **k):
            return watcher.subprocess.CompletedProcess(cmd, 1, stdout="", stderr="ERROR: ...")
        monkeypatch.setattr(watcher.subprocess, "run", fake_run)
        assert watcher._watcher_task_is_running() is True

    def test_fails_safe_true_on_subprocess_error(self, monkeypatch):
        def _raise(*a, **k):
            raise OSError("powershell.exe not found")
        monkeypatch.setattr(watcher.subprocess, "run", _raise)
        assert watcher._watcher_task_is_running() is True

    def test_fails_safe_true_on_decode_error(self, monkeypatch):
        """
        schtasks/powershell can emit OEM-codepage bytes that aren't valid
        in the process's ANSI codepage - text=True's default strict decode
        would raise UnicodeDecodeError (a ValueError subclass) here.
        Regression coverage for an opus-reviewer finding, 2026-09-14.
        """
        def _raise(*a, **k):
            raise UnicodeDecodeError("cp1252", b"\x81", 0, 1, "invalid byte")
        monkeypatch.setattr(watcher.subprocess, "run", _raise)
        assert watcher._watcher_task_is_running() is True


class TestEnsureWatcherRunning:

    def test_fresh_heartbeat_does_nothing(self, monkeypatch):
        watcher._write_heartbeat()
        calls = []
        monkeypatch.setattr(watcher.subprocess, "run",
                             lambda *a, **k: calls.append(a) or watcher.subprocess.CompletedProcess(a, 0))
        watcher.ensure_watcher_running()
        assert calls == []

    def test_stale_heartbeat_but_task_already_running_does_not_restart(self, monkeypatch, _isolated_data_dir):
        old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
        watcher._heartbeat_path().write_text(old.isoformat(), encoding="utf-8")

        run_calls = []

        def fake_run(cmd, **k):
            run_calls.append(cmd)
            return watcher.subprocess.CompletedProcess(cmd, 0, stdout="Running\r\n", stderr="")
        monkeypatch.setattr(watcher.subprocess, "run", fake_run)

        watcher.ensure_watcher_running()

        # Only the status query happened - never /run, which would risk a
        # duplicate watcher process (this machine's own documented
        # recurring nuisance - see CLAUDE.md).
        assert len(run_calls) == 1
        assert run_calls[0][0] == "powershell.exe"

    def test_stale_heartbeat_and_task_not_running_restarts_it(self, monkeypatch, _isolated_data_dir):
        old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
        watcher._heartbeat_path().write_text(old.isoformat(), encoding="utf-8")

        run_calls = []

        def fake_run(cmd, **k):
            run_calls.append(cmd)
            if cmd[0] == "powershell.exe":
                return watcher.subprocess.CompletedProcess(cmd, 0, stdout="Ready\r\n", stderr="")
            return watcher.subprocess.CompletedProcess(cmd, 0, stdout="SUCCESS", stderr="")
        monkeypatch.setattr(watcher.subprocess, "run", fake_run)

        watcher.ensure_watcher_running()

        assert len(run_calls) == 2
        assert run_calls[0][0] == "powershell.exe"
        assert run_calls[1][:3] == ["schtasks", "/run", "/tn"]
        assert "Shop Analysis - Watcher" in run_calls[1]

    def test_missing_heartbeat_and_task_not_running_restarts_it(self, monkeypatch):
        """No heartbeat at all (e.g. a watcher that crashed before ever writing one)
        must be treated the same as a stale one, not skipped."""
        run_calls = []

        def fake_run(cmd, **k):
            run_calls.append(cmd)
            if cmd[0] == "powershell.exe":
                return watcher.subprocess.CompletedProcess(cmd, 0, stdout="Ready\r\n", stderr="")
            return watcher.subprocess.CompletedProcess(cmd, 0, stdout="SUCCESS", stderr="")
        monkeypatch.setattr(watcher.subprocess, "run", fake_run)

        watcher.ensure_watcher_running()

        assert any(cmd[:2] == ["schtasks", "/run"] for cmd in run_calls)

    def test_never_raises_when_restart_itself_fails(self, monkeypatch):
        def fake_run(cmd, **k):
            if cmd[0] == "powershell.exe":
                return watcher.subprocess.CompletedProcess(cmd, 0, stdout="Ready\r\n", stderr="")
            raise OSError("schtasks.exe not found")
        monkeypatch.setattr(watcher.subprocess, "run", fake_run)

        watcher.ensure_watcher_running()  # must not raise


class TestUpdateInProgressGuard:
    """
    Regression coverage for an opus-reviewer finding, 2026-09-14: without
    this guard, a real auto-update in progress (which has taken multiple
    hours on this store before) could have the Watchdog task restart the
    watcher mid-install, holding files open the installer needs to
    replace - reintroducing Bug #3's own hang class through a new door.
    """

    def test_stale_heartbeat_but_update_in_progress_does_nothing(self, monkeypatch, _isolated_data_dir):
        import poslib.updater as updater_module
        monkeypatch.setattr(updater_module, "update_in_progress", lambda: True)

        old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
        watcher._heartbeat_path().write_text(old.isoformat(), encoding="utf-8")

        calls = []
        monkeypatch.setattr(watcher.subprocess, "run",
                             lambda *a, **k: calls.append(a) or watcher.subprocess.CompletedProcess(a, 0))

        watcher.ensure_watcher_running()

        assert calls == []  # never even queried the task - update_in_progress short-circuits first


class TestSafeLoopIteration:
    """
    The core regression coverage for Bug #2: a single exception anywhere in
    one loop pass must never propagate out of _safe_loop_iteration.
    """

    def test_an_exception_in_one_iteration_does_not_propagate(self, monkeypatch, tmp_path):
        w = watcher.Watcher(_FakeConfig(tmp_path))
        monkeypatch.setattr(w, "_loop_iteration",
                             lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        monkeypatch.setattr(watcher.time, "sleep", lambda s: None)

        w._safe_loop_iteration()  # must not raise

    def test_a_healthy_iteration_runs_normally(self, monkeypatch, tmp_path):
        w = watcher.Watcher(_FakeConfig(tmp_path))
        calls = []
        monkeypatch.setattr(w, "_loop_iteration", lambda: calls.append(1))

        w._safe_loop_iteration()

        assert calls == [1]

    def test_the_loop_keeps_going_across_repeated_failures(self, monkeypatch, tmp_path):
        """Not just one bad iteration - a persistently broken condition
        (e.g. _digest_due() itself always raising) must not wedge the loop."""
        w = watcher.Watcher(_FakeConfig(tmp_path))
        monkeypatch.setattr(watcher.time, "sleep", lambda s: None)
        attempts = []

        def always_fails():
            attempts.append(1)
            raise RuntimeError("still broken")
        monkeypatch.setattr(w, "_loop_iteration", always_fails)

        for _ in range(5):
            w._safe_loop_iteration()

        assert len(attempts) == 5

    def test_keyboard_interrupt_is_not_swallowed(self, monkeypatch, tmp_path):
        """
        bare `except Exception` must not catch KeyboardInterrupt (a
        BaseException subclass) - confirms Ctrl+C during an iteration
        still reaches run()'s own outer handler and stops the process
        cleanly, rather than being logged-and-continued like a real bug.
        """
        w = watcher.Watcher(_FakeConfig(tmp_path))
        monkeypatch.setattr(w, "_loop_iteration",
                             lambda: (_ for _ in ()).throw(KeyboardInterrupt()))

        with pytest.raises(KeyboardInterrupt):
            w._safe_loop_iteration()


class TestLoopIterationWritesHeartbeat:

    def _wired_for_isolation(self, w, monkeypatch):
        """
        Avoid touching the real dirty-event/rebuild/digest/backup machinery -
        these tests are only about the heartbeat-throttling logic itself.
        Critically, this includes rebuild() and _last_poll: leaving
        _last_poll at its __init__ default (0.0) makes the poll safety net
        ("time.time() - self._last_poll >= self.poll_seconds") true no
        matter how large poll_seconds is set to, which would call the REAL
        rebuild() against this test's fake, nonexistent database file -
        harmless but genuinely slow (_wait_until_settled's own 120s
        deadline, spent retrying a file that will never appear).
        """
        monkeypatch.setattr(w._dirty, "wait", lambda timeout: False)
        monkeypatch.setattr(w, "_digest_due", lambda: False)
        monkeypatch.setattr(w, "_backup_due", lambda: False)
        monkeypatch.setattr(w, "rebuild", lambda *a, **k: None)
        w.poll_seconds = 10_000
        w._last_poll = watcher.time.time()

    def test_writes_heartbeat_on_first_call(self, monkeypatch, tmp_path):
        w = watcher.Watcher(_FakeConfig(tmp_path))
        self._wired_for_isolation(w, monkeypatch)

        assert watcher._heartbeat_age_seconds() is None
        w._loop_iteration()
        assert watcher._heartbeat_age_seconds() is not None

    def test_does_not_rewrite_heartbeat_before_the_interval(self, monkeypatch, tmp_path):
        w = watcher.Watcher(_FakeConfig(tmp_path))
        self._wired_for_isolation(w, monkeypatch)

        w._last_heartbeat = watcher.time.time()  # "just wrote one"
        calls = []
        monkeypatch.setattr(watcher, "_write_heartbeat", lambda: calls.append(1))

        w._loop_iteration()

        assert calls == []


class TestEnsureWatchdogTaskExists:

    def test_does_nothing_in_a_dev_checkout(self, monkeypatch):
        import poslib.paths as paths_module
        monkeypatch.setattr(paths_module, "is_frozen", lambda: False)
        calls = []
        monkeypatch.setattr(watcher.subprocess, "run",
                             lambda *a, **k: calls.append(a) or watcher.subprocess.CompletedProcess(a, 0))

        watcher._ensure_watchdog_task_exists()

        assert calls == []

    def test_creates_the_task_in_a_frozen_build(self, monkeypatch, tmp_path):
        import poslib.paths as paths_module
        monkeypatch.setattr(paths_module, "is_frozen", lambda: True)
        monkeypatch.setattr(paths_module, "app_root", lambda: tmp_path)
        run_calls = []

        def fake_run(cmd, **k):
            run_calls.append(cmd)
            return watcher.subprocess.CompletedProcess(cmd, 0, stdout="SUCCESS", stderr="")
        monkeypatch.setattr(watcher.subprocess, "run", fake_run)

        watcher._ensure_watchdog_task_exists()

        assert len(run_calls) == 1
        cmd = run_calls[0]
        assert cmd[:3] == ["schtasks", "/create", "/f"]
        assert "Shop Analysis - Watchdog" in cmd
        assert "--ensure-watcher-running" in " ".join(cmd)
        assert "/sc" in cmd and "minute" in cmd
        assert "/mo" in cmd and "10" in cmd

    def test_never_raises_when_creation_fails(self, monkeypatch, tmp_path):
        import poslib.paths as paths_module
        monkeypatch.setattr(paths_module, "is_frozen", lambda: True)
        monkeypatch.setattr(paths_module, "app_root", lambda: tmp_path)

        def _raise(*a, **k):
            raise OSError("schtasks.exe not found")
        monkeypatch.setattr(watcher.subprocess, "run", _raise)

        watcher._ensure_watchdog_task_exists()  # must not raise
