# Product/Customer Static-Export Replatform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static export's per-product/per-customer/per-language pre-rendered HTML pages (1,631 products + 683 customers, × 3 languages = ~6,942 files today) with two JSON payloads (`products.json`, `customers.json`, one file each, no language variant) plus one thin HTML shell per language per entity type (`product.html`, `customer.html` — 6 files total instead of ~6,942), so the real production push failures documented in `CLAUDE.md`'s "Still open" / "Full-export push reliability fix" sections stop being caused by sheer file count, while every number and label a shop owner currently sees on `/products/<id>` or `/customers/<id>` remotely stays byte-for-byte the same.

**Architecture:** The local live dashboard (`app.py`'s `/products/<id>` and `/customers/<id>` routes, `templates/product_detail.html`, `templates/customer_detail.html`) is completely untouched — it keeps rendering exactly as it does today. Only `export_static.py`'s remote/Cloudflare-Pages export path changes: instead of looping over every product/customer × 3 languages and calling `render_template("product_detail.html"/"customer_detail.html", ...)` per entity, it (a) writes the *same* per-entity data (`Metrics.product_profile()`/`customer_profile()`, cleaned to JSON-safe types exactly like `app.py`'s existing `rows()`/`row_dict()` already do) into two JSON files, all entities in one file each, and (b) renders two new thin Jinja shell templates ONCE per language — `templates/product_shell.html`, `templates/customer_shell.html` — whose static labels/headings/table structure are server-rendered exactly like today (same `t(...)` calls, same locale files, zero new translation-string duplication), but whose per-entity numbers come from a `<script>` that fetches the JSON file, looks up `?id=`, and fills in the DOM — following the exact same "fetch JSON, render in vanilla JS" pattern `hub-site/app.js` already uses live, and the exact same "JS mirrors `Translator`'s number/money formatting" pattern `templates/_daterange_remote.html` already uses live via `Translator.js_format()`.

**Tech Stack:** Flask/Jinja2 (existing), vanilla JS (no new library — matches this repo's existing static-export JS, `hub-site/app.js` and `_daterange_remote.html`'s inline script), pandas/`poslib/metrics.py` (existing, unmodified — no financial computation changes anywhere in this plan).

**Spec:** This plan implements Stage 2 of the LLM-council verdict prompt the user supplied (no separate spec file — the prompt itself is the spec; its exact guardrails are reproduced in Global Constraints below). Read `CLAUDE.md`'s "Customer distribution" section, specifically "Remote product/customer drill-downs now exported in full" and "Full-export push reliability fix" (2026-08-31 entries), before starting any task — they document the current architecture, the real prior bugs (`_redirects` deployment quirk, upload batching/retry), and why this file count is a real production problem, not a hypothetical one.

## Global Constraints

- **No backend, API server, Pages Functions, or live tunnel.** Static files on Cloudflare Pages only, zero server-side code added anywhere.
- **Do not modify `poslib/remote.py`'s upload mechanics** (batching, retry, check-missing-hashes) — already fixed 2026-08-31, out of scope. Many small-ish JSON/HTML files must simply flow through its existing `_iter_export_files`/`push_remote` unchanged.
- **Do not touch `stock.json` / `stock-<token>.json`** format, the `remote.stock_json_token` gating mechanism, or any Cloudflare Access-app config, even incidentally.
- **Do not touch tickets or purchases export** (`ticket_ids`/`purchase_ids` loops in `export_static.py`) — already bounded/cheap, leave alone.
- **Do not touch `today`/dashboard summary/`daily.json`** — already JSON-driven, leave alone.
- **Do not touch `poslib/channels/*` or digest/notification code**, even opportunistically.
- **No new tables, pages, or features.** The client-side renderer must reproduce `templates/product_detail.html` / `templates/customer_detail.html`'s existing output **exactly** (every tile, every table column, every empty-state message, every banner condition) — this is a payload-transport change, not a UX redesign. The pre-existing (and already slightly broken-when-remote) competitor-price add/delete form on the product page is reproduced verbatim, non-functional POST target included — fixing that is out of scope.
- **No multi-tenant infrastructure, no general "future stores" abstraction, no plugin system, no speculative caching/offline-mode/new build tooling.** Do not introduce Node.js or any JS build step — this repo deliberately removed its Node.js/wrangler dependency (see `CLAUDE.md`'s "Environment note"); the new JS is a plain `<script>` file, no bundler, no test runner.
- **This plan touches zero financial computation.** `Metrics.product_profile()` and `Metrics.customer_profile()` are read, never modified; every number in the JSON is the exact same value `row_dict()`/`rows()` already produce for the live dashboard today, just serialized differently. **No Opus financial-logic review gate is triggered by this plan** — only the standard installer/Access-config-adjacent mandatory Opus *plan* review already required before implementation starts (see below), which is a different, already-scheduled gate.
- **Full `pytest tests -q` (including `tests/test_export_static.py`, ~4+ minutes against the real database on this machine) must pass before any task in this plan is considered done**, and again at the very end.
- **Migration order is fixed by the parent task**: land the JSON+shell export path as a *parallel* path first (old per-entity HTML loops stay in `export_static.py`, fully working, not deleted) — verify the new path live against a real store — only then delete the old loops. Do not combine landing and deleting in one task/commit.
- **Measure and log file count + total size + push duration before and after**, the same way the "before" baseline below was captured — this is a hypothesis to confirm, not an assumed fact.

**Baseline already measured** (this machine, live database, 2026-09-01, via a direct `export_static.export(cfg)` call into a scratch directory): **12,555 files, ~232.6 MB** for a full export (1,631 items + 683 customers × 3 languages, plus tickets/purchases/pages/presets). Use this exact export run's shape (same database, same machine) for the "after" comparison so the numbers are apples-to-apples.

---

## File Structure

- **Modify: `export_static.py`** — add two new builder functions (`_products_json_payload`, `_customers_json_payload`) and two new render calls (one per language, for the two new shells) alongside the *existing* per-entity loops (not replacing them yet — see Task 3/6 split).
- **Create: `templates/product_shell.html`** — thin shell, extends `base.html`, same static structure as `product_detail.html` but with `id="..."` placeholders instead of Jinja variable interpolation for per-entity values, plus one `<script src="{{ url_for('static', filename='remote-detail.js') }}">` and one small inline `<script>` bootstrapping it with this page's `t.js_format()`-style config and the entity type (`"product"`).
- **Create: `templates/customer_shell.html`** — same idea, mirroring `customer_detail.html`.
- **Create: `static/remote-detail.js`** — shared vanilla-JS module: (a) number/money/percent/signed-percent/date/datetime formatters mirroring `poslib/i18n.py`'s `Translator` methods exactly (extends the existing `js_format()` precedent from `templates/_daterange_remote.html`), (b) a `renderProduct(data, formatters, strings, dom)` and `renderCustomer(data, formatters, strings, dom)` function that fill in the shell's DOM from the fetched JSON, mirroring `hub-site/app.js`'s "fetch JSON, build DOM" pattern.
- **Modify: `poslib/i18n.py`** — extend `Translator.js_format()` (or add a new method, decided in Task 1) to also expose `percent_format`, `date_format`, `datetime_format`, and the literal dash string used for missing values, so `remote-detail.js` never hardcodes a format string that could drift from `locales/*.json`.
- **Modify: `templates/catalog.html`, `templates/products.html`, `templates/receivables.html`, `templates/customers.html`** — the 9 existing `url_for('page_product'/'page_customer', ...)` links gain an `is_static_export`-conditional branch pointing at the new shell URL (`product.html?id=...` / `customer.html?id=...`) instead, exactly mirroring the existing `is_static_export`-conditional pattern already in `templates/base.html`'s language switcher.
- **Modify: `tests/test_export_static.py`** — new tests for the JSON payload shape/counts and the new shell files' presence and static-label content.
- **Modify: `tests/test_i18n.py`** — new tests for the extended `js_format()` fields.
- Untouched, verify by inspection only: `templates/product_detail.html`, `templates/customer_detail.html`, `app.py`'s `page_product`/`page_customer` routes — these remain exactly as-is, serving the local live dashboard only.

---

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

## Task 2: `static/remote-detail.js` — shared formatter + DOM renderer

**Files:**
- Create: `static/remote-detail.js`
- Test: manual golden-value verification (see Step 4 below) — **no automated JS test is added**, deliberately: this repo has no Node.js/JS test runner (see Global Constraints — introducing one is out of scope), and a Python-side test cannot execute browser JS. This is a known, accepted gap; Task 5's live verification is what actually confirms formatting parity against the real local dashboard's Python output.

**Interfaces:**
- Consumes: the object `Translator.js_format()` produces (Task 1) — `{thousands, decimal, currency, money_format, percent_format, date_format, datetime_format, dash}` — passed in from the shell template as an inline `<script>`-embedded JSON literal (see Task 4), not fetched separately.
- Produces (global functions, attached to `window.RemoteDetail`, no module system — matches this repo's existing plain-`<script>` style):
  - `RemoteDetail.formatNumber(fmt, value, decimals=0)` → string
  - `RemoteDetail.formatMoney(fmt, value)` → string
  - `RemoteDetail.formatPercent(fmt, value, decimals=1)` → string
  - `RemoteDetail.formatSignedPercent(fmt, value, decimals=1)` → string
  - `RemoteDetail.formatDate(fmt, isoString)` → string
  - `RemoteDetail.formatDateTime(fmt, isoString)` → string
  - `RemoteDetail.renderProduct(data, fmt, strings, dom)` → void, fills in a product shell's DOM from one entry of `products.json`. `strings` carries the small set of dynamic translation pieces a shell can't resolve server-side (see Task 4 Step 1).
  - `RemoteDetail.renderCustomer(data, fmt, strings, dom)` → void, fills in a customer shell's DOM from one entry of `customers.json`. `strings` carries `segmentLabels`/`creditRiskLabels` lookup maps plus the owes-note template (see Task 4 Step 2).

- [ ] **Step 1: Write the formatter functions**

Create `static/remote-detail.js`:

```javascript
// remote-detail.js - shared formatting + DOM-fill logic for the JSON-backed
// remote product/customer detail shells (templates/product_shell.html,
// templates/customer_shell.html). Mirrors poslib/i18n.py's Translator
// methods exactly - see that file for the Python source of truth. Follows
// the same "JS replicates server-side number/money formatting" pattern
// already live in templates/_daterange_remote.html (Translator.js_format()),
// and the same "fetch JSON, fill in the DOM" pattern already live in
// hub-site/app.js.
(function (global) {
  "use strict";

  function isMissing(v) {
    return v === null || v === undefined || (typeof v === "number" && isNaN(v));
  }

  function formatNumber(fmt, value, decimals) {
    decimals = decimals || 0;
    if (isMissing(value)) { return fmt.dash; }
    var v = Number(value);
    if (isNaN(v)) { return fmt.dash; }
    if (v === Infinity || v === -Infinity) { return "∞"; }
    var s = Math.abs(v).toFixed(decimals);
    var parts = s.split(".");
    parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, "\x00");
    var joined = parts.join(fmt.decimal).split("\x00").join(fmt.thousands);
    return (v < 0 ? "-" : "") + joined;
  }

  function formatMoney(fmt, value) {
    if (isMissing(value)) { return fmt.dash; }
    var v = Number(value);
    if (isNaN(v)) { return fmt.dash; }
    var text = formatNumber(fmt, v, 0);
    return fmt.money_format.replace("{amount}", text).replace("{currency}", fmt.currency);
  }

  function formatPercent(fmt, value, decimals) {
    decimals = decimals === undefined ? 1 : decimals;
    if (isMissing(value)) { return fmt.dash; }
    var v = Number(value);
    if (isNaN(v)) { return fmt.dash; }
    return fmt.percent_format.replace("{value}", formatNumber(fmt, v * 100, decimals));
  }

  function formatSignedPercent(fmt, value, decimals) {
    if (isMissing(value)) { return fmt.dash; }
    var v = Number(value);
    if (isNaN(v)) { return fmt.dash; }
    var sign = v > 0 ? "+" : "";
    return sign + formatPercent(fmt, v, decimals);
  }

  // Hand-parses the fixed "YYYY-MM-DD[THH:MM[:SS]]" shape export_static.py
  // writes (Task 3's _json_safe) instead of going through the JS Date
  // constructor, deliberately - "new Date('YYYY-MM-DDTHH:MM:SS')" (no "Z"
  // suffix) is parsed as LOCAL time by every browser, which would silently
  // shift the displayed hour/day depending on the viewer's own timezone.
  // This exactly mirrors poslib/i18n.py:327-371 (_as_date/_as_datetime),
  // which is itself deliberately timezone-naive for the same reason - the
  // source data has no timezone concept at all (the POS PC's own local
  // clock), so neither side should invent one.
  function parseIso(isoString) {
    if (!isoString) { return null; }
    var m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(isoString);
    if (!m) { return null; }
    return {
      year: parseInt(m[1], 10), month: parseInt(m[2], 10), day: parseInt(m[3], 10),
      hour: m[4] ? parseInt(m[4], 10) : 0, minute: m[5] ? parseInt(m[5], 10) : 0,
    };
  }

  function pad2(n) { return (n < 10 ? "0" : "") + n; }

  function formatDate(fmt, isoString) {
    var d = parseIso(isoString);
    if (!d) { return fmt.dash; }
    return fmt.date_format
      .replace("{day}", pad2(d.day))
      .replace("{month}", pad2(d.month))
      .replace("{year}", d.year);
  }

  function formatDateTime(fmt, isoString) {
    var d = parseIso(isoString);
    if (!d) { return fmt.dash; }
    return fmt.datetime_format
      .replace("{day}", pad2(d.day))
      .replace("{month}", pad2(d.month))
      .replace("{year}", d.year)
      .replace("{hour}", pad2(d.hour))
      .replace("{minute}", pad2(d.minute));
  }

  global.RemoteDetail = {
    formatNumber: formatNumber,
    formatMoney: formatMoney,
    formatPercent: formatPercent,
    formatSignedPercent: formatSignedPercent,
    formatDate: formatDate,
    formatDateTime: formatDateTime,
  };
})(window);
```

- [ ] **Step 2: Write the DOM-fill functions**

Append to `static/remote-detail.js`, inside the same IIFE before `global.RemoteDetail = {...}`:

**Revised after plan review** to (a) accept a `strings` argument for the two dynamic translation pieces a shell can't resolve server-side (the family-note template, and — for `renderCustomer` — the segment/credit-risk lookup maps), (b) fix `renderTable` to carry a CSS class and a numeric sort `value` per cell, matching every `<td class="num">`/`data-value="..."` in the real templates, not just plain text, and (c) cover the competitor price row's signed-percent delta span, which is genuinely different from every other cell in this file: it's a `<span>` *inside* a `<td>`, not the whole cell's text, so it needs an `html` cell descriptor instead of `text`.

```javascript
  function setText(dom, key, text) {
    var el = dom[key];
    if (el) { el.textContent = text; }
  }

  function escapeHtml(s) {
    var div = document.createElement("div");
    div.textContent = String(s);
    return div.innerHTML;
  }

  function renderProduct(data, fmt, strings, dom) {
    var s = data.summary;
    // No photo URL: Metrics.product_profile() never returns one (this
    // database has 0 real item photos - see CLAUDE.md discovery #7), and
    // the live api_item_photo route it would otherwise come from has no
    // static-export equivalent - the OLD per-entity HTML export already
    // pointed this <img> at a URL that 404s remotely, silently hidden by
    // its own onerror handler (see product_detail.html:15). Leaving
    // dom.photo hidden here reproduces that same always-invisible
    // behavior, not a regression.
    setText(dom, "itemName", s.item_name);
    setText(dom, "itemNo", s.item_no || fmt.dash);
    setText(dom, "familyName", s.family_name ? " · " + s.family_name : "");
    setText(dom, "revenue", formatMoney(fmt, s.revenue_all));
    setText(dom, "unitsSold", formatNumber(fmt, s.qty_all));
    setText(dom, "profit", formatMoney(fmt, s.gross_profit_all));
    setText(dom, "marginPct", s.margin_pct === null ? strings.notMeasurable : formatPercent(fmt, s.margin_pct));
    setText(dom, "price", formatMoney(fmt, s.price));
    setText(dom, "cost", formatMoney(fmt, s.cost));
    setText(dom, "stock", formatNumber(fmt, s.stock));
    if (dom.stock) { dom.stock.style.color = (s.stock !== null && s.stock < 0) ? "var(--bad)" : ""; }
    setText(dom, "stockValue", formatMoney(fmt, s.stock_value));
    if (dom.neverSoldBanner) { dom.neverSoldBanner.hidden = s.days_since_sale !== null; }

    if (dom.familySection) {
      dom.familySection.hidden = !data.family;
      if (data.family) {
        if (dom.familyNote) {
          dom.familyNote.textContent = strings.familyNoteTemplate.replace("__FAMILY__", s.family_name);
        }
        setText(dom, "familyCompareName", s.item_name);
        if (dom.familyCompareMargin) {
          dom.familyCompareMargin.innerHTML = s.margin_pct === null
            ? '<span class="muted">' + fmt.dash + "</span>"
            : '<span class="delta ' + ((data.family.margin_pct !== null && s.margin_pct < data.family.margin_pct) ? "down" : "up")
              + '">' + escapeHtml(formatPercent(fmt, s.margin_pct)) + "</span>";
        }
        setText(dom, "familyCompareRevenue", formatMoney(fmt, s.revenue_all));
        setText(dom, "familyAvgName", s.family_name + strings.familyAverageSuffix);
        setText(dom, "familyAvgMargin", data.family.margin_pct === null ? fmt.dash : formatPercent(fmt, data.family.margin_pct));
        setText(dom, "familyAvgRevenue", formatMoney(fmt, data.family.revenue));
      }
    }

    renderTable(dom.competitorBody, data.competitor_prices, function (r) {
      var priceCell = { text: formatMoney(fmt, r.price), cls: "num", value: r.price };
      if (s.price) {
        var deltaCls = r.price < s.price ? "down" : "up";
        var deltaText = formatSignedPercent(fmt, (s.price - r.price) / r.price);
        priceCell = {
          html: escapeHtml(formatMoney(fmt, r.price)) +
                ' <span class="delta ' + deltaCls + '" style="font-size:12px;">' + escapeHtml(deltaText) + "</span>",
          cls: "num", value: r.price,
        };
      }
      return [
        { text: r.competitor_name, cls: "name" },
        priceCell,
        { text: formatDate(fmt, r.observed_date), cls: "num", value: r.observed_date },
        { text: r.note || fmt.dash, cls: "muted" },
        { text: "", cls: "" },
      ];
    });
    if (dom.competitorEmpty) { dom.competitorEmpty.hidden = !!(data.competitor_prices && data.competitor_prices.length); }

    renderTable(dom.salesHistoryBody, data.sales_history, function (r) {
      return [
        { text: formatDateTime(fmt, r.ticket_time), cls: "num", value: r.ticket_time },
        { text: String(r.receipt_id), cls: "num muted" },
        { text: formatNumber(fmt, r.qty), cls: "num" },
        { text: formatMoney(fmt, r.price), cls: "num" },
        { text: formatMoney(fmt, r.amount), cls: "num strong", value: r.amount },
        { text: formatMoney(fmt, r.gross_profit), cls: "num", value: r.gross_profit },
      ];
    });
    if (dom.salesHistoryEmpty) { dom.salesHistoryEmpty.hidden = !!(data.sales_history && data.sales_history.length); }

    renderTable(dom.purchaseHistoryBody, data.purchase_history, function (r) {
      return [
        { text: r.purchase_time ? formatDate(fmt, r.purchase_time) : fmt.dash, cls: "num", value: r.purchase_time || "" },
        { text: r.supplier_name || fmt.dash, cls: "muted" },
        { text: formatNumber(fmt, r.qty), cls: "num" },
        { text: formatMoney(fmt, r.price), cls: "num strong", value: r.price || 0 },
        { text: formatMoney(fmt, r.new_cost), cls: "num muted", value: r.new_cost || 0 },
        { text: formatNumber(fmt, r.new_stock), cls: "num", value: r.new_stock || 0 },
      ];
    });
    if (dom.purchaseHistoryEmpty) { dom.purchaseHistoryEmpty.hidden = !!(data.purchase_history && data.purchase_history.length); }
  }

  function renderCustomer(data, fmt, strings, dom) {
    var s = data.summary;
    setText(dom, "customerName", s.customer_name);
    setText(dom, "customerNo", s.customer_no || fmt.dash);
    setText(dom, "phone", s.phone ? " · " + s.phone : "");
    setText(dom, "city", s.city ? " · " + s.city : "");
    if (dom.segmentPill) {
      dom.segmentPill.hidden = !s.segment;
      if (s.segment) {
        dom.segmentPill.textContent = strings.segmentLabels[s.segment] || s.segment;
        dom.segmentPill.className = "pill seg-" + s.segment;
      }
    }
    setText(dom, "revenue", formatMoney(fmt, s.revenue));
    setText(dom, "revenue12m", formatMoney(fmt, s.revenue_12m));
    setText(dom, "profit", formatMoney(fmt, s.gross_profit));
    setText(dom, "marginPct", s.margin_pct === null ? strings.notMeasurable : formatPercent(fmt, s.margin_pct));
    setText(dom, "visits", formatNumber(fmt, s.visits));
    setText(dom, "avgBasket", formatMoney(fmt, s.avg_basket));
    setText(dom, "balanceAmount", s.balance ? formatMoney(fmt, s.balance) : fmt.dash);
    if (dom.balanceValue) { dom.balanceValue.style.color = (s.balance && s.balance > 0) ? "var(--bad)" : ""; }
    if (dom.creditRiskPill) {
      dom.creditRiskPill.hidden = !s.credit_risk;
      if (s.credit_risk) {
        dom.creditRiskPill.textContent = strings.creditRiskLabels[s.credit_risk] || s.credit_risk;
        dom.creditRiskPill.className = "pill pill-" +
          (s.credit_risk === "high" ? "bad" : (s.credit_risk === "medium" ? "warn" : "good"));
      }
    }
    if (dom.balanceSub) {
      dom.balanceSub.textContent = data.receivable
        ? strings.owesNoteTemplate.replace("__AMOUNT__", formatMoney(fmt, data.receivable.balance))
                                    .replace("__DAYS__", formatNumber(fmt, data.receivable.days_since_purchase))
        : strings.noBalanceText;
    }
    if (dom.atRiskBanner) { dom.atRiskBanner.hidden = !(data.receivable && data.receivable.at_risk); }
    if (dom.atRiskDays && data.receivable) { dom.atRiskDays.textContent = formatNumber(fmt, data.receivable.days_since_purchase); }
    if (dom.neverBoughtBanner) { dom.neverBoughtBanner.hidden = !!s.visits; }

    renderTable(dom.purchasesBody, data.purchases, function (r) {
      return [
        { text: formatDateTime(fmt, r.ticket_time), cls: "num", value: r.ticket_time },
        { text: String(r.receipt_id), cls: "num muted" },
        { text: r.item_name, cls: "name" },
        { text: formatNumber(fmt, r.qty), cls: "num" },
        { text: formatMoney(fmt, r.amount), cls: "num strong", value: r.amount },
        { text: formatMoney(fmt, r.gross_profit), cls: "num", value: r.gross_profit },
      ];
    });
    if (dom.purchasesEmpty) { dom.purchasesEmpty.hidden = !!(data.purchases && data.purchases.length); }

    renderTable(dom.paymentsBody, data.payments, function (r) {
      return [
        { text: formatDateTime(fmt, r.ticket_time), cls: "num", value: r.ticket_time },
        { text: String(r.receipt_id), cls: "num muted" },
        { text: formatMoney(fmt, r.amount), cls: "num strong", value: r.amount },
      ];
    });
    if (dom.paymentsEmpty) { dom.paymentsEmpty.hidden = !!(data.payments && data.payments.length); }
  }

  function renderTable(tbody, rows, rowToCells) {
    if (!tbody) { return; }
    tbody.innerHTML = "";
    (rows || []).forEach(function (r) {
      var tr = document.createElement("tr");
      rowToCells(r).forEach(function (cell) {
        var td = document.createElement("td");
        if (cell.cls) { td.className = cell.cls; }
        if (cell.value !== undefined) { td.dataset.value = cell.value; }
        if (cell.html !== undefined) { td.innerHTML = cell.html; } else { td.textContent = cell.text; }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
  }
```

Add `renderProduct` and `renderCustomer` to the `global.RemoteDetail = {...}` export object from Step 1.

**The `strings` argument's shape** (built per-shell in Task 4, since it needs server-rendered translations a shell can't look up dynamically client-side):
- Product shell: `{ familyNoteTemplate, familyAverageSuffix, notFound, notMeasurable }` (see Task 4 Step 1's script — `familyNoteTemplate` uses the literal token `"__FAMILY__"` in place of the real family name, filled in client-side via `.replace()`, since the actual family name is only known after the JSON fetch resolves, not at Jinja-render time).
- Customer shell: `{ notFound, notMeasurable, segmentLabels, creditRiskLabels, owesNoteTemplate, noBalanceText }` — `segmentLabels`/`creditRiskLabels` are small finite lookup objects (built in Task 4 Step 2 by iterating every real `segments.*`/`customers.credit_risk_*` key in the locale files, the same "embed a finite translation lookup map" pattern the product shell's competitor-error handling also needs — see Task 4 Step 1's note on that).

Note: this DOM-fill approach intentionally does **not** reproduce every CSS class/`data-value`/`data-sort` attribute of the original tables (e.g. `class="sortable"`'s click-to-sort behavior from `base.html`'s own script, or the colored up/down `delta` spans) — Task 4's shell markup must still include those classes/attributes on the static `<table>`/`<thead>` skeleton so the existing sortable-table and delta-styling CSS/JS in `base.html` keeps working on the client-rendered rows; `renderTable` above only needs to add `data-value` attributes matching the original template's numeric sort keys (see Task 4 for the exact per-column list) — extend `rowToCells` to return `{text, dataValue}` pairs instead of plain strings once Task 4's exact table layouts are in hand, and set `td.dataset.value = dataValue` when present. Revisit this Step once Task 4 is written, before calling Task 2 done.

- [ ] **Step 3: Manual golden-value check (documented, not automated)**

Once Task 3 (JSON export) and Task 4 (shells) exist, before Task 5's live push: run `python -c "from poslib.i18n import get_translator; t = get_translator('fr'); print(t.money(1234567.5)); print(t.percent(0.085)); print(t.datetime('2026-08-31 15:29:00'))"` and separately open the exported `fr/product.html?id=<some real id>` in a local browser (`python -m http.server` from the export dir works fine for this, or Cloudflare's own preview after Task 5's push) and eyeball that the same three kinds of value match character-for-character (including the French ` `-vs-regular space thousands separator, and the `,` decimal separator). Note the result inline in this plan file's Task 5 section when done — this is the actual parity check for this plan, not a pytest run.

- [ ] **Step 4: Commit**

```bash
git add static/remote-detail.js
git commit -m "feat(remote): add shared JS formatter/renderer for product+customer detail shells"
```

---

## Task 3: `export_static.py` — write `products.json` / `customers.json` (parallel path, old loops untouched)

**Files:**
- Modify: `export_static.py`
- Test: `tests/test_export_static.py`

**Interfaces:**
- Consumes: `Metrics.product_profile(item_id)` / `Metrics.customer_profile(customer_id)` (existing, unmodified — see `poslib/metrics.py:1351` and `poslib/metrics.py:2001`), `app.row_dict`/`app.rows` (existing, unmodified — `app.py:199-236`), `ownerdata.competitor_prices_for_item(cfg, item_id)` (existing, unmodified).
- Produces: `out_dir / "products.json"` — a JSON object keyed by `str(item_id)`, each value shaped `{"summary": {...}, "family": {...}|null, "sales_history": [...], "purchase_history": [...], "competitor_prices": [...]}`. `out_dir / "customers.json"` — a JSON object keyed by `str(customer_id)`, each value shaped `{"summary": {...}, "receivable": {...}|null, "purchases": [...], "payments": [...]}`. Every value inside is JSON-safe (no `NaN`, no Python `datetime` objects — see Step 1). Task 4 consumes these two files by `fetch()`.

- [ ] **Step 1: Write the failing test**

In `tests/test_export_static.py`, add a new test class (near the existing product/customer export tests — search the file for `products_dir`/`customers_dir` to find the right neighborhood):

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_export_static.py -k TestProductsCustomersJson -v`
Expected: FAIL — `products.json`/`customers.json` don't exist yet (`FileNotFoundError`).

- [ ] **Step 3: Write minimal implementation**

In `export_static.py`, add near the top (after the existing imports, before `PAGES`):

```python
def _json_safe(value):
    """
    Recursively convert a row_dict()/rows()-cleaned structure (which may
    still contain Python datetime objects, per app.py's rows()) into
    something json.dumps can serialize with allow_nan=False, matching the
    same "None for missing" convention every other JSON export in this
    file (daily_records, stock_records) already uses.

    row_dict()/rows() (app.py:199-236) already turn NaN into None, but NOT
    +/-inf - isnan(inf) is False, so it passes their cleaning untouched.
    metrics.py's item_movement() sets cover_months = np.inf for any item
    with no recent sale (metrics.py:1527-1531), which is a large share of
    a real catalog's dead stock - json.dumps would otherwise emit the bare
    token "Infinity", which is not valid JSON and makes every consumer's
    JSON.parse() throw. Caught in review before this was ever run for
    real - see this plan's Task 3 Step 4 test, which asserts against it.
    """
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    if isinstance(value, datetime.datetime):
        return value.isoformat(sep="T", timespec="seconds")
    if isinstance(value, datetime.date):
        return value.isoformat()
    return value
```

Then, inside `export()`, right after the existing `stock_records`/`stock_filename` block (after the `(out_dir / "_headers").write_text(...)` call, before `presets = _today_preset_ranges(today)`), add:

```python
        # Products/customers detail, replatformed from per-entity-per-
        # language pre-rendered HTML (see the old products_dir/customers_dir
        # loops further down, still present in parallel - see
        # docs/superpowers/plans/2026-09-01-product-customer-json-replatform.md)
        # to two JSON payloads consumed by templates/product_shell.html and
        # templates/customer_shell.html client-side, the same "one JSON
        # file, all entities, no per-language variant" shape as stock.json
        # above. Every value here is exactly what row_dict()/rows() already
        # produce for the live local dashboard - only the datetime -> ISO
        # string conversion (_json_safe) differs, since JSON has no native
        # datetime type.
        products_json: dict[str, dict] = {}
        for item_id in item_ids:
            profile = m.product_profile(item_id)
            if profile is None:
                raise RuntimeError(f"item {item_id} vanished mid-export")
            competitor_prices = ownerdata.competitor_prices_for_item(cfg, item_id)
            products_json[str(item_id)] = _json_safe({
                "summary": row_dict(profile["summary"]),
                "family": row_dict(profile["family"]),
                "sales_history": rows(profile["sales_history"], limit=200),
                "purchase_history": rows(profile["purchase_history"], limit=200),
                "competitor_prices": rows(competitor_prices),
            })
        (out_dir / "products.json").write_text(
            json.dumps(products_json, ensure_ascii=False, allow_nan=False), encoding="utf-8")

        customers_json: dict[str, dict] = {}
        for customer_id in customer_ids:
            profile = m.customer_profile(customer_id)
            if profile is None:
                raise RuntimeError(f"customer {customer_id} vanished mid-export")
            customers_json[str(customer_id)] = _json_safe({
                "summary": row_dict(profile["summary"]),
                "receivable": row_dict(profile["receivable"]),
                "purchases": rows(profile["purchases"], limit=200),
                "payments": rows(profile["payments"], limit=100),
            })
        (out_dir / "customers.json").write_text(
            json.dumps(customers_json, ensure_ascii=False, allow_nan=False), encoding="utf-8")
```

This duplicates the per-entity data fetch the existing `products_dir`/`customers_dir` loops (further down in the same function) already do — deliberately, per the Global Constraints' migration-order rule (old loops stay untouched and working until Task 6). Task 6 removes this duplication by deleting the old loops, not this new block.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_export_static.py -k TestProductsCustomersJson -v`
Expected: PASS. This is a real-database test (per the file's own module docstring) — expect it to take real time (the baseline single `export()` call already measured at several minutes on this machine's current data volume).

- [ ] **Step 5: Run the full export test file**

Run: `pytest tests/test_export_static.py -q`
Expected: all PASS, including the pre-existing tests (this task is purely additive — nothing existing was removed or changed).

- [ ] **Step 6: Commit**

```bash
git add export_static.py tests/test_export_static.py
git commit -m "feat(remote): export products.json/customers.json alongside the existing per-entity HTML (parallel path)"
```

---

## Task 4: `templates/product_shell.html` + `templates/customer_shell.html`, wired into `export_static.py`, links updated

**Files:**
- Create: `templates/product_shell.html`
- Create: `templates/customer_shell.html`
- Modify: `export_static.py` (render the two shells once per language)
- Modify: `templates/catalog.html:33`, `templates/products.html:85,128,170,212,259,299`, `templates/receivables.html:78`, `templates/customers.html:155`
- Test: `tests/test_export_static.py`

**Interfaces:**
- Consumes: `static/remote-detail.js`'s `window.RemoteDetail` (Task 2), `Translator.js_format()` (Task 1), `products.json`/`customers.json` (Task 3).
- Produces: `out_dir / lang / "product.html"` and `out_dir / lang / "customer.html"` (3 + 3 = 6 files total, one per language per entity type — not per entity).

- [ ] **Step 1: Write `templates/product_shell.html`**

This must reproduce every static element of `templates/product_detail.html` (re-read that file's full content before writing this — every `t(...)` call, every table header, every banner condition) but with `id="..."` hooks instead of Jinja value interpolation for anything that varies per-entity.

**Review findings baked into this version** (checked against `product_detail.html` line-by-line): section order matches the real template exactly (tiles → never-sold banner → family compare → competitor prices → sales history → purchase history — the real file puts competitor *before* the two history tables, not after); the product photo `<img>` is included; the competitor add/delete form is included, its `action` built client-side since it needs the per-entity `item_id` a shell can't know at render time; the competitor table has all 5 columns from the real template, including the delete-button column and the price's signed-percent delta span; every `<td>` gets the same CSS classes (`num`, `muted`, `name`, `strong`) the real template uses, via `RemoteDetail.renderTable`'s extended per-cell shape (Task 2, revised below).

```html
{% extends "base.html" %}
{% block title %}{{ t('nav.products') }}{% endblock %}

{% block breadcrumb %}
<div class="breadcrumb" style="padding: 10px 24px 0;">
  <a href="products.html">{{ t('products.detail_back') }}</a>
</div>
{% endblock %}

{% block content %}
<div class="page-head" style="display:flex; align-items:flex-start; gap:16px;">
  <img id="rd-photo" src="" alt="" width="72" height="72"
       style="object-fit:cover; border-radius:8px; border:1px solid var(--line);"
       onerror="this.hidden=true" hidden>
  <div>
    <h1 id="rd-item-name">{{ t('app.loading') }}</h1>
    <p><span id="rd-item-no"></span><span id="rd-family-name"></span></p>
  </div>
</div>

<section class="tiles tiles-big">
  <div class="tile">
    <div class="label">{{ t('products.col_revenue') }}</div>
    <div class="value" id="rd-revenue">—</div>
    <div class="sub">{{ t('products.col_units') }}: <span id="rd-units-sold"></span></div>
  </div>
  <div class="tile">
    <div class="label">{{ t('products.col_profit') }}</div>
    <div class="value" id="rd-profit">—</div>
    <div class="sub">{{ t('common.margin_pct') }}: <strong id="rd-margin-pct"></strong></div>
  </div>
  <div class="tile">
    <div class="label">{{ t('common.price') }} / {{ t('common.cost') }}</div>
    <div class="value" id="rd-price">—</div>
    <div class="sub">{{ t('common.cost') }}: <span id="rd-cost"></span></div>
  </div>
  <div class="tile">
    <div class="label">{{ t('products.col_stock') }}</div>
    <div class="value" id="rd-stock">—</div>
    <div class="sub">{{ t('inventory.total_value') }}: <span id="rd-stock-value"></span></div>
  </div>
</section>

<div class="banner banner-info" id="rd-never-sold-banner" hidden>{{ t('products.detail_never_sold') }}</div>

<section class="panel" id="rd-family-section" hidden>
  <h2>{{ t('products.detail_family_compare') }}</h2>
  <p class="note" id="rd-family-note"></p>
  <div class="table-wrap">
    <table>
      <thead>
        <tr><th></th><th class="num">{{ t('common.margin_pct') }}</th><th class="num">{{ t('customers.col_revenue') }}</th></tr>
      </thead>
      <tbody>
        <tr class="strong"><td id="rd-family-compare-name"></td><td class="num" id="rd-family-compare-margin"></td><td class="num" id="rd-family-compare-revenue"></td></tr>
        <tr><td id="rd-family-avg-name"></td><td class="num" id="rd-family-avg-margin"></td><td class="num" id="rd-family-avg-revenue"></td></tr>
      </tbody>
    </table>
  </div>
</section>

<section class="panel">
  <h2>{{ t('products.competitor_title') }}</h2>
  <p class="note">{{ t('products.competitor_note') }}</p>

  <div id="rd-competitor-error" class="banner banner-bad" hidden></div>

  <form id="rd-competitor-form" method="post" action=""
        style="display:flex; align-items:flex-end; gap:12px; flex-wrap:wrap; margin-bottom:16px;">
    <label style="display:flex; flex-direction:column; font-size:13px; color:var(--ink-soft);">
      {{ t('products.competitor_col_name') }}
      <input type="text" name="competitor_name" required maxlength="120">
    </label>
    <label style="display:flex; flex-direction:column; font-size:13px; color:var(--ink-soft);">
      {{ t('products.competitor_col_price') }}
      <input type="number" name="price" step="0.01" min="0.01" required>
    </label>
    <label style="display:flex; flex-direction:column; font-size:13px; color:var(--ink-soft);">
      {{ t('common.date') }}
      <input type="date" name="observed_date" required>
    </label>
    <label style="display:flex; flex-direction:column; font-size:13px; color:var(--ink-soft); flex:1; min-width:160px;">
      {{ t('products.competitor_col_note') }}
      <input type="text" name="note" maxlength="200">
    </label>
    <button type="submit" class="btn">{{ t('products.competitor_add') }}</button>
  </form>

  <div class="table-wrap" id="rd-competitor-wrap">
    <table class="sortable">
      <thead>
        <tr>
          <th>{{ t('products.competitor_col_name') }}</th>
          <th class="num">{{ t('products.competitor_col_price') }}</th>
          <th class="num">{{ t('common.date') }}</th>
          <th>{{ t('products.competitor_col_note') }}</th>
          <th class="nosort"></th>
        </tr>
      </thead>
      <tbody id="rd-competitor-body"></tbody>
    </table>
  </div>
  <div class="empty" id="rd-competitor-empty" hidden>{{ t('products.competitor_empty') }}</div>
</section>

<section class="panel">
  <h2>{{ t('products.detail_history_title') }}</h2>
  <div class="table-wrap scroll-y" id="rd-sales-history-wrap">
    <table class="sortable">
      <thead>
        <tr>
          <th class="num">{{ t('common.date') }}</th>
          <th class="num">{{ t('products.detail_col_ticket') }}</th>
          <th class="num">{{ t('common.quantity') }}</th>
          <th class="num">{{ t('common.price') }}</th>
          <th class="num">{{ t('common.revenue') }}</th>
          <th class="num">{{ t('common.gross_profit') }}</th>
        </tr>
      </thead>
      <tbody id="rd-sales-history-body"></tbody>
    </table>
  </div>
  <div class="empty" id="rd-sales-history-empty" hidden>{{ t('products.detail_no_history') }}</div>
</section>

<section class="panel">
  <h2>{{ t('products.purchase_history_title') }}</h2>
  <p class="note">{{ t('products.purchase_history_note') }}</p>
  <div class="table-wrap scroll-y" id="rd-purchase-history-wrap">
    <table class="sortable">
      <thead>
        <tr>
          <th class="num">{{ t('common.date') }}</th>
          <th>{{ t('common.supplier') }}</th>
          <th class="num">{{ t('common.quantity') }}</th>
          <th class="num">{{ t('products.purchase_col_price_paid') }}</th>
          <th class="num">{{ t('products.purchase_col_running_cost') }}</th>
          <th class="num">{{ t('products.purchase_col_new_stock') }}</th>
        </tr>
      </thead>
      <tbody id="rd-purchase-history-body"></tbody>
    </table>
  </div>
  <div class="empty" id="rd-purchase-history-empty" hidden>{{ t('products.purchase_history_empty') }}</div>
</section>

<script src="{{ url_for('static', filename='remote-detail.js') }}"></script>
<script>
(function () {
  "use strict";
  var fmt = {{ t.js_format() | tojson }};
  var strings = {
    familyNoteTemplate: {{ t('products.detail_family_note', family='__FAMILY__') | tojson }},
    familyAverageSuffix: {{ (' (' ~ t('common.average')|lower ~ ')') | tojson }},
    notFound: {{ t('app.no_findings') | tojson }},
    notMeasurable: {{ t('common.not_measurable') | tojson }},
  };
  var params = new URLSearchParams(window.location.search);
  var id = params.get("id");

  document.querySelectorAll(".lang-switch a.lang").forEach(function (a) {
    a.href = a.getAttribute("href") + window.location.search;
  });

  var dom = {
    photo: document.getElementById("rd-photo"),
    itemName: document.getElementById("rd-item-name"),
    itemNo: document.getElementById("rd-item-no"),
    familyName: document.getElementById("rd-family-name"),
    revenue: document.getElementById("rd-revenue"),
    unitsSold: document.getElementById("rd-units-sold"),
    profit: document.getElementById("rd-profit"),
    marginPct: document.getElementById("rd-margin-pct"),
    price: document.getElementById("rd-price"),
    cost: document.getElementById("rd-cost"),
    stock: document.getElementById("rd-stock"),
    stockValue: document.getElementById("rd-stock-value"),
    neverSoldBanner: document.getElementById("rd-never-sold-banner"),
    familySection: document.getElementById("rd-family-section"),
    familyNote: document.getElementById("rd-family-note"),
    familyCompareName: document.getElementById("rd-family-compare-name"),
    familyCompareMargin: document.getElementById("rd-family-compare-margin"),
    familyCompareRevenue: document.getElementById("rd-family-compare-revenue"),
    familyAvgName: document.getElementById("rd-family-avg-name"),
    familyAvgMargin: document.getElementById("rd-family-avg-margin"),
    familyAvgRevenue: document.getElementById("rd-family-avg-revenue"),
    competitorForm: document.getElementById("rd-competitor-form"),
    competitorError: document.getElementById("rd-competitor-error"),
    competitorBody: document.getElementById("rd-competitor-body"),
    competitorEmpty: document.getElementById("rd-competitor-empty"),
    salesHistoryBody: document.getElementById("rd-sales-history-body"),
    salesHistoryEmpty: document.getElementById("rd-sales-history-empty"),
    purchaseHistoryBody: document.getElementById("rd-purchase-history-body"),
    purchaseHistoryEmpty: document.getElementById("rd-purchase-history-empty"),
  };

  var competitorErrors = {
    {% for code in ['missing_name', 'invalid_price', 'invalid_date'] %}
    "{{ code }}": {{ t('products.competitor_error_' ~ code) | tojson }}{{ "," if not loop.last }}
    {% endfor %}
  };
  var formErrorParam = params.get("form_error");
  if (formErrorParam && dom.competitorError && competitorErrors[formErrorParam]) {
    dom.competitorError.hidden = false;
    dom.competitorError.textContent = competitorErrors[formErrorParam];
  }

  if (!id) {
    dom.itemName.textContent = strings.notFound;
  } else {
    if (dom.competitorForm) {
      dom.competitorForm.action = "/products/" + id + "/competitor-price?lang={{ lang }}&id=" + id;
    }
    // Relative to out_dir/<lang>/product.html - products.json lives one
    // directory up, at out_dir/products.json (see Task 3).
    fetch("../products.json").then(function (r) { return r.json(); }).then(function (data) {
      var entry = data[id];
      if (!entry) { dom.itemName.textContent = strings.notFound; return; }
      RemoteDetail.renderProduct(entry, fmt, strings, dom);
    }).catch(function () { dom.itemName.textContent = strings.notFound; });
  }
})();
</script>
{% endblock %}
```

Notes on the parts that changed from a naive per-field translation:

- `t('products.competitor_error_' ~ form_error)`'s server-side dynamic-key lookup (`product_detail.html:95`) can't be reproduced client-side the way Jinja does it, since a shell doesn't know `form_error`'s value until the page actually loads with it in the query string. Fixed the same way as `segments.*`/`customers.credit_risk_*` in the customer shell (Step 2): the finite set of real codes `app.py`'s `product_competitor_price_add` route can send back (`missing_name`, `invalid_price`, `invalid_date` — confirmed by reading that route directly, `app.py:687-731`) is embedded as a small lookup object, `competitorErrors`, at render time.
- The competitor form's `action` is built in JS (`"/products/" + id + "/competitor-price?..."`) because `url_for('product_competitor_price_add', item_id=...)` needs a concrete `item_id` the shell doesn't have until the JSON lookup resolves — but note the form's `action` is set *before* that fetch resolves (right after establishing `id` from the query string), since the real `item_id` path segment is just the same `id` value, not something that needs the JSON at all. Submitting this form still 404s on Cloudflare Pages (no backend), exactly matching today's already-broken remote behavior — this is deliberate parity, not a regression. One further pre-existing quirk, unchanged by this plan: `product_competitor_price_add`'s own success/error redirect target is always `url_for("page_product", ...)` — the live local route, never this shell's URL — so even in a hypothetical future where this form could actually submit somewhere, today's redirect logic would not send the visitor back to `product.html?id=...` anyway. Out of scope to fix here (this plan reproduces existing behavior, including its existing gaps).

The breadcrumb link near the top of this template (`<a href="products.html">`) is deliberately a plain relative link, not `url_for(...)` or an `is_static_export` branch — this shell template is only ever rendered by `export_static.py` (Task 4 Step 3), never by `app.py`'s live `page_product`/`page_customer` routes (those still render `product_detail.html`/`customer_detail.html` directly, unchanged), so a static-export context is always true here, and the plain relative path (matching how `catalog.html`/`products.html` are already exported as `<lang>/products.html` in the same directory) is always correct.

- [ ] **Step 2: Write `templates/customer_shell.html`**

Mirrors `templates/customer_detail.html`'s full structure (re-read that file — confirmed above, real section order is: page-head with segment pill → tiles including balance/credit-risk pill → at-risk banner → never-bought banner → purchases panel → payments panel). Two dynamic translation lookups this shell needs and a shell can't resolve per-entity at render time — `t('segments.' ~ summary.segment)` (7 possible codes: `champion`, `loyal`, `active`, `new`, `at_risk`, `lapsed`, `one_time` — confirmed against `locales/en.json`'s `segments` block) and `t('customers.credit_risk_' ~ summary.credit_risk)` (3 codes: `low`, `medium`, `high`) — are solved the same way as `renderCustomer`'s `strings.segmentLabels`/`strings.creditRiskLabels` (Task 2, revised above): a small finite lookup object built via a Jinja loop over the known codes, embedded once per language at render time.

```html
{% extends "base.html" %}
{% block title %}{{ t('nav.customers') }}{% endblock %}

{% block breadcrumb %}
<div class="breadcrumb" style="padding: 10px 24px 0;">
  <a href="customers.html">{{ t('customers.detail_back') }}</a>
</div>
{% endblock %}

{% block content %}
<div class="page-head">
  <h1 id="rd-customer-name">{{ t('app.loading') }}</h1>
  <p>
    <span id="rd-customer-no"></span><span id="rd-phone"></span><span id="rd-city"></span>
    <span class="pill" id="rd-segment-pill" hidden></span>
  </p>
</div>

<section class="tiles tiles-big">
  <div class="tile">
    <div class="label">{{ t('customers.col_revenue') }}</div>
    <div class="value" id="rd-revenue">—</div>
    <div class="sub">{{ t('customers.col_revenue_12m') }}: <span id="rd-revenue12m"></span></div>
  </div>
  <div class="tile">
    <div class="label">{{ t('customers.col_profit') }}</div>
    <div class="value" id="rd-profit">—</div>
    <div class="sub">{{ t('common.margin_pct') }}: <strong id="rd-margin-pct"></strong></div>
  </div>
  <div class="tile">
    <div class="label">{{ t('customers.col_visits') }}</div>
    <div class="value" id="rd-visits">—</div>
    <div class="sub">{{ t('customers.col_basket') }}: <span id="rd-avg-basket"></span></div>
  </div>
  <div class="tile">
    <div class="label">{{ t('common.balance') }}</div>
    <div class="value" id="rd-balance-value">
      <span id="rd-balance-amount">—</span>
      <span class="pill" id="rd-credit-risk-pill" style="font-size:12px; vertical-align:middle;" hidden></span>
    </div>
    <div class="sub" id="rd-balance-sub"></div>
  </div>
</section>

<div class="banner banner-bad" id="rd-at-risk-banner" hidden>{{ t('receivables.risk_flag') }} — {{ t('receivables.col_days') }}: <span id="rd-at-risk-days"></span></div>
<div class="banner banner-info" id="rd-never-bought-banner" hidden>{{ t('customers.detail_never_bought') }}</div>

<section class="panel">
  <h2>{{ t('customers.detail_purchases_title') }}</h2>
  <div class="table-wrap scroll-y" id="rd-purchases-wrap">
    <table class="sortable">
      <thead>
        <tr>
          <th class="num">{{ t('common.date') }}</th>
          <th class="num">{{ t('customers.detail_col_ticket') }}</th>
          <th>{{ t('common.product') }}</th>
          <th class="num">{{ t('common.quantity') }}</th>
          <th class="num">{{ t('common.revenue') }}</th>
          <th class="num">{{ t('common.gross_profit') }}</th>
        </tr>
      </thead>
      <tbody id="rd-purchases-body"></tbody>
    </table>
  </div>
  <div class="empty" id="rd-purchases-empty" hidden>{{ t('customers.detail_no_purchases') }}</div>
</section>

<section class="panel">
  <h2>{{ t('customers.detail_payments_title') }}</h2>
  <div class="table-wrap" id="rd-payments-wrap">
    <table class="sortable">
      <thead>
        <tr>
          <th class="num">{{ t('common.date') }}</th>
          <th class="num">{{ t('customers.detail_col_ticket') }}</th>
          <th class="num">{{ t('common.revenue') }}</th>
        </tr>
      </thead>
      <tbody id="rd-payments-body"></tbody>
    </table>
  </div>
  <div class="empty" id="rd-payments-empty" hidden>{{ t('customers.detail_no_payments') }}</div>
</section>

<script src="{{ url_for('static', filename='remote-detail.js') }}"></script>
<script>
(function () {
  "use strict";
  var fmt = {{ t.js_format() | tojson }};
  var strings = {
    notFound: {{ t('app.no_findings') | tojson }},
    notMeasurable: {{ t('common.not_measurable') | tojson }},
    owesNoteTemplate: {{ t('customers.detail_owes_note', amount='__AMOUNT__', days='__DAYS__') | tojson }},
    noBalanceText: {{ t('customers.detail_no_balance') | tojson }},
    segmentLabels: {
      {% for code in ['champion', 'loyal', 'active', 'new', 'at_risk', 'lapsed', 'one_time'] %}
      "{{ code }}": {{ t('segments.' ~ code) | tojson }}{{ "," if not loop.last }}
      {% endfor %}
    },
    creditRiskLabels: {
      {% for code in ['low', 'medium', 'high'] %}
      "{{ code }}": {{ t('customers.credit_risk_' ~ code) | tojson }}{{ "," if not loop.last }}
      {% endfor %}
    },
  };
  var params = new URLSearchParams(window.location.search);
  var id = params.get("id");

  document.querySelectorAll(".lang-switch a.lang").forEach(function (a) {
    a.href = a.getAttribute("href") + window.location.search;
  });

  var dom = {
    customerName: document.getElementById("rd-customer-name"),
    customerNo: document.getElementById("rd-customer-no"),
    phone: document.getElementById("rd-phone"),
    city: document.getElementById("rd-city"),
    segmentPill: document.getElementById("rd-segment-pill"),
    revenue: document.getElementById("rd-revenue"),
    revenue12m: document.getElementById("rd-revenue12m"),
    profit: document.getElementById("rd-profit"),
    marginPct: document.getElementById("rd-margin-pct"),
    visits: document.getElementById("rd-visits"),
    avgBasket: document.getElementById("rd-avg-basket"),
    balanceValue: document.getElementById("rd-balance-value"),
    balanceAmount: document.getElementById("rd-balance-amount"),
    creditRiskPill: document.getElementById("rd-credit-risk-pill"),
    balanceSub: document.getElementById("rd-balance-sub"),
    atRiskBanner: document.getElementById("rd-at-risk-banner"),
    atRiskDays: document.getElementById("rd-at-risk-days"),
    neverBoughtBanner: document.getElementById("rd-never-bought-banner"),
    purchasesBody: document.getElementById("rd-purchases-body"),
    purchasesEmpty: document.getElementById("rd-purchases-empty"),
    paymentsBody: document.getElementById("rd-payments-body"),
    paymentsEmpty: document.getElementById("rd-payments-empty"),
  };

  if (!id) {
    dom.customerName.textContent = strings.notFound;
  } else {
    // Relative to out_dir/<lang>/customer.html - customers.json lives one
    // directory up, at out_dir/customers.json (see Task 3).
    fetch("../customers.json").then(function (r) { return r.json(); }).then(function (data) {
      var entry = data[id];
      if (!entry) { dom.customerName.textContent = strings.notFound; return; }
      RemoteDetail.renderCustomer(entry, fmt, strings, dom);
    }).catch(function () { dom.customerName.textContent = strings.notFound; });
  }
})();
</script>
{% endblock %}
```

Note the `balance` tile's markup keeps the pill *inside* the `.value` div (matching `customer_detail.html:42-50` exactly, where the credit-risk pill sits on the same line as the money figure, not the `.sub` line) — don't move it into `rd-balance-sub` by mistake, that element is only for the "owes X, Y days" / "no balance" note underneath.

- [ ] **Step 3: Wire the two shells into `export_static.py`**

`render()` (defined earlier in the same function, see `export_static.py:266-275`) calls the real Flask routes via `client.get()`, but `product_shell.html`/`customer_shell.html` have no route of their own (the real `/products/<id>` and `/customers/<id>` routes still render `product_detail.html`/`customer_detail.html` for the local live dashboard, unchanged), so `render()` cannot be reused here. Instead, render the two shells directly via `render_template`, the same way the ticket/purchase/product/customer per-entity loops already do further down in this same function (see the existing `with app.test_request_context(...): html = render_template(...)` pattern at `export_static.py:301-310`) — this also correctly fires `app.py`'s `inject_globals()` context processor (so `is_static_export`, `lang`, `pages`, etc. are all set exactly as they are for every other exported page), since Flask context processors apply to any template render inside an active request context, not only to a real dispatched request.

**First, copy the new JS file.** `export_static.py:262-264` currently copies only `static/style.css` into each `lang_dir/static/`:

```python
            static_dir = lang_dir / "static"
            static_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(PROJECT_ROOT / "static" / "style.css", static_dir / "style.css")
```

Add a second copy right after it, in the same block:

```python
            shutil.copy2(PROJECT_ROOT / "static" / "remote-detail.js", static_dir / "remote-detail.js")
```

Without this, the shell's `<script src="{{ url_for('static', filename='remote-detail.js') }}">` 404s on the real deployment (Flask's local dev server serves `static/` directly so this bug would NOT show up testing locally — it only manifests once actually deployed to Cloudflare Pages, which has no server behind it) and `window.RemoteDetail` is undefined, so every shell page silently renders nothing.

**Then, wire the two shell renders.** Inside the `for lang in LANGUAGES:` loop in `export()`, after the existing `for slug in NESTED_PAGES:` block and before the `for filename, (start, end) in presets.items():` block, add:

```python
            with app.test_request_context(
                    f"/product.html?lang={lang}&__static__=1",
                    environ_overrides={"SCRIPT_NAME": f"/{lang}"}):
                html = render_template("product_shell.html", cache=cache_info)
            (lang_dir / "product.html").write_text(html, encoding="utf-8")

            with app.test_request_context(
                    f"/customer.html?lang={lang}&__static__=1",
                    environ_overrides={"SCRIPT_NAME": f"/{lang}"}):
                html = render_template("customer_shell.html", cache=cache_info)
            (lang_dir / "customer.html").write_text(html, encoding="utf-8")
```

Place this block right after the `for slug in NESTED_PAGES:` loop (still inside `for lang in LANGUAGES:`), before the preset-today loop.

**Language-switcher regression, found in review.** `templates/base.html:33` builds each language-switch link as `/{{ l.code }}/{{ page_slug }}`, where `page_slug = request.path.strip("/")` (`app.py:180`). For a shell rendered at `/product.html`, that produces `/fr/product.html` with **no `?id=`** — switching language on a detail page would silently lose which entity was being viewed. Task 4 Step 1's script already includes the fix (`document.querySelectorAll(".lang-switch a.lang").forEach(...)`, appending `window.location.search` to every language-switch link, run before the fetch so it works even in a "not found" state) — Step 2's customer shell script needs the identical block, in the same position (right after `id`/`params` are established, before the `dom = {...}` object).

- [ ] **Step 4: Update the four link-generating templates**

In each of the four files, change the `href` to branch on `is_static_export`. Example for `templates/catalog.html:33` (currently `<a href="{{ url_for('page_product', item_id=r.item_id) }}">{{ r.item_name }}</a>`):

```html
<a href="{% if is_static_export %}product.html?id={{ r.item_id }}{% else %}{{ url_for('page_product', item_id=r.item_id) }}{% endif %}">{{ r.item_name }}</a>
```

Apply the identical transform (swap `page_product`/`item_id` for `page_customer`/`customer_id` and `product.html` for `customer.html` where relevant) to:
- `templates/catalog.html:33`
- `templates/products.html:85, 128, 170, 212, 259, 299` (all six — this file lists products in several different tables, every occurrence needs the same change)
- `templates/receivables.html:78`
- `templates/customers.html:155`

- [ ] **Step 5: Write the failing test, then make it pass**

In `tests/test_export_static.py`:

```python
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
```

Run: `pytest tests/test_export_static.py -k "TestProductCustomerShells" -v` — first to see it fail (templates/wiring don't exist yet), then implement Steps 1-4 above, then re-run to see it pass.

- [ ] **Step 6: Full export test suite**

Run: `pytest tests/test_export_static.py -q`
Expected: all PASS, including every pre-existing test (old per-entity loops are still there and still producing identical output to before — this task only adds files and changes hrefs, it does not remove or alter the old export loops' own behavior).

- [ ] **Step 7: Commit**

```bash
git add templates/product_shell.html templates/customer_shell.html export_static.py \
        templates/catalog.html templates/products.html templates/receivables.html templates/customers.html \
        tests/test_export_static.py
git commit -m "feat(remote): add JSON-backed product/customer detail shells, link catalog/receivables to them"
```

---

## Task 5: Live verification (manual — real export, real push, real phone check)

**This task has no code changes.** It is the mandatory live-verification gate from the parent task's guardrails ("Never declare either stage 'fixed' or 'done' from dev-machine testing alone").

- [ ] **Step 1**: Run `pytest tests -q` (full suite, not just `test_export_static.py`) and confirm everything passes.

- [ ] **Step 2**: From a real machine with valid `CLOUDFLARE_API_TOKEN`/`CLOUDFLARE_ACCOUNT_ID` in `.env` and `remote.enabled: true` pointed at a real, reachable Cloudflare Pages project, run:

```python
from poslib.config import get_config
import export_static
from poslib import remote

cfg = get_config()
export_static.export(cfg)
remote.push_remote(cfg)
```

- [ ] **Step 3**: Measure and record the after-numbers, same method as the baseline in this plan's header:

```bash
find remote-site -type f | wc -l
du -sh remote-site   # or: (Get-ChildItem -Recurse -File remote-site | Measure-Object Length -Sum).Sum / 1MB
```

Compare against the **12,555 files / ~232.6 MB** baseline. Log both the before and after numbers, plus `push_remote`'s logged step timings (from `logs/pos-tool.log`, per-step `push_remote(...)` lines already emitted by the existing instrumentation — see `poslib/remote.py:415-460`), in `CLAUDE.md`'s customer-distribution section.

**Also measure a second, "warm" push** (re-run `export_static.export(cfg)` + `remote.push_remote(cfg)` again immediately after, with no real data change in between) and log its timing separately. Flagged in review: `products.json`/`customers.json` are each one large file covering every entity, so `poslib/remote.py`'s `check-missing-hashes` optimization (which today lets a single sale's push re-upload only the handful of per-entity files that actually changed) no longer helps for these two files specifically — *any* database change invalidates the whole blob, so every regular watcher cycle after this change re-uploads the full ~14MB of `products.json`+`customers.json` again, every time, not just on the rare cycle that touches a lot of data. The first-push number alone would overstate the ongoing reliability win; a warm-push number is what the watcher will actually pay on a normal day.

- [ ] **Step 4**: On the real deployed URL, check (ideally from the owner's own phone, per this repo's established "don't declare a live deploy fixed until the owner's phone confirms it" rule — see `CLAUDE.md`'s `_redirects` bug history):
  - `/<lang>/catalog` → click a product → lands on `/<lang>/product.html?id=<id>` and shows the exact same numbers/labels the OLD per-entity page showed (spot-check at least: one item with purchase history and a family, one item never sold, and — if still present in the live data — `DB786`, the item CLAUDE.md documents as having a real avg-cost/last-cost divergence, to confirm the two cost figures still render as two distinct correct numbers).
  - `/<lang>/receivables` → click a customer with a balance → `/<lang>/customer.html?id=<id>` shows the credit-risk pill, balance, and "owes" note correctly.
  - A customer/product with **no** history at all (empty purchases/sales) shows the correct empty-state message, not a blank table or a JS error (open the browser console and confirm no errors).
  - All three languages (`en`, `fr`, `ar`) render correctly, including Arabic's right-to-left layout and its thousands/decimal separators.
  - Stage 1's "Synced {when}" badge (from the earlier, independent Stage 1 task) is still present and correct on these new shell pages too — `base.html` is unchanged, so this should already be true, but confirm rather than assume.

- [ ] **Step 5**: Only after Step 4 passes, update this plan file's Task 5 section (or a follow-up commit's message) noting the confirmed-live status, then proceed to Task 6.

---

## Task 6: Delete the old per-entity HTML export loops

**Only start this task after Task 5's live verification has actually passed.**

**Files:**
- Modify: `export_static.py` (delete the `products_dir`/`customers_dir` loops — **not** `templates/product_detail.html`/`templates/customer_detail.html`, which stay: they still serve `app.py`'s local live `/products/<id>` and `/customers/<id>` routes, completely unrelated to this export path now)
- Modify: `tests/test_export_static.py` (remove/update any test that asserted the old per-entity HTML tree existed)
- Modify: `export_static.py`'s module docstring (the paragraph describing "Customer and product drill-down pages... ARE exported too, in full" needs rewriting to describe the new JSON+shell shape instead)

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing new — this task only removes the now-redundant old loops added originally for discovery in "Remote product/customer drill-downs now exported in full" (see `CLAUDE.md`).

- [ ] **Step 1: Remove the old loops**

In `export_static.py`, delete the `products_dir = lang_dir / "products"` block and the `customers_dir = lang_dir / "customers"` block in their entirety (inside the `for lang in LANGUAGES:` loop) — everything from `products_dir = lang_dir / "products"` through the end of the `for customer_id in customer_ids:` loop's body, just before the closing of the `for lang in LANGUAGES:` loop / the `finally: conn.close()`.

Also remove the `total_per_lang` calculation's now-inflated counting of `len(item_ids)`/`len(customer_ids)` as if they were per-language pages (they were per-language before; now `product.html`/`customer.html` are each counted once per language already via `len(PAGES)`-style accounting — update the log line at the end of `export()` to reflect the new shape, e.g. add `product.html`/`customer.html` to a page-count constant instead, or adjust the log message to say "products.json/customers.json (%d products, %d customers)" instead of folding them into `total_per_lang`).

- [ ] **Step 2: Update the module docstring**

Rewrite the paragraph starting "Customer and product drill-down pages ARE exported too, in full..." to describe the new architecture: one `products.json`/`customers.json` (all entities, no per-language variant) plus one `product.html`/`customer.html` shell per language, client-rendered — reference this plan file by path for the full rationale, matching how other module docstrings in this file already cross-reference design docs (e.g. the `stock.json` paragraph references `docs/superpowers/specs/2026-08-27-component5-hub-design.md`).

- [ ] **Step 3: Update/remove now-obsolete tests**

In `tests/test_export_static.py`, find and remove (or rewrite to assert the *absence* of) any test asserting the old `<lang>/products/<id>.html` / `<lang>/customers/<id>.html` files exist. Update `TestProductCustomerShells.test_writes_one_shell_per_language_not_per_entity` (from Task 4) to assert the OLD directories now do **not** exist:

```python
        assert not (out_dir / "en" / "products").exists()
        assert not (out_dir / "en" / "customers").exists()
```

- [ ] **Step 4: Full suite**

Run: `pytest tests -q`
Expected: all PASS.

- [ ] **Step 5: Re-measure and log the final after-numbers**

Repeat Task 5 Step 3's measurement (file count, size) now that the old loops are actually gone — this is the real final "after" number (Task 5's own measurement still included the old per-entity trees, since they weren't deleted yet at that point). Log this final number in `CLAUDE.md` alongside the Task 5 numbers, clearly labeled as the post-cleanup figure.

- [ ] **Step 6: Live re-verification**

Repeat Task 5 Steps 2 and 4 (export, push, phone-check) one more time now that the old files are gone from the export directory — Cloudflare Pages Direct Upload fully replaces the deployment's file set on every push, so this confirms the old per-entity URLs are gone from the live site (expected — `404.html`'s existing "not available remotely" page should now catch any stale bookmark to an old `/products/<id>` remote URL, since that route only ever existed as a static file, not a real server route) and that the new shells still work standalone.

- [ ] **Step 7: Commit**

```bash
git add export_static.py tests/test_export_static.py
git commit -m "refactor(remote): remove now-redundant per-entity product/customer HTML export (JSON+shell replatform verified live)"
```

---

## Self-Review Notes (from the plan-writing process, not a task)

- **Spec coverage**: all four "What the plan must specify" items from the parent prompt are covered — JSON shape (Task 3), shared renderer (Tasks 2/4), explicit parallel-then-cutover migration (Tasks 3/4 → 5 → 6), and before/after measurement (baseline in this file's header, Task 5 Step 3, Task 6 Step 5).
- **Financial-logic gate**: explicitly addressed in Global Constraints — this plan reads `Metrics.product_profile()`/`customer_profile()` unmodified; no computation changes anywhere, so the parent task's financial-review trigger does not apply here (only the standard plan-sanity Opus review that must happen next, before implementation).
- **`stock.json` isolation**: verified no task in this plan touches `stock_json_token`, `stock.json`, `stock-<token>.json`, or `_headers`'s CORS line for that file.
- **Known gap, called out explicitly rather than hidden**: Task 2 has no automated JS test (no Node.js in this repo's toolchain, by design) — parity is confirmed manually in Task 2 Step 4 and again in Task 5's live phone check. If this repeatedly causes regressions in practice, a future follow-up could reconsider a minimal JS test runner, but that is out of scope for this plan (Global Constraints explicitly forbids adding new build tooling here).
- **Known minor gap, accepted**: `Translator.number()` (`poslib/i18n.py:167`) formats via Python's `f"{v:,.{decimals}f}"`, while `RemoteDetail.formatNumber` uses JS's `Math.abs(v).toFixed(decimals)` — the two use different half-way rounding rules, so a value landing exactly on a `.5` boundary at `decimals=0` (rare, but possible for a computed average) could round differently server-side vs client-side. Not fixed in this plan (a correct fix means reimplementing Python's banker's-rounding in JS, which is more complexity than a boundary-case cosmetic mismatch on a decimals=0 display value justifies) — flagged here so it isn't mistaken for an unnoticed bug if ever spotted during Task 5's live check.
- **Plan revised 2026-09-01 after an Opus plan-sanity review** caught four blocking defects, all fixed above before implementation started: (1) `Metrics.item_movement()`'s `cover_months = np.inf` (for any item with no recent sale) would have produced literal `Infinity` tokens in `products.json`, invalid JSON that breaks every product page — fixed in `_json_safe` (Task 3) plus `allow_nan=False` on both `json.dumps` calls as a fail-loud backstop; (2) `static/remote-detail.js` was never copied into the per-language export directories, which would have 404'd on the real deployment (works locally, since Flask's dev server serves `static/` directly — only breaks once actually deployed) — fixed in Task 4 Step 3; (3) the shell markup materially diverged from the real templates (wrong section order, missing product photo element, missing/non-functional-but-required competitor form, missing table columns/CSS classes, unsolved `segments.*`/`credit_risk_*` dynamic translation lookups) — Tasks 2 and 4 rewritten to match `product_detail.html`/`customer_detail.html` line-by-line; (4) the language switcher would have silently dropped `?id=` when switching languages on a detail page (`base.html`'s link-building has no query-string awareness) — fixed with a small inline-script patch in both shells rather than touching the shared `base.html`. The review's non-blocking findings (a 25MB Cloudflare file-size ceiling this plan's data is currently well under but should keep being checked as the catalog grows, and the steady-state "warm push" cost of two large always-changing files replacing many small rarely-changing ones) are addressed via new test assertions (Task 3) and an added warm-push measurement (Task 5) respectively.
