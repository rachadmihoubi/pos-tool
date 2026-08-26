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
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from poslib.config import Config, get_config, setup_logging
from poslib.etl import ETL, ETLError

log = logging.getLogger(__name__)


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

    # -- being told something happened -------------------------------------

    def mark_dirty(self) -> None:
        self._dirty.set()

    # -- silent auto-update --------------------------------------------------

    def _check_for_update(self) -> bool:
        """
        Returns True if an update was found and its silent install was
        launched - the caller must stop and exit immediately so the
        installer can replace the running files. A failure here must
        never crash the watcher, same as the digest/backup/remote jobs.
        """
        try:
            from poslib.updater import check_and_apply_update
            return check_and_apply_update(self.cfg)
        except Exception:                                # noqa: BLE001
            log.exception("The update check failed")
            return False

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
            from poslib.remote import push_remote
            export(self.cfg)
            if not push_remote(self.cfg):
                log.debug("  remote push did not succeed this cycle - will retry next time.")
        except Exception:                                # noqa: BLE001
            # A failed export/push must never stop the watcher - the real
            # database and cache stay local either way.
            log.exception("The remote export/push failed")

    # -- the loop ----------------------------------------------------------

    def run(self) -> None:
        if self._check_for_update():
            log.info("Stopping for the update to install.")
            return

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

        last_poll = time.time()
        try:
            while not self._stop.is_set():
                if self._dirty.wait(timeout=2.0):
                    self._dirty.clear()
                    # Let a burst of writes finish before reacting to them.
                    time.sleep(1.0)
                    self._dirty.clear()
                    self.rebuild()
                    last_poll = time.time()

                # Safety net, in case a change was somehow missed.
                if time.time() - last_poll >= self.poll_seconds:
                    last_poll = time.time()
                    self.rebuild()

                if self._digest_due():
                    self._run_digest()

                if self._backup_due():
                    self._run_backup()

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
