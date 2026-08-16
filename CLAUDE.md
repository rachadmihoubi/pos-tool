# Shop Analysis — project brief for Claude

Read this before touching anything. It captures decisions and discoveries from
the build sessions that are not obvious from the code alone.

## What this is

See README.md for what the tool does. **Status: feature-complete through
Patch #4** (2026-08-16). Original build +
the cash/P&L, drill-down and date-range foundation ("Patch #2") + seven
Patch #3 features + silent background launch + remote viewing + Patch #4
(Today drill-down, cash-realized sales, Tickets tab, Stock catalog tab,
supplier purchase drill-down) are all built, tested (214 tests) and — for
the background launch and remote viewing — actually deployed and verified
live on this machine. See "What's left" below.

## The one rule that overrides everything else

**Never write to the source `.dblx` file.** It is opened read-only and always
copied to a temp folder before parsing (`poslib/etl.py:copy_database_readonly`).
Every change must preserve this. If you're ever tempted to open the source path
directly for anything other than a read-only copy, stop.

## Machine-specific thing to fix on a new PC

`config.yaml` → `database.path` is currently:
```
C:/Users/RACHAD/Desktop/Base de données4.dblx
```
This is **this machine's** path — this is a dev PC, not the shop's actual till
computer (no E: drive here). On a different PC the POS database will be at a
different location. **Check this path is correct before running anything** —
`poslib/etl.py` will raise a clear `ETLError` naming the missing file if it's
wrong, so this fails loudly, not silently.

## Three original discoveries — do not "fix" these back

1. **Boolean columns: a SET bit means TRUE.** (`poslib/jet4.py`, the
   `TYPE_BOOL` branch in `_parse_row`.) Verified empirically — see full
   reasoning in git history if needed; the short version is the opposite
   reading makes 1,570 products inactive and only the walk-in account has
   credit, both nonsensical.
2. **`Item.LastSold` is stale on ~60 products.** `metrics.py`'s
   `item_movement` uses the most recent *ticket* date (`last_sale_effective`)
   instead of the POS's own field.
3. **Purchase line totals don't reconcile** — every supplier money figure is
   built from cost-of-goods/stock-value instead of summed purchase lines.

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

10. **No supplier-payment records exist anywhere in this database — and
    `Supplier.TotalPurchased` is a trap, not a money field.** Checked
    directly against the raw schema: `StoreSafeIn` and `StoreSafeOut` —
    the tables R.Lynx provides specifically for tracking cash moving in
    and out of the till — are both completely empty (0 rows). `Charge`
    (generic expenses) has exactly 4 rows total, all shop/warehouse rent,
    nothing supplier-related. The only payment-adjacent figure anywhere is
    `Supplier.Account` (`balance` in `suppliers()`), a single running
    "amount currently owed" with no history, no dates, no line items.
    A first attempt at this session showed `total_purchased - balance` as
    an "estimated paid so far" — wrong, and reverted. `TotalPurchased`
    looks like it should be an all-time money total but its values are
    tiny (RUBY ROSE: `total_purchased` = 45, actual purchase value =
    67,798,680 DZD) — it's a count of something (roughly order-sized), not
    DZD, so subtracting real money (`balance`) from it produced a nonsense
    large negative "amount paid" for every single supplier. See the note
    on `suppliers()` in `metrics.py`. The honest alternative,
    `purchase_value` (summed from real purchase lines), is *also* not
    usable for this — discovery #3 already established purchase-line
    totals run roughly double what they should. There is no field in this
    database, computed or raw, that gives an honest "amount paid to
    suppliers" figure — `balance` (what's currently owed) is the only
    payment-related number shown anywhere, on the main Suppliers page.
    Same category as discovery #5 (no stocktake line detail) — don't
    build toward anything more granular unless a fresh copy from the
    actual till PC someday shows the shop started using
    `StoreSafeOut`/`Charge` for this.
11. **Remote viewing reached full read-only feature parity with the local
    dashboard** (Today date presets + true custom ranges, Tickets and
    Stock catalog tabs, ticket/purchase drill-down), decided via a
    5-advisor council review that unanimously rejected any live-tunnel
    approach — see `export_static.py`'s module docstring for the design
    (bounded ticket drill-down window, `daily.json` for client-side range
    slicing, no server-side exposure added at all). One real perf bug
    caught before shipping: naive per-page drill-down export took ~19
    minutes (rebuilding `Metrics` from scratch per page); reusing one
    shared `Metrics` instance cut that to ~40 seconds.

## Verified numbers (as of 2026-08-10, re-verify with `pytest tests -q`)

| Check | Value |
|---|---|
| Revenue, all time (excl. `ItemID<=0`) | 266,299,322 DZD |
| Gross profit, all time | 26,686,300 DZD |
| Account payments ("Paiement de règlement") | 30,420,753 DZD |
| Stock at cost | 59,168,540 DZD |
| Receivables (positive balances only) | 18,035,898 DZD |
| `Receipt` / `ReceiptEntry` / `Item` / `Customer` rows | 7,858+ / 39,736+ / 1,570 / 662 |

`tests/conftest.py`'s `expected_counts`/`expected_totals` gate every change —
"grows" values must never fall, point-in-time values (stock, receivables) are
checked within tolerance since they drift as trade happens.

## Deployment state (not visible from the code)

- **`install-startup.bat` has been run on this machine** — Task Scheduler
  entries "Shop Analysis - Dashboard" and "Shop Analysis - Digest" are live.
- **Remote viewing is live and deployed**: Cloudflare Pages project
  `promakeupmihoubipos`, gated by Cloudflare Access (email-allowlist policy
  "owner only", owner's email only). `remote.enabled: true` in config.yaml.
  See `poslib/present.py`, `export_static.py`, `poslib/remote.py`,
  `watcher.py` for how the push is wired.

## Cloudflare setup on this machine

Already done — one-time setup + troubleshooting gotchas moved to the
`cloudflare-remote-debug` skill (`.claude/skills/cloudflare-remote-debug/`).
Load it if you're touching `poslib/remote.py`, `export_static.py`, or
debugging the remote push.

## Cross-machine sync is automated

Sync mechanism: the `SessionStart` hook in `.claude/settings.json` (see that
file). Sync exclusions and why: see `.gitignore`'s own comments. This file
(`CLAUDE.md`) plus the git history are the actual continuity mechanism.

## What's left (optional, not blocking)

- **`.env` is empty on every machine** (gitignored, by design). Email and
  Telegram digest channels are wired up but need real credentials. WhatsApp
  additionally needs Meta template approval — see
  `poslib/channels/whatsapp_channel.py` before turning it on.
- **Arabic web font not fetched** (`tools/get_fonts.py` — harmless, falls
  back to Windows' own Arabic font).
- **Patch #1 (expiry stock) was explicitly dropped** — see discovery #6.
- **Task 14's manual spot-check against a live POS screenshot was not
  done** — it needs a screenshot from the shop owner, which wasn't
  available this session. The programmatic tender/on-account
  reconciliation (discovery #9) is the automated substitute; a manual
  screenshot comparison is still worth doing next time the owner is
  available, per the plan's Task 14 Step 2.
- Nothing else from Patch #2/#3/#4 is outstanding. New metrics belong in
  `poslib/metrics.py`, new diagnostic rules in `poslib/diagnostics.py`, new
  owner-entered data in `poslib/ownerdata.py` (never in a file `etl.py`
  rebuilds) — see the architecture rules at the bottom of `README.md`.

## Environment note

Python and Git are on PATH via winget installs
(`%LOCALAPPDATA%\Programs\Python\Python312`, `C:\Program Files\Git\bin`).
Node.js is present via a "pi-node" managed install; `wrangler` was added
globally on top of it. `.venv` is gitignored and rebuilt with `setup.bat`.

## gstack (REQUIRED — global install)

**Before doing ANY work, verify gstack is installed:**

```bash
test -d ~/.claude/skills/gstack/bin && echo "GSTACK_OK" || echo "GSTACK_MISSING"
```

If GSTACK_MISSING: STOP. Do not proceed. Tell the user:

> gstack is required for all AI-assisted work in this repo.
> Install it:
> ```bash
> git clone --depth 1 https://github.com/garrytan/gstack.git ~/.claude/skills/gstack
> cd ~/.claude/skills/gstack && ./setup --team
> ```
> Then restart your AI coding tool.

Do not skip skills, ignore gstack errors, or work around missing gstack.

Using gstack skills: After install, skills like /qa, /ship, /review, /investigate,
and /browse are available. Use /browse for all web browsing.
Use ~/.claude/skills/gstack/... for gstack file paths (the global path).
