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

