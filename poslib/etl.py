"""
etl.py - copies the POS database somewhere safe, reads it, and stores the
contents in a fast local file the rest of the tool can query.

WHY A SECOND COPY OF THE DATA?
------------------------------
Reading the Access file takes a few seconds. Doing that every time a screen
loads would make the dashboard slow. So we read it once, write everything
into a SQLite file called cache.db, and every screen reads from that. When
the POS saves a new sale, the watcher notices and we rebuild the cache.

THE SAFETY RULE
---------------
The POS database is opened READ-ONLY and is copied before it is read. The
copy is what gets parsed. There is no code path in this tool that opens the
source file for writing. Your live data cannot be changed or corrupted by
this tool, even if it crashes half way through.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.wintypes as wintypes
import datetime
import hashlib
import json
import logging
import msvcrt
import os
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Config, get_config
from .jet4 import (Jet4Error, Jet4Reader, Table, TYPE_BINARY, TYPE_BOOL,
                   TYPE_BYTE, TYPE_DATETIME, TYPE_FLOAT32, TYPE_FLOAT64,
                   TYPE_GUID, TYPE_INT16, TYPE_INT32, TYPE_MEMO, TYPE_MONEY,
                   TYPE_NUMERIC, TYPE_OLE, TYPE_TEXT)

log = logging.getLogger(__name__)

# Picture and attachment columns can be megabytes each and are no use for
# any report, so we leave them out of the cache to keep it small and fast.
SKIPPED_COLUMN_TYPES = {TYPE_OLE, TYPE_BINARY}

# How long ETL.refresh() waits for another process's cache rebuild to
# finish before giving up (see ETL._locked).
_LOCK_POLL_SECONDS = 0.5
_LOCK_TIMEOUT_SECONDS = 600

# How an Access column type is stored in SQLite.
SQLITE_TYPE = {
    TYPE_BOOL: "INTEGER",
    TYPE_BYTE: "INTEGER",
    TYPE_INT16: "INTEGER",
    TYPE_INT32: "INTEGER",
    TYPE_MONEY: "REAL",
    TYPE_FLOAT32: "REAL",
    TYPE_FLOAT64: "REAL",
    TYPE_DATETIME: "TEXT",     # kept as "YYYY-MM-DD HH:MM:SS" so it sorts
    TYPE_TEXT: "TEXT",
    TYPE_MEMO: "TEXT",
    TYPE_GUID: "TEXT",
    TYPE_NUMERIC: "REAL",
}

# Indexes worth having. These are the joins and filters every report does.
INDEX_PLAN: dict[str, list[list[str]]] = {
    "Receipt": [["CustomerID"], ["Time"], ["ReceiptType"], ["ReceiptNo"]],
    "ReceiptEntry": [["ReceiptID"], ["ItemID"], ["ReceiptID", "ItemID"]],
    "Item": [["ItemFamilyID"], ["LastSold"], ["ItemNo"]],
    "Customer": [["CustomerNo"], ["LastVisit"]],
    "PurchaseEntry": [["PurchaseID"], ["ItemID"]],
    "SupplierItem": [["SupplierID"], ["ItemID"]],
    "PricingUpdateEntry": [["ItemID"], ["PricingUpdateID"]],
    "ItemAdjustment": [["ItemID"]],
    "Batch": [["RegisterID"]],
}


class ETLError(Exception):
    """The database could not be copied or read."""


@dataclass
class RefreshResult:
    """What happened during a refresh, in a form the dashboard can show."""
    rebuilt: bool = False
    reason: str = ""
    duration_seconds: float = 0.0
    table_rows: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    source_path: str = ""
    source_size: int = 0
    source_modified: str = ""
    finished_at: str = ""

    @property
    def total_rows(self) -> int:
        return sum(self.table_rows.values())


# ---------------------------------------------------------------------------
# Copying the live file safely
# ---------------------------------------------------------------------------

def _copy_with_full_sharing(src: Path, dst: Path) -> None:
    """
    Copy a file that another program currently has open.

    The POS software keeps the database open while it runs, and on Windows
    that can stop an ordinary copy from working. This asks Windows directly
    for a read handle that tolerates the other program continuing to read,
    write and even delete the file.

    We ask for GENERIC_READ only. This handle cannot write to the source.
    """
    GENERIC_READ = 0x80000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    FILE_SHARE_DELETE = 0x00000004
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    CreateFileW = ctypes.windll.kernel32.CreateFileW
    CreateFileW.restype = wintypes.HANDLE
    CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                            wintypes.HANDLE]

    handle = CreateFileW(str(src), GENERIC_READ,
                         FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                         None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)

    if handle == INVALID_HANDLE_VALUE or handle is None:
        raise ETLError(
            f"Windows would not let us read {src}. "
            f"Error code {ctypes.get_last_error()}."
        )

    try:
        fd = ctypes.windll.msvcrt._open_osfhandle(ctypes.c_void_p(handle).value, 0x0000)
        if fd == -1:
            raise ETLError(f"Could not turn the Windows handle for {src} into a file.")
        with os.fdopen(fd, "rb", closefd=True) as fh, open(dst, "wb") as out:
            shutil.copyfileobj(fh, out, length=1024 * 1024)
    except ETLError:
        ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(handle))
        raise


def copy_database_readonly(src: Path, dst_dir: Path | None = None) -> Path:
    """
    Make a private copy of the POS database and return where it went.

    Tries the ordinary copy first because it is fastest, and falls back to
    the Windows sharing-aware copy if the POS has the file locked.
    """
    if not src.is_file():
        raise ETLError(
            f"Cannot find the POS database at:\n    {src}\n\n"
            "Check the 'database: path:' setting in config.yaml. If the POS "
            "software was moved or reinstalled, the file may be somewhere else."
        )

    dst_dir = dst_dir or Path(tempfile.gettempdir()) / "pos-tool-work"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / "working-copy.dblx"

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            shutil.copy2(src, dst)
            return dst
        except (PermissionError, OSError) as exc:
            last_error = exc
            log.debug("Plain copy failed (attempt %d): %s", attempt + 1, exc)

        if os.name == "nt":
            try:
                _copy_with_full_sharing(src, dst)
                return dst
            except Exception as exc:          # noqa: BLE001 - report anything
                last_error = exc
                log.debug("Sharing-aware copy failed (attempt %d): %s", attempt + 1, exc)

        time.sleep(1.0)

    raise ETLError(
        f"Could not copy the POS database after 3 tries.\n"
        f"    Source: {src}\n"
        f"    Last error: {last_error}\n\n"
        "This usually means the POS software is in the middle of saving. "
        "The tool will try again automatically."
    )


def file_fingerprint(path: Path) -> dict[str, Any]:
    """
    A cheap way to tell whether the database has changed since last time.

    We record size, last-modified time and a checksum of the contents. If all
    three match what we stored, there is nothing new to read and we skip the
    whole rebuild.
    """
    stat = path.stat()
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": str(path),
        "size": stat.st_size,
        "mtime": round(stat.st_mtime, 3),
        "sha256": digest.hexdigest(),
    }


# ---------------------------------------------------------------------------
# The ETL itself
# ---------------------------------------------------------------------------

class ETL:
    """Reads the POS database into cache.db."""

    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or get_config()
        self.source = self.cfg.source_db
        self.cache = self.cfg.cache_db

    # -- cross-process locking -----------------------------------------------

    @contextlib.contextmanager
    def _locked(self):
        """
        Cross-process advisory lock held for the whole refresh() call, not
        just the rebuild. Without this, two ETL instances started around the
        same time - e.g. the auto-started watcher and an interactively
        launched dashboard, both hitting the same real database right after
        install - can race on the same cache.building temp file: one holds
        it open via sqlite3.connect while the other's `if tmp_cache.exists():
        tmp_cache.unlink()` hits a PermissionError ([WinError 32], file in
        use), crashing the second process outright. Reproduced live
        2026-08-29 on a real till PC.

        Held for the *entire* refresh(), not just _build_cache(), so a
        second caller that was blocked waiting for the lock re-checks
        is_stale() (right after it acquires the lock) and sees the first
        caller's already-finished rebuild - it just returns instead of
        rebuilding a second time.

        msvcrt (stdlib, Windows-only) matches the rest of this module's own
        Windows-only API use (ctypes.wintypes above). Byte-range locks are
        held per open file handle by the OS itself, so even if a process
        crashes mid-rebuild without reaching the `finally` below, Windows
        releases the lock automatically when the handle closes - this can
        never leave a stale lock that blocks every future run forever.
        """
        lock_path = self.cache.with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        f = open(lock_path, "a+b")
        try:
            # msvcrt.locking needs at least one byte to lock; a freshly
            # created lock file is empty.
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                f.write(b"0")
                f.flush()
            f.seek(0)

            deadline = time.time() + _LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.time() >= deadline:
                        raise ETLError(
                            "Another process has been rebuilding the cache "
                            "for over 10 minutes - giving up rather than "
                            "waiting forever. If nothing is actually "
                            f"running, delete {lock_path} and try again."
                        )
                    time.sleep(_LOCK_POLL_SECONDS)
            try:
                yield
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            f.close()

    # -- public ------------------------------------------------------------

    def is_stale(self) -> tuple[bool, str]:
        """
        Does the cache need rebuilding? Returns (yes/no, why).

        Checking size and modified-time first is instant; we only bother with
        the checksum when one of those has moved.
        """
        if not self.cache.is_file():
            return True, "No cache yet - this is the first run."

        stored = self._read_meta()
        if not stored:
            return True, "The cache has no record of which database it came from."

        if stored.get("tool_version") != _tool_version():
            return True, "The tool was updated, so the cache is rebuilt once."

        if str(stored.get("path")) != str(self.source):
            return True, "The database path in config.yaml has changed."

        if not self.source.is_file():
            return False, "The database is not reachable; keeping the existing cache."

        stat = self.source.stat()
        if stat.st_size != stored.get("size"):
            return True, "The database has changed size - there is new data."
        if abs(round(stat.st_mtime, 3) - float(stored.get("mtime", 0))) > 0.001:
            return True, "The database was saved again - checking for new data."

        return False, "Nothing has changed since the last read."

    def refresh(self, force: bool = False) -> RefreshResult:
        """Rebuild the cache if the database has changed (or if forced)."""
        with self._locked():
            return self._refresh_locked(force)

    def _refresh_locked(self, force: bool) -> RefreshResult:
        started = time.time()

        stale, reason = self.is_stale()
        if not stale and not force:
            res = RefreshResult(rebuilt=False, reason=reason)
            meta = self._read_meta() or {}
            res.source_path = str(self.source)
            res.source_size = int(meta.get("size", 0))
            res.source_modified = str(meta.get("source_modified", ""))
            res.table_rows = json.loads(meta.get("row_counts", "{}") or "{}")
            res.finished_at = str(meta.get("parsed_at", ""))
            res.duration_seconds = time.time() - started
            return res

        if force and not stale:
            reason = "Rebuild was requested by hand."

        log.info("Rebuilding the cache. Reason: %s", reason)

        work_copy = copy_database_readonly(self.source)
        fingerprint = file_fingerprint(work_copy)

        # Size or mtime looked different, but the bytes might not actually
        # be - Access can rewrite a file with the same content, or a backup
        # tool can touch its timestamp. Skip the (expensive) parse in that
        # case. Guarded on tool_version matching too, so a tool update still
        # gets its one guaranteed re-read even if nothing else changed.
        stored = self._read_meta() or {}
        if (stored.get("tool_version") == _tool_version()
                and stored.get("sha256") == fingerprint["sha256"]):
            res = RefreshResult(rebuilt=False,
                                reason="The database's size or timestamp changed, but "
                                       "the contents are identical - nothing to re-read.")
            res.source_path = str(self.source)
            res.source_size = fingerprint["size"]
            res.source_modified = datetime.datetime.fromtimestamp(
                self.source.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            res.table_rows = json.loads(stored.get("row_counts", "{}") or "{}")
            res.finished_at = str(stored.get("parsed_at", ""))
            res.duration_seconds = time.time() - started
            return res

        result = RefreshResult(rebuilt=True, reason=reason)
        result.source_path = str(self.source)
        result.source_size = fingerprint["size"]
        result.source_modified = datetime.datetime.fromtimestamp(
            self.source.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")

        try:
            with Jet4Reader(str(work_copy)) as db:
                self._build_cache(db, fingerprint, result)
        except Jet4Error as exc:
            raise ETLError(
                f"The POS database could not be read:\n    {exc}\n\n"
                "If the POS software was in the middle of saving, this usually "
                "fixes itself on the next attempt."
            ) from exc

        result.duration_seconds = time.time() - started
        result.finished_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log.info("Cache rebuilt in %.1fs - %d rows across %d tables.",
                 result.duration_seconds, result.total_rows, len(result.table_rows))
        return result

    def connect(self) -> sqlite3.Connection:
        """Open the cache for reading. Read-only; queries cannot change it."""
        if not self.cache.is_file():
            raise ETLError(
                "There is no cache yet. Start the tool (start.bat) and let it "
                "read your database once."
            )
        uri = f"file:{self.cache.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    # -- building ----------------------------------------------------------

    def _build_cache(self, db: Jet4Reader, fingerprint: dict[str, Any],
                     result: RefreshResult) -> None:
        """
        Write every table into a brand new cache file, then swap it into
        place. Building to one side means a half-finished rebuild can never
        leave the dashboard with a broken cache.
        """
        tmp_cache = self.cache.with_suffix(".building")
        if tmp_cache.exists():
            tmp_cache.unlink()

        self.cache.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(tmp_cache)
        try:
            conn.execute("PRAGMA journal_mode=OFF")
            conn.execute("PRAGMA synchronous=OFF")

            for name in sorted(db.tables):
                table = db.tables[name]
                try:
                    count = self._load_table(conn, db, table, result)
                except Exception as exc:                 # noqa: BLE001
                    # One unreadable table must not stop the other 48.
                    msg = f"Table '{name}' could not be read: {exc}"
                    log.warning(msg)
                    result.warnings.append(msg)
                    continue
                if count:
                    result.table_rows[name] = count

            self._write_meta(conn, fingerprint, result)
            conn.commit()
            conn.execute("VACUUM")
            conn.commit()
        finally:
            conn.close()

        # Swap the finished file into place.
        if self.cache.exists():
            self.cache.unlink()
        tmp_cache.rename(self.cache)

    def _load_table(self, conn: sqlite3.Connection, db: Jet4Reader,
                    table: Table, result: RefreshResult) -> int:
        """Create one SQLite table and fill it."""
        columns = [c for c in table.columns
                   if c.name and c.col_type not in SKIPPED_COLUMN_TYPES]

        # Two columns with the same name would break the CREATE TABLE.
        seen: set[str] = set()
        unique: list[Any] = []
        for col in columns:
            base = col.name
            nm = base
            n = 2
            while nm.lower() in seen:
                nm = f"{base}_{n}"
                n += 1
            seen.add(nm.lower())
            unique.append((nm, col))

        if not unique:
            return 0

        col_sql = ", ".join(
            f'"{nm}" {SQLITE_TYPE.get(col.col_type, "TEXT")}' for nm, col in unique
        )
        conn.execute(f'DROP TABLE IF EXISTS "{table.name}"')
        conn.execute(f'CREATE TABLE "{table.name}" ({col_sql})')

        placeholders = ", ".join("?" for _ in unique)
        insert = (f'INSERT INTO "{table.name}" '
                  f'({", ".join(chr(34) + nm + chr(34) for nm, _ in unique)}) '
                  f"VALUES ({placeholders})")

        batch: list[tuple[Any, ...]] = []
        count = 0
        bad_values = 0

        for row in db.rows(table.name):
            values: list[Any] = []
            for _, col in unique:
                v = row.get(col.name)
                try:
                    values.append(_to_sqlite(v, col.col_type))
                except Exception:                        # noqa: BLE001
                    values.append(None)
                    bad_values += 1
            batch.append(tuple(values))
            count += 1

            if len(batch) >= 2000:
                conn.executemany(insert, batch)
                batch.clear()

        if batch:
            conn.executemany(insert, batch)

        if bad_values:
            result.warnings.append(
                f"Table '{table.name}': {bad_values} values could not be converted "
                "and were stored as empty."
            )

        # Access records its own idea of how many rows a table has. If that
        # disagrees badly with what we actually read, say so rather than
        # quietly reporting a number that might be short.
        declared = table.declared_rows
        if declared and count and abs(declared - count) > max(5, declared * 0.02):
            result.warnings.append(
                f"Table '{table.name}': the database header says {declared:,} rows "
                f"but {count:,} were readable."
            )

        for index_cols in INDEX_PLAN.get(table.name, []):
            have = {nm.lower() for nm, _ in unique}
            if not all(c.lower() in have for c in index_cols):
                continue
            idx_name = f"ix_{table.name}_{'_'.join(index_cols)}".lower()
            cols = ", ".join(f'"{c}"' for c in index_cols)
            conn.execute(f'CREATE INDEX IF NOT EXISTS "{idx_name}" ON "{table.name}" ({cols})')

        return count

    # -- bookkeeping -------------------------------------------------------

    def _write_meta(self, conn: sqlite3.Connection, fingerprint: dict[str, Any],
                    result: RefreshResult) -> None:
        conn.execute("DROP TABLE IF EXISTS _source_meta")
        conn.execute("""
            CREATE TABLE _source_meta (
                id              INTEGER PRIMARY KEY CHECK (id = 1),
                path            TEXT,
                size            INTEGER,
                mtime           REAL,
                sha256          TEXT,
                parsed_at       TEXT,
                source_modified TEXT,
                tool_version    TEXT,
                row_counts      TEXT,
                warnings        TEXT,
                duration        REAL
            )
        """)
        conn.execute(
            "INSERT INTO _source_meta "
            "(id, path, size, mtime, sha256, parsed_at, source_modified, "
            " tool_version, row_counts, warnings, duration) "
            "VALUES (1,?,?,?,?,?,?,?,?,?,?)",
            (
                str(self.source),
                fingerprint["size"],
                fingerprint["mtime"],
                fingerprint["sha256"],
                datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                result.source_modified,
                _tool_version(),
                json.dumps(result.table_rows),
                json.dumps(result.warnings),
                round(result.duration_seconds, 2),
            ),
        )

    def _read_meta(self) -> dict[str, Any] | None:
        if not self.cache.is_file():
            return None
        try:
            conn = self.connect()
        except (ETLError, sqlite3.Error):
            return None
        try:
            row = conn.execute("SELECT * FROM _source_meta WHERE id = 1").fetchone()
            return dict(row) if row else None
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    def cache_info(self) -> dict[str, Any]:
        """A summary of the cache for the dashboard's footer and data-quality page."""
        meta = self._read_meta() or {}
        return {
            "source_path": meta.get("path", str(self.source)),
            "source_modified": meta.get("source_modified", ""),
            "parsed_at": meta.get("parsed_at", ""),
            "row_counts": json.loads(meta.get("row_counts", "{}") or "{}"),
            "warnings": json.loads(meta.get("warnings", "[]") or "[]"),
            "duration": meta.get("duration", 0.0),
            "size": meta.get("size", 0),
            "tool_version": meta.get("tool_version", ""),
        }


# ---------------------------------------------------------------------------
# Value conversion
# ---------------------------------------------------------------------------

def _to_sqlite(value: Any, col_type: int) -> Any:
    """Turn one parsed Access value into something SQLite can store."""
    if value is None:
        return None

    if col_type == TYPE_BOOL:
        return 1 if value else 0

    if col_type == TYPE_DATETIME:
        if isinstance(value, datetime.datetime):
            # Text in this exact shape sorts and compares correctly in SQLite.
            return value.strftime("%Y-%m-%d %H:%M:%S")
        return None

    if col_type in (TYPE_MONEY, TYPE_FLOAT32, TYPE_FLOAT64, TYPE_NUMERIC):
        try:
            f = float(value)
        except (TypeError, ValueError):
            return None
        # SQLite cannot store infinity or "not a number".
        return f if f == f and abs(f) != float("inf") else None

    if col_type in (TYPE_BYTE, TYPE_INT16, TYPE_INT32):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    return value


def _tool_version() -> str:
    from . import __version__
    return __version__


# ---------------------------------------------------------------------------
# Command-line use: python -m poslib.etl
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Read the POS database into the local cache.")
    parser.add_argument("--force", action="store_true",
                        help="Rebuild even if nothing has changed.")
    args = parser.parse_args()

    from .config import setup_logging
    cfg = get_config()
    setup_logging(cfg)

    etl = ETL(cfg)
    try:
        result = etl.refresh(force=args.force)
    except ETLError as exc:
        print(f"\nERROR: {exc}\n")
        return 1

    if result.rebuilt:
        print(f"\nCache rebuilt in {result.duration_seconds:.1f}s "
              f"({result.total_rows:,} rows).")
        print(f"Reason: {result.reason}\n")
        for name, count in sorted(result.table_rows.items(),
                                  key=lambda kv: -kv[1])[:12]:
            print(f"  {name:<24} {count:>8,}")
    else:
        print(f"\nCache is already up to date. {result.reason}")

    if result.warnings:
        print("\nWarnings:")
        for w in result.warnings:
            print(f"  - {w}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
