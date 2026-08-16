# Today drill-down, cash-realized sales, stock catalog, supplier drill-down

Date: 2026-08-16
Status: approved design, not yet built

## Why

Three requests from the shop owner, all in service of the same goal: turn the
dashboard from a read-only summary into a real window onto the POS he can't
otherwise reach when he's away from the till computer.

1. The Today screen only shows "today." He wants to pick a period (today,
   yesterday, this week, last 7 days, a custom range) and see both the
   summary stats **and** the individual tickets underneath them, with each
   ticket opening into full line-level detail — the same depth of detail
   R.Lynx itself shows on its own ticket screen.
2. "Sales" must mean money actually received. A ticket partly paid on the
   customer's account (added to what they owe) should not be counted as a
   sale for that unpaid portion — it's a receivable, not revenue, until it's
   collected.
3. Stock needs a full searchable product catalog (reference, family/brand,
   quantity, price) for quick lookups, and Suppliers needs the same kind of
   transaction drill-down Customers already has.

This is architectural rather than a small patch because it changes how the
Today page is structured, introduces a new revenue concept that has to sit
correctly alongside the existing one without contradicting it, and adds two
new drill-down subsystems.

## Scope

**In scope:**
- Today page becomes period-selectable (today / yesterday / this week / last
  7 days / custom range), with cash-realized vs on-account shown as two
  separate, clearly labelled figures.
- New **Tickets** tab: period picker, ticket list, per-ticket detail page.
- New **Stock catalog** tab: full searchable/sortable product table.
- New **Suppliers** transaction list + per-transaction (purchase) detail page.
- A new `cash_realized` / `on_account` split, computed per ticket, used only
  by the two features above.

**Out of scope (confirmed via council review — see below):**
- Redefining "revenue" on Trend, Products & margin, or Cash/P&L. Those stay
  invoiced-revenue (accrual), unchanged.
- Changing the frozen all-time verified-numbers table in `tests/conftest.py`
  / README / CLAUDE.md. Those numbers keep their current meaning.
- Any write path. Every feature here is read-only, same as everything else
  in this tool.

### Why the split doesn't touch Trend/Products/Cash P&L

Margin % is inherently accrual: the cost of goods leaves the shelf at the
moment of sale, not the moment of payment. Computing margin against
cash-realized-only revenue would make margin crater in a month with heavy
on-account selling, for reasons that have nothing to do with the business —
a broken metric, not a more honest one. The frozen baseline exists as a
regression *gate* ("must never fall") to catch parsing bugs; redefining what
it measures would let it legitimately fall and defeat the gate's purpose.
Cash-realized is a genuinely different question ("how much cash came in")
from what those screens already answer ("how is the business performing"),
so it gets its own metric rather than replacing theirs.

The Cash tab is the one exception worth watching: `till_reconciliation()`
already recomputes cash from source rows for that page (the existing
"don't trust a POS aggregate" pattern, CLAUDE.md discovery #4). If the
owner wants a cash-realized figure surfaced there too later, that's a small,
separate addition reusing the same split — not part of this build.

## The cash-realized / on-account split

The POS records payment tender on the ticket *header* only — `Cash`,
`Cheque`, `Transfer`, `CreditAccount` on `Receipt` — not per line. There is
no field saying which specific line went on account. So the split is
computed once per ticket and applied evenly across that ticket's lines:

```
realized_tender = cash + cheque + transfer
total_tender     = cash + cheque + transfer + credit_account
realized_share   = realized_tender / total_tender      (0 if total_tender == 0)

per line:
  cash_revenue       = amount * realized_share
  on_account_revenue = amount * (1 - realized_share)
```

This is an approximation where a ticket carries several products and only
part of it was put on account — exactly the same kind of best-effort,
disclosed approximation this codebase already uses elsewhere (e.g.
attributing a purchase line to "whoever supplied most of the rest" in
`purchases`). A ticket that is fully paid (`credit_account == 0`) or fully
on account (`cash+cheque+transfer == 0`) splits cleanly with no
approximation involved.

**Collections stay exactly as they are today.** A line with no real product
attached (e.g. "Paiement de règlement") is not a sale under the existing
Rule 1 and this change does not touch that — collections are cash in, but
never "sales," on-account or otherwise.

### Verification, before trusting this

Two checks happen during implementation, both consistent with this
project's "verified empirically, don't assume" discipline:

1. **Tender reconciliation.** Confirm `Cash + Cheque + Transfer +
   CreditAccount` actually adds up to `Receipt.Total` across real tickets in
   the current cache. If it drifts, `total_tender` (not `Total`) is used as
   the denominator so the split stays internally consistent, and the size of
   any drift is reported on the Data Quality screen the same way every other
   correction in this tool is disclosed.
2. **Cross-check against Receivables.** The sum of `on_account_revenue`
   across a long window should move in the same direction as, and stay in a
   sane ratio to, the existing `receivables()` total (18,035,898 DZD
   currently) — two different code paths (ticket tenders vs. customer
   balances) describing the same underlying fact. A large, unexplained gap
   between them gets reported as a new Data Quality check, not hidden.
3. **Ground truth against the POS's own report.** R.Lynx's own "État ventes
   du jour" screen (Aujourd'hui/Hier/Cette semaine/Ce mois/Cette année,
   with `Crédits clients (-)` and `Règlements clients` rows) computes this
   live from the same source data. The owner can supply a screenshot of that
   report for a given day; the new Today figures should match it. This is a
   manual spot-check during implementation, not an automated test (the
   report changes every time it's viewed), but it's the most direct
   validation available.

## Feature 1 — Today becomes period-selectable

**Route:** `/today` gains the same `?start=`/`?end=` (or a `?period=`
shorthand for the presets) query handling already used by `/trend` and
`/cash` via `date_range_from_request()`.

**Preset list**, matching the wording the owner is used to from R.Lynx:
Today, Yesterday, This week, Last 7 days, Custom range. Presets are just
convenience links that resolve to concrete `start`/`end` dates and reuse the
existing `?start=`/`?end=` mechanism (`date_range_from_request()`) — there is
no separate "period" concept to keep in sync with the date picker already on
Trend/Cash. "This week" starts on Monday, consistent with the `weekday`
column already used throughout `metrics.py` (0 = Monday) and matches how
R.Lynx's own "Cette semaine" preset behaves.

**Behaviour:**
- **Single day selected** (today, yesterday, or a custom range collapsed to
  one day): keep the existing tile/comparison layout — revenue, tickets,
  margin, hourly chart, top items, top customers, comparisons vs. the day
  before / same weekday last week / weekday average — all recomputed for
  the chosen day instead of hardcoded to today. The revenue tile becomes
  **cash-realized sales**, with **on-account** shown as its own distinct
  figure right next to it (never merged in, never called a sale) — mirroring
  R.Lynx's own `Crédits clients (-)` line. Collections keep their existing
  separate treatment.
- **A range selected** (this week, last 7 days, custom range spanning
  multiple days): switch to period totals — reusing `headline()`'s existing
  windowed aggregation — instead of a day-over-day comparison, since a
  single "vs yesterday" delta doesn't mean anything for a multi-day range.
  Same cash-realized/on-account split applies to the period total.
- A link from this page into the new Tickets tab, pre-filled with the same
  period, for anyone who wants to drop from "how was this period" into
  "show me the actual tickets."

**metrics.py additions:**
- `today(target_date=None)` (or a new `day_stats(date)` the existing
  `today()` calls) — parameterised by date instead of hardcoded to
  `self.now.date()`, adding `cash_revenue` / `on_account_revenue` per the
  split above.
- `period_stats(start, end)` — the range case; built on the existing
  `headline()`/`_window_range()` machinery, plus the same split.

## Feature 2 — New Tickets tab

**Route:** `/tickets` — period picker (same presets as Today), lists every
ticket in the chosen period: time, customer (or "Client divers" for
walk-ins), ticket total, cash-realized amount, on-account amount, item
count. Sortable/filterable using the `table.sortable` / `data-filters`
infrastructure already in `base.html` — no new JS needed.

**Route:** `/tickets/<int:receipt_id>` — full detail: every line on the
ticket (item, qty, price, discount, amount — including a collection line
such as "Paiement de règlement" if the ticket carried one, clearly still
labelled as a collection, not a sale) and the ticket's own payment breakdown
(cash/cheque/transfer/on-account). Same visual idiom as
`customer_detail.html` / `product_detail.html`.

Deliberately **not** included: the POS's "Ancien solde / Nouveau solde"
(old/new account balance) line. Reproducing that would mean reconstructing
a customer's running balance history by replaying every balance-affecting
event in chronological order — a materially separate, riskier feature this
tool has no existing machinery for, not a detail this data readily gives
up. Worth a dedicated future spec if wanted; not part of this build.

**metrics.py additions:**
- `ticket_list(start, end)` — built from the existing `tickets` cached
  property, filtered by date range, with the cash/on-account split joined
  in.
- `ticket_detail(receipt_id)` — the ticket's header row plus its full
  `ReceiptEntry` lines (reusing `self.lines`, not `self.sales`, so
  collection lines on a mixed ticket are still visible, just still labelled
  as collections not sales). Returns `None` for an unknown ID → 404, same
  pattern as `customer_profile`/`product_profile`.

## Feature 3 — New Stock catalog tab

**Route:** `/catalog` (new top-level tab, separate from the existing Stock
page's dead-stock/risk analysis, per the owner's choice).

A full product table: reference (`item_no`), name, family/brand
(`family_name`), quantity (`stock`), cost, price. Sourced directly from the
`items` cached property already loaded — no new SQL, no new business logic,
just a new route + template using the existing sortable-table and
`data-filters` search-box idiom (same one `products.html`/`inventory.html`
already use for their smaller tables). Everything on this page already
exists in the codebase; this is presentation only.

## Feature 4 — Suppliers transaction drill-down

Purchases already group by `PurchaseEntry.PurchaseID` conceptually the same
way tickets group by `Receipt.ID` — the `purchases` cached property already
carries `purchase_id`, best-effort `supplier_id`, and `purchase_time`.

**Route:** `/suppliers/purchases` — list of purchase "transactions" grouped
by `purchase_id`: date (or "No date" — `purchase_coverage()` already reports
how often this is missing), supplier (or "Unknown supplier" when it can't be
traced), line count, total value.

**Route:** `/suppliers/purchases/<purchase_id>` — that purchase's lines
(item, qty, cost, new cost, new stock, amount), same visual idiom as the
other detail pages.

**metrics.py additions:**
- `supplier_transactions()` — `purchases` grouped by `purchase_id`.
- `purchase_detail(purchase_id)` — one purchase's lines. Returns `None` if
  unknown.

Untraceable data (~a fifth of lines have no supplier, most have no date,
per the existing `purchase_coverage()`) is shown plainly labelled, never
guessed or hidden.

## Non-goals

- No write path anywhere — every route here is a GET, same as the rest of
  the app.
- No change to how collections, returns, or unknown-cost lines are treated
  — those existing rules (README rules 1–4) are untouched.
- No receivables/aging engine, no cash-realized P&L — raised as future ideas
  during design review, explicitly deferred as separate, explicitly-scoped
  work if wanted later.

## Testing plan

- New pytest coverage for `ticket_list`/`ticket_detail`,
  `supplier_transactions`/`purchase_detail`, the period-parameterised
  `today`/`period_stats`, and the cash/on-account split itself (fully-paid
  ticket, fully-on-account ticket, split ticket, zero-tender edge case).
- A new Data Quality check reporting tender-reconciliation drift (see
  Verification above) and the on-account-vs-receivables cross-check —
  following the same pattern `verification()` already uses for the other
  gated figures.
- Existing `expected_counts`/`expected_totals` gates in `tests/conftest.py`
  are untouched — nothing in this build changes an all-time or invoiced
  figure.
- Manual spot-check against a POS-supplied screenshot for at least one real
  day before calling this done.
