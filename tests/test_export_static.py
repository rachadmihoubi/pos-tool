"""
Tests for export_static.py - the real local dashboard pages, rendered
statically for Cloudflare Pages.

These run against the real live database (via the `cfg` fixture already in
conftest.py, skipped if it's unreachable), but always write into a tmp_path
export directory - never the real remote-site/.
"""

from __future__ import annotations

import json

import pytest

from poslib.config import Config
from poslib.i18n import LANGUAGES

import export_static

# A generous ceiling, not a target - each page includes real aggregate
# tables (the customer list, the product list, etc.), which are summaries,
# not the full multi-year receipt history - this just catches something
# going seriously wrong (e.g. raw ticket-level data leaking in).
_MAX_HTML_BYTES = 3_000_000
_MAX_STATUS_BYTES = 20_000


def _cfg_with_export_dir(monkeypatch, cfg: Config, tmp_path):
    original = Config.path

    def patched(self, key, default=""):
        if key == "remote.export_dir":
            return tmp_path / "remote-site"
        return original(self, key, default)

    monkeypatch.setattr(cfg, "path", patched.__get__(cfg))
    return cfg


class TestExport:

    def test_writes_every_page_in_every_language(self, cfg, monkeypatch, tmp_path):
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)

        for lang in LANGUAGES:
            for slug in export_static.PAGES:
                path = out_dir / lang / f"{slug}.html"
                assert path.is_file(), f"missing {path}"

    def test_writes_redirects_and_404_and_status(self, cfg, monkeypatch, tmp_path):
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)

        assert (out_dir / "_redirects").is_file()
        assert (out_dir / "404.html").is_file()
        assert (out_dir / "status.json").is_file()

        redirects = (out_dir / "_redirects").read_text(encoding="utf-8")
        assert "/today" in redirects

    def test_pages_are_reasonably_sized(self, cfg, monkeypatch, tmp_path):
        """
        Structural sanity ceiling - not the full multi-year receipt
        history leaking into a page.
        """
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)

        for slug in export_static.PAGES:
            size = (out_dir / "en" / f"{slug}.html").stat().st_size
            assert size < _MAX_HTML_BYTES, f"{slug}.html is {size:,} bytes"

    def test_status_json_is_valid_and_lean(self, cfg, monkeypatch, tmp_path):
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)

        status_path = out_dir / "status.json"
        data = json.loads(status_path.read_text(encoding="utf-8"))
        assert "parsed_at" in data
        assert "source_modified" in data
        assert "generated_at" in data
        assert status_path.stat().st_size < _MAX_STATUS_BYTES

    def test_no_template_errors_leak_onto_the_page(self, cfg, monkeypatch, tmp_path):
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)

        html = (out_dir / "en" / "today.html").read_text(encoding="utf-8")
        assert "Traceback" not in html
        assert "{{" not in html
        assert "Undefined" not in html

    def test_style_css_is_embedded_via_prefixed_static_link(self, cfg, monkeypatch, tmp_path):
        """
        Each page links to its own per-language-prefixed stylesheet
        (/en/static/style.css etc, via SCRIPT_NAME + url_for) rather than
        an unprefixed /static/style.css that wouldn't resolve correctly
        once the page lives under /en/, /fr/, /ar/.
        """
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)

        for lang in LANGUAGES:
            html = (out_dir / lang / "today.html").read_text(encoding="utf-8")
            assert f'href="/{lang}/static/style.css"' in html

    def test_style_css_file_actually_exists_per_language(self, cfg, monkeypatch, tmp_path):
        """
        Regression test: pages link to /<lang>/static/style.css, but
        Flask's dev server serves that dynamically from disk - a static
        export needs the actual file copied into each language's own
        static/ folder, or the page loads completely unstyled (a real bug
        that shipped: the file simply wasn't there).
        """
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)

        for lang in LANGUAGES:
            css_path = out_dir / lang / "static" / "style.css"
            assert css_path.is_file(), f"{css_path} was not written"
            assert css_path.stat().st_size > 1000, "style.css looks too small/empty"
            assert ".tile" in css_path.read_text(encoding="utf-8")

    def test_nav_links_are_prefixed_per_language(self, cfg, monkeypatch, tmp_path):
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)

        html = (out_dir / "fr" / "today.html").read_text(encoding="utf-8")
        assert 'href="/fr/cash"' in html
        assert 'href="/fr/customers"' in html

    def test_language_switcher_links_to_sibling_language_pages(self, cfg, monkeypatch, tmp_path):
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)

        html = (out_dir / "en" / "cash.html").read_text(encoding="utf-8")
        assert 'href="/fr/cash"' in html
        assert 'href="/ar/cash"' in html

    def test_refresh_button_and_excel_export_link_are_hidden(self, cfg, monkeypatch, tmp_path):
        """
        Neither works without a live server - the refresh button hits
        /api/refresh, the export link hits /export, and both must not
        appear on the static pages (per-page HTML, not just the JS guard).
        """
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)

        html = (out_dir / "en" / "today.html").read_text(encoding="utf-8")
        assert 'id="refresh-btn"' not in html
        assert "/export?lang=" not in html

    def test_customer_and_product_drilldowns_are_exported(self, cfg, metrics, monkeypatch, tmp_path):
        """
        Customer/product detail pages are exported in full, same as
        purchases - there are only ~1,600 products and ~660 customers in
        this database, the same catalog/roster order of magnitude as
        purchases (already exported without a window), not an
        unboundedly-growing series like tickets. The walk-in till
        account has no real profile and must be skipped.
        """
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)

        catalog = metrics.catalog()
        real_customer_ids = metrics.customers.loc[
            metrics.customers["customer_id"] != metrics.walkin_id, "customer_id"]

        for lang in LANGUAGES:
            products_dir = out_dir / lang / "products"
            customers_dir = out_dir / lang / "customers"
            assert products_dir.is_dir(), f"missing {products_dir}"
            assert customers_dir.is_dir(), f"missing {customers_dir}"

            product_files = {p.stem for p in products_dir.glob("*.html")}
            customer_files = {p.stem for p in customers_dir.glob("*.html")}
            assert product_files == {str(int(i)) for i in catalog["item_id"]}
            assert customer_files == {str(int(i)) for i in real_customer_ids}
            assert str(int(metrics.walkin_id)) not in customer_files

            html = (products_dir / f"{product_files.pop()}.html").read_text(encoding="utf-8")
            assert "Traceback" not in html
            assert "Undefined" not in html

    def test_ticket_and_purchase_drilldowns_are_exported(self, cfg, monkeypatch, tmp_path):
        """
        Unlike customer/product, ticket and purchase drill-downs ARE
        exported - bounded to a recent window for tickets (there's no
        cutoff for purchases; there are only a few hundred of them total).
        """
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)

        for lang in LANGUAGES:
            tickets_dir = out_dir / lang / "tickets"
            purchases_dir = out_dir / lang / "suppliers" / "purchases"
            assert tickets_dir.is_dir(), f"missing {tickets_dir}"
            assert purchases_dir.is_dir(), f"missing {purchases_dir}"
            ticket_files = list(tickets_dir.glob("*.html"))
            assert ticket_files, "expected at least one exported ticket in this database"
            for f in ticket_files:
                assert f.stem.isdigit(), f"unexpected ticket filename {f.name}"

    def test_today_presets_show_different_content(self, cfg, monkeypatch, tmp_path):
        """
        The whole point of pre-rendering separate files per preset: they
        must not all be the same page under different names.
        """
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)

        today_html = (out_dir / "en" / "today.html").read_text(encoding="utf-8")
        yesterday_html = (out_dir / "en" / "today-yesterday.html").read_text(encoding="utf-8")
        assert today_html != yesterday_html

    def test_daily_rollup_json_is_written_and_valid(self, cfg, monkeypatch, tmp_path):
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)

        daily_path = out_dir / "daily.json"
        assert daily_path.is_file()
        records = json.loads(daily_path.read_text(encoding="utf-8"))
        assert isinstance(records, list)
        if records:
            for key in ("date", "revenue", "cash_revenue", "on_account_revenue",
                        "gross_profit", "tickets"):
                assert key in records[0]
            dates = [r["date"] for r in records]
            assert dates == sorted(dates), "daily.json must be chronological for the client-side slicer"

    def test_stock_json_is_written_and_valid(self, cfg, monkeypatch, tmp_path):
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)

        token = cfg.get("remote.stock_json_token", "")
        stock_path = out_dir / (f"stock-{token}.json" if token else "stock.json")
        assert stock_path.is_file()
        records = json.loads(stock_path.read_text(encoding="utf-8"))
        assert isinstance(records, list)
        if records:
            # A configured token switches the file to cost instead of
            # price (see export_static.py's module docstring) - the two
            # never appear together, and neither ever appears with the
            # other money/internal fields this file must stay lean on.
            money_key, other_money_key = ("cost", "price") if token else ("price", "cost")
            for key in ("reference", "name", "stock", "boxes", "boxes_remainder", money_key):
                assert key in records[0]
            for forbidden in ("margin", "family_name", "item_id", "item_no", "inactive",
                              "qty_per_parcel", other_money_key):
                assert forbidden not in records[0]

    def test_stock_json_has_purchase_costs_only_when_tokenized(self, cfg, monkeypatch, tmp_path):
        """
        avg_cost/last_purchase_cost are cost figures, same sensitivity as
        the existing "cost" field - they may only ever appear on the
        unguessable stock-<token>.json filename, never on the public
        stock.json.
        """
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)

        token = cfg.get("remote.stock_json_token", "")
        stock_path = out_dir / (f"stock-{token}.json" if token else "stock.json")
        records = json.loads(stock_path.read_text(encoding="utf-8"))
        if not records:
            pytest.skip("no active items to check")
        if token:
            assert "avg_cost" in records[0]
            assert "last_purchase_cost" in records[0]
        else:
            assert "avg_cost" not in records[0]
            assert "last_purchase_cost" not in records[0]

    def test_stock_json_excludes_inactive_items(self, cfg, metrics, monkeypatch, tmp_path):
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)

        catalog = metrics.catalog()
        if not catalog["inactive"].any():
            pytest.skip("no inactive items in this database to check against")
        inactive_names = set(catalog.loc[catalog["inactive"], "item_name"])

        token = cfg.get("remote.stock_json_token", "")
        out_dir = export_static.export(cfg)
        stock_path = out_dir / (f"stock-{token}.json" if token else "stock.json")
        records = json.loads(stock_path.read_text(encoding="utf-8"))
        exported_names = {r["name"] for r in records}
        assert not (inactive_names & exported_names)

    def test_headers_file_grants_cors_on_stock_json(self, cfg, monkeypatch, tmp_path):
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)

        token = cfg.get("remote.stock_json_token", "")
        stock_filename = f"stock-{token}.json" if token else "stock.json"

        headers_path = out_dir / "_headers"
        assert headers_path.is_file()
        content = headers_path.read_text(encoding="utf-8")
        assert f"/{stock_filename}" in content
        assert "Access-Control-Allow-Origin" in content


class TestProductsCustomersJson:

    def test_products_json_has_every_item_keyed_by_id(self, cfg, monkeypatch, tmp_path):
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)
        data = json.loads((out_dir / "products.json").read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert len(data) > 0
        some_id = next(iter(data))
        entry = data[some_id]
        assert set(entry.keys()) == {"summary", "family", "sales_history",
                                      "purchase_history", "competitor_prices"}
        assert "item_name" in entry["summary"]
        # JSON round-trips cleanly with no NaN/Infinity tokens (json.loads
        # would already have raised on those with default settings, but
        # assert explicitly so a future change to allow_nan doesn't silently
        # let one back in).
        raw = (out_dir / "products.json").read_text(encoding="utf-8")
        assert "NaN" not in raw
        assert "Infinity" not in raw
        # Cloudflare Pages rejects any single asset over 25MB
        # (poslib/remote.py's _MAX_FILE_SIZE_BYTES - a rejection that fails
        # SILENTLY, only a log.warning, see remote.py:395-398). Reviewed
        # 2026-09-01: today's real data lands around 9-10MB, but
        # sales_history/purchase_history are capped at 200 rows/product
        # (not today's actual row count), so the bounded worst case is
        # materially higher - this assertion is a canary for growth, not a
        # today-only sanity check. If this ever fails, split products.json
        # into per-entity files rather than raising the cap blindly.
        assert len(raw.encode("utf-8")) < 20 * 1024 * 1024

    def test_customers_json_has_every_customer_keyed_by_id_excluding_walkin(
            self, cfg, monkeypatch, tmp_path):
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)
        data = json.loads((out_dir / "customers.json").read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert len(data) > 0
        some_id = next(iter(data))
        entry = data[some_id]
        assert set(entry.keys()) == {"summary", "receivable", "purchases", "payments"}
        assert "customer_name" in entry["summary"]
        raw = (out_dir / "customers.json").read_text(encoding="utf-8")
        assert len(raw.encode("utf-8")) < 20 * 1024 * 1024
        from poslib.metrics import Metrics
        from poslib.etl import ETL
        etl = ETL(cfg)
        conn = etl.connect()
        try:
            m = Metrics(conn, cfg)
            assert str(int(m.walkin_id)) not in data
        finally:
            conn.close()

    def test_products_json_datetimes_are_iso_strings(self, cfg, monkeypatch, tmp_path):
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)
        data = json.loads((out_dir / "products.json").read_text(encoding="utf-8"))
        # Find any entry with at least one sales_history row and check its
        # ticket_time is a plain ISO-ish string, not a dict/list (which is
        # what an un-cleaned Timestamp would serialize to via a bad default).
        for entry in data.values():
            if entry["sales_history"]:
                ts = entry["sales_history"][0]["ticket_time"]
                assert isinstance(ts, str)
                assert ts[4] == "-" and ts[7] == "-"
                break
        else:
            pytest.fail("no product with sales_history found to check date serialization")


class TestProductCustomerShells:

    def test_writes_one_shell_per_language_not_per_entity(self, cfg, monkeypatch, tmp_path):
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)
        for lang in LANGUAGES:
            assert (out_dir / lang / "product.html").is_file()
            assert (out_dir / lang / "customer.html").is_file()
        # The old per-entity trees still exist too (parallel path, not
        # removed until Task 6) - both must be true right now.
        assert (out_dir / "en" / "products").is_dir()
        assert (out_dir / "en" / "customers").is_dir()

    def test_shell_contains_static_labels_and_fetch_call(self, cfg, monkeypatch, tmp_path):
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)
        html = (out_dir / "en" / "product.html").read_text(encoding="utf-8")
        assert "fetch(\"../products.json\")" in html
        assert "id=\"rd-item-name\"" in html
        html = (out_dir / "en" / "customer.html").read_text(encoding="utf-8")
        assert "fetch(\"../customers.json\")" in html

    def test_catalog_links_point_at_shell_when_static_export(self, cfg, monkeypatch, tmp_path):
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)
        html = (out_dir / "en" / "catalog.html").read_text(encoding="utf-8")
        assert "product.html?id=" in html
        assert "/products/" not in html


class TestStatusPayload:

    def test_handles_missing_cache_info_gracefully(self):
        from poslib.present import status_payload
        payload = status_payload({})
        assert payload["parsed_at"] == ""
        assert payload["source_modified"] == ""
        assert payload["row_counts"] == {}
