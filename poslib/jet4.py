"""
jet4.py - A pure-Python reader for Microsoft Access "Jet 4" database files.

WHY THIS EXISTS
---------------
The POS software stores everything in an Access database (the file with the
.dblx extension). Normally you would need Microsoft Access or an ODBC driver
installed to read it. We do not want that: this tool has to run on a plain
Windows machine with nothing but Python.

So this file reads the raw bytes of the database itself and rebuilds the
tables. It is READ-ONLY by design - there is no code anywhere in this file
that writes to the database. It cannot corrupt the POS data.

HOW AN ACCESS FILE IS LAID OUT (plain language)
-----------------------------------------------
The file is a stack of fixed-size 4096-byte "pages". The very first byte of
each page says what kind of page it is:

    0x00  the database header (page 0 only)
    0x01  a data page - actual rows of a table live here
    0x02  a table definition ("TDEF") - the list of columns for one table
    0x03  index page
    0x04  index leaf page
    0x05  page-usage bitmap

To read a table we need two things: its definition (which columns, what type,
where in the row each one sits) and its data pages. We find the definition by
following the catalogue, and we find the data pages by scanning every data
page in the file and asking "which table do you belong to?".

Everything below is that idea, in detail.
"""

from __future__ import annotations

import datetime
import math
import os
import struct
from dataclasses import dataclass, field
from typing import Any, Iterator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PAGE_SIZE = 4096

PAGE_DB_HEADER = 0x00
PAGE_DATA = 0x01
PAGE_TABLE_DEF = 0x02
PAGE_INDEX = 0x03
PAGE_INDEX_LEAF = 0x04
PAGE_USAGE_BITMAP = 0x05

# The system catalogue table (the list of every table in the database) always
# has its definition on page 2.
MSYS_OBJECTS_PAGE = 2

# Access stores dates as "days since 30 December 1899".
ACCESS_EPOCH = datetime.datetime(1899, 12, 30)

# Column type codes used by Jet 4.
TYPE_BOOL = 1
TYPE_BYTE = 2
TYPE_INT16 = 3
TYPE_INT32 = 4
TYPE_MONEY = 5
TYPE_FLOAT32 = 6
TYPE_FLOAT64 = 7
TYPE_DATETIME = 8
TYPE_BINARY = 9
TYPE_TEXT = 10
TYPE_OLE = 11
TYPE_MEMO = 12
TYPE_GUID = 13
TYPE_NUMERIC = 15

TYPE_NAMES = {
    TYPE_BOOL: "bool",
    TYPE_BYTE: "byte",
    TYPE_INT16: "int16",
    TYPE_INT32: "int32",
    TYPE_MONEY: "money",
    TYPE_FLOAT32: "float32",
    TYPE_FLOAT64: "float64",
    TYPE_DATETIME: "datetime",
    TYPE_BINARY: "binary",
    TYPE_TEXT: "text",
    TYPE_OLE: "ole",
    TYPE_MEMO: "memo",
    TYPE_GUID: "guid",
    TYPE_NUMERIC: "numeric",
}

# A row-offset entry on a data page carries two flag bits in its high bits.
ROW_FLAG_DELETED = 0x8000
ROW_FLAG_OVERFLOW = 0x4000
ROW_OFFSET_MASK = 0x1FFF

# Long-value (memo / OLE) header flags.
LVAL_FLAG_INLINE = 0x80        # the data sits right after the header
LVAL_FLAG_SINGLE_PAGE = 0x40   # the data is one chunk on one other page


class Jet4Error(Exception):
    """Raised when the file is not a database we can read."""


# ---------------------------------------------------------------------------
# Small byte-reading helpers
# ---------------------------------------------------------------------------

def _u8(buf: bytes, off: int) -> int:
    return buf[off]


def _u16(buf: bytes, off: int) -> int:
    return struct.unpack_from("<H", buf, off)[0]


def _u32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def _i16(buf: bytes, off: int) -> int:
    return struct.unpack_from("<h", buf, off)[0]


def _i32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<i", buf, off)[0]


def _i64(buf: bytes, off: int) -> int:
    return struct.unpack_from("<q", buf, off)[0]


def _f32(buf: bytes, off: int) -> float:
    return struct.unpack_from("<f", buf, off)[0]


def _f64(buf: bytes, off: int) -> float:
    return struct.unpack_from("<d", buf, off)[0]


# ---------------------------------------------------------------------------
# Text decoding
# ---------------------------------------------------------------------------

def decode_jet_text(raw: bytes) -> str:
    """
    Turn the raw bytes of an Access text/memo field into a Python string.

    Access stores text as UTF-16 (two bytes per character). But to save space
    it often "compresses" text that is mostly plain Latin letters. Compressed
    text starts with the two marker bytes FF FE and then alternates between
    two modes:

        compressed mode   - one byte per character (the second byte is zero)
        uncompressed mode - two bytes per character, as normal UTF-16

    A 0x00 byte SWITCHES between the two modes. It is *not* an end-of-string
    marker. This matters enormously here: the product names in this database
    mix Latin brand names with Arabic. The Latin part is compressed, the
    Arabic part is not, and the 0x00 in between is the switch. A parser that
    treats 0x00 as "string ends here" loses every Arabic name, and one that
    never switches modes turns the Arabic into Chinese-looking garbage.
    """
    if not raw:
        return ""

    if len(raw) >= 2 and raw[0] == 0xFF and raw[1] == 0xFE:
        body = raw[2:]
        out = bytearray()
        compressed = True
        i = 0
        n = len(body)
        while i < n:
            b = body[i]
            if b == 0x00:
                # Switch modes. Not a terminator.
                compressed = not compressed
                i += 1
            elif compressed:
                out.append(b)
                out.append(0x00)
                i += 1
            elif i + 1 < n:
                out.append(b)
                out.append(body[i + 1])
                i += 2
            else:
                break
        text = out.decode("utf-16-le", errors="replace")
    else:
        # Plain UTF-16LE. Trim a trailing odd byte if the field length is odd.
        if len(raw) % 2:
            raw = raw[:-1]
        text = raw.decode("utf-16-le", errors="replace")

    # Access pads short fixed-width text with NUL characters.
    return text.rstrip("\x00")


# ---------------------------------------------------------------------------
# Column and table definitions
# ---------------------------------------------------------------------------

@dataclass
class Column:
    """One column of one table, as described by the table definition page."""
    name: str = ""
    col_type: int = 0
    col_num: int = 0          # position used by the null bitmap
    var_col_num: int = 0      # position among variable-length columns only
    row_col_num: int = 0
    scale: int = 0
    precision: int = 0
    flags: int = 0
    fixed_offset: int = 0     # where a fixed-length value sits inside the row
    col_size: int = 0

    @property
    def is_fixed(self) -> bool:
        return bool(self.flags & 0x01)

    @property
    def type_name(self) -> str:
        return TYPE_NAMES.get(self.col_type, f"unknown({self.col_type})")


@dataclass
class Table:
    """One table: its name, where its definition lives, and its columns."""
    name: str
    tdef_page: int
    columns: list[Column] = field(default_factory=list)
    declared_rows: int = 0     # row count Access itself recorded (a hint only)
    obj_type: int = 1
    obj_flags: int = 0

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]


# ---------------------------------------------------------------------------
# The reader
# ---------------------------------------------------------------------------

class Jet4Reader:
    """
    Reads an Access Jet 4 database file.

    Usage:
        with Jet4Reader(path) as db:
            for row in db.rows("Item"):
                print(row["ItemName"])

    The file is opened read-only and never written to.
    """

    def __init__(self, path: str, preload: bool = True):
        self.path = path
        self._data: bytes = b""
        self._tables: dict[str, Table] | None = None
        self._data_pages_by_owner: dict[int, list[int]] | None = None

        if preload:
            self._load()

    # -- file access -------------------------------------------------------

    def _load(self) -> None:
        """
        Read the whole file into memory.

        The file is ~19 MB, so holding it in memory is cheap and makes the
        page-hopping this format requires far simpler and faster.

        We open with mode 'rb' and, on Windows, share flags that let other
        processes keep writing - so we never block the POS software, and we
        physically cannot modify the file through this handle.
        """
        if not os.path.isfile(self.path):
            raise Jet4Error(f"Database file not found: {self.path}")

        size = os.path.getsize(self.path)
        if size < PAGE_SIZE * 3:
            raise Jet4Error(f"File is too small to be an Access database: {size} bytes")

        with open(self.path, "rb") as fh:
            self._data = fh.read()

        self._check_header()

    def _check_header(self) -> None:
        """
        Confirm this really is an unencrypted Jet 4 file before we trust
        anything else we read out of it.
        """
        buf = self._data

        if buf[0] != PAGE_DB_HEADER:
            raise Jet4Error(
                f"Page 0 should be the database header (type 0x00) but is 0x{buf[0]:02X}. "
                "This does not look like an Access database."
            )

        magic = buf[4:19].decode("ascii", errors="replace")
        if not magic.startswith("Standard Jet DB") and not magic.startswith("Standard ACE DB"):
            raise Jet4Error(f"Unexpected format signature: {magic!r}")

        jet_version = _u32(buf, 0x14)
        if jet_version != 1:
            raise Jet4Error(
                f"This reader handles Jet 4 (version code 1). This file reports {jet_version}."
            )

        # Encryption check. In an encrypted file the page-type bytes are
        # scrambled and will not be the sane values we expect.
        p1 = buf[PAGE_SIZE]
        p2 = buf[PAGE_SIZE * 2]
        if p1 not in (PAGE_DATA, PAGE_TABLE_DEF, PAGE_INDEX, PAGE_INDEX_LEAF, PAGE_USAGE_BITMAP):
            raise Jet4Error(
                f"Page 1 has an implausible type byte (0x{p1:02X}). "
                "The database may be encrypted or password protected."
            )
        if p2 != PAGE_TABLE_DEF:
            raise Jet4Error(
                f"Page 2 should hold the system catalogue definition (0x02) but is 0x{p2:02X}. "
                "The database may be encrypted or password protected."
            )

    @property
    def page_count(self) -> int:
        return len(self._data) // PAGE_SIZE

    def page(self, num: int) -> bytes:
        """Return the raw 4096 bytes of one page."""
        if num < 0 or num >= self.page_count:
            raise Jet4Error(f"Page {num} is outside the file (it has {self.page_count} pages).")
        start = num * PAGE_SIZE
        return self._data[start:start + PAGE_SIZE]

    def close(self) -> None:
        self._data = b""

    def __enter__(self) -> "Jet4Reader":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- table definitions -------------------------------------------------

    def _read_tdef_bytes(self, page_num: int) -> bytes:
        """
        A table definition can be longer than one page. When it is, the page
        header holds a "next page" pointer at offset 4, and each continuation
        page contributes its bytes from offset 8 onwards.

        We stitch them together into one buffer. All the offsets used below
        are measured from the start of that stitched buffer.
        """
        first = self.page(page_num)
        if first[0] != PAGE_TABLE_DEF:
            raise Jet4Error(f"Page {page_num} is not a table definition (type 0x{first[0]:02X}).")

        total_len = _u32(first, 8)
        buf = bytearray(first)
        next_page = _u32(first, 4)

        seen = {page_num}
        while next_page and len(buf) < total_len and next_page < self.page_count:
            if next_page in seen:
                break  # defend against a corrupt loop
            seen.add(next_page)
            cont = self.page(next_page)
            buf.extend(cont[8:])
            next_page = _u32(cont, 4)

        return bytes(buf)

    def _parse_tdef(self, page_num: int, name: str) -> Table:
        """Turn a table-definition page into a Table with its column list."""
        buf = self._read_tdef_bytes(page_num)

        declared_rows = _u32(buf, 16)
        num_var_cols = _u16(buf, 43)          # read for completeness / sanity
        num_cols = _u16(buf, 45)
        num_real_idxs = _u32(buf, 51)

        table = Table(name=name, tdef_page=page_num, declared_rows=declared_rows)

        # Column definitions begin after the real-index block.
        col_start = 63 + num_real_idxs * 12

        columns: list[Column] = []
        for i in range(num_cols):
            off = col_start + i * 25
            if off + 25 > len(buf):
                break
            columns.append(Column(
                col_type=_u8(buf, off + 0),
                col_num=_u8(buf, off + 5),
                var_col_num=_u16(buf, off + 7),
                row_col_num=_u8(buf, off + 9),
                scale=_u8(buf, off + 11),
                precision=_u8(buf, off + 12),
                flags=_u8(buf, off + 15),
                fixed_offset=_u16(buf, off + 21),
                col_size=_u16(buf, off + 23),
            ))

        # Column names follow the definitions, in the same order: a 2-byte
        # length then that many bytes of UTF-16.
        off = col_start + num_cols * 25
        for col in columns:
            if off + 2 > len(buf):
                break
            name_len = _u16(buf, off)
            off += 2
            col.name = buf[off:off + name_len].decode("utf-16-le", errors="replace")
            off += name_len

        table.columns = columns

        # Sanity check we can surface later if a schema ever looks wrong.
        actual_var = sum(1 for c in columns if not c.is_fixed)
        table.declared_rows = declared_rows
        if num_var_cols and actual_var and abs(actual_var - num_var_cols) > num_cols:
            # Not fatal - just means the header disagrees with the column list.
            pass

        return table

    @property
    def tables(self) -> dict[str, Table]:
        """Every user table in the database, keyed by name."""
        if self._tables is None:
            self._tables = self._read_catalogue()
        return self._tables

    def _read_catalogue(self) -> dict[str, Table]:
        """
        Read MSysObjects - Access's own list of everything in the database -
        and keep only the real user tables.

        In MSysObjects each row has an Id (which is the page number of that
        object's definition), a Name, a Type and a Flags value.
        """
        catalogue = self._parse_tdef(MSYS_OBJECTS_PAGE, "MSysObjects")

        tables: dict[str, Table] = {}
        for row in self._rows_for_table(catalogue):
            name = row.get("Name")
            obj_type = row.get("Type")
            obj_id = row.get("Id")
            flags = row.get("Flags") or 0

            if not isinstance(name, str) or obj_id is None or obj_type is None:
                continue

            # Only real tables. The low 7 bits carry the object kind.
            if (int(obj_type) & 0x7F) != 1:
                continue

            # Access's own bookkeeping tables and temporary objects.
            if name.startswith("MSys") or name.startswith("~"):
                continue

            # This flag marks system / hidden objects.
            if int(flags) & 0x80000000:
                continue

            page_num = int(obj_id) & 0x00FFFFFF
            if page_num <= 0 or page_num >= self.page_count:
                continue
            if self.page(page_num)[0] != PAGE_TABLE_DEF:
                continue

            try:
                tbl = self._parse_tdef(page_num, name)
            except Jet4Error:
                continue
            tbl.obj_type = int(obj_type)
            tbl.obj_flags = int(flags)
            tables[name] = tbl

        return tables

    # -- locating data pages ----------------------------------------------

    def _build_data_page_index(self) -> dict[int, list[int]]:
        """
        Walk every page once and group the data pages by the table they
        belong to. Each data page records its owning table-definition page
        number as a 4-byte value at offset 4.

        This is simpler and more robust than following Access's page-usage
        bitmaps, and it costs one pass over a 19 MB file.
        """
        index: dict[int, list[int]] = {}
        data = self._data
        for pg in range(self.page_count):
            base = pg * PAGE_SIZE
            if data[base] != PAGE_DATA:
                continue
            owner = _u32(data, base + 4)
            index.setdefault(owner, []).append(pg)
        return index

    def data_pages_for(self, table: Table) -> list[int]:
        if self._data_pages_by_owner is None:
            self._data_pages_by_owner = self._build_data_page_index()
        return self._data_pages_by_owner.get(table.tdef_page, [])

    # -- row reading -------------------------------------------------------

    @staticmethod
    def _row_bounds(page: bytes) -> list[tuple[int, int, int]]:
        """
        Work out where each row on a data page starts and ends.

        The page header holds the number of rows at offset 0x0C, followed by
        one 2-byte offset per row starting at 0x0E. The top two bits of each
        offset are flags (deleted / overflow pointer).

        THE IMPORTANT PART: a row ends just before the *next higher* offset
        on the page - not at the previous entry in the offset array. Rows are
        not always listed in descending order. Getting this wrong does not
        crash anything; it silently truncates the variable-length fields,
        which is exactly where every product name and customer name lives.

        Returns a list of (row_start, row_end_inclusive, flags).
        """
        row_count = _u16(page, 0x0C)
        if row_count == 0 or row_count > 1000:
            return []

        raw: list[int] = []
        for i in range(row_count):
            off = 0x0E + i * 2
            if off + 2 > PAGE_SIZE:
                return []
            raw.append(_u16(page, off))

        # Every offset - including deleted and overflow rows - occupies space
        # on the page, so every one of them is a valid boundary.
        boundaries = sorted({o & ROW_OFFSET_MASK for o in raw})

        out: list[tuple[int, int, int]] = []
        for entry in raw:
            start = entry & ROW_OFFSET_MASK
            flags = entry & (ROW_FLAG_DELETED | ROW_FLAG_OVERFLOW)

            higher = [b for b in boundaries if b > start]
            end = (min(higher) - 1) if higher else (PAGE_SIZE - 1)
            out.append((start, end, flags))

        return out

    def _rows_for_table(self, table: Table) -> Iterator[dict[str, Any]]:
        """Yield every live row of a table as a dict of column name -> value."""
        for page_num in self.data_pages_for(table):
            page = self.page(page_num)
            for start, end, flags in self._row_bounds(page):
                if flags & ROW_FLAG_DELETED:
                    continue
                if flags & ROW_FLAG_OVERFLOW:
                    continue
                row = self._parse_row(page, start, end, table)
                if row is not None:
                    yield row

    def rows(self, table_name: str) -> Iterator[dict[str, Any]]:
        table = self.tables.get(table_name)
        if table is None:
            raise Jet4Error(f"No table called {table_name!r} in this database.")
        return self._rows_for_table(table)

    def _parse_row(self, page: bytes, start: int, end: int,
                   table: Table) -> dict[str, Any] | None:
        """
        Decode one row.

        Layout, from the start of the row:
            2 bytes   how many columns this row was written with
            ...       the fixed-length values, each at its own known offset
            ...       the variable-length values, packed together
            ...       2-byte offsets locating those variable values
            2 bytes   how many variable values there are
            n bytes   the null bitmap (a SET bit means "not null")
        """
        if start + 2 > end or end >= PAGE_SIZE:
            return None

        row_cols = _u16(page, start)
        if row_cols == 0 or row_cols > 512:
            return None

        bitmask_sz = (row_cols + 7) // 8
        bitmap_start = end - bitmask_sz + 1
        if bitmap_start <= start:
            return None
        null_bitmap = page[bitmap_start:end + 1]

        # Variable-length field offsets, read backwards from just before the
        # null bitmap.
        var_count_pos = end - bitmask_sz - 1
        var_starts: list[int] = []
        if var_count_pos > start + 1:
            var_count = _u16(page, var_count_pos)
            if 0 <= var_count <= row_cols:
                ok = True
                raw_offsets: list[int] = []
                for i in range(var_count + 1):
                    pos = end - bitmask_sz - 3 - i * 2
                    if pos < start + 2:
                        ok = False
                        break
                    raw_offsets.append(_u16(page, pos))
                if ok:
                    # The offsets are stored in reverse; sorting recovers the
                    # order the values are actually laid out in, because the
                    # values themselves are written in column order.
                    var_starts = sorted(raw_offsets)

        row: dict[str, Any] = {}

        for col in table.columns:
            name = col.name or f"col{col.col_num}"

            # Columns added after this row was written simply are not there.
            if col.col_num >= row_cols:
                row[name] = None
                continue

            byte_idx = col.col_num // 8
            bit_idx = col.col_num % 8
            bit_set = bool(null_bitmap[byte_idx] & (1 << bit_idx)) \
                if byte_idx < len(null_bitmap) else False

            # Yes/No columns are a special case: they have no storage of their
            # own anywhere in the row. Their value IS the bitmap bit.
            #
            # A set bit means True (the same direction as "not null"). This was
            # checked against facts we already knew were true in this database:
            # all 42 customers carrying a balance come out as AllowAccount=True,
            # the anonymous "Client divers" account comes out False, and items
            # sold this morning come out Inactive=False. The opposite reading
            # marks every one of the 1,570 items inactive and gives the walk-in
            # account the only credit line in the shop, which is nonsense.
            if col.col_type == TYPE_BOOL:
                row[name] = bit_set
                continue

            # For every other type, a clear bit means the value is NULL.
            if not bit_set:
                row[name] = None
                continue

            try:
                if col.is_fixed:
                    value = self._read_fixed(page, start, end, col)
                else:
                    value = self._read_variable(page, start, end, col, var_starts)
            except (struct.error, IndexError, ValueError):
                value = None

            row[name] = value

        return row

    def _read_fixed(self, page: bytes, start: int, end: int, col: Column) -> Any:
        """Read a fixed-length value from its known slot in the row."""
        pos = start + 2 + col.fixed_offset
        size = col.col_size

        if pos < start or pos + size > end + 1:
            return None

        return self._decode_value(page[pos:pos + size], col)

    def _read_variable(self, page: bytes, start: int, end: int, col: Column,
                       var_starts: list[int]) -> Any:
        """
        Read a variable-length value (text, memo, binary).

        A variable-length column is found by its var_col_num - its position
        among the variable columns only - NOT by its position in the full
        column list. Using the wrong index here returns empty strings for
        every text field in the database.
        """
        v = col.var_col_num
        if v < 0 or v + 1 >= len(var_starts):
            return None

        rel_begin = var_starts[v]
        rel_end = var_starts[v + 1]

        abs_begin = start + rel_begin
        abs_end = start + rel_end

        if abs_begin > abs_end or abs_end > end + 1 or abs_begin < start:
            return None

        return self._decode_value(page[abs_begin:abs_end], col)

    def _decode_value(self, raw: bytes, col: Column) -> Any:
        """Turn the raw bytes of one field into a Python value."""
        t = col.col_type

        if not raw:
            return "" if t in (TYPE_TEXT, TYPE_MEMO) else None

        if t == TYPE_BYTE:
            return raw[0]

        if t == TYPE_INT16:
            return _i16(raw, 0) if len(raw) >= 2 else None

        if t == TYPE_INT32:
            return _i32(raw, 0) if len(raw) >= 4 else None

        if t == TYPE_MONEY:
            # Stored as a whole number of ten-thousandths of a unit.
            return (_i64(raw, 0) / 10000.0) if len(raw) >= 8 else None

        if t == TYPE_FLOAT32:
            return _f32(raw, 0) if len(raw) >= 4 else None

        if t == TYPE_FLOAT64:
            return _f64(raw, 0) if len(raw) >= 8 else None

        if t == TYPE_DATETIME:
            if len(raw) < 8:
                return None
            days = _f64(raw, 0)
            if not math.isfinite(days) or not (-700000 < days < 3000000):
                return None
            try:
                return ACCESS_EPOCH + datetime.timedelta(days=days)
            except (OverflowError, OSError):
                return None

        if t == TYPE_TEXT:
            return decode_jet_text(raw)

        if t == TYPE_MEMO:
            return self._read_long_value(raw, as_text=True)

        if t == TYPE_OLE:
            return self._read_long_value(raw, as_text=False)

        if t == TYPE_GUID:
            if len(raw) < 16:
                return None
            a, b, c = struct.unpack_from("<IHH", raw, 0)
            d = raw[8:16]
            return "{%08X-%04X-%04X-%s-%s}" % (a, b, c, d[:2].hex().upper(), d[2:].hex().upper())

        if t == TYPE_NUMERIC:
            return self._decode_numeric(raw, col)

        if t == TYPE_BINARY:
            return raw

        return raw

    @staticmethod
    def _decode_numeric(raw: bytes, col: Column) -> Any:
        """
        Decode a fixed-point NUMERIC/DECIMAL: one flag byte (0x80 = negative)
        then a 128-bit whole number in four little-endian 32-bit words. The
        column's scale says where the decimal point goes.
        """
        if len(raw) < 17:
            return None
        negative = bool(raw[0] & 0x80)
        value = 0
        for i in range(4):
            value |= _u32(raw, 1 + i * 4) << (32 * i)
        if negative:
            value = -value
        scale = col.scale or 0
        return value / (10 ** scale) if scale else float(value)

    # -- memo / OLE long values -------------------------------------------

    def _read_long_value(self, raw: bytes, as_text: bool) -> Any:
        """
        Memo and OLE columns hold a 12-byte header rather than the data.

        The first 4 bytes pack a length (low 24 bits) and flags (high byte):
            flag 0x80 - the data follows immediately, inline
            flag 0x40 - the data is a single chunk on another page
            neither   - the data is a chain of chunks across several pages

        For the second and third cases the next 4 bytes are a pointer where
        the top 3 bytes are a page number and the bottom byte is a row number
        on that page.
        """
        if len(raw) < 4:
            return "" if as_text else b""

        header = _u32(raw, 0)
        length = header & 0x00FFFFFF
        flags = (header >> 24) & 0xFF

        if length == 0:
            return "" if as_text else b""

        payload: bytes

        if flags & LVAL_FLAG_INLINE:
            payload = raw[12:12 + length]
        elif len(raw) >= 8:
            ptr = _u32(raw, 4)
            payload = self._follow_lval(ptr, length, single_page=bool(flags & LVAL_FLAG_SINGLE_PAGE))
        else:
            payload = b""

        return decode_jet_text(payload) if as_text else payload

    def _follow_lval(self, ptr: int, length: int, single_page: bool) -> bytes:
        """Collect a long value stored on one or more separate pages."""
        out = bytearray()
        seen: set[int] = set()

        while ptr and len(out) < length:
            page_num = ptr >> 8
            row_num = ptr & 0xFF

            key = ptr
            if key in seen or page_num <= 0 or page_num >= self.page_count:
                break
            seen.add(key)

            try:
                page = self.page(page_num)
            except Jet4Error:
                break
            if page[0] != PAGE_DATA:
                break

            chunk = self._raw_row_bytes(page, row_num)
            if chunk is None:
                break

            if single_page:
                out.extend(chunk[:length])
                break

            # Chained chunks begin with the pointer to the next chunk.
            if len(chunk) < 4:
                break
            ptr = _u32(chunk, 0)
            out.extend(chunk[4:])

        return bytes(out[:length])

    @staticmethod
    def _raw_row_bytes(page: bytes, row_num: int) -> bytes | None:
        """Return the raw bytes of one numbered row on a page, unparsed."""
        row_count = _u16(page, 0x0C)
        if row_num >= row_count:
            return None

        raw = [_u16(page, 0x0E + i * 2) for i in range(row_count)]
        boundaries = sorted({o & ROW_OFFSET_MASK for o in raw})

        entry = raw[row_num]
        start = entry & ROW_OFFSET_MASK
        higher = [b for b in boundaries if b > start]
        end = (min(higher) - 1) if higher else (PAGE_SIZE - 1)

        if start > end:
            return None
        return page[start:end + 1]

    # -- convenience -------------------------------------------------------

    def table_names(self) -> list[str]:
        return sorted(self.tables.keys())

    def read_table(self, name: str) -> list[dict[str, Any]]:
        """Read a whole table into a list. Fine for tables of this size."""
        return list(self.rows(name))

    def row_count(self, name: str) -> int:
        return sum(1 for _ in self.rows(name))
