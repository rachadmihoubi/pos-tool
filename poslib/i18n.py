"""
i18n.py - the three languages the tool speaks: English, French and Arabic.

RULES THIS FILE ENFORCES
------------------------
1. No text that a person reads is ever written in the code. Every label,
   heading, chart axis, finding, Excel column and digest sentence comes from
   locales/en.json, locales/fr.json or locales/ar.json.

2. The three files must contain exactly the same keys. There is a test that
   fails the build if they drift apart, so a screen can never end up half
   translated.

3. Arabic reads right to left. That is not just the text - the whole page
   flips, and so do the charts. `is_rtl` drives that.

4. Numbers stay in Western digits (1 234 567), which is what is used in
   Algeria, in all three languages. Only the separators change.
"""

from __future__ import annotations

import datetime
import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from poslib.paths import app_root

log = logging.getLogger(__name__)

LOCALES_DIR = app_root() / "locales"

# The languages the tool ships with. English is the fallback for anything
# that is somehow missing.
LANGUAGES = ("en", "fr", "ar")
FALLBACK = "en"

RTL_LANGUAGES = {"ar"}

# How each language is offered in the language switcher, written in itself.
LANGUAGE_NAMES = {
    "en": "English",
    "fr": "Français",
    "ar": "العربية",
}

# Thousands and decimal separators per language.
NUMBER_FORMATS = {
    "en": {"thousands": ",", "decimal": "."},
    "fr": {"thousands": " ", "decimal": ","},   # narrow no-break space
    "ar": {"thousands": " ", "decimal": ","},
}

# Month and weekday names come from the locale files, not from the system,
# so the tool reads the same on any machine.


class Translator:
    """
    Looks up text in one language.

        t = Translator("fr")
        t("today.title")                       -> "Aujourd'hui"
        t("today.vs_yesterday", value="12 %")  -> "12 % contre hier"
    """

    def __init__(self, lang: str, strings: dict[str, Any],
                 fallback_strings: dict[str, Any]):
        self.lang = lang
        self._strings = strings
        self._fallback = fallback_strings

    # -- lookup ------------------------------------------------------------

    def __call__(self, key: str, **params: Any) -> str:
        return self.get(key, **params)

    def get(self, key: str, **params: Any) -> str:
        """
        Find the text for a key. Falls back to English, and as a last resort
        returns the key itself so a missing string is obvious on screen
        rather than showing an empty space.
        """
        text = _dig(self._strings, key)
        if text is None:
            text = _dig(self._fallback, key)
            if text is not None and self.lang != FALLBACK:
                log.debug("Missing %s translation for %r", self.lang, key)
        if text is None:
            log.warning("No translation anywhere for %r", key)
            return key

        if not isinstance(text, str):
            return str(text)

        if params:
            try:
                return text.format(**params)
            except (KeyError, IndexError, ValueError):
                # A bad placeholder must never take a screen down.
                log.warning("Could not fill in %r for language %s", key, self.lang)
                return text
        return text

    def has(self, key: str) -> bool:
        return _dig(self._strings, key) is not None or _dig(self._fallback, key) is not None

    # -- direction ---------------------------------------------------------

    @property
    def is_rtl(self) -> bool:
        return self.lang in RTL_LANGUAGES

    @property
    def direction(self) -> str:
        return "rtl" if self.is_rtl else "ltr"

    @property
    def start(self) -> str:
        """"left" or "right" - whichever edge a line of text starts at."""
        return "right" if self.is_rtl else "left"

    @property
    def end(self) -> str:
        return "left" if self.is_rtl else "right"

    @property
    def language_name(self) -> str:
        return LANGUAGE_NAMES.get(self.lang, self.lang)

    # -- numbers -----------------------------------------------------------

    def js_format(self) -> dict[str, str]:
        """
        The pieces of this language's number/money formatting a client-side
        script needs to replicate `number()`/`money()` in the browser -
        used only by the remote static export's custom-range picker, which
        has no server left to ask once it's deployed.
        """
        fmt = NUMBER_FORMATS.get(self.lang, NUMBER_FORMATS[FALLBACK])
        return {
            "thousands": fmt["thousands"],
            "decimal": fmt["decimal"],
            "currency": self.get("common.currency"),
            "money_format": self.get("common.money_format",
                                     amount="{amount}", currency="{currency}"),
        }

    def number(self, value: Any, decimals: int = 0) -> str:
        """A plain number with the right separators for this language."""
        if value is None:
            return "—"
        try:
            v = float(value)
        except (TypeError, ValueError):
            return "—"
        if v != v:                       # not a number
            return "—"
        if v in (float("inf"), float("-inf")):
            return "∞"

        fmt = NUMBER_FORMATS.get(self.lang, NUMBER_FORMATS[FALLBACK])
        s = f"{v:,.{decimals}f}"
        # Swap the Python defaults for this language's separators.
        s = s.replace(",", "\x00").replace(".", fmt["decimal"]).replace("\x00", fmt["thousands"])
        return s

    def money(self, value: Any, decimals: int = 0, symbol: bool = True) -> str:
        """An amount of money, with the currency after it."""
        if value is None:
            return "—"
        try:
            v = float(value)
        except (TypeError, ValueError):
            return "—"
        if v != v:
            return "—"
        text = self.number(v, decimals)
        if not symbol:
            return text
        return self.get("common.money_format", amount=text,
                        currency=self.get("common.currency"))

    def money_short(self, value: Any) -> str:
        """
        A big amount shortened so it fits on a tile: 12.2M, 843k.
        Used only where the exact figure is also available on hover.
        """
        if value is None:
            return "—"
        try:
            v = float(value)
        except (TypeError, ValueError):
            return "—"
        if v != v:
            return "—"

        sign = "-" if v < 0 else ""
        a = abs(v)
        if a >= 1_000_000:
            return f"{sign}{self.number(a / 1_000_000, 1)}{self.get('common.millions')}"
        if a >= 1_000:
            return f"{sign}{self.number(a / 1_000, 1)}{self.get('common.thousands')}"
        return f"{sign}{self.number(a, 0)}"

    def percent(self, value: Any, decimals: int = 1) -> str:
        """A fraction (0.085) shown as a percentage (8.5 %)."""
        if value is None:
            return "—"
        try:
            v = float(value)
        except (TypeError, ValueError):
            return "—"
        if v != v:
            return "—"
        return self.get("common.percent_format", value=self.number(v * 100, decimals))

    def signed_percent(self, value: Any, decimals: int = 1) -> str:
        """A change, with a + or - in front so the direction is obvious."""
        if value is None:
            return "—"
        try:
            v = float(value)
        except (TypeError, ValueError):
            return "—"
        if v != v:
            return "—"
        sign = "+" if v > 0 else ""
        return sign + self.percent(v, decimals)

    # -- dates -------------------------------------------------------------

    def date(self, value: Any) -> str:
        d = _as_date(value)
        if d is None:
            return "—"
        return self.get("common.date_format",
                        day=f"{d.day:02d}", month=f"{d.month:02d}", year=d.year)

    def datetime(self, value: Any) -> str:
        d = _as_datetime(value)
        if d is None:
            return "—"
        return self.get("common.datetime_format",
                        day=f"{d.day:02d}", month=f"{d.month:02d}", year=d.year,
                        hour=f"{d.hour:02d}", minute=f"{d.minute:02d}")

    def month_name(self, month_no: int) -> str:
        return self.get(f"months.{int(month_no)}")

    def month_label(self, value: Any) -> str:
        """"March 2026" - used on chart axes."""
        d = _as_date(value)
        if d is None:
            return "—"
        return f"{self.month_name(d.month)} {d.year}"

    def weekday_name(self, weekday_no: int) -> str:
        """0 is Monday, matching how the data is stored."""
        return self.get(f"weekdays.{int(weekday_no)}")

    def days_ago(self, days: Any) -> str:
        if days is None:
            return "—"
        try:
            n = int(days)
        except (TypeError, ValueError):
            return "—"
        if n <= 0:
            return self.get("common.today")
        if n == 1:
            return self.get("common.yesterday")
        return self.get("common.days_ago", days=self.number(n))

    def relative_hours(self, hours: Any) -> str:
        """"3 minutes ago", "2 hours ago" - used for the refresh time."""
        if hours is None:
            return "—"
        try:
            h = float(hours)
        except (TypeError, ValueError):
            return "—"
        if h < 1 / 60:
            return self.get("common.just_now")
        if h < 1:
            return self.get("common.minutes_ago", minutes=self.number(round(h * 60)))
        if h < 24:
            return self.get("common.hours_ago", hours=self.number(round(h, 1), 1))
        return self.get("common.days_ago", days=self.number(round(h / 24)))


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _dig(data: dict[str, Any], dotted: str) -> Any:
    """Find a value by dotted key, e.g. "today.title"."""
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _is_missing(value: Any) -> bool:
    """
    True for anything that means "no value".

    This has to catch pandas' NaT, which is genuinely awkward: it passes an
    isinstance check against datetime, so it looks like a real date right up
    until you ask it for the day of the month and get "not a number" back.
    The reliable test is that a missing value is never equal to itself.
    """
    if value is None:
        return True
    try:
        return bool(value != value)
    except (TypeError, ValueError):
        return False


def _as_date(value: Any) -> datetime.date | None:
    if _is_missing(value):
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", value)
        if m:
            try:
                return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                return None
    # pandas Timestamp and anything else that can turn itself into a date
    to_pydatetime = getattr(value, "to_pydatetime", None)
    if callable(to_pydatetime):
        try:
            return to_pydatetime().date()
        except (ValueError, TypeError):
            return None
    return None


def _as_datetime(value: Any) -> datetime.datetime | None:
    if _is_missing(value):
        return None
    if isinstance(value, datetime.datetime):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
                    "%Y-%m-%d"):
            try:
                return datetime.datetime.strptime(value[:19], fmt)
            except ValueError:
                continue
        return None
    to_pydatetime = getattr(value, "to_pydatetime", None)
    if callable(to_pydatetime):
        try:
            return to_pydatetime()
        except (ValueError, TypeError):
            return None
    d = _as_date(value)
    return datetime.datetime(d.year, d.month, d.day) if d else None


@lru_cache(maxsize=8)
def _load_strings(lang: str) -> dict[str, Any]:
    path = LOCALES_DIR / f"{lang}.json"
    if not path.is_file():
        log.error("Missing language file: %s", path)
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log.error("Language file %s has a syntax error: %s", path, exc)
        return {}


@lru_cache(maxsize=8)
def get_translator(lang: str) -> Translator:
    """Get the translator for a language, loading its file once."""
    lang = normalise(lang)
    return Translator(lang, _load_strings(lang), _load_strings(FALLBACK))


def normalise(lang: str | None) -> str:
    """Turn anything a browser or config might say into one of our three."""
    if not lang:
        return FALLBACK
    code = str(lang).strip().lower().replace("_", "-").split("-")[0]
    return code if code in LANGUAGES else FALLBACK


def available_languages() -> list[dict[str, str]]:
    """The list the language switcher is built from."""
    return [
        {"code": code, "name": LANGUAGE_NAMES[code],
         "rtl": code in RTL_LANGUAGES}
        for code in LANGUAGES
    ]


def reload_strings() -> None:
    """Forget the loaded files - used by the tests and after editing a locale."""
    _load_strings.cache_clear()
    get_translator.cache_clear()


def all_keys(data: dict[str, Any], prefix: str = "") -> set[str]:
    """Every dotted key in a locale file. Used by the consistency test."""
    keys: set[str] = set()
    for k, v in data.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            keys |= all_keys(v, full)
        else:
            keys.add(full)
    return keys


def check_locales() -> dict[str, set[str]]:
    """
    Compare the three files and report anything one has that another lacks.
    An empty result means all three are complete.
    """
    sets = {lang: all_keys(_load_strings(lang)) for lang in LANGUAGES}
    everything: set[str] = set()
    for s in sets.values():
        everything |= s
    return {lang: everything - keys for lang, keys in sets.items()}
