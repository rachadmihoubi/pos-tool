"""
watcher.py - notices when the POS saves a sale and refreshes the tool.

It watches the folder the database lives in. When the file changes it does
NOT read it straight away: the POS may be half way through writing, and
reading a half-written file gives nonsense. Instead it waits until the file
has stopped changing for a few seconds, then rebuilds the cache.

It also runs the daily digest at the hour set in config.yaml.

Everything here is careful never to crash: if a rebuild fails the watcher
logs it, waits, and tries again. It has to survive being left running for
months.
"""

from __future__ import annotations

import datetime
import logging
import subprocess
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from poslib.config import Config, get_config, setup_logging
from poslib.etl import ETL, ETLError
from poslib.paths import user_data_dir

log = logging.getLogger(__name__)

# -- self-healing: a heartbeat file plus a separate recurring "watchdog"
# scheduled task (packaging/setup.iss) that restarts the watcher if it goes
# stale. Added 2026-09-14 after CLAUDE.md's "Bug #2" (a real incident,
# 2026-09-05): the watcher died from an unhandled exception and stayed dead
# for ~17 hours until the next Windows logon, because its scheduled task's
# only trigger is onlogon (one-shot, not recurring) and nothing else was
# watching for it to come back. See ensure_watcher_running's own docstring
# for how the restart decision is made.
_HEARTBEAT_FILE_NAME = "watcher_heartbeat.txt"
# Comfortably above the default poll_seconds (120s) and min_gap (60s), with
# margin for a slow rebuild on a big database - not a measurement of a
# healthy cycle's real cost, just a bound loose enough that a genuinely
# healthy watcher never trips it.
_HEARTBEAT_STALE_SECONDS = 15 * 60
_WATCHER_TASK_NAME = "Shop Analysis - Watcher"


def _heartbeat_path() -> Path:
    return user_data_dir() / _HEARTBEAT_FILE_NAME


def _write_heartbeat() -> None:
    """Records that the watcher's main loop is alive and looping. Never raises."""
    try:
        _heartbeat_path().write_text(
            datetime.datetime.now(datetime.timezone.utc).isoformat(), encoding="utf-8")
    except OSError:
        log.debug("Could not write the watcher heartbeat file", exc_info=True)


def _heartbeat_age_seconds() -> float | None:
    """Seconds since the last heartbeat, or None if it's missing/unreadable."""
    try:
        text = _heartbeat_path().read_text(encoding="utf-8").strip()
        written = datetime.datetime.fromisoformat(text)
        if written.tzinfo is None:
            written = written.replace(tzinfo=datetime.timezone.utc)
        return (datetime.datetime.now(datetime.timezone.utc) - written).total_seconds()
    except (OSError, ValueError):
        return None


def _watcher_task_is_running() -> bool:
    """
    Asks Task Scheduler itself whether "Shop Analysis - Watcher" is
    currently executing - not process enumeration, so this can't be
    confused by a separately-opened dashboard instance of the same exe
    (this machine has a documented history of stray duplicate processes -
    see CLAUDE.md). Fails safe: any error here returns True ("assume it
    might be running"), so a query hiccup can never trigger a duplicate
    restart - a duplicate watcher is worse than one missed restart
    attempt, which the next watchdog cycle retries anyway.
    """
    try:
        result = subprocess.run(
            ["schtasks", "/query", "/tn", _WATCHER_TASK_NAME, "/fo", "list", "/v"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return True
        for line in result.stdout.splitlines():
            if line.strip().lower().startswith("status:"):
                return "running" in line.lower()
        return True
    except (OSError, subprocess.SubprocessError):
        return True


def ensure_watcher_running() -> None:
    """
    Run periodically by the "Shop Analysis - Watchdog" scheduled task
    (packaging/setup.iss), never by the watcher itself - see
    main.py's --ensure-watcher-running dispatch. Restarts the watcher via
    its own scheduled task if its heartbeat has gone stale and Task
    Scheduler confirms it isn't already running. Never raises - a failure
    here just means no restart happens this cycle, the same fail-safe
    contract as every other watcher-adjacent function in this codebase.
    """
    age = _heartbeat_age_seconds()
    if age is not None and age < _HEARTBEAT_STALE_SECONDS:
        log.debug("Watcher heartbeat is %.0fs old - healthy.", age)
        return

    if _watcher_task_is_running():
        log.debug("Watcher heartbeat is %s but the task is still running - "
                  "leaving it alone.",
                  "missing" if age is None else f"{age:.0f}s old")
        return

    log.warning("Watcher heartbeat is %s - restarting it.",
                "missing" if age is None else f"{age:.0f}s old")
    try:
        result = subprocess.run(
            ["schtasks", "/run", "/tn", _WATCHER_TASK_NAME],
            capture_output=True, text=True, timeout=30,
        )
        log.info("Restarted the watcher task (exit %d): %s", result.returncode,
                 (result.stdout or result.stderr or "").strip() or "(no output)")
    except (OSError, subprocess.SubprocessError) as exc:
        log.error("Could not restart the watcher task: %s", exc)


class DatabaseChanged(FileSystemEventHandler):
    """Notes that the database file was touched. Does not read it yet."""

    def __init__(self, target: Path, on_change) -> None:
        self.target_name = target.name.lower()
        self.on_change = on_change

    def _matches(self, path: str) -> bool:
        name = Path(path).name.lower()
        if name == self.target_name:
            return True
        # Access writes a lock file beside the database while saving. Seeing
        # it appear or disappear is a good sign something happened.
        return name.startswith(self.target_name.rsplit(".", 1)[0]) and \
            name.endswith((".ldb", ".laccdb"))

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if self._matches(event.src_path) or \
                (getattr(event, "dest_path", None) and self._matches(event.dest_path)):
            self.on_change()


class Watcher:
    """Keeps the cache in step with the POS database."""

    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or get_config()
        self.etl = ETL(self.cfg)
        self.source = self.cfg.source_db

        self.quiet_seconds = float(self.cfg.get("watcher.quiet_seconds", 5))
        self.min_gap = float(self.cfg.get("watcher.min_seconds_between_rebuilds", 60))
        self.poll_seconds = float(self.cfg.get("watcher.poll_seconds", 120))

        self._dirty = threading.Event()
        self._stop = threading.Event()
        self._last_rebuild = 0.0
        self._last_digest_date: datetime.date | None = None
        self._last_backup_date: datetime.date | None = None
        self._last_remote_push = 0.0
        self._last_poll = 0.0
        self._last_heartbeat = 0.0

    # -- being told something happened -------------------------------------

    def mark_dirty(self) -> None:
        self._dirty.set()

    # -- waiting for the POS to finish writing -----------------------------

    def _wait_until_settled(self) -> bool:
        """
        Wait until the file has stopped growing.

        Returns True when it is safe to read. Gives up after a while so a
        POS that writes continuously cannot block the tool forever.
        """
        deadline = time.time() + 120
        last: tuple[int, float] | None = None
        stable_since: float | None = None

        while time.time() < deadline and not self._stop.is_set():
            try:
                stat = self.source.stat()
                now = (stat.st_size, round(stat.st_mtime, 2))
            except OSError:
                # The file is momentarily locked or being replaced.
                time.sleep(1.0)
                last, stable_since = None, None
                continue

            if now == last:
                if stable_since is None:
                    stable_since = time.time()
                elif time.time() - stable_since >= self.quiet_seconds:
                    return True
            else:
                last, stable_since = now, None

            time.sleep(1.0)

        log.warning("The database kept changing for two minutes; reading it anyway.")
        return True

    # -- the work ----------------------------------------------------------

    def rebuild(self, force: bool = False) -> None:
        since = time.time() - self._last_rebuild
        if not force and since < self.min_gap:
            log.debug("Only %.0fs since the last read; leaving it.", since)
            return

        if not self._wait_until_settled():
            return

        try:
            result = self.etl.refresh(force=force)
        except ETLError as exc:
            log.error("Could not read the database: %s", exc)
            return
        except Exception:                                # noqa: BLE001
            log.exception("Unexpected problem while reading the database")
            return

        self._last_rebuild = time.time()
        if result.rebuilt:
            log.info("Read %s rows in %.1fs. %s",
                     f"{result.total_rows:,}", result.duration_seconds, result.reason)
            for warning in result.warnings:
                log.warning("  %s", warning)
        else:
            log.debug("Nothing new. %s", result.reason)

        # No point deploying a new snapshot when nothing in the cache
        # actually changed - that would burn through Cloudflare's quota for
        # a no-op.
        if result.rebuilt and self._remote_push_due():
            self._run_remote_push()

    # -- the daily digest --------------------------------------------------

    def _digest_due(self) -> bool:
        hour = int(self.cfg.get("digest.hour", 20))
        minute = int(self.cfg.get("digest.minute", 0))
        now = datetime.datetime.now()

        if self._last_digest_date == now.date():
            return False
        return (now.hour, now.minute) >= (hour, minute)

    def _run_digest(self) -> None:
        today = datetime.date.today()
        self._last_digest_date = today
        log.info("Producing the daily digest.")
        try:
            from poslib.digest import send_digest
            results = send_digest()
            for r in results:
                if r.ok:
                    log.info("  digest -> %s: sent%s", r.channel,
                             f" ({r.detail})" if r.detail else "")
                else:
                    log.warning("  digest -> %s: %s", r.channel, r.error)
        except Exception:                                # noqa: BLE001
            # A failed digest must never stop the watcher.
            log.exception("The daily digest failed")

    # -- the daily backup ----------------------------------------------------

    def _backup_due(self) -> bool:
        if not self.cfg.get("backup.enabled", True):
            return False
        hour = int(self.cfg.get("backup.hour", 3))
        minute = int(self.cfg.get("backup.minute", 0))
        now = datetime.datetime.now()

        if self._last_backup_date == now.date():
            return False
        return (now.hour, now.minute) >= (hour, minute)

    def _run_backup(self) -> None:
        today = datetime.date.today()
        self._last_backup_date = today
        log.info("Running the daily backup.")
        try:
            from poslib.backup import run_backup
            result = run_backup(self.cfg)
            if result.ran:
                log.info("  backup -> %s (%s)", result.folder, result.reason)
                if result.removed_folders:
                    log.info("  removed old backups: %s", ", ".join(result.removed_folders))
            else:
                log.debug("  backup -> %s", result.reason)
        except Exception:                                # noqa: BLE001
            # A failed backup must never stop the watcher.
            log.exception("The daily backup failed")

    # -- remote viewing -------------------------------------------------------

    def _remote_push_due(self) -> bool:
        if not bool(self.cfg.get("remote.enabled", False)):
            return False
        interval = float(self.cfg.get("remote.push_interval_seconds", 90))
        return (time.time() - self._last_remote_push) >= interval

    def _run_remote_push(self) -> None:
        # Set first, like the digest/backup markers - a slow or failing
        # push must not be retried in a tight loop before its own interval
        # is up again.
        self._last_remote_push = time.time()
        try:
            from export_static import export
            from poslib.remote import mark_push_succeeded, push_remote
            export(self.cfg)
            if push_remote(self.cfg):
                mark_push_succeeded()
            else:
                log.debug("  remote push did not succeed this cycle - will retry next time.")
        except Exception:                                # noqa: BLE001
            # A failed export/push must never stop the watcher - the real
            # database and cache stay local either way.
            log.exception("The remote export/push failed")

    # -- the loop ----------------------------------------------------------

    # 30s: frequent enough that a stuck-not-crashed loop (one iteration
    # taking a very long time) is caught almost as fast as a genuinely dead
    # process, cheap enough (a few bytes) not to matter on a till PC's disk.
    _HEARTBEAT_WRITE_INTERVAL_SECONDS = 30.0

    def _loop_iteration(self) -> None:
        """One pass of the main loop's real work - see _safe_loop_iteration
        for the try/except that wraps this, and run() for the loop itself."""
        if time.time() - self._last_heartbeat >= self._HEARTBEAT_WRITE_INTERVAL_SECONDS:
            _write_heartbeat()
            self._last_heartbeat = time.time()

        if self._dirty.wait(timeout=2.0):
            self._dirty.clear()
            # Let a burst of writes finish before reacting to them.
            time.sleep(1.0)
            self._dirty.clear()
            self.rebuild()
            self._last_poll = time.time()

        # Safety net, in case a change was somehow missed.
        if time.time() - self._last_poll >= self.poll_seconds:
            self._last_poll = time.time()
            self.rebuild()

        if self._digest_due():
            self._run_digest()

        if self._backup_due():
            self._run_backup()

    def _safe_loop_iteration(self) -> None:
        """
        Hardened 2026-09-14 (CLAUDE.md's "Bug #2"): the loop used to call
        _loop_iteration's body directly with no try/except at all, so any
        single unexpected exception anywhere in it - not just inside
        rebuild()/_run_digest()/_run_backup(), which already guard their
        own real work - killed the entire process silently (console=False,
        no crash dialog). A store PC that stays logged in for days had no
        way back short of a fresh logon. This can now only ever log and
        keep going. Never raises.
        """
        try:
            self._loop_iteration()
        except Exception:                                # noqa: BLE001
            log.exception("Unexpected error in the watcher's main loop - continuing")
            # A brief pause so a persistently-broken condition (e.g.
            # _digest_due() itself raising every call) can't spin the loop
            # hot instead of just logging once per pass.
            time.sleep(1.0)

    def run(self) -> None:
        folder = self.source.parent
        if not folder.is_dir():
            log.error("The folder to watch does not exist: %s", folder)
            log.error("Check 'database: path:' in config.yaml.")
            return

        log.info("Watching %s", self.source)
        log.info("Digest at %02d:%02d each day.",
                 int(self.cfg.get("digest.hour", 20)),
                 int(self.cfg.get("digest.minute", 0)))

        handler = DatabaseChanged(self.source, self.mark_dirty)
        observer = Observer()
        observer.schedule(handler, str(folder), recursive=False)
        observer.start()

        # Read once at startup so the dashboard is never empty.
        self.rebuild(force=False)
        _write_heartbeat()
        self._last_poll = time.time()
        self._last_heartbeat = time.time()

        try:
            while not self._stop.is_set():
                self._safe_loop_iteration()
        except KeyboardInterrupt:
            log.info("Stopping.")
        finally:
            observer.stop()
            observer.join(timeout=5)

    def stop(self) -> None:
        self._stop.set()


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Watch the POS database and keep the dashboard up to date.")
    parser.add_argument("--once", action="store_true",
                        help="Read the database once and stop.")
    parser.add_argument("--digest-now", action="store_true",
                        help="Produce and send the daily digest immediately.")
    parser.add_argument("--backup-now", action="store_true",
                        help="Run the daily backup immediately.")
    args = parser.parse_args()

    from poslib.config import ConfigError
    try:
        cfg = get_config()
    except ConfigError as exc:
        print(f"\nThere is a problem with config.yaml:\n\n{exc}\n")
        return 1

    setup_logging(cfg)
    watcher = Watcher(cfg)

    if args.digest_now:
        watcher._run_digest()
        return 0

    if args.backup_now:
        watcher._run_backup()
        return 0

    if args.once:
        watcher.rebuild(force=True)
        return 0

    watcher.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
