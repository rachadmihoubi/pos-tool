"""
Tests for hub-site/ - the multi-store hub's static site.

There is no JS test runner in this project (no Node.js at all - see
CLAUDE.md's "Environment note"), so these are structural checks only: the
files exist, stores.json is valid and shaped correctly, and the HTML/JS
reference each other and the fields stock.json actually has. Not a
substitute for opening the deployed page in a real browser once it's live.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HUB_DIR = Path(__file__).resolve().parent.parent / "hub-site"

sys.path.insert(0, str(HUB_DIR.parent))
from poslib.provision import HUB_REGISTRY_FILENAME  # noqa: E402


def test_all_expected_files_exist():
    for name in ("index.html", "style.css", "app.js", HUB_REGISTRY_FILENAME):
        assert (HUB_DIR / name).is_file(), f"missing hub-site/{name}"


def test_stores_json_is_valid_and_shaped_correctly():
    data = json.loads((HUB_DIR / HUB_REGISTRY_FILENAME).read_text(encoding="utf-8"))
    assert isinstance(data.get("hub_version"), int) and data["hub_version"] >= 1
    assert "stores" in data
    assert isinstance(data["stores"], list)
    assert len(data["stores"]) >= 1
    for store in data["stores"]:
        assert isinstance(store["name"], str) and store["name"]
        assert isinstance(store["url"], str) and store["url"].startswith("https://")
        # Either the plain public "stock.json" (price, no cost) or a
        # store-specific "stock-<token>.json" (cost, no price) - see
        # export_static.py's module docstring.
        assert re.fullmatch(r".*/stock(-[0-9a-f]+)?\.json", store["url"])


def test_index_html_references_its_own_assets():
    html = (HUB_DIR / "index.html").read_text(encoding="utf-8")
    assert 'href="style.css"' in html
    assert 'src="app.js"' in html
    assert 'id="search-box"' in html
    assert 'id="store-links"' in html
    assert 'id="results-body"' in html


def test_app_js_uses_the_real_stock_json_field_names():
    js = (HUB_DIR / "app.js").read_text(encoding="utf-8")
    for field in ("reference", "name", "stock", "price", "cost", "boxes", "boxes_remainder",
                  "avg_cost", "last_purchase_cost"):
        assert field in js, f"app.js never references stock file's {field!r} field"
    # Must fetch the exact permanent, tokenized filename - not the plain
    # "stores.json" this page used before 2026-08-31 (see
    # poslib/provision.py's "Cross-store hub registration" section for why).
    assert f'"{HUB_REGISTRY_FILENAME}"' in js
    assert "Promise.allSettled" in js, \
        "must use allSettled, not Promise.all, so one unreachable store doesn't hide the rest"


def test_store_link_regex_strips_both_plain_and_tokenized_stock_filenames():
    """
    Regression test: the store-link button must collapse a store's stock
    URL back to its dashboard root whether it's the plain public
    "stock.json" or a store-specific "stock-<token>.json" (see
    export_static.py's module docstring) - a regex that only matched the
    plain filename left the tokenized URL unchanged, sending the owner to
    a nonexistent path (Cloudflare's custom 404, "Not available remotely")
    instead of the store's dashboard.
    """
    js = (HUB_DIR / "app.js").read_text(encoding="utf-8")
    match = re.search(r'\.replace\((/[^,]+/), "/"\)', js)
    assert match, "expected a regex-based .replace(...) call stripping the stock filename"
    pattern = re.compile(match.group(1)[1:-1])

    plain = "https://example.pages.dev/stock.json"
    tokenized = "https://example.pages.dev/stock-f1cab0dac3a8e273d6293d71c808c877.json"
    assert pattern.sub("/", plain) == "https://example.pages.dev/"
    assert pattern.sub("/", tokenized) == "https://example.pages.dev/"
