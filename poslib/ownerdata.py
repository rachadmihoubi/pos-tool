"""
ownerdata.py - data the owner types into the tool himself, not read from the
POS.

This is deliberately its own SQLite file (`data/owner.db`), a connection
this module opens and closes itself - `poslib/etl.py` never touches it, and
never will. An ETL refresh fully rebuilds and replaces `cache.db` from
scratch every time (see `ETL._build_cache`); anything owner-entered stored
there would be destroyed on the next cache rebuild. If the tool ever gets
more owner-entered features, they belong in this file too, next to
competitor prices - never inside cache.db.

This is also the first place in the app that *writes* data rather than only
reading the POS database, so every write is validated before it touches
disk - see `OwnerDataError`.
"""

from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path

import pandas as pd

from .config import Config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS competitor_price (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    competitor_name TEXT NOT NULL,
    price REAL NOT NULL,
    observed_date TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_competitor_price_item ON competitor_price(item_id);
"""


class OwnerDataError(Exception):
    """Something typed into a form doesn't make sense - never written."""


def _db_path(cfg: Config) -> Path:
    path = cfg.path("catalog.owner_data_db", "data/owner.db")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect(cfg: Config) -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(cfg))
    conn.executescript(_SCHEMA)
    return conn


def add_competitor_price(cfg: Config, item_id: int, competitor_name: str,
                          price: float, observed_date: datetime.date,
                          note: str = "") -> None:
    """
    Log what a competitor was charging for one of our products on a given
    date. Raises OwnerDataError on anything that doesn't make sense -
    validated here, not left to the database to reject.
    """
    competitor_name = (competitor_name or "").strip()
    if not competitor_name:
        raise OwnerDataError("competitor name is required")
    if price is None or price <= 0:
        raise OwnerDataError("price must be a positive number")
    if observed_date is None:
        raise OwnerDataError("observed date is required")

    conn = _connect(cfg)
    try:
        conn.execute(
            "INSERT INTO competitor_price "
            "(item_id, competitor_name, price, observed_date, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (int(item_id), competitor_name, float(price), observed_date.isoformat(),
             (note or "").strip(), datetime.datetime.now().isoformat()))
        conn.commit()
    finally:
        conn.close()


def competitor_prices_for_item(cfg: Config, item_id: int) -> pd.DataFrame:
    """Every logged competitor price for one product, newest observed first."""
    conn = _connect(cfg)
    try:
        df = pd.read_sql_query(
            "SELECT id, item_id, competitor_name, price, observed_date, note "
            "FROM competitor_price WHERE item_id = ? "
            "ORDER BY observed_date DESC, id DESC",
            conn, params=(int(item_id),))
    finally:
        conn.close()
    df["observed_date"] = pd.to_datetime(df["observed_date"], errors="coerce")
    return df


def delete_competitor_price(cfg: Config, price_id: int) -> None:
    conn = _connect(cfg)
    try:
        conn.execute("DELETE FROM competitor_price WHERE id = ?", (int(price_id),))
        conn.commit()
    finally:
        conn.close()
