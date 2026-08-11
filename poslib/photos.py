"""
photos.py - best-effort extraction of the Item.Picture field.

The cached SQLite database never has this column - poslib/etl.py strips OLE
and binary columns to keep the cache small and fast (SKIPPED_COLUMN_TYPES).
So a photo has to be read straight from a fresh copy of the source .dblx, on
demand, the first time it's asked for. The result - photo bytes, or "no
photo" - is cached to a small file so this only happens once per item.

As of the last check against the live database, none of the 1,570 real
products have anything in this field, so this is intentionally lightweight:
a best-effort attempt to recognise a plain image inside whatever bytes
Access handed back, not a full OLE Structured Storage / Package parser. If
it can't make sense of the bytes, it reports "no photo" rather than
guessing - the same "fails loudly/quietly, never wrongly" spirit as the
rest of the tool.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from .config import Config
from .etl import copy_database_readonly
from .jet4 import Jet4Reader

log = logging.getLogger(__name__)

# Recognisable image formats, sniffed by their leading bytes. If the OLE
# field wraps the image in a Structured Storage / "Package" container, the
# magic bytes won't sit at offset 0 - a short forward scan below covers that
# without attempting a full OLE parse.
_SIGNATURES: dict[bytes, str] = {
    b"\xff\xd8\xff": "jpg",
    b"\x89PNG\r\n\x1a\n": "png",
    b"BM": "bmp",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
}
_SCAN_WINDOW = 4096  # how far into the blob to look for a signature


def _sniff(raw: bytes) -> tuple[str, int] | None:
    """Find the earliest known image signature within the first bytes of
    the blob. Returns (extension, offset), or None if nothing recognisable
    is there."""
    window = raw[:_SCAN_WINDOW]
    best: tuple[str, int] | None = None
    for sig, ext in _SIGNATURES.items():
        idx = window.find(sig)
        if idx != -1 and (best is None or idx < best[1]):
            best = (ext, idx)
    return best


def _cache_dir(cfg: Config) -> Path:
    d = cfg.path("catalog.photo_cache_dir", "static/photo-cache")
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_item_photo(cfg: Config, item_id: int) -> tuple[bytes, str] | None:
    """
    Return (bytes, extension) for one item's photo, or None if it has no
    photo - or nothing the tool can recognise as one. Cached to disk after
    the first lookup, including the "no photo" answer (as a zero-byte
    `<id>.none` sentinel), so a fresh Jet4Reader is never opened twice for
    the same item.
    """
    cache_dir = _cache_dir(cfg)
    sentinel = cache_dir / f"{item_id}.none"
    if sentinel.is_file():
        return None

    for existing in cache_dir.glob(f"{item_id}.*"):
        if existing.suffix != ".none":
            return existing.read_bytes(), existing.suffix.lstrip(".")

    raw = _read_raw_picture(cfg, item_id)
    found = _sniff(raw) if raw else None
    if not found:
        sentinel.touch()
        return None

    ext, offset = found
    data = raw[offset:]
    (cache_dir / f"{item_id}.{ext}").write_bytes(data)
    return data, ext


def _read_raw_picture(cfg: Config, item_id: int) -> bytes | None:
    """
    Open a fresh, read-only copy of the source database and read one item's
    Picture column directly. The low-level reader already supports OLE/memo
    long-values - only the ETL cache pipeline strips them.

    Uses its own temp folder (not the one ETL's own refresh cycle copies
    into) so an on-demand photo lookup never contends with the watcher
    rebuilding the cache at the same time.
    """
    try:
        working_copy = copy_database_readonly(
            cfg.source_db, dst_dir=Path(tempfile.gettempdir()) / "pos-tool-photo-work")
        db = Jet4Reader(str(working_copy))
        for row in db.rows("Item"):
            if row.get("ID") == item_id:
                pic = row.get("Picture")
                return bytes(pic) if pic else None
        return None
    except Exception:
        log.exception("Could not read Item.Picture for item_id=%s", item_id)
        return None
