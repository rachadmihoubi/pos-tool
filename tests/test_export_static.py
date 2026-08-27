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

    def test_customer_and_product_drilldowns_are_not_exported(self, cfg, monkeypatch, tmp_path):
        """
        Customer/product detail pages are deliberately excluded from the
        remote export - there's no natural recency cutoff for a customer
        or product (unlike a ticket or purchase, see
        `export_static.DRILLDOWN_WINDOW_DAYS`), so exporting them would
        only ever grow. Confirmed structurally rather than just by
        omission from PAGES (in case someone adds a route without
        updating this test's intent).
        """
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)

        for lang in LANGUAGES:
            lang_dir = out_dir / lang
            flat_files = {p.name for p in lang_dir.glob("*.html")}
            expected = ({f"{slug}.html" for slug in export_static.PAGES} |
                       {f"{name}.html" for name in export_static.TODAY_PRESET_FILES})
            assert flat_files == expected
            assert not (lang_dir / "customers").exists()
            assert not (lang_dir / "products").exists()

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

        stock_path = out_dir / "stock.json"
        assert stock_path.is_file()
        records = json.loads(stock_path.read_text(encoding="utf-8"))
        assert isinstance(records, list)
        if records:
            for key in ("item_no", "name", "stock", "price"):
                assert key in records[0]
            # No cost/margin/family/internal IDs - this one file is
            # reachable without an Access login (see
            # docs/superpowers/specs/2026-08-27-component5-hub-design.md).
            for forbidden in ("cost", "margin", "family_name", "item_id", "inactive"):
                assert forbidden not in records[0]

    def test_stock_json_excludes_inactive_items(self, cfg, metrics, monkeypatch, tmp_path):
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)

        catalog = metrics.catalog()
        if not catalog["inactive"].any():
            pytest.skip("no inactive items in this database to check against")
        inactive_item_nos = set(catalog.loc[catalog["inactive"], "item_no"].astype(str))

        out_dir = export_static.export(cfg)
        records = json.loads((out_dir / "stock.json").read_text(encoding="utf-8"))
        exported_item_nos = {r["item_no"] for r in records}
        assert not (inactive_item_nos & exported_item_nos)

    def test_headers_file_grants_cors_on_stock_json(self, cfg, monkeypatch, tmp_path):
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)

        headers_path = out_dir / "_headers"
        assert headers_path.is_file()
        content = headers_path.read_text(encoding="utf-8")
        assert "/stock.json" in content
        assert "Access-Control-Allow-Origin" in content


class TestStatusPayload:

    def test_handles_missing_cache_info_gracefully(self):
        from poslib.present import status_payload
        payload = status_payload({})
        assert payload["parsed_at"] == ""
        assert payload["source_modified"] == ""
        assert payload["row_counts"] == {}
