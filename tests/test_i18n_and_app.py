"""
Tests for the three languages, the diagnostics engine and the dashboard.

The most valuable one here is test_all_three_languages_have_the_same_keys:
it is what stops a screen ever being half translated.
"""

from __future__ import annotations

import json
import re

import pytest

from poslib.diagnostics import Diagnostics
from poslib.i18n import (LANGUAGES, LOCALES_DIR, all_keys, check_locales,
                         get_translator, normalise)


class TestLocales:

    def test_all_three_files_exist_and_parse(self):
        for lang in LANGUAGES:
            path = LOCALES_DIR / f"{lang}.json"
            assert path.is_file(), f"missing {path}"
            json.loads(path.read_text(encoding="utf-8"))

    def test_all_three_languages_have_the_same_keys(self):
        """
        If this fails, some screen will show an English string in the middle
        of French or Arabic. Add the missing keys to the file named below.
        """
        missing = check_locales()
        problems = {lang: sorted(keys) for lang, keys in missing.items() if keys}
        assert not problems, (
            "these languages are missing translations:\n" +
            "\n".join(f"  {lang}: {keys}" for lang, keys in problems.items()))

    def test_placeholders_match_across_languages(self):
        """
        A translation that uses {name} where English uses {customer} would
        show the raw text instead of the number at exactly the wrong moment.
        """
        from poslib.i18n import _load_strings

        english = _load_strings("en")

        def dig(data, dotted):
            node = data
            for part in dotted.split("."):
                if not isinstance(node, dict) or part not in node:
                    return None
                node = node[part]
            return node

        problems = []
        for key in sorted(all_keys(english)):
            base = dig(english, key)
            if not isinstance(base, str):
                continue
            wanted = set(re.findall(r"\{(\w+)\}", base))
            for lang in ("fr", "ar"):
                other = dig(_load_strings(lang), key)
                if not isinstance(other, str):
                    continue
                got = set(re.findall(r"\{(\w+)\}", other))
                if got != wanted:
                    problems.append(f"{key} [{lang}]: expected {sorted(wanted)}, "
                                    f"found {sorted(got)}")
        assert not problems, "placeholder mismatch:\n  " + "\n  ".join(problems)

    def test_arabic_is_right_to_left(self):
        assert get_translator("ar").is_rtl
        assert get_translator("ar").direction == "rtl"
        assert not get_translator("en").is_rtl
        assert not get_translator("fr").is_rtl

    def test_arabic_strings_are_actually_arabic(self):
        t = get_translator("ar")
        for key in ("nav.today", "nav.customers", "diagnostics.title",
                    "inventory.dead_title"):
            text = t.get(key)
            assert any("؀" <= c <= "ۿ" for c in text), \
                f"{key} is not translated into Arabic: {text!r}"

    def test_number_formatting_per_language(self):
        assert get_translator("en").money(1234567) == "1,234,567 DZD"
        assert " " in get_translator("fr").number(1234567)
        assert get_translator("ar").money(1000).endswith("دج")

    def test_unknown_language_falls_back(self):
        assert normalise("de") == "en"
        assert normalise(None) == "en"
        assert normalise("AR-dz") == "ar"

    def test_missing_key_returns_the_key(self):
        assert get_translator("en").get("nope.not.here") == "nope.not.here"

    def test_missing_values_do_not_crash(self):
        import pandas as pd
        for lang in LANGUAGES:
            t = get_translator(lang)
            for bad in (None, float("nan"), pd.NaT):
                assert isinstance(t.money(bad), str)
                assert isinstance(t.date(bad), str)
                assert isinstance(t.percent(bad), str)
                assert isinstance(t.datetime(bad), str)


class TestDiagnostics:

    @pytest.fixture(scope="class")
    @classmethod
    def diag(cls, metrics, cfg):
        return Diagnostics(metrics, cfg)

    def test_it_produces_findings(self, diag):
        findings = diag.findings()
        assert len(findings) >= 8, "expected the rules engine to find things"

    def test_findings_are_split_into_working_and_failing(self, diag):
        assert diag.ranked("failing")
        assert diag.ranked("working")

    def test_ranked_by_money_not_severity(self, diag):
        """
        A medium problem worth 12 million matters more than a high one worth
        forty thousand, so money decides the order.
        """
        failing = [f for f in diag.ranked("failing") if f.order_hint == 0]
        amounts = [abs(f.money) for f in failing]
        assert amounts == sorted(amounts, reverse=True)

    def test_every_finding_renders_in_every_language(self, diag):
        for lang in LANGUAGES:
            t = get_translator(lang)
            rendered = diag.render(t)
            for f in rendered["failing"] + rendered["working"]:
                assert f["title"] and not f["title"].startswith("findings."), \
                    f"{f['id']} has no title in {lang}"
                assert f["statement"] and not f["statement"].startswith("findings.")
                assert f["action"] and not f["action"].startswith("findings.")
                assert "{" not in f["statement"], \
                    f"{f['id']} has an unfilled placeholder in {lang}: {f['statement']}"
                assert "{" not in f["action"]

    def test_failing_findings_carry_money_and_an_action(self, diag):
        for f in diag.ranked("failing"):
            t = get_translator("en")
            rendered = f.render(t)
            assert rendered["action"], f"{f.id} has no action"
            assert len(rendered["action"]) > 25, \
                f"{f.id} has a vague action: {rendered['action']}"

    def test_no_generic_advice(self, diag):
        """Every action must name something specific to do."""
        banned = ["consider optimizing", "consider optimising", "you may want to",
                  "it might be a good idea", "review your"]
        t = get_translator("en")
        for f in diag.findings():
            action = f.render(t)["action"].lower()
            for phrase in banned:
                assert phrase not in action, f"{f.id} gives generic advice"

    def test_urgent_is_a_short_list(self, diag):
        assert len(diag.urgent()) <= 5


class TestDashboard:

    @pytest.fixture(scope="class")
    @classmethod
    def client(cls):
        from app import app
        app.config["TESTING"] = True
        return app.test_client()

    PAGES = ["/today", "/trend", "/customers", "/receivables", "/inventory",
             "/products", "/suppliers", "/cash", "/diagnostics", "/data-quality"]

    @pytest.mark.parametrize("lang", LANGUAGES)
    def test_every_page_loads_in_every_language(self, client, lang):
        for path in self.PAGES:
            response = client.get(f"{path}?lang={lang}")
            assert response.status_code == 200, \
                f"{path} failed in {lang} with {response.status_code}"

    @pytest.mark.parametrize("lang", LANGUAGES)
    def test_no_untranslated_keys_leak_onto_a_page(self, client, lang):
        pattern = re.compile(
            r"\b(?:app|nav|common|today|trend|customers|segments|receivables|"
            r"inventory|products|suppliers|cash|diagnostics|findings|dataquality)"
            r"\.[a-z_][a-z_0-9.]*")
        for path in self.PAGES:
            body = client.get(f"{path}?lang={lang}").get_data(as_text=True)
            leaks = sorted(set(pattern.findall(body)))
            assert not leaks, f"{path} shows raw keys in {lang}: {leaks[:5]}"

    def test_arabic_pages_are_right_to_left(self, client):
        for path in self.PAGES:
            body = client.get(f"{path}?lang=ar").get_data(as_text=True)
            assert 'dir="rtl"' in body, f"{path} is not right-to-left in Arabic"

    def test_language_choice_is_remembered(self, client):
        response = client.get("/today?lang=ar")
        cookies = response.headers.getlist("Set-Cookie")
        assert any("pos_lang=ar" in c for c in cookies)

    @pytest.mark.parametrize("lang", LANGUAGES)
    @pytest.mark.parametrize("path", ["/trend", "/cash"])
    def test_date_range_picker_loads_in_every_language(self, client, lang, path):
        response = client.get(f"{path}?lang={lang}&start=2025-01-01&end=2025-06-30")
        assert response.status_code == 200, \
            f"{path} with a date range failed in {lang} with {response.status_code}"

    @pytest.mark.parametrize("path", ["/trend", "/cash"])
    def test_malformed_date_range_falls_back_gracefully(self, client, path):
        # A bad URL must never 500 - it should just fall back to the
        # page's usual default window.
        for query in ("start=not-a-date", "start=9999-01-01", "end=2025-13-40"):
            response = client.get(f"{path}?{query}")
            assert response.status_code == 200, \
                f"{path}?{query} returned {response.status_code}, not 200"

    @pytest.mark.parametrize("lang", LANGUAGES)
    def test_customer_drilldown_loads_in_every_language(self, client, metrics, lang):
        cs = metrics.customer_summary()
        if cs.empty:
            pytest.skip("no customers with measurable purchases in this database")
        customer_id = int(cs.iloc[0]["customer_id"])
        response = client.get(f"/customers/{customer_id}?lang={lang}")
        assert response.status_code == 200

    @pytest.mark.parametrize("lang", LANGUAGES)
    def test_product_drilldown_loads_in_every_language(self, client, metrics, lang):
        pm = metrics.product_margin()
        if pm.empty:
            pytest.skip("no products with sales in this database")
        item_id = int(pm.iloc[0]["item_id"])
        response = client.get(f"/products/{item_id}?lang={lang}")
        assert response.status_code == 200

    def test_customer_drilldown_404_for_unknown_id(self, client):
        assert client.get("/customers/999999999").status_code == 404

    def test_customer_drilldown_404_for_walkin(self, client):
        # The anonymous walk-in till is not a real customer - it has no
        # profile page, even though customer_id=1 exists in the database.
        assert client.get("/customers/1").status_code == 404

    def test_product_drilldown_404_for_unknown_id(self, client):
        assert client.get("/products/999999999").status_code == 404

    def test_home_redirects_to_the_default_page(self, client):
        response = client.get("/")
        assert response.status_code == 302
        assert "/today" in response.headers["Location"]

    def test_status_endpoint(self, client):
        data = client.get("/api/status").get_json()
        assert "parsed_at" in data and data["rows"] > 0

    def test_normal_requests_still_show_the_refresh_button(self, client):
        """
        is_static_export must default to False for every real request - a
        normal local page load must look exactly as it always did.
        """
        body = client.get("/today").get_data(as_text=True)
        assert 'id="refresh-btn"' in body
        assert 'href="?lang=en"' in body or 'href="?lang=fr"' in body

    def test_static_marker_hides_the_refresh_button(self, client):
        body = client.get("/today?__static__=1").get_data(as_text=True)
        assert 'id="refresh-btn"' not in body

    @pytest.mark.parametrize("query", [
        "", "?start=2025-01-01&end=2025-01-01", "?start=2025-01-01&end=2025-01-07",
        "?start=2025-01-01&end=2025-01-31",
    ])
    def test_today_accepts_single_day_and_range_queries(self, client, query):
        response = client.get(f"/today{query}")
        assert response.status_code == 200, f"/today{query} returned {response.status_code}"


class TestCompetitorPriceRoutes:
    """
    The competitor-price log is the first write path in the app. These
    exercise the full route (validation, redirect-with-error convention,
    round trip through the page) against an isolated owner.db, never the
    real one.
    """

    @pytest.fixture
    def client(self):
        from app import app
        app.config["TESTING"] = True
        return app.test_client()

    @pytest.fixture(autouse=True)
    def _isolated_owner_db(self, monkeypatch, tmp_path):
        from poslib.config import Config, get_config
        cfg = get_config()
        db_path = tmp_path / "owner.db"
        original = Config.path

        def patched(self, key, default=""):
            if key == "catalog.owner_data_db":
                return db_path
            return original(self, key, default)

        monkeypatch.setattr(cfg, "path", patched.__get__(cfg))
        yield db_path

    @pytest.fixture
    def item_id(self, metrics) -> int:
        return int(metrics.product_margin().iloc[0]["item_id"])

    def test_add_then_shows_on_product_page(self, client, item_id):
        response = client.post(f"/products/{item_id}/competitor-price", data={
            "competitor_name": "Test Rival", "price": "199.99",
            "observed_date": "2026-08-01", "note": "seen in store",
        })
        assert response.status_code == 302
        assert "form_error" not in response.headers["Location"]

        page = client.get(f"/products/{item_id}").get_data(as_text=True)
        assert "Test Rival" in page

    def test_missing_name_redirects_with_error(self, client, item_id):
        response = client.post(f"/products/{item_id}/competitor-price", data={
            "competitor_name": "", "price": "100", "observed_date": "2026-08-01",
        })
        assert response.status_code == 302
        assert "form_error=missing_name" in response.headers["Location"]

    def test_invalid_price_redirects_with_error(self, client, item_id):
        response = client.post(f"/products/{item_id}/competitor-price", data={
            "competitor_name": "Rival", "price": "not-a-number",
            "observed_date": "2026-08-01",
        })
        assert response.status_code == 302
        assert "form_error=invalid_price" in response.headers["Location"]

    def test_negative_price_redirects_with_error(self, client, item_id):
        response = client.post(f"/products/{item_id}/competitor-price", data={
            "competitor_name": "Rival", "price": "-5", "observed_date": "2026-08-01",
        })
        assert response.status_code == 302
        assert "form_error=invalid_price" in response.headers["Location"]

    def test_invalid_date_redirects_with_error(self, client, item_id):
        response = client.post(f"/products/{item_id}/competitor-price", data={
            "competitor_name": "Rival", "price": "100", "observed_date": "not-a-date",
        })
        assert response.status_code == 302
        assert "form_error=invalid_date" in response.headers["Location"]

    def test_add_then_delete_round_trip(self, client, item_id):
        from poslib import ownerdata
        from poslib.config import get_config

        client.post(f"/products/{item_id}/competitor-price", data={
            "competitor_name": "ToDelete", "price": "50", "observed_date": "2026-08-01",
        })
        prices = ownerdata.competitor_prices_for_item(get_config(), item_id)
        price_id = int(prices.iloc[0]["id"])

        response = client.post(f"/products/{item_id}/competitor-price/{price_id}/delete")
        assert response.status_code == 302

        page = client.get(f"/products/{item_id}").get_data(as_text=True)
        assert "ToDelete" not in page


class TestDigest:

    def test_it_builds_in_every_language(self, cfg):
        from poslib.digest import build_digest

        digest = build_digest(cfg)
        for lang in LANGUAGES:
            text = digest.text(lang)
            assert len(text) > 200
            assert "{" not in text, f"unfilled placeholder in the {lang} digest"

            page = digest.html(lang)
            assert page.startswith("<!doctype html>")
            assert f'lang="{lang}"' in page
            if lang == "ar":
                assert 'dir="rtl"' in page

    def test_whatsapp_template_variables(self, cfg):
        from poslib.digest import build_digest

        variables = build_digest(cfg).template_variables("ar")
        assert len(variables) == 5
        assert all(isinstance(v, str) and v for v in variables)


class TestChannels:

    def test_the_file_channel_can_never_be_switched_off(self, cfg):
        from poslib.channels import FileChannel

        channel = FileChannel(cfg)
        assert channel.always_on
        assert channel.enabled

    def test_a_channel_that_fails_reports_instead_of_raising(self, cfg):
        """
        The whole point of the channel design: one failing must not stop the
        others or bring the tool down.
        """
        from poslib.channels.base import Channel

        class Exploding(Channel):
            name = "telegram"          # borrow a name that has settings

            @property
            def enabled(self):
                return True

            def send(self, digest, language):
                raise RuntimeError("the internet is down")

        result = Exploding(cfg).deliver(digest=None, language="en")
        assert result.ok is False
        assert "internet is down" in result.error

    def test_channels_without_credentials_say_why(self, cfg):
        from poslib.channels import EmailChannel, TelegramChannel, WhatsAppChannel

        for cls in (EmailChannel, TelegramChannel, WhatsAppChannel):
            ready, why = cls(cfg).is_ready()
            if not ready:
                assert why and len(why) > 10, \
                    f"{cls.__name__} does not explain why it cannot run"

    def test_no_unofficial_whatsapp_library_is_used(self):
        """
        Driving WhatsApp Web gets the shop's number banned. Only Meta's
        official Cloud API is allowed here.
        """
        from pathlib import Path

        source = (Path(__file__).resolve().parent.parent /
                  "poslib" / "channels" / "whatsapp_channel.py").read_text(encoding="utf-8")
        for banned in ("selenium", "pywhatkit", "yowsup", "whatsapp_web",
                       "webdriver"):
            assert f"import {banned}" not in source
            assert f"from {banned}" not in source
        assert "graph.facebook.com" in source
