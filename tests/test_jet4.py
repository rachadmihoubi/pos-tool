"""
Tests for the Access database reader.

These prove the parser decodes real rows correctly - especially the two
things that are easy to get subtly wrong and hard to notice: Arabic text and
the true/false convention.
"""

from __future__ import annotations

import datetime
import struct

import pytest

from poslib.jet4 import (Jet4Error, Jet4Reader, PAGE_SIZE, TYPE_BOOL,
                         TYPE_DATETIME, TYPE_MONEY, TYPE_TEXT, decode_jet_text)


# ---------------------------------------------------------------------------
#  Text decoding - no database needed
# ---------------------------------------------------------------------------

class TestTextDecoding:
    """
    Access text is UTF-16, but often "compressed" to one byte per character
    with a 0x00 acting as a switch between the two modes. Treating that 0x00
    as an end-of-string marker loses every Arabic product name in the file.
    """

    def test_plain_utf16(self):
        raw = "RUBY ROSE".encode("utf-16-le")
        assert decode_jet_text(raw) == "RUBY ROSE"

    def test_compressed_latin(self):
        # FF FE marker, then one byte per character.
        raw = b"\xff\xfe" + b"VIORYLE"
        assert decode_jet_text(raw) == "VIORYLE"

    def test_arabic_is_not_compressed(self):
        arabic = "بريو لافر"
        raw = b"\xff\xfe\x00" + arabic.encode("utf-16-le")
        assert decode_jet_text(raw) == arabic

    def test_switching_between_modes(self):
        """
        The real shape of a product name in this shop: a Latin brand, then
        Arabic, then a Latin code. Each switch is a single 0x00 byte.
        """
        arabic = "فوندوتا ليكيد"
        raw = (b"\xff\xfe"
               + b"VIORYLE "                       # compressed
               + b"\x00" + arabic.encode("utf-16-le")   # switch, uncompressed
               + b"\x00" + b" Q0130")              # switch back, compressed
        assert decode_jet_text(raw) == f"VIORYLE {arabic} Q0130"

    def test_zero_byte_is_a_switch_not_a_terminator(self):
        raw = b"\xff\xfe" + b"AB" + b"\x00" + "ج".encode("utf-16-le") + b"\x00" + b"CD"
        result = decode_jet_text(raw)
        assert result == "ABجCD"
        assert len(result) == 5, "text was cut short at the switch byte"

    def test_empty_and_padding(self):
        assert decode_jet_text(b"") == ""
        assert decode_jet_text("AB\x00\x00".encode("utf-16-le")) == "AB"

    def test_odd_length_does_not_raise(self):
        assert isinstance(decode_jet_text(b"\x41\x00\x42"), str)


# ---------------------------------------------------------------------------
#  Reading the real database
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("db")
class TestRealDatabase:

    def test_it_is_an_unencrypted_jet4_file(self, db: Jet4Reader):
        assert db.page(0)[0] == 0x00, "page 0 should be the database header"
        assert db.page(2)[0] == 0x02, "page 2 should be the catalogue definition"
        assert db.page_count > 100

    def test_finds_the_expected_tables(self, db: Jet4Reader):
        names = set(db.table_names())
        for expected in ("Receipt", "ReceiptEntry", "Item", "Customer",
                         "Supplier", "ItemFamily", "PurchaseEntry"):
            assert expected in names, f"table {expected} was not found"

    def test_no_system_tables_leak_through(self, db: Jet4Reader):
        for name in db.table_names():
            assert not name.startswith("MSys")
            assert not name.startswith("~")

    def test_row_counts(self, db: Jet4Reader, expected_counts):
        """
        The file grows as sales are made, so counts may only ever be equal to
        or higher than when this was checked. A count that has FALLEN means
        rows are being missed.
        """
        for table, minimum in expected_counts.items():
            actual = db.row_count(table)
            assert actual >= minimum, (
                f"{table} has {actual} rows but should have at least {minimum}. "
                "Rows are being missed - check the row-end calculation.")

    def test_item_columns_are_named(self, db: Jet4Reader):
        columns = db.tables["Item"].column_names
        for expected in ("ID", "ItemName", "Stock", "Cost", "Price", "LastSold"):
            assert expected in columns

    def test_known_row_decodes_correctly(self, db: Jet4Reader):
        """
        Item 1 is the catch-all "Article divers". Its number and name are
        fixed and known, so it is a good anchor for the whole row layout.
        """
        items = {r["ID"]: r for r in db.rows("Item")}
        assert 1 in items
        assert items[1]["ItemNo"] == "AR0001"
        assert items[1]["ItemName"] == "Article divers"

    def test_arabic_product_names_render(self, db: Jet4Reader):
        """
        The real test of the text decoder: names must contain actual Arabic
        letters, not the CJK characters a wrong mode switch produces.
        """
        names = [r["ItemName"] or "" for r in db.rows("Item")]
        arabic = [n for n in names if any("؀" <= c <= "ۿ" for c in n)]
        assert len(arabic) > 100, "almost no Arabic found - the decoder is wrong"

        cjk = [n for n in names if any("一" <= c <= "鿿" for c in n)]
        assert not cjk, f"Arabic was decoded as CJK: {cjk[:3]}"

        mixed = [n for n in arabic if any("A" <= c <= "Z" for c in n)]
        assert mixed, "no name mixes a Latin brand with Arabic, which is unexpected"

    def test_nulls_are_none_not_zero(self, db: Jet4Reader):
        """
        A missing cost is not a cost of zero. Confusing the two turns an
        unknown margin into a 100% margin.
        """
        rows = list(db.rows("Item"))
        assert any(r["LastPurchased"] is None for r in rows), \
            "no null dates at all, which is suspicious"
        # Item 1 has no stock recorded.
        item1 = next(r for r in rows if r["ID"] == 1)
        assert item1["Stock"] is None or item1["Stock"] == 0

    def test_booleans_use_the_set_bit_convention(self, db: Jet4Reader):
        """
        A set bit means True. The opposite reading marks every product in the
        shop inactive and gives the anonymous walk-in till the only credit
        account, which is nonsense.
        """
        customers = {r["ID"]: r for r in db.rows("Customer")}

        owing = [c for c in customers.values() if (c.get("Account") or 0) > 0]
        assert owing, "no customer owes anything, which contradicts the data"
        for c in owing:
            assert c["AllowAccount"] is True, (
                f"{c['CustomerName']} owes money but is not allowed an account - "
                "the boolean convention is inverted")

        assert customers[1]["AllowAccount"] is False, \
            "the anonymous walk-in account should not have a credit line"

        items = list(db.rows("Item"))
        inactive = sum(1 for i in items if i["Inactive"])
        assert inactive < len(items) / 2, \
            "most products are marked inactive - the boolean convention is inverted"

    def test_dates_are_real_datetimes(self, db: Jet4Reader):
        times = [r["Time"] for r in db.rows("Receipt") if r["Time"]]
        assert times
        assert all(isinstance(t, datetime.datetime) for t in times)
        assert min(times).year == 2024
        assert min(times) < max(times)

    def test_money_is_scaled_correctly(self, db: Jet4Reader):
        """
        Money is stored as ten-thousandths. Forgetting to divide makes every
        figure ten thousand times too big.
        """
        prices = [r["Price"] for r in db.rows("Item")
                  if r["Price"] is not None and r["Price"] > 0]
        assert prices
        # Wholesale cosmetics in DZD: tens to a few thousand per unit.
        assert 10 <= sum(prices) / len(prices) <= 20000, \
            "average price is implausible - the money scale is wrong"

    def test_variable_columns_found_by_var_col_num(self, db: Jet4Reader):
        """
        Variable-length fields are located by their position among variable
        columns, not their position in the full column list. Getting it wrong
        returns empty strings for everything.
        """
        rows = list(db.rows("Item"))
        named = [r for r in rows if r["ItemName"]]
        assert len(named) > len(rows) * 0.95, \
            "many product names are empty - variable columns are misaligned"

        numbered = [r for r in rows if r["ItemNo"]]
        assert len(numbered) > len(rows) * 0.95

    def test_reading_twice_gives_the_same_answer(self, db: Jet4Reader):
        first = [r["ID"] for r in db.rows("Customer")]
        second = [r["ID"] for r in db.rows("Customer")]
        assert first == second

    def test_unknown_table_raises_clearly(self, db: Jet4Reader):
        with pytest.raises(Jet4Error, match="No table"):
            list(db.rows("NotATable"))


# ---------------------------------------------------------------------------
#  Refusing files we cannot read
# ---------------------------------------------------------------------------

class TestRejectsBadFiles:

    def test_missing_file(self, tmp_path):
        with pytest.raises(Jet4Error, match="not found"):
            Jet4Reader(str(tmp_path / "nope.dblx"))

    def test_too_small(self, tmp_path):
        path = tmp_path / "tiny.dblx"
        path.write_bytes(b"\x00" * 100)
        with pytest.raises(Jet4Error, match="too small"):
            Jet4Reader(str(path))

    def test_not_an_access_file(self, tmp_path):
        path = tmp_path / "wrong.dblx"
        path.write_bytes(b"PK\x03\x04" + b"\x00" * (PAGE_SIZE * 4))
        with pytest.raises(Jet4Error):
            Jet4Reader(str(path))

    def test_wrong_jet_version(self, tmp_path):
        """A Jet 3 file should be refused clearly rather than misread."""
        page = bytearray(PAGE_SIZE * 4)
        page[0] = 0x00
        page[4:19] = b"Standard Jet DB"
        struct.pack_into("<I", page, 0x14, 0)          # Jet 3
        page[PAGE_SIZE] = 0x01
        page[PAGE_SIZE * 2] = 0x02
        path = tmp_path / "jet3.mdb"
        path.write_bytes(bytes(page))
        with pytest.raises(Jet4Error, match="Jet 4"):
            Jet4Reader(str(path))
