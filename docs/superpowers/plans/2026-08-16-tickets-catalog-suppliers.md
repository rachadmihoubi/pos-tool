# Today Drill-Down, Cash-Realized Sales, Stock Catalog, Supplier Drill-Down Implementation Plan

> **STATUS: COMPLETE — shipped as Patch #4.** All features here are built,
> tested, and live. Checkboxes below were never ticked during execution
> (this plan predates that convention being followed strictly), but the
> work is done — see CLAUDE.md's "Patch #4 session discoveries" and
> "Remote-parity follow-up" sections for what actually shipped. Kept here
> as historical record of the original design, not an active task list.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Today screen into a period-selectable view with a real
cash-realized-vs-on-account sales split, add a new Tickets tab (period list
+ full ticket drill-down), a new Stock catalog tab (full searchable product
table), and a Suppliers transaction list + per-purchase drill-down.

**Architecture:** Every change is additive to the existing Flask app. New
business logic goes only in `poslib/metrics.py` (project rule); new routes
in `app.py` reuse the existing `open_metrics()` / `rows()` / `row_dict()` /
`date_range_from_request()` helpers already there; new templates extend
`base.html` and reuse its existing `table.sortable` / `data-filters` JS and
the `_daterange.html` picker idiom — no new client-side JS is written. No
route in this plan accepts POST or touches the database beyond reading it.

**Tech Stack:** Python 3.12, Flask, pandas, Jinja2, pytest. No new
dependencies.

**Spec:** `docs/superpowers/specs/2026-08-16-tickets-catalog-suppliers-design.md`
(read it — it has the full rationale for what's in scope and what's
explicitly not, including *why* Trend/Products/Cash P&L and the frozen
all-time verified-numbers table are untouched).

## Global Constraints

- **Never write to the source `.dblx` file.** Every route in this plan is a
  read-only GET. (CLAUDE.md's one rule that overrides everything else.)
- **All business logic lives in `poslib/metrics.py`.** `app.py` routes only
  call metrics methods and pass the result to a template — no calculation
  in `app.py` or in a template.
- **No text a person reads is written in code.** Every new label comes from
  `locales/en.json` / `fr.json` / `ar.json`, added to all three in the same
  task that introduces it. A test fails if the three files' keys ever drift
  apart (`tests/test_i18n_and_app.py::test_all_three_languages_have_the_same_keys`)
  — do not add a key to one file and forget the other two.
- **The frozen all-time verified-numbers table (`tests/conftest.py`'s
  `expected_counts`/`expected_totals`, and `metrics.py`'s `verification()`)
  is never modified by this plan.** Nothing built here changes an all-time
  or invoiced-revenue figure — the cash-realized split is a new, additive
  metric that sits beside the existing ones.
- **"This week" starts on Monday** (`weekday == 0`), matching the `weekday`
  column already used throughout `metrics.py`.
- Every new pytest test that reads real data uses the existing `metrics`
  session fixture (real database, read-only copy) and asserts structural
  properties (columns present, sums are sane, nothing raises) rather than
  fixed values — the shop keeps trading, so fixed expected numbers from
  today would be wrong tomorrow. Only the already-frozen checks in
  `conftest.py` use fixed values, and this plan does not add to that list.

---

## Task 1: Cash-realized / on-account split on `tickets`

**Files:**
- Modify: `poslib/metrics.py` (the `tickets` cached property, currently
  ending around line 218-219 with `df["is_walkin"] = ...` / `return df`)
- Test: `tests/test_metrics.py`

**Interfaces:**
- Produces: `Metrics.tickets` gains four new float columns —
  `realized_tender`, `total_tender`, `realized_share`, `cash_revenue`,
  `on_account_revenue`. `cash_revenue + on_account_revenue == revenue` for
  every row (up to floating point). Every later task that needs the split
  reads these columns off `self.tickets`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_metrics.py`, inside a new `TestCashRealizedSplit` class
(place it after `TestCash`, i.e. after the line containing
`class TestCatalog:` starts — insert the new class immediately before that
line so it reads naturally between `TestCash` and `TestCatalog`):

```python
class TestCashRealizedSplit:

    def test_split_adds_up_to_revenue(self, metrics: Metrics):
        tk = metrics.tickets
        gap = (tk["cash_revenue"] + tk["on_account_revenue"] - tk["revenue"]).abs()
        assert (gap < 0.01).all(), "cash + on-account must always equal revenue"

    def test_fully_paid_ticket_has_no_on_account(self, metrics: Metrics):
        tk = metrics.tickets
        fully_paid = tk[(tk["credit_account"].fillna(0) == 0) & (tk["revenue"] > 0)]
        if fully_paid.empty:
            pytest.skip("no fully cash/cheque/transfer-paid ticket with revenue in this database")
        assert (fully_paid["on_account_revenue"].abs() < 0.01).all()

    def test_fully_on_account_ticket_has_no_cash(self, metrics: Metrics):
        tk = metrics.tickets
        tender = tk["cash"].fillna(0) + tk["cheque"].fillna(0) + tk["transfer"].fillna(0)
        fully_credit = tk[(tender == 0) & (tk["credit_account"].fillna(0) > 0) & (tk["revenue"] > 0)]
        if fully_credit.empty:
            pytest.skip("no fully-on-account ticket with revenue in this database")
        assert (fully_credit["cash_revenue"].abs() < 0.01).all()

    def test_zero_tender_defaults_to_fully_realized(self, metrics: Metrics):
        """
        A ticket with no tender recorded at all (cash=cheque=transfer=
        credit_account=0) is not evidence the customer owes money - nothing
        was ever put "on account" for it, so it must not silently show up
        as on-account revenue.
        """
        tk = metrics.tickets
        no_tender = tk[(tk["total_tender"] == 0) & (tk["revenue"] != 0)]
        if no_tender.empty:
            pytest.skip("every ticket with revenue in this database records some tender")
        assert (no_tender["on_account_revenue"].abs() < 0.01).all()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_metrics.py::TestCashRealizedSplit -v`
Expected: FAIL with `AttributeError` / `KeyError: 'cash_revenue'` (the
column does not exist yet).

- [ ] **Step 3: Write minimal implementation**

In `poslib/metrics.py`, find the end of the `tickets` cached property:

```python
        df["date"] = df["ticket_time"].dt.normalize()
        df["month"] = df["ticket_time"].dt.to_period("M").dt.to_timestamp()
        df["weekday"] = df["ticket_time"].dt.weekday
        df["hour"] = df["ticket_time"].dt.hour
        df["is_walkin"] = df["customer_id"] == self.walkin_id
        return df
```

Replace it with:

```python
        df["date"] = df["ticket_time"].dt.normalize()
        df["month"] = df["ticket_time"].dt.to_period("M").dt.to_timestamp()
        df["weekday"] = df["ticket_time"].dt.weekday
        df["hour"] = df["ticket_time"].dt.hour
        df["is_walkin"] = df["customer_id"] == self.walkin_id

        # Cash-realized vs on-account, scoped to the Today and Tickets
        # screens only (see docs/superpowers/specs/2026-08-16-tickets-
        # catalog-suppliers-design.md for why this doesn't touch revenue
        # anywhere else). The POS records tender per ticket header, not per
        # line, so a ticket that is partly on account has every one of its
        # lines prorated by the same ratio - the best the data supports.
        # A ticket with no tender recorded at all defaults to fully
        # realized: silence is not evidence a customer owes money.
        df["realized_tender"] = (df["cash"].fillna(0) + df["cheque"].fillna(0) +
                                 df["transfer"].fillna(0))
        df["total_tender"] = df["realized_tender"] + df["credit_account"].fillna(0)
        df["realized_share"] = np.where(df["total_tender"] > 0,
                                        df["realized_tender"] / df["total_tender"], 1.0)
        df["cash_revenue"] = df["revenue"] * df["realized_share"]
        df["on_account_revenue"] = df["revenue"] - df["cash_revenue"]
        return df
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_metrics.py::TestCashRealizedSplit -v`
Expected: PASS (or SKIP for any test whose precondition doesn't hold in the
current data — that's fine, it still ran without error).

- [ ] **Step 5: Commit**

```bash
git add poslib/metrics.py tests/test_metrics.py
git commit -m "feat: add cash-realized vs on-account split to tickets"
```

---

## Task 2: Tender-reconciliation and on-account-vs-receivables Data Quality checks

**Files:**
- Modify: `poslib/metrics.py` (`data_quality()`, currently ending around
  the `"coverage": {...}` block before its closing `}`)
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: `Metrics.tickets` columns from Task 1
  (`total_tender`, `on_account_revenue`), `Metrics.collections`,
  `Metrics.receivables_summary()`.
- Produces: `data_quality()`'s returned dict gains two new keys:
  `tender_reconciliation` (`{"tickets_checked": int, "tickets_mismatched":
  int, "share_mismatched": float, "max_gap": float}`) and
  `on_account_reconciliation` (`{"on_account_all_time": float,
  "collections_all_time": float, "expected_receivables": float,
  "actual_receivables": float, "gap": float, "explains_receivables":
  bool}`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_metrics.py`, inside `TestDataQuality`:

```python
    def test_tender_reconciliation_is_reported(self, metrics: Metrics):
        tr = metrics.data_quality()["tender_reconciliation"]
        assert tr["tickets_checked"] > 0
        assert 0.0 <= tr["share_mismatched"] <= 1.0
        assert tr["max_gap"] >= 0

    def test_on_account_reconciliation_does_not_raise(self, metrics: Metrics):
        oar = metrics.data_quality()["on_account_reconciliation"]
        assert oar["on_account_all_time"] >= 0
        assert oar["actual_receivables"] >= 0
        assert isinstance(oar["explains_receivables"], bool)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_metrics.py::TestDataQuality::test_tender_reconciliation_is_reported -v`
Expected: FAIL with `KeyError: 'tender_reconciliation'`.

- [ ] **Step 3: Write minimal implementation**

In `poslib/metrics.py`, inside `data_quality()`, find:

```python
            "coverage": {
                "first_sale": self.data_range["first"],
                "last_sale": self.data_range["last"],
                "days": self.data_range["days"],
                "age_hours": self.data_range["age_hours"],
            },
        }
```

Replace with:

```python
            "coverage": {
                "first_sale": self.data_range["first"],
                "last_sale": self.data_range["last"],
                "days": self.data_range["days"],
                "age_hours": self.data_range["age_hours"],
            },

            "tender_reconciliation": self._tender_reconciliation(),
            "on_account_reconciliation": self._on_account_reconciliation(),
        }

    def _tender_reconciliation(self) -> dict[str, Any]:
        """
        How well Cash+Cheque+Transfer+CreditAccount adds up to the ticket's
        own Total. The cash-realized split (see `tickets`) uses the tender
        total as its own denominator regardless, so this never affects the
        split - it only says how much to trust it.
        """
        tk = self.tickets
        gap = (tk["total_tender"] - tk["total"].fillna(0)).abs()
        mismatched = tk[gap > 1]
        return {
            "tickets_checked": int(len(tk)),
            "tickets_mismatched": int(len(mismatched)),
            "share_mismatched": (len(mismatched) / len(tk)) if len(tk) else 0.0,
            "max_gap": float(gap.max()) if not tk.empty else 0.0,
        }

    def _on_account_reconciliation(self) -> dict[str, Any]:
        """
        Cross-checks the new on-account figure against the existing
        Receivables total, which is computed an entirely different way
        (from Customer.balance, not from ticket tenders). They are not
        expected to match exactly - opening balances and any adjustment
        made directly in the POS are invisible to ticket-level tender data
        - but a wildly different order of magnitude would mean one of the
        two is wrong.
        """
        on_account_all = float(self.tickets["on_account_revenue"].sum())
        collections_all = float(self.collections["amount"].sum())
        expected = on_account_all - collections_all
        actual = float(self.receivables_summary()["total"])
        gap = actual - expected
        return {
            "on_account_all_time": on_account_all,
            "collections_all_time": collections_all,
            "expected_receivables": expected,
            "actual_receivables": actual,
            "gap": gap,
            "explains_receivables": bool(expected and 0.5 <= actual / expected <= 2.0),
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_metrics.py::TestDataQuality -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add poslib/metrics.py tests/test_metrics.py
git commit -m "feat: report tender and on-account reconciliation on the data-quality screen"
```

---

## Task 3: Show the two new checks on the Data Quality screen

**Files:**
- Modify: `templates/dataquality.html`
- Modify: `locales/en.json`, `locales/fr.json`, `locales/ar.json`
- Test: `tests/test_i18n_and_app.py` (existing tests cover this
  automatically once the keys exist — see Step 4)

**Interfaces:**
- Consumes: `dq.tender_reconciliation`, `dq.on_account_reconciliation` from
  Task 2 (already passed to the template as `dq` — see `app.py`'s
  `page_dataquality()`, unchanged by this task).

- [ ] **Step 1: Add the locale keys**

In `locales/en.json`, inside the `"dataquality"` object, immediately after
the `"margin_check_handled"` line (before `"verification_title"`), add:

```json
    "tender_title": "How well payment amounts add up to the ticket total",
    "tender_body": "{mismatched} tickets out of {checked} ({share}) have Cash + Cheque + Transfer + On-account that does not match the ticket's own total, by up to {gap}.",
    "tender_handled": "The cash-realized split always uses the payment total itself as its base, not the ticket total, so this never throws the split off - it only says how often the two disagree.",

    "on_account_title": "Cross-check: on-account sales against money owed",
    "on_account_body": "All-time on-account sales of {on_account} minus {collections} collected back leaves an expected {expected} still owed. The Money Owed screen currently shows {actual}.",
    "on_account_handled": "These are worked out two completely different ways - one from ticket payment amounts, one from customer balances - so they will not match exactly. A gap this size is expected from opening balances and anything adjusted directly in the till software.",
```

In `locales/fr.json`, at the same position, add:

```json
    "tender_title": "À quel point les montants payés correspondent au total du ticket",
    "tender_body": "{mismatched} tickets sur {checked} ({share}) ont un total Espèces + Chèque + Virement + À crédit qui ne correspond pas au total du ticket, avec un écart pouvant aller jusqu'à {gap}.",
    "tender_handled": "La répartition ventes encaissées / à crédit se base toujours sur le total des paiements lui-même, jamais sur le total du ticket, donc ceci ne fausse jamais la répartition - cela indique seulement à quelle fréquence les deux diffèrent.",

    "on_account_title": "Vérification croisée : ventes à crédit et créances",
    "on_account_body": "Les ventes à crédit depuis toujours ({on_account}) moins les {collections} déjà encaissés laissent un solde attendu de {expected} encore dû. L'écran Créances affiche actuellement {actual}.",
    "on_account_handled": "Ces deux montants sont calculés de deux façons entièrement différentes - l'un à partir des paiements des tickets, l'autre à partir des soldes clients - ils ne correspondront donc pas exactement. Un écart de cette taille est normal, dû aux soldes d'ouverture et à tout ajustement fait directement dans le logiciel de caisse.",
```

In `locales/ar.json`, at the same position, add:

```json
    "tender_title": "مدى تطابق مبالغ الدفع مع إجمالي الفاتورة",
    "tender_body": "{mismatched} فاتورة من أصل {checked} ({share}) لا يتطابق فيها مجموع نقداً + شيك + تحويل + على الحساب مع إجمالي الفاتورة نفسه، بفارق يصل إلى {gap}.",
    "tender_handled": "تقسيم المبيعات المحصّلة نقداً عن المبيعات على الحساب يعتمد دائماً على مجموع الدفعات نفسه، وليس على إجمالي الفاتورة، لذلك هذا لا يؤثر أبداً على التقسيم - فقط يوضح مدى تكرار هذا الاختلاف.",

    "on_account_title": "تحقق تقاطعي: المبيعات على الحساب مقابل الديون",
    "on_account_body": "المبيعات على الحساب منذ البداية ({on_account}) ناقص {collections} تم تحصيلها يترك رصيداً متوقعاً قدره {expected} لا يزال مستحقاً. تعرض شاشة الديون حالياً {actual}.",
    "on_account_handled": "يُحسب هذان الرقمان بطريقتين مختلفتين تماماً - أحدهما من مبالغ دفع الفواتير، والآخر من أرصدة الزبائن - لذلك لن يتطابقا تماماً. فارق بهذا الحجم متوقّع بسبب الأرصدة الافتتاحية وأي تعديل يتم مباشرة داخل برنامج الصندوق.",
```

- [ ] **Step 2: Run the locale-parity test to verify it still passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_i18n_and_app.py::TestLocales -v`
Expected: PASS (all three files gained exactly the same keys).

- [ ] **Step 3: Add the panels to the template**

In `templates/dataquality.html`, find the closing of the "quirks" section:

```html
  {{ issue(
      t('dataquality.margin_check_title'),
      t('dataquality.margin_check_body',
        value=t.money(dq.reported_vs_calculated_margin.difference),
        share=t.percent(dq.reported_vs_calculated_margin.share, 3)),
      t('dataquality.margin_check_handled')) }}
</section>
```

Replace with:

```html
  {{ issue(
      t('dataquality.margin_check_title'),
      t('dataquality.margin_check_body',
        value=t.money(dq.reported_vs_calculated_margin.difference),
        share=t.percent(dq.reported_vs_calculated_margin.share, 3)),
      t('dataquality.margin_check_handled')) }}

  {{ issue(
      t('dataquality.tender_title'),
      t('dataquality.tender_body',
        mismatched=t.number(dq.tender_reconciliation.tickets_mismatched),
        checked=t.number(dq.tender_reconciliation.tickets_checked),
        share=t.percent(dq.tender_reconciliation.share_mismatched),
        gap=t.money(dq.tender_reconciliation.max_gap)),
      t('dataquality.tender_handled')) }}

  {{ issue(
      t('dataquality.on_account_title'),
      t('dataquality.on_account_body',
        on_account=t.money(dq.on_account_reconciliation.on_account_all_time),
        collections=t.money(dq.on_account_reconciliation.collections_all_time),
        expected=t.money(dq.on_account_reconciliation.expected_receivables),
        actual=t.money(dq.on_account_reconciliation.actual_receivables)),
      t('dataquality.on_account_handled')) }}
</section>
```

- [ ] **Step 4: Run the full dashboard test to verify the page still loads in every language**

Run: `.venv\Scripts\python.exe -m pytest tests/test_i18n_and_app.py::TestDashboard -v -k data`

Wait — there is no existing test targeting `/data-quality` by name; the
relevant coverage is `test_every_page_loads_in_every_language`, which
already includes `/data-quality` in its `PAGES` list. Run the whole class
instead:

Run: `.venv\Scripts\python.exe -m pytest tests/test_i18n_and_app.py::TestDashboard -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add templates/dataquality.html locales/en.json locales/fr.json locales/ar.json
git commit -m "feat: show tender and on-account reconciliation on the data-quality screen"
```

---

## Task 4: Parameterize `today()` by date and switch its comparisons to cash-realized

**Files:**
- Modify: `poslib/metrics.py` (the `today()` method, lines ~480-591 in the
  current file)
- Test: `tests/test_metrics.py`

**Interfaces:**
- Produces: `Metrics.today(target_date: datetime.date | None = None) ->
  dict[str, Any]`. Same return shape as before, plus `cash_revenue` and
  `on_account_revenue` inside `today`/`yesterday`/`last_week_same_day`.
  Calling `today()` with no argument behaves exactly as it did before
  (defaults to `self.now.date()`), so no existing caller breaks.
  `vs_yesterday`/`vs_last_week`/`vs_weekday_avg`/`weekday_average` now
  compare `cash_revenue`, not invoiced `revenue`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_metrics.py`, inside a new `TestTodayByDate` class placed
right after the new `TestCashRealizedSplit` class from Task 1:

```python
class TestTodayByDate:

    def test_default_still_means_the_actual_today(self, metrics: Metrics):
        assert metrics.today()["today"]["date"] == metrics.now.date()

    def test_target_date_overrides_today(self, metrics: Metrics):
        first = metrics.data_range["first"]
        if first is None:
            pytest.skip("no sales in this database")
        a_day = first.date()
        d = metrics.today(target_date=a_day)
        assert d["today"]["date"] == a_day

    def test_cash_and_on_account_are_present_and_add_up(self, metrics: Metrics):
        d = metrics.today()
        for bucket in ("today", "yesterday", "last_week_same_day"):
            row = d[bucket]
            assert abs(row["cash_revenue"] + row["on_account_revenue"] - row["revenue"]) < 0.01

    def test_a_past_day_is_not_clipped_to_now(self, metrics: Metrics):
        """
        Viewing a finished past day should show that whole day, not the
        slice up to the current wall-clock time - only the live "today"
        gets clipped for fairness against still-finishing days.
        """
        first = metrics.data_range["first"]
        if first is None:
            pytest.skip("no sales in this database")
        a_day = first.date()
        if a_day == metrics.now.date():
            pytest.skip("first sale happens to be today in this database")
        clipped = metrics.sales[(metrics.sales["ticket_time"].dt.date == a_day) &
                                (metrics.sales["ticket_time"].dt.time <= metrics.now.time())]
        whole_day = metrics.sales[metrics.sales["ticket_time"].dt.date == a_day]
        if len(whole_day) == len(clipped):
            pytest.skip("this particular day has no sales after the current time of day")
        d = metrics.today(target_date=a_day)
        assert abs(d["today"]["revenue"] - float(whole_day["amount"].sum())) < 0.01
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_metrics.py::TestTodayByDate -v`
Expected: FAIL with `TypeError: today() got an unexpected keyword argument 'target_date'`.

- [ ] **Step 3: Write minimal implementation**

In `poslib/metrics.py`, replace the entire `today()` method (from
`def today(self) -> dict[str, Any]:` through its closing `}` and the blank
line before the next `# ===` section header) with:

```python
    def today(self, target_date: datetime.date | None = None) -> dict[str, Any]:
        """
        Everything the Today screen shows, for any single day - defaults to
        the actual current day when `target_date` is not given, which is
        every existing caller's behaviour unchanged.

        "Sales" means cash-realized only: Cash + Cheque + Transfer tender,
        never the portion of a sale put on the customer's account. The
        on-account portion is reported alongside it, always separately,
        never folded in - see `tickets`' `cash_revenue`/`on_account_revenue`
        columns and docs/superpowers/specs/2026-08-16-tickets-catalog-
        suppliers-design.md for why this definition is scoped to this
        screen and the Tickets screen only.

        Comparisons are chosen to be fair rather than flattering:
          - the day before target_date, for a same-shape comparison
          - the same weekday the week before, because a Sunday is not a
            Tuesday
          - the average of the last 8 of the same weekday, so one freak day
            does not set the bar
        When target_date is the live current day, every comparison is cut
        off at the same time of day target_date has reached so far, so an
        in-progress day is compared fairly against finished ones. When
        target_date is a day that has already finished, nothing is clipped
        - the whole day is compared against whole days.
        """
        target = target_date or self.now.date()
        is_current_day = target == self.now.date()
        day_before = target - datetime.timedelta(days=1)
        same_day_last_week = target - datetime.timedelta(days=7)
        cutoff_time = self.now.time() if is_current_day else None

        def day_slice(d: datetime.date, until_time: datetime.time | None = None
                      ) -> pd.DataFrame:
            s = self.sales[self.sales["ticket_time"].dt.date == d]
            if until_time is not None:
                s = s[s["ticket_time"].dt.time <= until_time]
            return s

        def ticket_slice(d: datetime.date, until_time: datetime.time | None = None
                         ) -> pd.DataFrame:
            tk = self.tickets[self.tickets["ticket_time"].dt.date == d]
            if until_time is not None:
                tk = tk[tk["ticket_time"].dt.time <= until_time]
            return tk

        def day_stats(d: datetime.date, clip: bool = False) -> dict[str, Any]:
            cut = cutoff_time if clip else None
            s = day_slice(d, cut)
            tk = ticket_slice(d, cut)
            known = s[s["cost_known"]]
            rev = float(s["amount"].sum())
            n = int(tk["receipt_id"].nunique())
            return {
                "date": d,
                "revenue": rev,
                "cash_revenue": float(tk["cash_revenue"].sum()),
                "on_account_revenue": float(tk["on_account_revenue"].sum()),
                "gross_profit": float(s["gross_profit"].sum()),
                "margin_pct": (float(known["gross_profit"].sum()) /
                               float(known["amount"].sum())) if float(known["amount"].sum()) else None,
                "tickets": n,
                "avg_basket": rev / n if n else 0.0,
                "units": float(s["qty"].sum()),
            }

        now_stats = day_stats(target, clip=True)
        yest = day_stats(day_before, clip=True)
        last_week = day_stats(same_day_last_week, clip=True)

        # Average of the last 8 occurrences of this weekday, cash-realized,
        # up to the same time of day, ignoring days the shop took nothing.
        weekday = target.weekday()
        same_weekday_totals: list[float] = []
        d = target - datetime.timedelta(days=7)
        while len(same_weekday_totals) < 8 and d >= (self.data_range["first"].date()
                                                     if self.data_range["first"] is not None else target):
            tk = ticket_slice(d, cutoff_time)
            v = float(tk["cash_revenue"].sum())
            if v > 0:
                same_weekday_totals.append(v)
            d -= datetime.timedelta(days=7)
        weekday_avg = float(np.mean(same_weekday_totals)) if same_weekday_totals else 0.0

        # How the money came in on target_date - the whole day, never
        # clipped, since there is nothing "in progress" about a payments
        # breakdown for a day already being fully shown elsewhere on the
        # page.
        tk_target = self.tickets[self.tickets["ticket_time"].dt.date == target]
        payments = {
            "cash": float(tk_target["cash"].sum()),
            "cheque": float(tk_target["cheque"].sum()),
            "transfer": float(tk_target["transfer"].sum()),
            "credit": float(tk_target["credit_account"].sum()),
        }

        s_target = day_slice(target)
        top_items = (s_target.groupby(["item_id", "item_name"], as_index=False)
                     .agg(qty=("qty", "sum"), revenue=("amount", "sum"),
                          gross_profit=("gross_profit", "sum"))
                     .sort_values("revenue", ascending=False)
                     .head(10))

        by_hour = (s_target.groupby("hour", as_index=False)
                   .agg(revenue=("amount", "sum"))
                   .sort_values("hour"))

        top_customers = (s_target[s_target["customer_id"] != self.walkin_id]
                         .groupby("customer_id", as_index=False)
                         .agg(revenue=("amount", "sum")))
        if not top_customers.empty:
            top_customers = (top_customers
                             .merge(self.customers[["customer_id", "customer_name"]],
                                    on="customer_id", how="left")
                             .sort_values("revenue", ascending=False).head(5))

        def delta(a: float, b: float) -> float | None:
            return ((a - b) / b) if b else None

        return {
            "today": now_stats,
            "yesterday": yest,
            "last_week_same_day": last_week,
            "weekday_average": weekday_avg,
            "weekday_sample": len(same_weekday_totals),
            "vs_yesterday": delta(now_stats["cash_revenue"], yest["cash_revenue"]),
            "vs_last_week": delta(now_stats["cash_revenue"], last_week["cash_revenue"]),
            "vs_weekday_avg": delta(now_stats["cash_revenue"], weekday_avg),
            "payments": payments,
            "collections_today": float(
                self.collections[self.collections["ticket_time"].dt.date == target]["amount"].sum()),
            "top_items": top_items,
            "by_hour": by_hour,
            "top_customers": top_customers,
            "data_age_hours": self.data_range["age_hours"],
            "last_sale_time": self.data_range["last"],
            "is_current_day": is_current_day,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_metrics.py::TestTodayByDate -v`
Expected: PASS.

Also run the full metrics suite to confirm nothing else broke (`today()`
is used by `app.py`'s `/today` route, which Task 5 updates next):

Run: `.venv\Scripts\python.exe -m pytest tests/test_metrics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add poslib/metrics.py tests/test_metrics.py
git commit -m "feat: let today() target any date and compare cash-realized revenue"
```

---

## Task 5: `period_stats()` for a multi-day range

**Files:**
- Modify: `poslib/metrics.py` (add near `today()`, same "PAGE 1 - TODAY /
  LIVE" section)
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: `Metrics._window_range` (existing helper), `Metrics.tickets`
  columns from Task 1.
- Produces: `Metrics.period_stats(start: datetime.date, end: datetime.date)
  -> dict[str, Any]` with keys `start`, `end`, `revenue`, `cash_revenue`,
  `on_account_revenue`, `gross_profit`, `margin_pct`, `tickets`,
  `avg_basket`, `units`, `collections`, `top_items` (DataFrame),
  `top_customers` (DataFrame).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_metrics.py`, inside `TestTodayByDate` (from Task 4):

```python
    def test_period_stats_matches_headline_for_all_time(self, metrics: Metrics):
        first = metrics.data_range["first"]
        last = metrics.data_range["last"]
        if first is None:
            pytest.skip("no sales in this database")
        p = metrics.period_stats(first.date(), last.date() + datetime.timedelta(days=1))
        h = metrics.headline()
        assert abs(p["revenue"] - h["revenue"]) < 1.0

    def test_period_stats_cash_and_on_account_add_up(self, metrics: Metrics):
        first = metrics.data_range["first"]
        if first is None:
            pytest.skip("no sales in this database")
        p = metrics.period_stats(first.date(), first.date() + datetime.timedelta(days=30))
        assert abs(p["cash_revenue"] + p["on_account_revenue"] - p["revenue"]) < 0.01

    def test_period_stats_empty_range_does_not_raise(self, metrics: Metrics):
        far_future = datetime.date(2200, 1, 1)
        p = metrics.period_stats(far_future, far_future)
        assert p["revenue"] == 0.0
        assert p["tickets"] == 0
```

Add `import datetime` at the top of `tests/test_metrics.py` if it is not
already imported (check first — several existing tests already use
`datetime.datetime.now()`, so it almost certainly already is).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_metrics.py::TestTodayByDate::test_period_stats_matches_headline_for_all_time -v`
Expected: FAIL with `AttributeError: 'Metrics' object has no attribute 'period_stats'`.

- [ ] **Step 3: Write minimal implementation**

In `poslib/metrics.py`, immediately after the `today()` method (before the
`# =====================================================================` /
`#  PAGE 2 - TREND` section header), add:

```python
    def period_stats(self, start: datetime.date, end: datetime.date) -> dict[str, Any]:
        """
        The same shape of figures as `today()`, for an arbitrary whole-day
        range - the Today screen's view when a range preset (this week,
        last 7 days, a custom multi-day range) is chosen instead of a
        single day. `end` is inclusive, matching `_window_range`.
        """
        s = self._window_range(self.sales, start, end)
        tk = self._window_range(self.tickets, start, end)
        coll = self._window_range(self.collections, start, end)

        revenue = float(s["amount"].sum())
        known = s[s["cost_known"]]
        rev_known = float(known["amount"].sum())
        gp_known = float(known["gross_profit"].sum())
        n_tickets = int(tk["receipt_id"].nunique())

        top_items = (s.groupby(["item_id", "item_name"], as_index=False)
                     .agg(qty=("qty", "sum"), revenue=("amount", "sum"),
                          gross_profit=("gross_profit", "sum"))
                     .sort_values("revenue", ascending=False).head(10))

        top_customers = (s[s["customer_id"] != self.walkin_id]
                         .groupby("customer_id", as_index=False)
                         .agg(revenue=("amount", "sum")))
        if not top_customers.empty:
            top_customers = (top_customers
                             .merge(self.customers[["customer_id", "customer_name"]],
                                    on="customer_id", how="left")
                             .sort_values("revenue", ascending=False).head(5))

        return {
            "start": start,
            "end": end,
            "revenue": revenue,
            "cash_revenue": float(tk["cash_revenue"].sum()),
            "on_account_revenue": float(tk["on_account_revenue"].sum()),
            "gross_profit": float(s["gross_profit"].sum()),
            "margin_pct": (gp_known / rev_known) if rev_known else None,
            "tickets": n_tickets,
            "avg_basket": (revenue / n_tickets) if n_tickets else 0.0,
            "units": float(s["qty"].sum()),
            "collections": float(coll["amount"].sum()),
            "top_items": top_items,
            "top_customers": top_customers,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_metrics.py::TestTodayByDate -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add poslib/metrics.py tests/test_metrics.py
git commit -m "feat: add period_stats() for the Today screen's range view"
```

---

## Task 6: `/today` route becomes period-selectable

**Files:**
- Modify: `app.py` (`page_today()`, currently lines ~249-280)
- Test: `tests/test_i18n_and_app.py`

**Interfaces:**
- Consumes: `Metrics.today(target_date)` (Task 4), `Metrics.period_stats`
  (Task 5), `date_range_from_request()` (existing, unchanged), `rows()`
  (existing helper).
- Produces: `/today` accepts the existing `?start=`/`?end=` query params.
  When they resolve to a single day, renders the day view (unchanged
  template contract from before, plus the new cash/on-account fields).
  When they resolve to a multi-day range (or a named preset shortcut —
  see Step 3), renders a period view. No params at all still means "today",
  exactly as before.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_i18n_and_app.py`, inside `TestDashboard`:

```python
    @pytest.mark.parametrize("query", [
        "", "?start=2025-01-01&end=2025-01-01", "?start=2025-01-01&end=2025-01-07",
        "?start=2025-01-01&end=2025-01-31",
    ])
    def test_today_accepts_single_day_and_range_queries(self, client, query):
        response = client.get(f"/today{query}")
        assert response.status_code == 200, f"/today{query} returned {response.status_code}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest "tests/test_i18n_and_app.py::TestDashboard::test_today_accepts_single_day_and_range_queries" -v`

This will likely already PASS for the single-day cases (the route already
ignores unrecognised query params today without erroring — `date_range_from_request`
isn't even called yet by `/today`) — that's fine, it just means Step 2 here
is a weaker signal than usual. What matters is Step 4 below, after the
route actually starts *using* the range. Note the expectation and move on;
do not spend time forcing a red bar that the route's current, pre-change
behaviour happens to pass by accident.

- [ ] **Step 3: Write the implementation**

In `app.py`, replace the whole `page_today()` function:

```python
@app.route("/today")
def page_today() -> str:
    m, etl, conn = open_metrics()
    try:
        t = get_translator(current_language())
        start, end = date_range_from_request()

        if start is None and end is None:
            single_day = m.now.date()
        elif start is not None and (end is None or end == start):
            single_day = start
        elif start is None and end is not None:
            single_day = end
        else:
            # Both given and different (the only way to reach this branch,
            # since the branch above already caught start == end) - a real
            # multi-day range.
            single_day = None

        if single_day is not None:
            d = m.today(target_date=single_day)
            period = None
        else:
            d = None
            period = m.period_stats(start, end)

        cache = etl.cache_info()

        if d is not None:
            by_hour = d["by_hour"]
            all_hours = m.sales["hour"].dropna()
            lo = int(all_hours.quantile(0.01)) if not all_hours.empty else 8
            hi = int(all_hours.quantile(0.99)) if not all_hours.empty else 20
            hours = list(range(max(0, lo), min(23, hi) + 1))
            hour_map = dict(zip(by_hour["hour"].tolist(),
                                by_hour["revenue"].tolist())) if not by_hour.empty else {}
            hour_chart = charts.combo_chart(
                labels=[f"{h:02d}" for h in hours],
                bars=[float(hour_map.get(h, 0.0)) for h in hours],
                rtl=t.is_rtl, max_labels=24)
            weekday_name = t.weekday_name(single_day.weekday())
        else:
            hour_chart = None
            weekday_name = None

        return render_template(
            "today.html",
            d=d,
            period=period,
            single_day=single_day,
            weekday_name=weekday_name,
            top_items=rows(d["top_items"]) if d is not None else rows(period["top_items"]),
            top_customers=rows(d["top_customers"]) if d is not None else rows(period["top_customers"]),
            hour_chart=hour_chart,
            cache=cache,
        )
    finally:
        conn.close()
```

Note the branch on `start`/`end`: a single day can arrive as `start=end=X`
(what the preset links produced in Task 7), as only `start` given with no
`end`, or as only `end` given with no `start` — all three collapse to
viewing that one day. Anything else (a genuine `start != end` pair) is a
range. No params at all is "today".

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest "tests/test_i18n_and_app.py::TestDashboard::test_today_accepts_single_day_and_range_queries" -v`
Expected: PASS.

This will currently fail at template-render time because `today.html`
doesn't yet know about `period`/`single_day`/a `None` `d` - that's expected
and gets fixed in Task 7. Do not consider this task done until Task 7 also
passes; run the two together once Task 7 lands:

Run: `.venv\Scripts\python.exe -m pytest tests/test_i18n_and_app.py::TestDashboard -v`

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_i18n_and_app.py
git commit -m "feat: /today accepts a single-day or range query"
```

---

## Task 7: Today template — preset picker, cash vs on-account, range view

**Files:**
- Modify: `templates/today.html`
- Modify: `locales/en.json`, `locales/fr.json`, `locales/ar.json`

**Interfaces:**
- Consumes: everything `page_today()` now passes (Task 6): `d` (single-day
  view, or `None`), `period` (range view, or `None`), `single_day`,
  `weekday_name`, `top_items`, `top_customers`, `hour_chart`.

- [ ] **Step 1: Add the locale keys**

In `locales/en.json`, inside `"common"` (anywhere after `"clear_range"` is
fine — e.g. right after it), add:

```json
    "this_week": "This week",
    "last_7_days": "Last 7 days",
    "custom_range": "Custom range",
```

In `locales/fr.json`, same position:

```json
    "this_week": "Cette semaine",
    "last_7_days": "7 derniers jours",
    "custom_range": "Période personnalisée",
```

In `locales/ar.json`, same position:

```json
    "this_week": "هذا الأسبوع",
    "last_7_days": "آخر 7 أيام",
    "custom_range": "فترة مخصصة",
```

In `locales/en.json`, inside `"today"`, add these new keys (anywhere in the
object; appending after `"chart_revenue_axis"` is fine):

```json
    "preset_title": "Choose a period",
    "on_account": "On account (not yet paid)",
    "on_account_note": "Money the customer still owes on this period's sales - not counted as a sale until it's actually collected.",
    "cash_realized_note": "Only Cash, Cheque and Transfer tender counts as a sale here. Anything put on a customer's account is shown separately, on the right.",
    "period_title": "{start} to {end}",
    "period_revenue": "Sales (cash-realized)",
    "period_tickets": "Tickets",
    "period_margin": "Margin",
    "view_tickets": "See every ticket in this period"
```

In `locales/fr.json`, inside `"today"`, add:

```json
    "preset_title": "Choisir une période",
    "on_account": "À crédit (pas encore payé)",
    "on_account_note": "Argent que le client doit encore sur les ventes de cette période - non compté comme une vente tant qu'il n'est pas réellement encaissé.",
    "cash_realized_note": "Seuls les paiements en espèces, chèque et virement comptent comme une vente ici. Tout ce qui est mis sur le compte d'un client est affiché séparément, à droite.",
    "period_title": "Du {start} au {end}",
    "period_revenue": "Ventes (encaissées)",
    "period_tickets": "Tickets",
    "period_margin": "Marge",
    "view_tickets": "Voir tous les tickets de cette période"
```

In `locales/ar.json`, inside `"today"`, add:

```json
    "preset_title": "اختر فترة",
    "on_account": "على الحساب (لم يُدفع بعد)",
    "on_account_note": "أموال لا يزال الزبون مديناً بها من مبيعات هذه الفترة - لا تُحتسب كبيع حتى يتم تحصيلها فعلياً.",
    "cash_realized_note": "فقط الدفع نقداً أو بشيك أو تحويل يُحتسب كبيع هنا. أي مبلغ وُضع على حساب الزبون يظهر بشكل منفصل، على اليمين.",
    "period_title": "من {start} إلى {end}",
    "period_revenue": "المبيعات (المحصّلة نقداً)",
    "period_tickets": "الفواتير",
    "period_margin": "الهامش",
    "view_tickets": "عرض كل فواتير هذه الفترة"
```

- [ ] **Step 2: Run the locale-parity test**

Run: `.venv\Scripts\python.exe -m pytest tests/test_i18n_and_app.py::TestLocales -v`
Expected: PASS.

- [ ] **Step 3: Add the preset picker**

In `templates/today.html`, right after the `{% block content %}` line and
before the existing `<div class="page-head">`, add:

```html
<section class="panel" style="margin-bottom: 18px;">
  <h2>{{ t('today.preset_title') }}</h2>
  {% set today_str = now.strftime('%Y-%m-%d') %}
  {% set yesterday_str = (now - timedelta(days=1)).strftime('%Y-%m-%d') %}
  {% set week_start_str = (now - timedelta(days=now.weekday())).strftime('%Y-%m-%d') %}
  {% set last7_str = (now - timedelta(days=6)).strftime('%Y-%m-%d') %}
  <div class="filter-row">
    <a class="btn {{ 'btn-primary' if not request.args.get('start') and not request.args.get('end') else '' }}"
       href="?lang={{ lang }}">{{ t('common.today') }}</a>
    <a class="btn {{ 'btn-primary' if request.args.get('start') == yesterday_str and request.args.get('end') == yesterday_str else '' }}"
       href="?lang={{ lang }}&start={{ yesterday_str }}&end={{ yesterday_str }}">{{ t('common.yesterday') }}</a>
    <a class="btn {{ 'btn-primary' if request.args.get('start') == week_start_str and request.args.get('end') == today_str else '' }}"
       href="?lang={{ lang }}&start={{ week_start_str }}&end={{ today_str }}">{{ t('common.this_week') }}</a>
    <a class="btn {{ 'btn-primary' if request.args.get('start') == last7_str and request.args.get('end') == today_str else '' }}"
       href="?lang={{ lang }}&start={{ last7_str }}&end={{ today_str }}">{{ t('common.last_7_days') }}</a>
  </div>
  {% include "_daterange.html" %}
</section>
```

This needs `timedelta` available in the template. In `app.py`'s
`inject_globals()` context processor, find:

```python
    return {
        "t": t,
        "lang": lang,
```

Replace with:

```python
    return {
        "t": t,
        "lang": lang,
        "timedelta": datetime.timedelta,
```

- [ ] **Step 4: Branch the page body on `d` vs `period`**

In `templates/today.html`, wrap the entire existing body (from
`{% if d.data_age_hours is not none ... %}` through the end of the
`top_items`/`top_customers` `<section class="split-2">`, i.e. everything
currently between the preset picker you just added and
`{% endblock %}`) inside `{% if d %} ... {% else %} ... {% endif %}`:

```html
{% if d %}
  {% if d.data_age_hours is not none and d.data_age_hours > 24 %}
    <div class="banner banner-warn">
      {{ t('app.stale_warning', age=t.relative_hours(d.data_age_hours)) }}
      {{ t('app.data_from', when=t.datetime(d.last_sale_time)) }}
    </div>
  {% endif %}

  <div class="page-head">
    <h1>{{ t('today.title') }}</h1>
    <p>{{ t.date(single_day) }}{% if weekday_name %} · {{ weekday_name }}{% endif %}</p>
  </div>

  {# ------------------------------------------------------- the big numbers #}
  <section class="tiles tiles-big">
    <div class="tile">
      <div class="label">{{ t('today.revenue') }}</div>
      <div class="value">{{ t.money(d.today.cash_revenue) }}</div>
      <div class="sub">
        {{ delta(d.vs_yesterday) }} {{ t('today.vs_yesterday') }}
      </div>
    </div>

    <div class="tile">
      <div class="label">{{ t('today.on_account') }}</div>
      <div class="value">{{ t.money(d.today.on_account_revenue) }}</div>
      <div class="sub">{{ t('today.on_account_note') }}</div>
    </div>

    <div class="tile">
      <div class="label">{{ t('today.tickets') }}</div>
      <div class="value">{{ t.number(d.today.tickets) }}</div>
      <div class="sub">{{ t('today.avg_basket') }}: {{ t.money(d.today.avg_basket) }}</div>
    </div>

    <div class="tile">
      <div class="label">{{ t('today.gross_profit') }}</div>
      <div class="value">{{ t.money(d.today.gross_profit) }}</div>
      <div class="sub">
        {{ t('today.margin') }}:
        <strong>{{ t.percent(d.today.margin_pct) if d.today.margin_pct is not none else t('common.not_measurable') }}</strong>
      </div>
    </div>
  </section>

  <p class="muted" style="margin: -8px 0 20px; font-size: 13px;">
    {{ t('today.cash_realized_note') }}
  </p>

  {# ------------------------------------------------------------ comparisons #}
  <section class="split-2">
    <div class="panel">
      <h2>{{ t('trend.table_title') }}</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th></th>
              <th class="num">{{ t('common.revenue') }}</th>
              <th class="num">{{ t('common.tickets') }}</th>
              <th class="num">{{ t('today.avg_basket') }}</th>
              <th class="num">{{ t('common.margin_pct') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr class="strong">
              <td>{{ t.date(single_day) }}</td>
              <td class="num">{{ t.money(d.today.cash_revenue) }}</td>
              <td class="num">{{ t.number(d.today.tickets) }}</td>
              <td class="num">{{ t.money(d.today.avg_basket) }}</td>
              <td class="num">{{ t.percent(d.today.margin_pct) if d.today.margin_pct is not none else '—' }}</td>
            </tr>
            <tr>
              <td>{{ t.date(d.yesterday.date) }}</td>
              <td class="num">{{ t.money(d.yesterday.cash_revenue) }}</td>
              <td class="num">{{ t.number(d.yesterday.tickets) }}</td>
              <td class="num">{{ t.money(d.yesterday.avg_basket) }}</td>
              <td class="num">{{ t.percent(d.yesterday.margin_pct) if d.yesterday.margin_pct is not none else '—' }}</td>
            </tr>
            <tr>
              <td>{{ t('today.vs_last_week') }}</td>
              <td class="num">{{ t.money(d.last_week_same_day.cash_revenue) }}</td>
              <td class="num">{{ t.number(d.last_week_same_day.tickets) }}</td>
              <td class="num">{{ t.money(d.last_week_same_day.avg_basket) }}</td>
              <td class="num">{{ t.percent(d.last_week_same_day.margin_pct) if d.last_week_same_day.margin_pct is not none else '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="panel">
      <h2>{{ t('today.payments_title') }}</h2>
      {% set pay_total = d.payments.cash + d.payments.cheque + d.payments.transfer + d.payments.credit %}
      {% if pay_total > 0 %}
        <div class="table-wrap">
          <table>
            <tbody>
              {% for key, label in [('cash', 'today.payment_cash'), ('cheque', 'today.payment_cheque'),
                                    ('transfer', 'today.payment_transfer'), ('credit', 'today.payment_credit')] %}
                {% if d.payments[key] %}
                <tr>
                  <td>{{ t(label) }}</td>
                  <td class="num strong">{{ t.money(d.payments[key]) }}</td>
                  <td class="num muted">{{ t.percent(d.payments[key] / pay_total) }}</td>
                </tr>
                {% endif %}
              {% endfor %}
            </tbody>
          </table>
        </div>
        {% if d.collections_today %}
          <p class="note" style="margin-top: 12px;">
            {{ t('today.collections') }}: <strong>{{ t.money(d.collections_today) }}</strong>
          </p>
        {% endif %}
      {% else %}
        <div class="empty">{{ t('today.no_sales_yet') }}</div>
      {% endif %}
    </div>
  </section>

  {# ------------------------------------------------------------ by the hour #}
  <section class="panel">
    <h2>{{ t('today.by_hour') }}</h2>
    <p class="note">{{ t('today.chart_hour_axis') }} · {{ t('today.chart_revenue_axis') }}</p>
    {{ ch.combo(hour_chart, t) }}
  </section>
{% else %}
  <div class="page-head">
    <h1>{{ t('today.title') }}</h1>
    <p>{{ t('today.period_title', start=t.date(period.start), end=t.date(period.end)) }}</p>
  </div>

  <section class="tiles tiles-big">
    <div class="tile">
      <div class="label">{{ t('today.period_revenue') }}</div>
      <div class="value">{{ t.money(period.cash_revenue) }}</div>
    </div>
    <div class="tile">
      <div class="label">{{ t('today.on_account') }}</div>
      <div class="value">{{ t.money(period.on_account_revenue) }}</div>
      <div class="sub">{{ t('today.on_account_note') }}</div>
    </div>
    <div class="tile">
      <div class="label">{{ t('today.period_tickets') }}</div>
      <div class="value">{{ t.number(period.tickets) }}</div>
      <div class="sub">{{ t('today.avg_basket') }}: {{ t.money(period.avg_basket) }}</div>
    </div>
    <div class="tile">
      <div class="label">{{ t('today.gross_profit') }}</div>
      <div class="value">{{ t.money(period.gross_profit) }}</div>
      <div class="sub">
        {{ t('today.period_margin') }}:
        <strong>{{ t.percent(period.margin_pct) if period.margin_pct is not none else t('common.not_measurable') }}</strong>
      </div>
    </div>
  </section>

  <p class="muted" style="margin: -8px 0 20px; font-size: 13px;">
    {{ t('today.cash_realized_note') }}
  </p>
{% endif %}

{# --------------------------------------------- top sellers, both views #}
<section class="split-2">
  <div class="panel">
    <h2>{{ t('today.top_items') }}</h2>
    {% if top_items %}
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{{ t('common.product') }}</th>
              <th class="num">{{ t('common.quantity') }}</th>
              <th class="num">{{ t('common.revenue') }}</th>
              <th class="num">{{ t('common.gross_profit') }}</th>
            </tr>
          </thead>
          <tbody>
            {% for r in top_items %}
            <tr>
              <td class="name">{{ r.item_name }}</td>
              <td class="num">{{ t.number(r.qty) }}</td>
              <td class="num strong">{{ t.money(r.revenue) }}</td>
              <td class="num">{{ t.money(r.gross_profit) }}</td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    {% else %}
      <div class="empty">{{ t('today.no_sales_yet') }}</div>
    {% endif %}
  </div>

  <div class="panel">
    <h2>{{ t('today.top_customers') }}</h2>
    {% if top_customers %}
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{{ t('common.customer') }}</th>
              <th class="num">{{ t('common.revenue') }}</th>
            </tr>
          </thead>
          <tbody>
            {% for r in top_customers %}
            <tr>
              <td class="name">{{ r.customer_name or '—' }}</td>
              <td class="num strong">{{ t.money(r.revenue) }}</td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    {% else %}
      <div class="empty">{{ t('today.no_sales_yet') }}</div>
    {% endif %}
  </div>
</section>

<p style="margin-top: 8px;">
  <a class="btn"
     href="{{ url_for('page_tickets') }}?lang={{ lang }}{% if single_day %}&start={{ single_day.strftime('%Y-%m-%d') }}&end={{ single_day.strftime('%Y-%m-%d') }}{% elif period %}&start={{ period.start.strftime('%Y-%m-%d') }}&end={{ period.end.strftime('%Y-%m-%d') }}{% endif %}">
    {{ t('today.view_tickets') }}
  </a>
</p>
```

Note: `url_for('page_tickets')` is added by Task 11 — this task's route
link will 500 (`BuildError`) until Task 11 lands. That is expected and
acceptable within this plan's sequencing (each task's own tests must pass
before its commit; the full-suite regression run happens at the very end,
in Task 20, after every task has landed). Do not skip ahead to build
`page_tickets` early just to silence this — Task 11 is next.

Also remove the now-duplicated `<div class="page-head">` — the original
template had one before the `{% if d.data_age_hours ... %}` block; that
whole original opening (including the old page-head) is what Step 4
replaced, so check the diff leaves exactly one `page-head` per branch (one
inside `{% if d %}`, one inside `{% else %}`), not a leftover third one
above the `{% if %}`.

The existing `{% macro delta(value) %}` at the top of the file, and the
`{% block autorefresh %}` at the bottom, are unchanged — leave them exactly
as they are.

- [ ] **Step 5: Manual check, then commit**

Run the app and eyeball it before committing (the automated check for this
task is the route test from Task 6, which needs this template to pass):

```bash
.venv\Scripts\python.exe -m pytest tests/test_i18n_and_app.py::TestDashboard -v
```

Expected: still failing on anything that hits `page_tickets` (Task 11 not
done yet) — that's fine. Everything else should pass. Then:

```bash
git add templates/today.html app.py locales/en.json locales/fr.json locales/ar.json
git commit -m "feat: period-selectable Today screen with cash-realized/on-account tiles"
```

---

## Task 8: `ticket_list()` and `ticket_detail()` in metrics.py

**Files:**
- Modify: `poslib/metrics.py` (new methods, placed in a new
  `# PAGE 1B - TICKETS` section right after `period_stats()`, before the
  `# PAGE 2 - TREND` header)
- Test: `tests/test_metrics.py`

**Interfaces:**
- Produces: `Metrics.ticket_list(start: datetime.date, end: datetime.date)
  -> pd.DataFrame` with columns `receipt_id`, `ticket_no`, `ticket_time`,
  `customer_id`, `customer_name`, `revenue`, `cash_revenue`,
  `on_account_revenue`, `collected`, `total`, `n_lines`.
  `Metrics.ticket_detail(receipt_id: int) -> dict[str, Any] | None` with
  keys `header` (dict) and `lines` (DataFrame with `entry_id`, `item_name`,
  `qty`, `price`, `discount`, `amount`, `is_sale`, `is_return`). Returns
  `None` for an unknown receipt_id.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_metrics.py`, in a new `TestTickets` class placed right
after `TestTodayByDate`:

```python
class TestTickets:

    def test_ticket_list_columns(self, metrics: Metrics):
        first = metrics.data_range["first"]
        if first is None:
            pytest.skip("no sales in this database")
        tl = metrics.ticket_list(first.date(), first.date() + datetime.timedelta(days=60))
        if tl.empty:
            pytest.skip("no tickets in the first 60 days of this database")
        for col in ("receipt_id", "ticket_no", "ticket_time", "customer_name",
                    "revenue", "cash_revenue", "on_account_revenue", "total"):
            assert col in tl.columns

    def test_ticket_list_empty_range(self, metrics: Metrics):
        far_future = datetime.date(2200, 1, 1)
        tl = metrics.ticket_list(far_future, far_future)
        assert tl.empty

    def test_ticket_detail_round_trips_from_ticket_list(self, metrics: Metrics):
        first = metrics.data_range["first"]
        if first is None:
            pytest.skip("no sales in this database")
        tl = metrics.ticket_list(first.date(), first.date() + datetime.timedelta(days=60))
        if tl.empty:
            pytest.skip("no tickets in the first 60 days of this database")
        receipt_id = int(tl.iloc[0]["receipt_id"])
        detail = metrics.ticket_detail(receipt_id)
        assert detail is not None
        assert detail["header"]["receipt_id"] == receipt_id
        assert not detail["lines"].empty

    def test_ticket_detail_unknown_id_returns_none(self, metrics: Metrics):
        assert metrics.ticket_detail(999999999) is None

    def test_ticket_detail_keeps_collection_lines_labelled(self, metrics: Metrics):
        """
        A ticket that mixes a real sale with an account-payment line (a
        "Paiement de règlement") must show that line too, still marked as
        not-a-sale - never silently dropped or folded into the sale total.
        """
        mixed_receipt_ids = (set(metrics.sales["receipt_id"]) &
                             set(metrics.collections["receipt_id"]))
        if not mixed_receipt_ids:
            pytest.skip("no ticket in this database mixes a sale with a collection")
        receipt_id = next(iter(mixed_receipt_ids))
        detail = metrics.ticket_detail(receipt_id)
        assert (~detail["lines"]["is_sale"]).any()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_metrics.py::TestTickets -v`
Expected: FAIL with `AttributeError: 'Metrics' object has no attribute 'ticket_list'`.

- [ ] **Step 3: Write minimal implementation**

In `poslib/metrics.py`, immediately after `period_stats()` (from Task 5),
add:

```python
    # =====================================================================
    #  PAGE 1B - TICKETS
    # =====================================================================

    def ticket_list(self, start: datetime.date, end: datetime.date) -> pd.DataFrame:
        """
        Every ticket in a date range, for the Tickets screen: who, when,
        how much, and how much of that was cash-realized vs put on the
        customer's account. Built from `tickets`, filtered to the range -
        not a new aggregation.
        """
        tk = self._window_range(self.tickets, start, end)
        if tk.empty:
            return tk
        tk = tk.merge(self.customers[["customer_id", "customer_name"]],
                      on="customer_id", how="left")
        tk["customer_name"] = tk["customer_name"].fillna("—")
        return tk[["receipt_id", "ticket_no", "ticket_time", "customer_id",
                   "customer_name", "revenue", "cash_revenue", "on_account_revenue",
                   "collected", "total", "n_lines"]].sort_values(
            "ticket_time", ascending=False).reset_index(drop=True)

    def ticket_detail(self, receipt_id: int) -> dict[str, Any] | None:
        """
        Everything on one ticket, for the drill-down page: its header
        (customer, time, totals, cash-realized vs on-account) and every
        line on it, sales and collections both - a collection line stays
        labelled as a collection, never folded into "sale". Returns None
        if the ticket does not exist.
        """
        tk = self.tickets[self.tickets["receipt_id"] == receipt_id]
        if tk.empty:
            return None
        header = tk.iloc[0].to_dict()

        cust = self.customers[self.customers["customer_id"] == header["customer_id"]]
        header["customer_name"] = (cust.iloc[0]["customer_name"]
                                   if not cust.empty else "—")

        lines = (self.lines[self.lines["receipt_id"] == receipt_id]
                 .sort_values("entry_id"))

        return {
            "header": header,
            "lines": lines[["entry_id", "item_name", "qty", "price", "discount",
                            "amount", "is_sale", "is_return"]],
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_metrics.py::TestTickets -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add poslib/metrics.py tests/test_metrics.py
git commit -m "feat: add ticket_list() and ticket_detail() to metrics"
```

---

## Task 9: `/tickets` and `/tickets/<id>` routes + templates

**Files:**
- Modify: `app.py` (add routes, add `nav.tickets` to `inject_globals()`'s
  `pages` list, add a `home()` mapping entry — not strictly required since
  the default page stays `today`, but add it for consistency with every
  other page)
- Create: `templates/tickets.html`
- Create: `templates/ticket_detail.html`
- Modify: `locales/en.json`, `locales/fr.json`, `locales/ar.json`
- Modify: `tests/test_i18n_and_app.py`

**Interfaces:**
- Consumes: `Metrics.ticket_list`, `Metrics.ticket_detail` (Task 8),
  `date_range_from_request`, `rows`, `row_dict` (all existing).
- Produces: routes `page_tickets` (GET `/tickets`) and `page_ticket`
  (GET `/tickets/<int:receipt_id>`), referenced by name from Task 7's
  `url_for('page_tickets')` link.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_i18n_and_app.py`, inside `TestDashboard`:

```python
    def test_tickets_page_loads(self, client):
        response = client.get("/tickets?start=2020-01-01&end=2030-01-01")
        assert response.status_code == 200

    @pytest.mark.parametrize("lang", LANGUAGES)
    def test_ticket_drilldown_loads_in_every_language(self, client, metrics, lang):
        tl = metrics.ticket_list(datetime.date(2000, 1, 1), datetime.date(2100, 1, 1))
        if tl.empty:
            pytest.skip("no tickets in this database")
        receipt_id = int(tl.iloc[0]["receipt_id"])
        response = client.get(f"/tickets/{receipt_id}?lang={lang}")
        assert response.status_code == 200

    def test_ticket_drilldown_404_for_unknown_id(self, client):
        assert client.get("/tickets/999999999").status_code == 404
```

Add `import datetime` at the top of `tests/test_i18n_and_app.py` if not
already present (check first).

Also update the `PAGES` list a few lines above `TestDashboard`'s existing
`PAGES = [...]` to include `/tickets`:

```python
    PAGES = ["/today", "/trend", "/customers", "/receivables", "/inventory",
             "/products", "/suppliers", "/cash", "/diagnostics", "/data-quality",
             "/tickets"]
```

And update `test_no_untranslated_keys_leak_onto_a_page`'s regex to include
the new `tickets` namespace:

```python
        pattern = re.compile(
            r"\b(?:app|nav|common|today|trend|customers|segments|receivables|"
            r"inventory|products|suppliers|cash|diagnostics|findings|dataquality|"
            r"tickets)"
            r"\.[a-z_][a-z_0-9.]*")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_i18n_and_app.py::TestDashboard -v -k ticket`
Expected: FAIL (`404` for `/tickets` since the route doesn't exist yet, or
a `BuildError` from Task 7's `url_for('page_tickets')`).

- [ ] **Step 3: Add the locale keys**

In `locales/en.json`, add `"tickets": "Tickets"` to the `"nav"` object
(after `"suppliers": "Suppliers",` is a natural spot), and add a whole new
top-level `"tickets"` object — place it right after the `"today"` object
closes (i.e. between `"today": { ... }` and `"trend": { ... }`):

```json
  "tickets": {
    "title": "Tickets",
    "subtitle": "Every ticket for the period you choose",
    "col_time": "Time",
    "col_ticket_no": "Ticket #",
    "col_customer": "Customer",
    "col_total": "Total",
    "col_cash": "Cash-realized",
    "col_on_account": "On account",
    "col_lines": "Lines",
    "status_cash": "Fully paid",
    "status_split": "Partly on account",
    "status_account": "Fully on account",
    "empty": "No tickets in this period.",
    "detail_back": "Back to tickets",
    "detail_lines_title": "Lines",
    "detail_payment_title": "Payment",
    "detail_collection_line": "Account payment, not a sale",
    "col_qty": "Qty",
    "col_price": "Price",
    "col_discount": "Discount",
    "col_amount": "Amount"
  },
```

In `locales/fr.json`, add `"tickets": "Tickets"` to `"nav"`, and:

```json
  "tickets": {
    "title": "Tickets",
    "subtitle": "Tous les tickets de la période choisie",
    "col_time": "Heure",
    "col_ticket_no": "N° ticket",
    "col_customer": "Client",
    "col_total": "Total",
    "col_cash": "Encaissé",
    "col_on_account": "À crédit",
    "col_lines": "Lignes",
    "status_cash": "Payé intégralement",
    "status_split": "Partiellement à crédit",
    "status_account": "Entièrement à crédit",
    "empty": "Aucun ticket sur cette période.",
    "detail_back": "Retour aux tickets",
    "detail_lines_title": "Lignes",
    "detail_payment_title": "Paiement",
    "detail_collection_line": "Règlement client, pas une vente",
    "col_qty": "Qté",
    "col_price": "Prix",
    "col_discount": "Remise",
    "col_amount": "Montant"
  },
```

In `locales/ar.json`, add `"tickets": "الفواتير"` to `"nav"`, and:

```json
  "tickets": {
    "title": "الفواتير",
    "subtitle": "كل فاتورة في الفترة التي تختارها",
    "col_time": "الوقت",
    "col_ticket_no": "رقم الفاتورة",
    "col_customer": "الزبون",
    "col_total": "الإجمالي",
    "col_cash": "محصّل نقداً",
    "col_on_account": "على الحساب",
    "col_lines": "الأسطر",
    "status_cash": "مدفوع بالكامل",
    "status_split": "جزئياً على الحساب",
    "status_account": "بالكامل على الحساب",
    "empty": "لا توجد فواتير في هذه الفترة.",
    "detail_back": "العودة إلى الفواتير",
    "detail_lines_title": "الأسطر",
    "detail_payment_title": "الدفع",
    "detail_collection_line": "تسديد حساب، وليس بيعاً",
    "col_qty": "الكمية",
    "col_price": "السعر",
    "col_discount": "الخصم",
    "col_amount": "المبلغ"
  },
```

Run: `.venv\Scripts\python.exe -m pytest tests/test_i18n_and_app.py::TestLocales -v`
Expected: PASS.

- [ ] **Step 4: Add the routes**

In `app.py`, add `"tickets"` to the `pages` list inside `inject_globals()`:

```python
    pages = [
        ("today", url_for("page_today")),
        ("tickets", url_for("page_tickets")),
        ("trend", url_for("page_trend")),
```

Add the two new routes right after `page_today()` (before `@app.route("/trend")`):

```python
@app.route("/tickets")
def page_tickets() -> str:
    m, etl, conn = open_metrics()
    try:
        start, end = date_range_from_request()
        if start is None:
            start = m.now.date()
        if end is None:
            end = m.now.date()
        tl = m.ticket_list(start, end)
        return render_template(
            "tickets.html",
            tickets=rows(tl),
            start=start,
            end=end,
            cache=etl.cache_info(),
        )
    finally:
        conn.close()


@app.route("/tickets/<int:receipt_id>")
def page_ticket(receipt_id: int) -> str:
    m, etl, conn = open_metrics()
    try:
        detail = m.ticket_detail(receipt_id)
        if detail is None:
            abort(404)
        return render_template(
            "ticket_detail.html",
            header=row_dict(detail["header"]),
            lines=rows(detail["lines"]),
            cache=etl.cache_info(),
        )
    finally:
        conn.close()
```

Also update `home()`'s `target` dict and the `default` mapping list to
include `"tickets": "page_tickets"`, for consistency with every other page
(not because it will ever be the default, but so a future config change
could set it without a `KeyError`):

```python
    target = {"today": "page_today", "tickets": "page_tickets", "trend": "page_trend",
              "customers": "page_customers", "receivables": "page_receivables",
              "inventory": "page_inventory", "products": "page_products",
              "suppliers": "page_suppliers", "cash": "page_cash",
              "diagnostics": "page_diagnostics",
              "dataquality": "page_dataquality"}.get(default, "page_today")
```

- [ ] **Step 5: Write the templates**

Create `templates/tickets.html`:

```html
{% extends "base.html" %}
{% block title %}{{ t('tickets.title') }}{% endblock %}

{% block content %}
<div class="page-head">
  <h1>{{ t('tickets.title') }}</h1>
  <p>{{ t('tickets.subtitle') }}</p>
</div>

{% include "_daterange.html" %}

<section class="panel">
  <div class="filter-row">
    <input type="search" placeholder="{{ t('common.search') }}" data-filters="ticket-table">
    <span class="muted"><span id="ticket-table-count">{{ tickets|length }}</span></span>
  </div>
  {% if tickets %}
    <div class="table-wrap scroll-y">
      <table class="sortable" id="ticket-table">
        <thead>
          <tr>
            <th class="num">{{ t('tickets.col_time') }}</th>
            <th class="num">{{ t('tickets.col_ticket_no') }}</th>
            <th>{{ t('tickets.col_customer') }}</th>
            <th class="num">{{ t('tickets.col_total') }}</th>
            <th class="num">{{ t('tickets.col_cash') }}</th>
            <th class="num">{{ t('tickets.col_on_account') }}</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {% for r in tickets %}
          <tr>
            <td class="num" data-value="{{ r.ticket_time }}">{{ t.datetime(r.ticket_time) }}</td>
            <td class="num muted">{{ r.ticket_no }}</td>
            <td class="name">{{ r.customer_name }}</td>
            <td class="num strong" data-value="{{ r.revenue }}">{{ t.money(r.revenue) }}</td>
            <td class="num" data-value="{{ r.cash_revenue }}">{{ t.money(r.cash_revenue) }}</td>
            <td class="num" data-value="{{ r.on_account_revenue }}">
              {% if r.on_account_revenue and r.on_account_revenue > 0.01 %}
                <span class="pill pill-warn">{{ t.money(r.on_account_revenue) }}</span>
              {% else %}—{% endif %}
            </td>
            <td><a href="{{ url_for('page_ticket', receipt_id=r.receipt_id) }}">{{ t('common.date') }}</a></td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  {% else %}
    <div class="empty">{{ t('tickets.empty') }}</div>
  {% endif %}
</section>
{% endblock %}
```

Fix the link text before moving on — `{{ t('common.date') }}` is a
placeholder mistake, not a real link label. Replace that `<td>` with:

```html
            <td><a href="{{ url_for('page_ticket', receipt_id=r.receipt_id) }}">→</a></td>
```

Create `templates/ticket_detail.html`:

```html
{% extends "base.html" %}
{% block title %}{{ header.ticket_no }}{% endblock %}

{% block breadcrumb %}
<div class="breadcrumb" style="padding: 10px 24px 0;">
  <a href="{{ url_for('page_tickets') }}">{{ t('tickets.detail_back') }}</a>
</div>
{% endblock %}

{% block content %}
<div class="page-head">
  <h1>{{ header.ticket_no }}</h1>
  <p>{{ t.datetime(header.ticket_time) }} · {{ header.customer_name }}</p>
</div>

<section class="tiles">
  <div class="tile">
    <div class="label">{{ t('common.total') }}</div>
    <div class="value">{{ t.money(header.revenue) }}</div>
  </div>
  <div class="tile">
    <div class="label">{{ t('tickets.col_cash') }}</div>
    <div class="value">{{ t.money(header.cash_revenue) }}</div>
  </div>
  <div class="tile">
    <div class="label">{{ t('tickets.col_on_account') }}</div>
    <div class="value">{{ t.money(header.on_account_revenue) }}</div>
  </div>
</section>

<section class="panel">
  <h2>{{ t('tickets.detail_payment_title') }}</h2>
  <div class="table-wrap">
    <table>
      <tbody>
        {% for key, label in [('cash', 'today.payment_cash'), ('cheque', 'today.payment_cheque'),
                              ('transfer', 'today.payment_transfer'), ('credit_account', 'today.payment_credit')] %}
          {% if header[key] %}
          <tr>
            <td>{{ t(label) }}</td>
            <td class="num strong">{{ t.money(header[key]) }}</td>
          </tr>
          {% endif %}
        {% endfor %}
      </tbody>
    </table>
  </div>
</section>

<section class="panel">
  <h2>{{ t('tickets.detail_lines_title') }}</h2>
  {% if lines %}
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>{{ t('common.product') }}</th>
            <th class="num">{{ t('tickets.col_qty') }}</th>
            <th class="num">{{ t('tickets.col_price') }}</th>
            <th class="num">{{ t('tickets.col_discount') }}</th>
            <th class="num">{{ t('tickets.col_amount') }}</th>
          </tr>
        </thead>
        <tbody>
          {% for r in lines %}
          <tr>
            <td class="name">
              {{ r.item_name }}
              {% if not r.is_sale %}<span class="pill pill-info">{{ t('tickets.detail_collection_line') }}</span>{% endif %}
              {% if r.is_return %}<span class="pill pill-warn">{{ t('common.returns') if t.has('common.returns') else '' }}</span>{% endif %}
            </td>
            <td class="num">{{ t.number(r.qty) }}</td>
            <td class="num">{{ t.money(r.price) }}</td>
            <td class="num">{{ t.money(r.discount) if r.discount else '—' }}</td>
            <td class="num strong">{{ t.money(r.amount) }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  {% else %}
    <div class="empty">{{ t('tickets.empty') }}</div>
  {% endif %}
</section>
{% endblock %}
```

The `t.has(...)` guard around a "returns" pill avoids inventing a locale
key that doesn't exist yet; simplify it once you check whether one already
does. Search first:

Run: `.venv\Scripts\python.exe -c "import json; d=json.load(open('locales/en.json', encoding='utf-8')); print(d.get('common', {}).get('returns'))"`

If that prints `None`, replace the whole `{% if r.is_return %}...{% endif
%}` line in the template with nothing (drop it) rather than add a new key
for a cosmetic label that isn't essential to this task's scope — a return
already shows as a negative `qty`/`amount`, which is self-explanatory in
context.

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_i18n_and_app.py -v`
Expected: PASS, including the Task 6/7 `/today` tests that were blocked on
`url_for('page_tickets')` existing.

- [ ] **Step 7: Commit**

```bash
git add app.py templates/tickets.html templates/ticket_detail.html \
        locales/en.json locales/fr.json locales/ar.json tests/test_i18n_and_app.py
git commit -m "feat: add the Tickets tab (period list + ticket drill-down)"
```

---

## Task 10: `catalog()` in metrics.py

**Files:**
- Modify: `poslib/metrics.py` (add near the top of the "PAGE 5 - STOCK"
  section — search for `def inventory_summary` and add just before it)
- Test: `tests/test_metrics.py`

**Interfaces:**
- Produces: `Metrics.catalog() -> pd.DataFrame` with columns `item_id`,
  `item_no`, `item_name`, `family_name`, `stock`, `cost`, `price`,
  `inactive`, sorted by `item_name`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_metrics.py`, inside `TestCatalog` (the existing class —
search for `class TestCatalog:`), add these methods at the end of that
class:

```python
    def test_catalog_has_every_item(self, metrics: Metrics, expected_counts):
        cat = metrics.catalog()
        assert len(cat) >= expected_counts["Item"]

    def test_catalog_columns(self, metrics: Metrics):
        cat = metrics.catalog()
        for col in ("item_id", "item_no", "item_name", "family_name",
                    "stock", "cost", "price"):
            assert col in cat.columns

    def test_catalog_sorted_by_name(self, metrics: Metrics):
        cat = metrics.catalog()
        assert list(cat["item_name"]) == sorted(cat["item_name"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_metrics.py::TestCatalog -v -k catalog_has_every_item`
Expected: FAIL with `AttributeError: 'Metrics' object has no attribute 'catalog'`.

- [ ] **Step 3: Write minimal implementation**

In `poslib/metrics.py`, find `def inventory_summary(self)` (search for it —
it's the first method under the `# PAGE 5 - STOCK` section header) and add
this new method immediately before it:

```python
    def catalog(self) -> pd.DataFrame:
        """
        The full product catalogue for the Stock catalog screen: reference,
        name, family/brand, quantity, cost, price. A thin, sorted view over
        `items` - no new business logic, just what a search screen needs.
        """
        cols = ["item_id", "item_no", "item_name", "family_name", "stock",
                "cost", "price", "inactive"]
        return self.items[cols].sort_values("item_name").reset_index(drop=True)

```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_metrics.py::TestCatalog -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add poslib/metrics.py tests/test_metrics.py
git commit -m "feat: add catalog() for the Stock catalog screen"
```

---

## Task 11: `/catalog` route, template, and nav entry

**Files:**
- Modify: `app.py`
- Create: `templates/catalog.html`
- Modify: `locales/en.json`, `locales/fr.json`, `locales/ar.json`
- Modify: `tests/test_i18n_and_app.py`

**Interfaces:**
- Consumes: `Metrics.catalog()` (Task 10).
- Produces: route `page_catalog` (GET `/catalog`).

- [ ] **Step 1: Write the failing test**

Add `"/catalog"` to the `PAGES` list in `tests/test_i18n_and_app.py`
(from Task 9's edit):

```python
    PAGES = ["/today", "/trend", "/customers", "/receivables", "/inventory",
             "/products", "/suppliers", "/cash", "/diagnostics", "/data-quality",
             "/tickets", "/catalog"]
```

And extend the leak-detector regex again:

```python
        pattern = re.compile(
            r"\b(?:app|nav|common|today|trend|customers|segments|receivables|"
            r"inventory|products|suppliers|cash|diagnostics|findings|dataquality|"
            r"tickets|catalog)"
            r"\.[a-z_][a-z_0-9.]*")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest "tests/test_i18n_and_app.py::TestDashboard::test_every_page_loads_in_every_language" -v`
Expected: FAIL (404 on `/catalog`).

- [ ] **Step 3: Add the locale keys**

In `locales/en.json`, add `"catalog": "Stock catalog"` to `"nav"`, and add
a new top-level `"catalog"` object right after `"inventory": { ... }`
closes and before `"products": { ... }` opens:

```json
  "catalog": {
    "title": "Stock catalog",
    "subtitle": "Every product - reference, family, quantity and price.",
    "col_reference": "Reference"
  },
```

In `locales/fr.json`, add `"catalog": "Catalogue produits"` to `"nav"`, and:

```json
  "catalog": {
    "title": "Catalogue produits",
    "subtitle": "Tous les produits - référence, famille, quantité et prix.",
    "col_reference": "Référence"
  },
```

In `locales/ar.json`, add `"catalog": "كتالوج المنتجات"` to `"nav"`, and:

```json
  "catalog": {
    "title": "كتالوج المنتجات",
    "subtitle": "كل منتج - المرجع، العلامة، الكمية والسعر.",
    "col_reference": "المرجع"
  },
```

Run: `.venv\Scripts\python.exe -m pytest tests/test_i18n_and_app.py::TestLocales -v`
Expected: PASS.

- [ ] **Step 4: Add the route**

In `app.py`, add `"catalog"` to the `pages` list in `inject_globals()`
(right after `"inventory"` is a natural spot):

```python
        ("inventory", url_for("page_inventory")),
        ("catalog", url_for("page_catalog")),
        ("products", url_for("page_products")),
```

Add the route right after `page_inventory()`:

```python
@app.route("/catalog")
def page_catalog() -> str:
    m, etl, conn = open_metrics()
    try:
        cat = m.catalog()
        return render_template(
            "catalog.html",
            products=rows(cat),
            product_count=len(cat),
            cache=etl.cache_info(),
        )
    finally:
        conn.close()
```

- [ ] **Step 5: Write the template**

Create `templates/catalog.html`, modelled directly on the "all products"
section already in `templates/products.html`:

```html
{% extends "base.html" %}
{% block title %}{{ t('catalog.title') }}{% endblock %}

{% block content %}
<div class="page-head">
  <h1>{{ t('catalog.title') }}</h1>
  <p>{{ t('catalog.subtitle') }}</p>
</div>

<section class="panel">
  <div class="filter-row">
    <input type="search" placeholder="{{ t('common.search') }}" data-filters="catalog-table">
    <span class="muted"><span id="catalog-table-count">{{ products|length }}</span> {{ t('common.of_total', total=t.number(product_count)) }}</span>
  </div>
  <div class="table-wrap scroll-y">
    <table class="sortable" id="catalog-table">
      <thead>
        <tr>
          <th>{{ t('catalog.col_reference') }}</th>
          <th>{{ t('common.product') }}</th>
          <th>{{ t('common.family') }}</th>
          <th class="num">{{ t('common.stock') }}</th>
          <th class="num">{{ t('common.cost') }}</th>
          <th class="num">{{ t('common.price') }}</th>
        </tr>
      </thead>
      <tbody>
        {% for r in products %}
        <tr>
          <td class="muted">{{ r.item_no or '—' }}</td>
          <td class="name">{{ r.item_name }}</td>
          <td class="name muted">{{ r.family_name }}</td>
          <td class="num" data-value="{{ r.stock or 0 }}">
            {% if r.stock is not none and r.stock < 0 %}<span class="pill pill-bad">{{ t.number(r.stock) }}</span>
            {% else %}{{ t.number(r.stock) }}{% endif %}
          </td>
          <td class="num" data-value="{{ r.cost or 0 }}">{{ t.money(r.cost) }}</td>
          <td class="num strong" data-value="{{ r.price or 0 }}">{{ t.money(r.price) }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</section>
{% endblock %}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_i18n_and_app.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app.py templates/catalog.html locales/en.json locales/fr.json locales/ar.json \
        tests/test_i18n_and_app.py
git commit -m "feat: add the Stock catalog tab"
```

---

## Task 12: `supplier_transactions()` and `purchase_detail()` in metrics.py

**Files:**
- Modify: `poslib/metrics.py` (add right after `supplier_summary()`, before
  `supplier_cost_trend()`)
- Test: `tests/test_metrics.py`

**Interfaces:**
- Produces: `Metrics.supplier_transactions() -> pd.DataFrame` with columns
  `purchase_id`, `supplier_id`, `supplier_name`, `purchase_time`, `lines`,
  `total`, `items`. `Metrics.purchase_detail(purchase_id: int) ->
  dict[str, Any] | None` with keys `header` (dict) and `lines` (DataFrame
  with `entry_id`, `item_id`, `item_name`, `qty`, `price`, `cost`,
  `new_cost`, `new_stock`, `amount`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_metrics.py`, inside a new `TestSupplierTransactions`
class, placed right after the existing `class TestCash:` block ends (i.e.
before `class TestCatalog:`):

```python
class TestSupplierTransactions:

    def test_transactions_columns(self, metrics: Metrics):
        st = metrics.supplier_transactions()
        if st.empty:
            pytest.skip("no purchase data in this database")
        for col in ("purchase_id", "supplier_id", "supplier_name",
                    "purchase_time", "lines", "total"):
            assert col in st.columns

    def test_untraceable_supplier_shows_as_blank_not_guessed(self, metrics: Metrics):
        st = metrics.supplier_transactions()
        if st.empty or st["supplier_id"].notna().all():
            pytest.skip("every purchase in this database traces to a supplier")
        untraced = st[st["supplier_id"].isna()]
        assert (untraced["supplier_name"] == "").all()

    def test_purchase_detail_round_trips(self, metrics: Metrics):
        st = metrics.supplier_transactions()
        if st.empty:
            pytest.skip("no purchase data in this database")
        purchase_id = st.iloc[0]["purchase_id"]
        detail = metrics.purchase_detail(purchase_id)
        assert detail is not None
        assert not detail["lines"].empty

    def test_purchase_detail_unknown_id_returns_none(self, metrics: Metrics):
        assert metrics.purchase_detail(-999999999) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_metrics.py::TestSupplierTransactions -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Write minimal implementation**

In `poslib/metrics.py`, find `def supplier_cost_trend(self)` and add these
two new methods immediately before it:

```python
    def supplier_transactions(self) -> pd.DataFrame:
        """
        Purchases grouped into transactions, the same way tickets group
        ticket lines - one row per `purchase_id`, for the Suppliers
        drill-down list. Supplier and date are blank exactly as often as
        `purchase_coverage()` already reports they are missing - nothing
        here is guessed.
        """
        p = self.purchases
        if p.empty:
            return p
        g = (p.groupby("purchase_id", as_index=False)
             .agg(supplier_id=("supplier_id", "first"),
                  purchase_time=("purchase_time", "first"),
                  lines=("entry_id", "count"),
                  total=("amount", "sum"),
                  items=("item_id", "nunique")))
        g = g.merge(self.suppliers[["supplier_id", "supplier_name"]],
                    on="supplier_id", how="left")
        g["supplier_name"] = g["supplier_name"].fillna("")
        return g.sort_values("purchase_time", ascending=False,
                             na_position="last").reset_index(drop=True)

    def purchase_detail(self, purchase_id: Any) -> dict[str, Any] | None:
        """One purchase's lines, for the Suppliers drill-down detail page."""
        p = self.purchases[self.purchases["purchase_id"] == purchase_id]
        if p.empty:
            return None
        header = {
            "purchase_id": purchase_id,
            "supplier_id": p.iloc[0]["supplier_id"],
            "purchase_time": p.iloc[0]["purchase_time"],
            "total": float(p["amount"].sum()),
            "lines": int(len(p)),
        }
        sup = self.suppliers[self.suppliers["supplier_id"] == header["supplier_id"]]
        header["supplier_name"] = sup.iloc[0]["supplier_name"] if not sup.empty else ""

        return {
            "header": header,
            "lines": p[["entry_id", "item_id", "item_name", "qty", "price",
                       "cost", "new_cost", "new_stock", "amount"]].sort_values("entry_id"),
        }

```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_metrics.py::TestSupplierTransactions -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add poslib/metrics.py tests/test_metrics.py
git commit -m "feat: add supplier_transactions() and purchase_detail() to metrics"
```

---

## Task 13: Suppliers transaction list + detail routes and templates

**Files:**
- Modify: `app.py`
- Modify: `templates/suppliers.html` (add a link to the new list)
- Create: `templates/supplier_transactions.html`
- Create: `templates/purchase_detail.html`
- Modify: `locales/en.json`, `locales/fr.json`, `locales/ar.json`
- Modify: `tests/test_i18n_and_app.py`

**Interfaces:**
- Consumes: `Metrics.supplier_transactions`, `Metrics.purchase_detail`
  (Task 12).
- Produces: routes `page_supplier_transactions` (GET
  `/suppliers/purchases`) and `page_purchase` (GET
  `/suppliers/purchases/<purchase_id>` — note: **not** `<int:purchase_id>`,
  because `purchase_id` can be a float-typed pandas value if the source
  column has nulls elsewhere; use a plain `<purchase_id>` string converter
  and cast inside the route, same defensive spirit as
  `date_range_from_request()`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_i18n_and_app.py`, inside `TestDashboard`:

```python
    def test_supplier_transactions_page_loads(self, client):
        response = client.get("/suppliers/purchases")
        assert response.status_code == 200

    def test_purchase_drilldown_loads(self, client, metrics):
        st = metrics.supplier_transactions()
        if st.empty:
            pytest.skip("no purchase data in this database")
        purchase_id = st.iloc[0]["purchase_id"]
        response = client.get(f"/suppliers/purchases/{purchase_id}")
        assert response.status_code == 200

    def test_purchase_drilldown_404_for_unknown_id(self, client):
        assert client.get("/suppliers/purchases/not-a-real-id").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_i18n_and_app.py::TestDashboard -v -k purchase`
Expected: FAIL (404, route doesn't exist).

- [ ] **Step 3: Add the locale keys**

In `locales/en.json`, inside `"suppliers"`, add (anywhere in the object,
e.g. right after `"cost_trend_empty"` if that key exists, or simply at the
end before the closing `}` of `"suppliers"`):

```json
    "transactions_title": "Purchase transactions",
    "transactions_subtitle": "Every purchase this tool can group into one transaction, with a drill-down into its lines.",
    "col_purchase_date": "Date",
    "col_purchase_total": "Total",
    "col_lines": "Lines",
    "unknown_supplier": "Unknown supplier",
    "no_date": "No date",
    "view_transactions": "See all purchase transactions",
    "detail_back": "Back to transactions"
```

In `locales/fr.json`, inside `"suppliers"`:

```json
    "transactions_title": "Transactions d'achat",
    "transactions_subtitle": "Chaque achat que cet outil peut regrouper en une transaction, avec accès au détail de ses lignes.",
    "col_purchase_date": "Date",
    "col_purchase_total": "Total",
    "col_lines": "Lignes",
    "unknown_supplier": "Fournisseur inconnu",
    "no_date": "Sans date",
    "view_transactions": "Voir toutes les transactions d'achat",
    "detail_back": "Retour aux transactions"
```

In `locales/ar.json`, inside `"suppliers"`:

```json
    "transactions_title": "معاملات الشراء",
    "transactions_subtitle": "كل عملية شراء يمكن لهذه الأداة تجميعها في معاملة واحدة، مع إمكانية الاطلاع على تفاصيل أسطرها.",
    "col_purchase_date": "التاريخ",
    "col_purchase_total": "الإجمالي",
    "col_lines": "الأسطر",
    "unknown_supplier": "مورّد غير معروف",
    "no_date": "بدون تاريخ",
    "view_transactions": "عرض كل معاملات الشراء",
    "detail_back": "العودة إلى المعاملات"
```

Note: since these are added to the existing `"suppliers"` namespace (not a
new one), the leak-detector regex in `test_i18n_and_app.py` does **not**
need a new namespace entry — `suppliers` is already in the pattern.

Run: `.venv\Scripts\python.exe -m pytest tests/test_i18n_and_app.py::TestLocales -v`
Expected: PASS.

- [ ] **Step 4: Add the routes**

In `app.py`, add right after `page_suppliers()`:

```python
@app.route("/suppliers/purchases")
def page_supplier_transactions() -> str:
    m, etl, conn = open_metrics()
    try:
        st = m.supplier_transactions()
        return render_template(
            "supplier_transactions.html",
            transactions=rows(st),
            cache=etl.cache_info(),
        )
    finally:
        conn.close()


@app.route("/suppliers/purchases/<purchase_id>")
def page_purchase(purchase_id: str) -> str:
    m, etl, conn = open_metrics()
    try:
        try:
            pid: Any = int(purchase_id)
        except ValueError:
            pid = purchase_id
        detail = m.purchase_detail(pid)
        if detail is None:
            abort(404)
        return render_template(
            "purchase_detail.html",
            header=row_dict(detail["header"]),
            lines=rows(detail["lines"]),
            cache=etl.cache_info(),
        )
    finally:
        conn.close()
```

- [ ] **Step 5: Link to the list from the main Suppliers page**

In `templates/suppliers.html`, find the closing `{% endblock %}` and add a
new panel right before it (after the `<section class="split-2">` block
that holds `gaps_title`/`cost_trend_title`):

```html
<p style="margin-top: 8px;">
  <a class="btn" href="{{ url_for('page_supplier_transactions') }}">
    {{ t('suppliers.view_transactions') }}
  </a>
</p>
{% endblock %}
```

(Replacing the file's final `{% endblock %}` with those three lines plus
`{% endblock %}`.)

- [ ] **Step 6: Write the templates**

Create `templates/supplier_transactions.html`:

```html
{% extends "base.html" %}
{% block title %}{{ t('suppliers.transactions_title') }}{% endblock %}

{% block breadcrumb %}
<div class="breadcrumb" style="padding: 10px 24px 0;">
  <a href="{{ url_for('page_suppliers') }}">{{ t('suppliers.title') }}</a>
</div>
{% endblock %}

{% block content %}
<div class="page-head">
  <h1>{{ t('suppliers.transactions_title') }}</h1>
  <p>{{ t('suppliers.transactions_subtitle') }}</p>
</div>

<section class="panel">
  <div class="filter-row">
    <input type="search" placeholder="{{ t('common.search') }}" data-filters="transactions-table">
    <span class="muted"><span id="transactions-table-count">{{ transactions|length }}</span></span>
  </div>
  {% if transactions %}
    <div class="table-wrap scroll-y">
      <table class="sortable" id="transactions-table">
        <thead>
          <tr>
            <th class="num">{{ t('suppliers.col_purchase_date') }}</th>
            <th>{{ t('common.supplier') }}</th>
            <th class="num">{{ t('suppliers.col_lines') }}</th>
            <th class="num">{{ t('suppliers.col_purchase_total') }}</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {% for r in transactions %}
          <tr>
            <td class="num" data-value="{{ r.purchase_time or '' }}">
              {{ t.date(r.purchase_time) if r.purchase_time else t('suppliers.no_date') }}
            </td>
            <td class="name">{{ r.supplier_name if r.supplier_name else t('suppliers.unknown_supplier') }}</td>
            <td class="num">{{ t.number(r.lines) }}</td>
            <td class="num strong" data-value="{{ r.total }}">{{ t.money(r.total) }}</td>
            <td><a href="{{ url_for('page_purchase', purchase_id=r.purchase_id|int) }}">→</a></td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  {% else %}
    <div class="empty">{{ t('suppliers.gaps_empty') }}</div>
  {% endif %}
</section>
{% endblock %}
```

Create `templates/purchase_detail.html`:

```html
{% extends "base.html" %}
{% block title %}{{ t('suppliers.transactions_title') }}{% endblock %}

{% block breadcrumb %}
<div class="breadcrumb" style="padding: 10px 24px 0;">
  <a href="{{ url_for('page_supplier_transactions') }}">{{ t('suppliers.detail_back') }}</a>
</div>
{% endblock %}

{% block content %}
<div class="page-head">
  <h1>{{ header.supplier_name if header.supplier_name else t('suppliers.unknown_supplier') }}</h1>
  <p>{{ t.date(header.purchase_time) if header.purchase_time else t('suppliers.no_date') }}</p>
</div>

<section class="tiles">
  <div class="tile">
    <div class="label">{{ t('suppliers.col_purchase_total') }}</div>
    <div class="value">{{ t.money(header.total) }}</div>
  </div>
  <div class="tile">
    <div class="label">{{ t('suppliers.col_lines') }}</div>
    <div class="value">{{ t.number(header.lines) }}</div>
  </div>
</section>

<section class="panel">
  <h2>{{ t('tickets.detail_lines_title') }}</h2>
  {% if lines %}
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>{{ t('common.product') }}</th>
            <th class="num">{{ t('tickets.col_qty') }}</th>
            <th class="num">{{ t('common.cost') }}</th>
            <th class="num">{{ t('tickets.col_amount') }}</th>
          </tr>
        </thead>
        <tbody>
          {% for r in lines %}
          <tr>
            <td class="name">{{ r.item_name }}</td>
            <td class="num">{{ t.number(r.qty) }}</td>
            <td class="num">{{ t.money(r.cost) }}</td>
            <td class="num strong">{{ t.money(r.amount) }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  {% else %}
    <div class="empty">{{ t('suppliers.gaps_empty') }}</div>
  {% endif %}
</section>
{% endblock %}
```

This reuses `tickets.detail_lines_title`/`tickets.col_qty`/`tickets.col_amount`
(from Task 9) rather than inventing duplicate keys — a purchase's "lines"
table is conceptually the same idea as a ticket's.

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_i18n_and_app.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app.py templates/suppliers.html templates/supplier_transactions.html \
        templates/purchase_detail.html locales/en.json locales/fr.json locales/ar.json \
        tests/test_i18n_and_app.py
git commit -m "feat: add supplier purchase transaction list and drill-down"
```

---

## Task 14: Full regression pass + README/CLAUDE.md update

**Files:**
- Modify: `README.md` (the "The screens" table)
- Modify: `CLAUDE.md` (status line, verified-numbers note if the tender
  reconciliation check surfaced anything worth recording as a discovery)

**Interfaces:** none new — this task is verification and documentation
only.

- [ ] **Step 1: Run the entire test suite**

```bash
.venv\Scripts\python.exe -m pytest tests -q
```

Expected: PASS, including every existing test (the frozen
`expected_counts`/`expected_totals`/`verification()` checks must be
unaffected — if any of those fail, something in this plan touched revenue
outside its stated scope and needs to be found and fixed before proceeding,
not worked around).

- [ ] **Step 2: Manual spot-check against a live POS report**

Ask the shop owner for a fresh screenshot of R.Lynx's "État ventes du jour"
(or equivalent) for a specific day, or for today. Open `/today` for that
same day (`?start=YYYY-MM-DD&end=YYYY-MM-DD`) and compare:
- The tool's "Sales (cash-realized)" tile should be close to the POS
  report's `Total` row (after its own `Crédits clients (-)` deduction) —
  not identical (the tool's split is a per-ticket proration where the POS
  report may compute it differently at the line level), but in the same
  ballpark, not off by an order of magnitude or a wildly different sign.
- The tool's "On account" tile should be in the same ballpark as the
  report's `Crédits clients (-)` row for that period.

If the numbers disagree by more than what a reasonable proration
difference explains, stop and investigate before calling this plan done —
do not silently adjust the formula to force a match without understanding
why they differed.

- [ ] **Step 3: Update README.md's screens table**

In `README.md`, find the `## The screens` table and update it:

```markdown
| Screen | What it answers |
|---|---|
| **Today** | How is this period going, against a comparable day/period, with sales split into what's actually been collected vs what's still on the customer's account |
| **Tickets** | Every ticket for a period you choose, drilling into full line-level detail |
| **Trend** | Is the business getting better or worse, and why |
| **Customers** | Who is worth chasing, who is quietly leaving, and a printable call list |
| **Money owed** | Who owes you what, and who has gone quiet while owing it |
| **Stock** | Where your money is stuck, and what is about to run out |
| **Stock catalog** | Every product - reference, family, quantity and price - to look one up quickly |
| **Products & margin** | What actually makes money, and what quietly loses it |
| **Suppliers** | Who you depend on, how often you reorder, and every purchase transaction in full detail |
| **What to fix** | Every problem found, ranked by money, with what to do about each |
| **Data quality** | How much to trust each number on the other screens |
```

- [ ] **Step 4: Update CLAUDE.md**

In `CLAUDE.md`, update the top status line — find:

```
See README.md for what the tool does. **Status: feature-complete through
Patch #3** (2026-08-10). Original build +
```

Replace with:

```
See README.md for what the tool does. **Status: feature-complete through
Patch #4** (fill in today's date). Original build +
```

Add a new discoveries section after the existing "Patch #3 session
discoveries" block (before "## Verified numbers"), documenting the actual
tender-reconciliation result found in Step 2 above — write this only after
Step 2 is actually done, using the real numbers observed, e.g.:

```markdown
## Patch #4 session discoveries (fill in today's date) — cash-realized sales

9. **[Fill in after Step 2's spot-check]** — record whether
   Cash+Cheque+Transfer+CreditAccount reconciled cleanly against each
   ticket's Total in the real database, and by how much it didn't if it
   didn't. This is what `_tender_reconciliation()` in `metrics.py` and the
   Data Quality screen now report live, but the number at build time is
   worth recording here the same way every other discovery in this file
   is.
```

This step cannot be filled in with a placeholder by the plan itself since
the real figure only exists once Step 1/2 have actually run against the
real database — fill in the real number, not the word "TBD", before
committing.

- [ ] **Step 5: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: record Patch #4 (Today drill-down, Tickets, Stock catalog, supplier drill-down)"
```

---

## Self-Review Notes

- **Spec coverage:** Today period-selectable (Tasks 4, 6, 7) ✓. Cash-realized
  split scoped to Today + Tickets only, not Trend/Products/Cash P&L/frozen
  baseline (Tasks 1, 4, 5, 8 — none of them touch `headline()`, `monthly()`,
  `family_margin()`, `product_margin()`, `income_statement()`, or
  `verification()`) ✓. Tickets tab list + detail (Tasks 8-9) ✓. Stock
  catalog tab (Tasks 10-11) ✓. Suppliers transaction list + detail (Tasks
  12-13) ✓. Verification steps from the spec (tender reconciliation,
  on-account-vs-receivables cross-check, POS screenshot spot-check) ✓
  (Tasks 2, 3, 14).
- **Non-goals honored:** no write route anywhere in this plan; no change to
  `tests/conftest.py`'s `expected_counts`/`expected_totals`; no change to
  `verification()`'s existing entries (Task 2 adds to `data_quality()`
  instead, which is descriptive, not gated); ticket detail does not attempt
  to reconstruct historical account balances (dropped from the spec in the
  same session this plan was written, see spec's revision note).
- **Type/name consistency check:** `cash_revenue`/`on_account_revenue`
  (Task 1) are the exact names used by `today()` (Task 4), `period_stats()`
  (Task 5), `ticket_list()`/`ticket_detail()` (Task 8), and every template
  that reads them (Tasks 7, 9). `page_tickets`/`page_ticket`/`page_catalog`/
  `page_supplier_transactions`/`page_purchase` are the exact endpoint names
  used by every `url_for(...)` call across Tasks 7, 9, 11, 13.
