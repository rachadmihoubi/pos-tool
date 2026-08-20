"""
Shared setup for the tests.

The tests run against the REAL POS database, because a made-up one would
not prove the parser handles this file. They only ever read it: the database
is copied to a temporary folder first, and every check is read-only.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from poslib.config import get_config          # noqa: E402
from poslib.etl import ETL                    # noqa: E402
from poslib.jet4 import Jet4Reader            # noqa: E402
from poslib.metrics import Metrics            # noqa: E402


@pytest.fixture(scope="session")
def cfg():
    return get_config()


@pytest.fixture(scope="session")
def source_db(cfg) -> Path:
    path = cfg.source_db
    if not path.is_file():
        pytest.skip(f"The POS database is not where config.yaml says it is: {path}")
    return path


@pytest.fixture(scope="session")
def working_copy(source_db, tmp_path_factory) -> Path:
    """
    A private copy of the database.

    Every test reads this copy, never the original, so a test can never
    affect live data even if something goes badly wrong.
    """
    folder = tmp_path_factory.mktemp("pos-db")
    target = folder / "copy.dblx"
    shutil.copy2(source_db, target)
    return target


@pytest.fixture(scope="session")
def db(working_copy) -> Jet4Reader:
    reader = Jet4Reader(str(working_copy))
    yield reader
    reader.close()


@pytest.fixture(scope="session")
def etl(cfg) -> ETL:
    e = ETL(cfg)
    e.refresh()
    return e


@pytest.fixture(scope="session")
def metrics(etl, cfg) -> Metrics:
    conn = etl.connect()
    m = Metrics(conn, cfg)
    yield m
    conn.close()


@pytest.fixture(scope="session")
def expected_counts() -> dict[str, int]:
    """
    Row counts recorded when the extraction was checked by hand.

    These are floors, not equalities: the shop keeps trading, so the tables
    grow. A count BELOW one of these means rows are being missed.
    """
    return {
        "Receipt": 7858,
        "ReceiptEntry": 39736,
        "Item": 1570,
        "Customer": 662,
        "Supplier": 42,
        "ItemFamily": 22,
    }


@pytest.fixture(scope="session")
def expected_totals() -> dict[str, float]:
    """
    Money figures checked by hand against the database.

    The all-time ones can only grow, by exactly the value of new sales. The
    point-in-time ones (stock, receivables) move in both directions as trade
    happens, so they are checked within a tolerance.

    STALE as of the 2026-08-20 devis fix (CLAUDE.md discovery #13): "DV"
    tickets are now correctly excluded from revenue/profit, so
    revenue_all_time/gross_profit_all_time/revenue_12m/gross_profit_12m
    below may need lowering the next time this is checked against the real
    database - a small drop from that change is expected, not a bug.
    """
    return {
        "revenue_all_time": 266_299_322,
        "gross_profit_all_time": 26_686_300,
        "reglement_value": 30_420_753,
        "revenue_12m": 167_589_951,
        "gross_profit_12m": 14_635_724,
        "stock_value": 59_168_540,
        "receivables": 18_035_898,
    }
