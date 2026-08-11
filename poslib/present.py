"""
present.py - the freshness signal for the remote (static-export) dashboard.

The actual page content is rendered by reusing app.py's real routes and
templates directly (see export_static.py) - this module only builds the
small `status.json` payload, the same {parsed_at, source_modified} freshness
fields the local dashboard's own footer already shows, so "last updated"
never means something different remotely than it does locally.
"""

from __future__ import annotations

import datetime
from typing import Any


def status_payload(etl_cache_info: dict[str, Any],
                    generated_at: datetime.datetime | None = None) -> dict[str, Any]:
    """
    The lightweight freshness signal a remote monitoring tool (or a
    lighter-weight check than loading the whole page) can read - the exact
    same fields the local dashboard's footer already shows
    (`cache.parsed_at`, `cache.source_modified`).
    """
    return {
        "parsed_at": str(etl_cache_info.get("parsed_at") or ""),
        "source_modified": str(etl_cache_info.get("source_modified") or ""),
        "row_counts": etl_cache_info.get("row_counts") or {},
        "generated_at": (generated_at or datetime.datetime.now()).isoformat(),
    }
