"""
Tests for export_static.py - the real local dashboard pages, rendered
statically for Cloudflare Pages.

These run against the real live database (via the `cfg` fixture already in
conftest.py, skipped if it's unreachable), but always write into a tmp_path
export directory - never the real remote-site/.
"""

from __future__ import annotations

import json

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

    def test_drilldown_pages_are_not_exported(self, cfg, monkeypatch, tmp_path):
        """
        The lean-payload boundary: individual customer/product detail
        pages are deliberately excluded from the frequent remote export -
        confirmed here structurally rather than just by omission from
        PAGES (in case someone adds a route without updating this test's
        intent).
        """
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)

        for lang in LANGUAGES:
            lang_dir = out_dir / lang
            files = {p.name for p in lang_dir.glob("*.html")}
            assert files == {f"{slug}.html" for slug in export_static.PAGES}


class TestStatusPayload:

    def test_handles_missing_cache_info_gracefully(self):
        from poslib.present import status_payload
        payload = status_payload({})
        assert payload["parsed_at"] == ""
        assert payload["source_modified"] == ""
        assert payload["row_counts"] == {}
