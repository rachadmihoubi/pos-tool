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

