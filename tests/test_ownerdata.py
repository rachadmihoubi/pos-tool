"""
Tests for poslib/ownerdata.py - the owner-entered competitor price log.

Entirely independent of the live-database fixtures in conftest.py: this
module never reads the POS database at all, so these tests use a throwaway
SQLite file under tmp_path instead.
"""

from __future__ import annotations

import datetime
import inspect

import pytest

from poslib import ownerdata
from poslib.config import get_config


def _cfg_with_db(monkeypatch, tmp_path):
    from poslib.config import Config
    cfg = get_config()
    db_path = tmp_path / "owner.db"
    original = Config.path

    def patched(self, key, default=""):
        if key == "catalog.owner_data_db":
            return db_path
        return original(self, key, default)

    monkeypatch.setattr(cfg, "path", patched.__get__(cfg))
    return cfg


class TestNeverTouchesTheCache:

    def test_module_never_imports_etl_or_metrics(self):
        """
        Structural guard: this module must never be able to open cache.db,
        since an ETL refresh fully rebuilds and replaces that file - owner
        data stored there would be destroyed on the next cache rebuild.
        """
        source = inspect.getsource(ownerdata)
        assert "from .etl import" not in source
        assert "from .metrics import" not in source
        assert "import etl" not in source
        assert "import metrics" not in source


class TestAddCompetitorPrice:

    def test_round_trip(self, monkeypatch, tmp_path):
        cfg = _cfg_with_db(monkeypatch, tmp_path)
        ownerdata.add_competitor_price(
            cfg, item_id=42, competitor_name="Beauty Plus",
            price=250.0, observed_date=datetime.date(2026, 8, 1),
            note="seen in their front window")

        prices = ownerdata.competitor_prices_for_item(cfg, 42)
        assert len(prices) == 1
        row = prices.iloc[0]
        assert row["competitor_name"] == "Beauty Plus"
        assert row["price"] == 250.0
        assert row["note"] == "seen in their front window"

    def test_only_returns_prices_for_the_requested_item(self, monkeypatch, tmp_path):
        cfg = _cfg_with_db(monkeypatch, tmp_path)
        ownerdata.add_competitor_price(cfg, 1, "A", 100.0, datetime.date(2026, 1, 1))
        ownerdata.add_competitor_price(cfg, 2, "B", 200.0, datetime.date(2026, 1, 1))
        assert len(ownerdata.competitor_prices_for_item(cfg, 1)) == 1
        assert len(ownerdata.competitor_prices_for_item(cfg, 2)) == 1

    def test_newest_observed_first(self, monkeypatch, tmp_path):
        cfg = _cfg_with_db(monkeypatch, tmp_path)
        ownerdata.add_competitor_price(cfg, 7, "Old sighting", 100.0, datetime.date(2025, 1, 1))
        ownerdata.add_competitor_price(cfg, 7, "New sighting", 110.0, datetime.date(2026, 6, 1))
        prices = ownerdata.competitor_prices_for_item(cfg, 7)
        assert prices.iloc[0]["competitor_name"] == "New sighting"

    def test_empty_competitor_name_is_rejected(self, monkeypatch, tmp_path):
        cfg = _cfg_with_db(monkeypatch, tmp_path)
        with pytest.raises(ownerdata.OwnerDataError):
            ownerdata.add_competitor_price(cfg, 1, "  ", 100.0, datetime.date(2026, 1, 1))

    def test_zero_or_negative_price_is_rejected(self, monkeypatch, tmp_path):
        cfg = _cfg_with_db(monkeypatch, tmp_path)
        with pytest.raises(ownerdata.OwnerDataError):
            ownerdata.add_competitor_price(cfg, 1, "Someone", 0, datetime.date(2026, 1, 1))
        with pytest.raises(ownerdata.OwnerDataError):
            ownerdata.add_competitor_price(cfg, 1, "Someone", -5, datetime.date(2026, 1, 1))

    def test_missing_date_is_rejected(self, monkeypatch, tmp_path):
        cfg = _cfg_with_db(monkeypatch, tmp_path)
        with pytest.raises(ownerdata.OwnerDataError):
            ownerdata.add_competitor_price(cfg, 1, "Someone", 100.0, None)

    def test_no_rows_yet_returns_empty_not_raises(self, monkeypatch, tmp_path):
        cfg = _cfg_with_db(monkeypatch, tmp_path)
        assert ownerdata.competitor_prices_for_item(cfg, 999).empty


class TestDeleteCompetitorPrice:

    def test_delete_removes_only_that_row(self, monkeypatch, tmp_path):
        cfg = _cfg_with_db(monkeypatch, tmp_path)
        ownerdata.add_competitor_price(cfg, 5, "A", 100.0, datetime.date(2026, 1, 1))
        ownerdata.add_competitor_price(cfg, 5, "B", 200.0, datetime.date(2026, 1, 2))
        prices = ownerdata.competitor_prices_for_item(cfg, 5)
        assert len(prices) == 2

        to_delete = int(prices.iloc[0]["id"])
        ownerdata.delete_competitor_price(cfg, to_delete)

        remaining = ownerdata.competitor_prices_for_item(cfg, 5)
        assert len(remaining) == 1
        assert to_delete not in remaining["id"].tolist()

    def test_deleting_a_nonexistent_id_does_not_raise(self, monkeypatch, tmp_path):
        cfg = _cfg_with_db(monkeypatch, tmp_path)
        ownerdata.delete_competitor_price(cfg, 999999)
