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
             "/products", "/suppliers", "/diagnostics", "/data-quality"]

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
            r"inventory|products|suppliers|diagnostics|findings|dataquality)"
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

    def test_home_redirects_to_the_default_page(self, client):
        response = client.get("/")
        assert response.status_code == 302
        assert "/today" in response.headers["Location"]

    def test_status_endpoint(self, client):
        data = client.get("/api/status").get_json()
        assert "parsed_at" in data and data["rows"] > 0


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
