> Moved verbatim out of CLAUDE.md on 2026-09-14 (docs/slim-claude-md). Nothing edited.

## Three original discoveries — do not "fix" these back

1. **Boolean columns: a SET bit means TRUE.** (`poslib/jet4.py`, the
   `TYPE_BOOL` branch in `_parse_row`.) Verified empirically — see full
   reasoning in git history if needed; the short version is the opposite
   reading makes 1,570 products inactive and only the walk-in account has
   credit, both nonsensical.
2. **`Item.LastSold` is stale on ~60 products.** `metrics.py`'s
   `item_movement` uses the most recent *ticket* date (`last_sale_effective`)
   instead of the POS's own field.
3. **~~Purchase line totals don't reconcile~~ — RESOLVED, see discovery #12.**
   This was a real bug, not a quirk to preserve: supplier PAYMENT lines
   (`ItemID = -2`, "Paiement de règlement") were being counted as if they
   were goods purchased. Excluding them (`purchases()`'s `is_purchase`
   filter) reconciles purchase totals to within ~1.4% of cost-of-goods +
   stock-value — supplier money figures are built from summed purchase
   lines again, same as everything else, not from cost-of-goods/stock-value
   as a workaround. Kept here, struck through, so the history of what was
   wrong and why isn't lost — the workaround language ("purchase amounts
   are reported as relative shares... never as an amount of money spent")
   is gone from `purchase_coverage()`'s docstring too.

Also: `Receipt.TotalCost` is zero on `ReceiptType=1` ("DV") tickets — cost of
goods is always computed from `ReceiptEntry` lines, never the header.

## Patch #3 session discoveries (2026-08-10) — same spirit, new instances

4. **`Batch`'s `*Shift` columns don't track live sales.** The one till
   session in this database has stayed open since 2024-08-12, and its
   `CashShift`/`ChequeShift`/etc. sit frozen at 0 despite ~39M DZD of real
   cash sales having happened under it — R.Lynx apparently only updates
   those columns on session close/reopen, not per-sale. `till_reconciliation()`
   in `metrics.py` recomputes "expected" cash from actual `Receipt.BatchID`-
   joined tickets instead, the same "recompute from source rows, don't trust
   a POS-computed aggregate" rule as discovery #2/#3 above.
5. **No per-product stocktake detail exists.** `StockTake` has aggregate
   over/under/net-cost totals per physical count; there is no
   `StockTakeEntry`-style line table in this database (confirmed absent even
   at the schema-definition level, not just "no rows yet" — it exists in
   R.Lynx's own newer demo template but not here). `shrinkage_events()` is
   therefore event-level only, clearly labeled as such — don't try to join it
   to individual products, the data to do so honestly isn't here.
   **Update, 2026-09-07**: directly confirmed the "newer demo template"
   reference above. `BlankDB` — the actual per-store starter database
   bundled inside the *currently installed* R.Lynx 10.3.0.0 on store #1's
   own till PC (`C:\Program Files (x86)\R.Lynx™ Point De Vente\BlankDB`) —
   defines a real `StockTakeEntry` table: `ID, StockTakeID, ItemID,
   Counted, Expected, QtyPerParcel, Cost, MakeInactive`. So the schema
   fully supports per-product stocktake detail as of this install's
   version; this store's live `E:\Base de données4.dblx` just predates
   that table being added (or was never migrated onto it). Don't assume
   this generalizes to the other two stores without checking each one's
   own live database directly — schema can drift between stores if they
   were provisioned from different R.Lynx versions. Worth checking next
   time this is revisited: if `StockTakeEntry` exists with real rows in a
   given store's live `.dblx`, `shrinkage_events()` could be upgraded from
   event-level to per-product for that store.
6. **Expiry-date tracking was investigated and dropped.** R.Lynx's "Dates
   péremptions" UI section doesn't correspond to any table/column in this
   database (checked exhaustively, schema-level, both the real DB and
   R.Lynx's own demo template) — the feature apparently doesn't persist in
   this edition, or this database predates it. Not built. Revisit only if a
   fresh copy from the actual till PC someday shows otherwise.
7. **`Item.Picture` (OLE) is stripped by the ETL cache** (`SKIPPED_COLUMN_TYPES`
   in `etl.py`) but the low-level reader can still read it directly —
   `poslib/photos.py` opens a fresh copy for this on demand. Empirically, 0 of
   1,570 real items currently have a photo, so this path is deliberately
   lightweight (best-effort OLE-wrapper sniffing, not a full parser).

## Patch #4 session discoveries (2026-08-16) — cash-realized sales

9. **Tender reconciliation is clean.** `_tender_reconciliation()` found
   Cash+Cheque+Transfer+CreditAccount matches each ticket's own `Total` on
   7,867 of 7,950 tickets (98.96%); the 83 mismatches (1.04%) top out at a
   132,000 DZD gap on the single worst ticket. Good enough to trust the
   cash-realized split as built. `_on_account_reconciliation()` cross-checked
   the new on-account figure against the existing Receivables total two
   completely different ways: all-time on-account sales (48,951,498 DZD)
   minus all-time collections (30,699,564 DZD) predicts 18,251,934 DZD still
   owed; the Money Owed screen (built from `Customer.balance`, never touched
   by this patch) currently shows 18,035,429 DZD — a 216,506 DZD gap
   (~1.2% of the expected figure), which `explains_receivables` reports as
   within tolerance. The two independently-computed figures agreeing this
   closely is a good sign both are reading the data correctly.

## Remote-parity follow-up (2026-08-16) — full read-only remote feature parity

10. **Remote viewing reached full read-only feature parity with the local
    dashboard** (Today date presets + true custom ranges, Tickets and
    Stock catalog tabs, ticket/purchase drill-down), decided via a
    5-advisor council review that unanimously rejected any live-tunnel
    approach — see `export_static.py`'s module docstring for the design
    (bounded ticket drill-down window, `daily.json` for client-side range
    slicing, no server-side exposure added at all). One real perf bug
    caught before shipping: naive per-page drill-down export took ~19
    minutes (rebuilding `Metrics` from scratch per page); reusing one
    shared `Metrics` instance cut that to ~40 seconds.
11. **A first attempt at "how much has been paid to suppliers" was wrong
    and reverted — `Supplier.TotalPurchased` is a trap, not a money
    field.** It looks like it should be an all-time purchase total but its
    values are tiny (RUBY ROSE: `total_purchased` = 45, actual purchase
    value = 67,798,680 DZD) — a count of something roughly order-sized,
    not DZD. `total_purchased - balance` produced a nonsense large
    negative "amount paid" for every single supplier; caught by eyeballing
    the live page, not by a test. See the note on `suppliers()` in
    `metrics.py`. This was fixed properly, not just reverted — see #12.
12. **Supplier payments ARE recorded — as their own single-line "purchase"
    transactions inside `PurchaseEntry`, the exact same idiom as customer
    collections on the sales side.** Missed entirely in the first pass of
    discovery #11 (checked `StoreSafeIn`/`StoreSafeOut`/`Charge` instead —
    all empty or irrelevant — but never checked `PurchaseEntry` itself for
    a sales-side-style pseudo-item, even though `lines`' `is_sale`/
    `is_collection` split is exactly that pattern already). The user
    caught it by showing screenshots of R.Lynx recording a payment as an
    "Achat" (purchase) named "Paiement de règlement". Verified directly
    against the real database: 271 rows with `ItemID = -2`, `ItemName =
    "Paiement de règlement"`, totalling 225,852,701 DZD, each its own
    unique `PurchaseID` containing nothing else. `purchases()` now splits
    `is_purchase`/`is_payment` on this; `supplier_payments` is the
    payment-only view; `purchase_coverage()` reports `payments_total`/
    `payments_count` separately from purchase value.
    **This also explains discovery #3**: those payment lines were being
    summed into purchase totals as if they were goods bought — removing
    them brought the purchase-vs-(COGS+stock) ratio from 1.75 down to
    1.01. Checked whether payments trace to a specific supplier the same
    way real purchase lines do (via `SupplierItem`): they don't — zero
    `SupplierItem` rows exist for `ItemID = -2`, by item or by
    `PurchaseID`. So `supplier_payments` is a dated all-time total and
    line list, never a per-supplier breakdown; `balance` (what's
    currently owed) remains the only *per-supplier* payment figure.
    Cross-check: `real_purchase_value - payments_total` over all suppliers
    ≈ 1.10× the sum of current balances — consistent with the already-
    known ~11% of purchase lines that don't trace to a supplier at all,
    a good sign this reading is correct.

## Owner-reported fixes (2026-08-20) — devis tickets and account payments

The owner flagged two things wrong on the Today/Tickets screens from live
screenshots (a "DV" ticket and a mixed sale+payment ticket):

13. **"DV" tickets (`Receipt.ReceiptType == 1`, ticket numbers like
    `DV0076/26`) are devis — price quotes the customer reviewed, never a
    completed sale.** No goods left the shop, no money changed hands, but
    they were being counted as full sales everywhere (Today, Trend, Cash
    P&L, ticket counts) because `ReceiptType` was loaded into `lines`/
    `tickets` but never actually filtered on anything. Fixed as Rule 6 in
    `metrics.py`: `lines["is_devis"]` excludes a devis ticket's lines from
    `is_sale`/`sales` (the single source of truth every revenue figure is
    built from), and the new `completed_tickets` property (⁠`tickets` minus
    devis) is what every ticket-count/avg-basket figure uses instead of
    `tickets` directly — so a devis can't inflate a ticket count or drag an
    average basket down either. The ticket itself still appears on the
    Tickets screen and is still viewable in drill-down (so the owner can
    still look up what was quoted), just clearly labelled "Devis" and
    worth 0 DZD of sales. This is a real correction, not a preference: it
    can only ever make historical revenue/gross-profit figures fall
    slightly (there are only nine DV tickets total per the existing Rule 2
    note), so **the frozen floors in `tests/conftest.py`
    (`revenue_all_time`, `gross_profit_all_time`, `revenue_12m`,
    `gross_profit_12m`) may need lowering** — re-run `pytest tests -q` on
    a machine with the real database and adjust them down if they now
    fail, the same "re-verify by hand" step this file has always asked
    for after a rule change.
14. **A "Paiement de règlement" (account payment) is real cash landing in
    the till today, even though Rule 1 correctly keeps it out of "sales."**
    The owner's screenshot showed a ticket that mixed a real product sale
    with a customer paying down an old balance — the ticket's "Total" tile
    only reflected the sale, and the payment was buried in the lines table
    with no total anywhere adding it back in, which read as the money
    having vanished. Rule 1 (a collection is not a sale) is still correct
    and unchanged — margin/product accounting must stay accrual — but the
    Today screen's headline figure now answers a different, narrower
    question: "how much cash came into the till today". `today()` and
    `period_stats()` gained a `cash_in` figure (`cash_revenue` +
    `collections`, both still returned separately so the split is never
    hidden) and that is what the Today screen's headline tile and
    day/week/period comparisons now show, labelled accordingly. The ticket
    drill-down page also gained a "Collected on account" tile so a mixed
    ticket's payment line is visible as a number, not just a labelled row
    to spot in the lines table. Trend, Products & margin and Cash P&L are
    untouched — this stays scoped to Today/Tickets, the same boundary the
    2026-08-16 cash-realized/on-account split already drew (see
    `docs/superpowers/specs/2026-08-16-tickets-catalog-suppliers-design.md`).

