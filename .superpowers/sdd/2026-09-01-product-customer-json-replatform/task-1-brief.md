## Task 1: Extend `Translator` with the date/percent JS-format fields

**Files:**
- Modify: `poslib/i18n.py:137-151` (the existing `js_format()` method)
- Test: `tests/test_i18n.py`

**Interfaces:**
- Consumes: nothing new (uses `NUMBER_FORMATS`, `self.get(...)`, already in the file).
- Produces: `Translator.js_format()` now also returns `percent_format` (str, e.g. `"{value}%"`), `date_format` (str, e.g. `"{day}/{month}/{year}"`), `datetime_format` (str, e.g. `"{day}/{month}/{year} {hour}:{minute}"`), and `dash` (str, always `"—"` — the literal `Translator` already returns for every missing-value case, given as a JSON string so `remote-detail.js` never hardcodes the character). Task 2 consumes exactly these four new keys plus the three existing ones (`thousands`, `decimal`, `currency`, `money_format`).

- [ ] **Step 1: Write the failing test**

In `tests/test_i18n.py`, add (find the existing test class that already covers `js_format` — if there isn't one, add a new `TestJsFormat` class near the other `Translator` method tests):

```python
class TestJsFormatExtended:

    def test_includes_percent_date_datetime_and_dash(self):
        t = get_translator("en")
        fmt = t.js_format()
        assert fmt["percent_format"] == t.get("common.percent_format", value="{value}")
        assert fmt["date_format"] == t.get("common.date_format", day="{day}", month="{month}", year="{year}")
        assert fmt["datetime_format"] == t.get(
            "common.datetime_format", day="{day}", month="{month}", year="{year}",
            hour="{hour}", minute="{minute}")
        assert fmt["dash"] == "—"

    def test_still_includes_existing_fields(self):
        t = get_translator("fr")
        fmt = t.js_format()
        assert fmt["thousands"] == " "
        assert fmt["decimal"] == ","
        assert fmt["currency"] == t.get("common.currency")
        assert "money_format" in fmt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_i18n.py -k JsFormatExtended -v`
Expected: FAIL — `KeyError: 'percent_format'` (the new keys don't exist yet).

- [ ] **Step 3: Write minimal implementation**

In `poslib/i18n.py`, replace the body of `js_format` (currently lines 137-151):

```python
    def js_format(self) -> dict[str, str]:
        """
        The pieces of this language's number/money/percent/date formatting
        a client-side script needs to replicate number()/money()/percent()/
        date()/datetime() in the browser - used by the remote static
        export's custom-range picker (money/number only, historically) and
        by the product/customer JSON-replatformed detail shells (all six
        fields - see static/remote-detail.js).
        """
        fmt = NUMBER_FORMATS.get(self.lang, NUMBER_FORMATS[FALLBACK])
        return {
            "thousands": fmt["thousands"],
            "decimal": fmt["decimal"],
            "currency": self.get("common.currency"),
            "money_format": self.get("common.money_format",
                                     amount="{amount}", currency="{currency}"),
            "percent_format": self.get("common.percent_format", value="{value}"),
            "date_format": self.get("common.date_format",
                                    day="{day}", month="{month}", year="{year}"),
            "datetime_format": self.get("common.datetime_format",
                                        day="{day}", month="{month}", year="{year}",
                                        hour="{hour}", minute="{minute}"),
            "dash": "—",
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_i18n.py -k JsFormatExtended -v`
Expected: PASS

- [ ] **Step 5: Run the full i18n test file to check nothing else broke**

Run: `pytest tests/test_i18n.py -v`
Expected: all PASS (this method is additive-only, no existing key removed).

- [ ] **Step 6: Commit**

```bash
git add poslib/i18n.py tests/test_i18n.py
git commit -m "feat(i18n): expose percent/date/datetime format pieces to client-side JS"
```

---

