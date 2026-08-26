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

## Verified numbers (as of 2026-08-10, re-verify with `pytest tests -q`)

**Stale after discovery #13 above** — devis tickets are now correctly
excluded from revenue/profit, so `revenue_all_time`, `gross_profit_all_time`,
`revenue_12m` and `gross_profit_12m` below may come out slightly lower than
this table on the next real run. That is expected; lower the frozen floors
in `tests/conftest.py` to match rather than treating a small drop as a bug.

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

## Customer distribution — status as of 2026-08-26

The tool is going from "single dev-PC project" to "installed on a real
customer's till PCs." First customer: one owner with **3 separate
stores**, each its own PC and its own independent `.dblx` database. Full
design is written up in
`docs/superpowers/specs/2026-08-25-installer-updates-multistore-design.md`
— read that file before touching packaging, `poslib/remote.py`, or
anything related to updates or multi-store hosting. Its "Suggested build
order" section sequences the work into the 5 components below.

### Component checklist (read this first — it's the current source of truth)

| # | Component | Status | Plan / where the detail lives |
|---|---|---|---|
| 1 | Packaging: PyInstaller onedir + Inno Setup `Setup.exe` | **DONE** | `docs/superpowers/plans/2026-08-25-packaging-installer.md` (status banner + SDD ledger at `.superpowers/sdd/2026-08-25-packaging-installer/progress.md`). Two carried-forward items: a leftover test install at `C:\Program Files\Shop Analysis\` still needs manual uninstall (needs explicit go-ahead — was blocked by the permission classifier); the `console=False` crash-visibility gap was accepted as-is by the user, candidate to revisit alongside Component 3. |
| 2 | DB auto-detect wizard page + silent watcher auto-start (`schtasks /sc onlogon`) | **NOT STARTED** | `docs/superpowers/plans/2026-08-26-db-autodetect-watcher-autostart.md` — plan fully written, zero tasks executed (`packaging/setup.iss` has no diff yet). This is the next thing to build. |
| 3 | Silent auto-update via GitHub Releases | **NOT STARTED** | No plan written yet. Depends on Component 2 being done first (same build-order reasoning as the spec). |
| 4 | Cloudflare Pages push over direct REST API (no `wrangler`/Node.js) | **DONE, committed, phone-verified** | `poslib/remote.py`, commit `5df4d73` (2026-08-26), superseding the scoped-token approach in `docs/superpowers/plans/2026-08-25-cloudflare-token-auth.md` (see that file's status banner). Verified by pushing to a disposable throwaway Cloudflare Pages project (created and deleted via the API — the real store project `promakeupmihoubipos` was never touched) and confirming it loaded correctly from an actual phone, not just a "success" API response. 25 unit tests passing (`tests/test_remote.py`). |
| 5 | Multi-store hub page + cross-store stock search | **NOT STARTED** | No plan written yet. No auto-matching, per the decision below — V1 shows matching rows side by side. |

Key decisions worth knowing without re-reading the whole spec:
- **This dev PC is not one of the 3 customer stores** — it stays on the
  existing git-based setup unchanged. The new installer is only for
  customer PCs.
- **No merged/summed numbers across the 3 stores** — the owner explicitly
  wants each store's performance reviewed separately; the hub is a shared
  front door, not a data-merging layer.
- **No auto-matching for cross-store stock** — product codes/names aren't
  guaranteed consistent across the 3 independent R.Lynx databases
  (confirmed with the owner, not assumed). Auto-matching risks a wrong
  number feeding a real buying decision — same class of mistake as
  discovery #11 below. V1 shows matching rows side by side and lets the
  owner's own eyes do the matching; a real combined total would need a
  manual product-linking step, not built.

### The actual product goal (owner's own words, 2026-08-26) — read this before prioritizing anything

The owner does not want a local dashboard. He already has R.Lynx's own POS
screen at the till for local use — **the entire reason this tool exists is
so he (or each store's owner) can check that store's numbers on his phone
when he isn't physically at the shop.** This reframes priority for
everything not yet built:

1. **Install must need zero technical steps** — no terminal, no typed
   config, ideally not even the DB path (Component 2, DB auto-detect,
   still not built — first-run today still needs someone to hand-edit
   `config.yaml`'s `database.path`, which fails this bar).
2. **The background watcher must start itself, silently, on its own, the
   moment the store PC is turned on — invisible to the till workers.**
   This is the one piece the approved spec assumed rather than designed:
   Component 3 ("Silent, automatic updates") talks about the watcher
   "already running continuously via the existing Task Scheduler entries"
   as a given, but nothing in `packaging/setup.iss` actually creates that
   entry — today it only exists on this dev PC because `install-startup.bat`
   was run by hand. **Planned but not yet built** (Component 2 above,
   `docs/superpowers/plans/2026-08-26-db-autodetect-watcher-autostart.md`):
   `packaging/setup.iss` should bake in the exact same
   `schtasks /create ... /sc onlogon` call that `install-startup.bat`
   already uses for "Shop Analysis - Dashboard" (the proven, already-working
   mechanism — not a new one), but pointed at `ShopAnalysis.exe --watcher`
   instead of `start-quiet.bat`, run as an Inno Setup `[Run]`/
   `[UninstallRun]` step so it's installed and removed automatically, no
   separate `.bat` the owner has to run. **Only the watcher needs to
   auto-start — not `app.py`'s Flask server.** `watcher.py` rebuilds the
   cache and pushes to Cloudflare entirely on its own timer
   (`_remote_push_due`/`_run_remote_push`); a local browser dashboard is
   not required for remote viewing to work at all.
3. **`remote.enabled: true` plus a real Cloudflare Pages project per store
   is what makes "viewable from his phone" actually true** — this piece
   is inherently a one-time technical setup (a Cloudflare API token,
   `Pages:Edit`-scoped, shared across the account per Component 4's
   decision) that rachad does once per store when installing, not
   something the non-technical owner ever sees or types.
4. Local dashboard viewing (the whole original single-shop build) still
   works and isn't being removed — it's just explicitly **not** the
   priority for the 3-store customer rollout. Don't spend customer-facing
   design effort making the local dashboard experience nicer; spend it on
   auto-start reliability and the remote/hub experience instead.

Next build-order step per this reframing: execute Component 2's plan
(`docs/superpowers/plans/2026-08-26-db-autodetect-watcher-autostart.md`,
already written, not yet started) — then Component 3 (auto-update
mechanism) and Component 5 (hub + cross-store stock search), neither of
which has a plan written yet.

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
- **A test install from packaging verification is still on this dev PC**
  at `C:\Program Files\Shop Analysis\` (with `unins000.exe`) — uninstall
  needs the user's explicit go-ahead (system-modifying action outside the
  repo, was blocked by the permission classifier when first attempted).
  Not urgent, not touched by anything else here, but don't forget it's
  there. See Component 1 in "Customer distribution" above.
- **Work tree cleaned 2026-08-26**: removed `build/`, `dist/`,
  `dist-installer/` (regenerable PyInstaller/Inno Setup output),
  `.wrangler/` (stale, wrangler no longer used), `__pycache__`/
  `.pytest_cache` (regenerable), and `graphify-out/` (stray unrelated
  `/graphify` skill output). All were already gitignored — nothing tracked
  changed. Real data (`data/`, `digests/`, `backups/`, `cache.db`,
  `logs/`, `remote-site/`, `static/photo-cache/`) was left untouched.

## Environment note

Python and Git are on PATH via winget installs
(`%LOCALAPPDATA%\Programs\Python\Python312`, `C:\Program Files\Git\bin`).
Node.js is present via a "pi-node" managed install; `wrangler` was added
globally on top of it but is **no longer used by pos-tool itself** —
`poslib/remote.py` pushes to Cloudflare Pages over direct REST calls as of
2026-08-26 (see "Customer distribution" above), precisely so customer
installs don't need Node.js/wrangler at all. `.wrangler/`'s local cache
dir was removed from this dev PC as stale for the same reason. `.venv` is
gitignored and rebuilt with `setup.bat`.

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
