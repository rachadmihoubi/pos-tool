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
live on the dev PC (see "Machine identity" above for which physical PC that
is). See "What's left" below.

## The one rule that overrides everything else

**Never write to the source `.dblx` file.** It is opened read-only and always
copied to a temp folder before parsing (`poslib/etl.py:copy_database_readonly`).
Every change must preserve this. If you're ever tempted to open the source path
directly for anything other than a read-only copy, stop.

## Machine identity — check this before assuming which PC you're on

This repo is git-synced (via the `SessionStart` hook's `git pull --ff-only`)
across multiple physical PCs: rachad's own dev PC, and the store till PCs
(each till computer also doubles as a place to continue developing pos-tool
"while at work" — a plain git-clone checkout of this same repo, separate
from that store's own packaged installer / `%LOCALAPPDATA%` install used
for real production). Because `config.yaml` is git-tracked (see below) and
genuinely differs per machine, inferring "which PC am I on" indirectly each
session — file timestamps, whether a particular drive exists, whatever —
has already caused real confusion at least once (see the 2026-08-29
"till PC" notes further down this file, under "Store #1 migration...").
**Don't infer it. Check the hostname against this table.**

At the start of a session, run (PowerShell): `$env:COMPUTERNAME`

| Hostname | Role | This machine's correct `database.path` |
|---|---|---|
| `DESKTOP-ERN4KAR` | **Dev PC** — rachad's own machine, not a real store (confirmed directly by the user, 2026-08-31) | `C:/Users/RACHAD/Desktop/pos tools/Base de données4.dblx` |
| `DESKTOP-94UHGGD` | **Store #1 (Pro Makeup Boumati) PC** — used both for the real till (via `E:` drive backups) and for dev work on this git checkout while at work; this machine's `config.yaml` `database.path` is intentionally left uncommitted/local-only (see the "till PC identity" memory) since it genuinely differs from every other machine | `E:/Base de données4.dblx` (kept uncommitted in `config.yaml` on this machine — do not push it) |

If a third machine (a second or third store) starts being used for dev
work on this repo too, add its hostname here the same way — this table is
meant to grow, not stay at two rows.

### Why this matters: `config.yaml` is git-tracked and shared

`config.yaml` (unlike `.env`, which is gitignored) is committed to this
repo and pulled on every session start. Most of it — thresholds, digest
settings, UI defaults — is meant to be shared across machines. But a few
fields are genuinely machine-specific: `database.path` always, and
potentially `remote.cloudflare_project_name` / `remote.stock_json_token` if
a store PC's dev checkout is ever pointed at that store's real Cloudflare
project instead of the dev/staging one. If two machines each commit
`config.yaml` with their own different values for these fields, whichever
machine pulls last silently gets the other machine's value.

**`database.path` mostly fails loudly, not silently** — `poslib/etl.py`
raises a clear `ETLError` naming the missing file if the path is wrong, so
a wrong path after a pull is usually caught immediately rather than causing
quiet bad behavior. **`remote.*` fields would NOT fail loudly** — a wrong
project name or token would just silently push to (or fail to reach) the
wrong Cloudflare project, with no error naming the mistake. **If
`config.yaml` changes after a `git pull` you didn't expect, check it
against this machine's row in the table above before assuming it's
correct**, and restore this machine's own values rather than working
against a value that just arrived from the other PC.

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
| 1 | Packaging: PyInstaller onedir + Inno Setup `Setup.exe` | **DONE** | `docs/superpowers/plans/2026-08-25-packaging-installer.md` (status banner + SDD ledger at `.superpowers/sdd/2026-08-25-packaging-installer/progress.md`). One carried-forward item remains: the `console=False` crash-visibility gap was accepted as-is by the user, candidate to revisit alongside Component 3. (The leftover test install at `C:\Program Files\Shop Analysis\` was manually removed 2026-08-26.) |
| 2 | DB auto-detect wizard page + silent watcher auto-start (`schtasks /sc onlogon`) | **DONE, interactively verified 2026-08-26** | `docs/superpowers/plans/2026-08-26-db-autodetect-watcher-autostart.md` (status banner has the full detail). Both tasks committed (`eb22bfb`..`10deb48`, 5 commits incl. two real fixes: placeholder-config detection, admin/user-account mismatch warning). Real install on this dev PC confirmed: no-overwrite guard, correct scheduled-task target/user/trigger, headless watcher run (no window, correct DB file watched, digest job ran), clean `/VERYSILENT` uninstall (task + folder both removed). One residual gap: the DB wizard page's own auto-detect/Browse flow wasn't click-through-exercised (this dev PC already had a configured `config.yaml` so the page was skipped by design) — the underlying code did go through a review + fix cycle already. |
| 3 | Silent auto-update via GitHub Releases | **CODE-COMPLETE; elevation gap fixed 2026-08-27, still NOT customer-rollout-ready** | `docs/superpowers/plans/2026-08-26-component3-auto-update.md` for the original build; `docs/superpowers/specs/2026-08-27-update-elevation-fix.md` for the elevation fix. Detect/download/checksum-verify/reject all verified 2026-08-27 against a real, now-deleted throwaway `v1.0.1` GitHub release. **Known blocking gap #1 — FIXED 2026-08-27**: the watcher's scheduled task stays de-elevated (`setup.iss`, unchanged, per commit `10deb48`) and no longer calls `check_and_apply_update()` at all. A second scheduled task, "Shop Analysis - Updater" (`packaging/setup.iss`), created at install time running as `SYSTEM`/`/rl highest`/`/sc onlogon`, owns the whole check→download→verify→install flow instead — SYSTEM tasks never hit an interactive UAC prompt (same mechanism as Windows' own built-in `SilentCleanup` task), so no stored credentials and no dialog nobody is there to click. It runs `ShopAnalysis.exe --apply-update --data-dir "<installing user's %LOCALAPPDATA%\Shop Analysis>"` (new `main.py` dispatch + new `SHOP_ANALYSIS_DATA_DIR` override in `poslib/paths.py::user_data_dir()`), because SYSTEM's own `%LOCALAPPDATA%` is not the shop's — solved the same way `setup.iss`'s existing `WriteDatabaseConfig` already captures `{localappdata}` at install time, not by having the elevated process guess. The two cheap fixes previously ruled out (per-user install; a `/CURRENTUSER` override) stay ruled out; this is the "second always-elevated helper task" option the table used to describe as not-yet-built. `check_and_apply_update()`/`check_for_update()`/etc. in `poslib/updater.py` are unchanged — only which process, running as what account, calls them changed. `update.enabled` stays defaulted to `false` in both `config.template.yaml` and `poslib/updater.py`'s own fail-safe default — this fix removes the *reason* it was off, but flipping it on for a real store still needs the last item below, not done as part of this fix. **Known gap #2 — FIXED 2026-08-27**: `poslib/updater.py` now writes a small marker (`update_attempted.txt` in `user_data_dir()`, holding the release tag) right after successfully launching an installer; `check_for_update()` refuses to retry the *same* tag again (logs an error instead - "publish a corrected release to resume") so a genuinely mis-cut release (bundled `VERSION` not actually bumped) can no longer loop download→install→relaunch forever - a launch that fails outright is *not* marked attempted, so it still retries normally next login. **Fully real-machine-verified 2026-08-27, both the mechanism and the real installer** (see `docs/superpowers/specs/2026-08-27-update-elevation-fix.md` for the full log of both passes): first, a dev-Python substitute test (`SYSTEM`/`/rl highest` task running `main.py --apply-update --data-dir <repo>`) confirmed **zero UAC prompt** and exit code `0`. Then Inno Setup was installed (`winget install --id JRSoftware.InnoSetup`) and the *actual* `packaging/pos-tool.spec` + `packaging/setup.iss` were built into a real `Setup.exe` and installed on this machine: `schtasks /query` confirmed both tasks created exactly as designed (`"Shop Analysis - Watcher"` as the installing user, `"Shop Analysis - Updater"` as `Système` with `--data-dir` correctly baked to the real `%LOCALAPPDATA%`); `schtasks /run` on the Updater task showed no prompt and the install's own log recorded `poslib.updater  Auto-update disabled via config - skipping check.` at INFO level — direct proof the real frozen build ran elevated end-to-end. Full manual cleanup afterward (process killed, Program Files/`%LOCALAPPDATA%\Shop Analysis`/build output all removed, both tasks confirmed gone); the real git-clone watcher kept pushing to Cloudflare throughout, completely undisturbed. **One minor finding from this pass, fixed and re-verified same session**: the uninstaller initially couldn't remove every file in one pass because the install's own `--watcher` process (started unconditionally by `setup.iss`'s `[Run]` section, even on a silent install) was still holding some files open — `CloseApplications=force` covers the *installer*'s file-copy phase but not the *uninstaller*'s file-removal phase. Added a `taskkill /F /IM ShopAnalysis.exe` `[UninstallRun]` entry ordered before file removal; reproduced the exact failure and re-ran the uninstall — process killed automatically, `Program Files` fully removed in one pass, both tasks confirmed gone. **Still open**: exercising the already-up-to-date no-op path against a real newer release (needs an actual second GitHub release to publish and compare against) — low priority, can be checked the first time a real update actually ships. |
| 4 | Cloudflare Pages push over direct REST API (no `wrangler`/Node.js) | **DONE, committed, phone-verified** | `poslib/remote.py`, commit `5df4d73` (2026-08-26), superseding the scoped-token approach in `docs/superpowers/plans/2026-08-25-cloudflare-token-auth.md` (see that file's status banner). Verified by pushing to a disposable throwaway Cloudflare Pages project (created and deleted via the API — the real store project `promakeupmihoubipos` was never touched) and confirming it loaded correctly from an actual phone, not just a "success" API response. 25 unit tests passing (`tests/test_remote.py`). |
| 5 | Multi-store hub page + cross-store stock search | **DONE — both halves of this component are now complete.** Hub is LIVE at `promakeupmihoubi-hub.pages.dev`, fully phone-verified by the owner 2026-08-28: store-link button, reference-based search, and cost-instead-of-price all confirmed working (see "Hub search shows cost, not price" section below for the token/Access-repoint work, and the follow-up stale-hub-deploy bug fixed the same day). **Installer-driven provisioning also DONE, verified 2026-08-29 on store #1's own till PC** (resumed there per the branch's own SDD progress notes, after being paused on the dev PC pending live-account go-ahead) — all 8 tasks of `docs/superpowers/plans/2026-08-28-component5-cloudflare-auto-provisioning.md` complete on branch `worktree-component5-cloudflare-provisioning`. Three real bugs were caught by live testing that no code review had found (all fixed and re-verified): an over-loose permission-group name match false-positived on unrelated Cloudflare "Custom Pages" Access groups; both Access-app-creation calls were missing the required `"type": "self_hosted"` field; and back-to-back Access-app creation for the same project could transiently 400 with "domain does not belong to zone" during Cloudflare's own brief domain-registration lag (fixed with a bounded retry). After all three fixes, a real `Setup.exe` was built and run through actual Windows UAC elevation end-to-end on the till PC and finished cleanly — no error dialog, provisioning log recorded success, and both `GET /` (302, gated) and `GET /stock-<token>.json` (200) were independently confirmed with `curl`. Every disposable Cloudflare resource and local test install created during this verification was fully torn down afterward. See "Component 5 installer provisioning — SDD progress" below for the session-by-session detail. | `docs/superpowers/specs/2026-08-27-component5-hub-design.md` for the design; `docs/superpowers/plans/2026-08-27-component5-hub-page.md` for the build and live-deploy log (all of Tasks 1-4 done, including the real-phone check). Corrects a real gap found in the original master spec (a Cloudflare Pages deployment fully replaces a project's site, so 3 stores can't share one hub project as a write target the way the master spec assumed) and a hard blocker (Access login sessions can't cross `*.pages.dev` origins — cookie scoping, not a Cloudflare quirk). Settled design: each store keeps pushing only its own project; `export_static.py` now emits one extra file, `stock.json` (item code, name, quantity, price — price inclusion was the owner's explicit call, a real widening from "low-sensitivity" but accepted), excluding inactive items. Making that one path reachable without login needs **two** Access applications per store, not one edited application with a path-scoped policy — Cloudflare has no such thing; a policy can't be scoped to a path within an app. The fix: a second, narrower Access application scoped to just `.../stock.json` with a Bypass policy, alongside the existing broad owner-only application — Cloudflare evaluates the most specific matching application first. **Empirically verified twice now**: first against a disposable throwaway project (2026-08-27, deleted afterward, same precedent Component 4 held itself to), then for real against the live `promakeupmihoubipos` project and a brand-new live `promakeupmihoubi-hub` project (2026-08-27) — `GET https://promakeupmihoubipos.pages.dev/` → 302 to Access login (broad app untouched); `GET .../stock.json` → 200 with real item rows, no redirect; `GET https://promakeupmihoubi-hub.pages.dev/` → 302 to Access login (new owner-only app on the hub). Built and pushed 2026-08-27, with two real corrections found while going live (both logged in the plan file): (1) the live store's `stock.json` initially 404'd even with the Bypass app working, because this dev PC's watcher only re-pushes when the ETL detects new source rows — the `export_static.py` code adding `stock.json` had landed on `main` with no new till activity since, so nothing had re-exported it; fixed by running `export(cfg)` + `push_remote(cfg)` directly once, bypassing that gate. (2) Cloudflare's Direct Upload API does **not** auto-create a project on first push (contrary to this component's own original assumption) — `POST /accounts/{id}/pages/projects` has to be called explicitly first; `tools/deploy_hub.py` does not yet have this fallback built in (a one-off manual step for now, not blocking). All three live Access applications (`promakeupmihoubipos.pages.dev` broad, `promakeupmihoubipos.pages.dev/stock.json` bypass, `promakeupmihoubi-hub.pages.dev` broad) created with a temporary Cloudflare token (`Pages:Edit` + `Access: Apps and Policies:Edit`) the user pasted in for one-time use, held in-memory only, never written to disk — user was asked to revoke it once done, same disposable-credential pattern as Component 4 and the original verification pass. **Built 2026-08-27**: `hub-site/` (static switcher + client-side cross-store search page, `Promise.allSettled`-based so one unreachable store doesn't hide the rest), `poslib/remote.py::push_remote` gained optional `project`/`export_dir` overrides (backward compatible — every existing watcher call is unaffected) so it can push a directory that isn't a store's own `config.yaml`-configured export, and `tools/deploy_hub.py`, a one-off manual CLI reusing that override to push `hub-site/` to its own Cloudflare Pages project. 34 new/updated unit tests passing (`tests/test_remote.py`, `tests/test_hub_site.py`, `tests/test_deploy_hub.py`); full suite (`pytest tests -q`) green at 310 passed, 1 skipped. Owner's explicit goal for the *other* half of this component: **the installer should provision new stores' Cloudflare setup automatically**, no manual Zero Trust dashboard clicking — resolved as a one-time-use powerful Cloudflare token (permission group "Access: Apps and Policies", Edit — confirmed distinct from `Pages:Edit`), pasted in only during a *new* store's interactive install (never persisted), that creates the project + both Access applications via Cloudflare's Access Management API and then mints the narrow ongoing `Pages:Edit`-only token for the watcher's permanent use. **Feasibility RESOLVED 2026-08-28 — confirmed a scoped token CAN mint other tokens.** The last step (minting the narrow ongoing token programmatically) is Cloudflare's *user*-scoped `POST /user/tokens` endpoint; this was empirically untested until now, so nothing was built against the assumption it would work. Tested directly with three real disposable tokens against the live Cloudflare API (all three since revoked by the user): the first two both failed every token-management call with a generic `9109 Unauthorized` — including on a plain `GET /user` self-info call — which looked at first like a hard platform block, but turned out to be a false negative both times: token 1 was built via the generic "Custom Token" flow, which Cloudflare's own docs say cannot expose the "User > API Tokens > Edit" permission at all (only a specific dashboard template, "Create Additional Tokens," can); token 2 used that correct template but had its permission dropdown accidentally left on "Account · Cloudflare Pages · Edit" instead of "User · API Tokens · Edit" — a dashboard mix-up, not a platform limit, so it never actually carried token-management rights either. A third token, created via "Create Additional Tokens" with the permission dropdowns actually set to **User · API Tokens · Edit**, worked end-to-end: `GET /user/tokens/permission_groups` and `GET /user/tokens` (list — correctly showed all 3 real account tokens, including the production "pos tool" watcher token) both succeeded; `POST /user/tokens` successfully minted a brand-new working `Pages Write`-scoped token; `DELETE /user/tokens/{id}` successfully revoked it again, confirmed gone via a follow-up list call. Full create→verify→delete lifecycle proven. (One unrelated, harmless quirk: this same working token still got `9109` on plain `GET /user` — "API Tokens Write" apparently doesn't cover basic self-info — irrelevant to the actual need.) **Conclusion: the original Component 5 design is viable as written, no fallback-to-manual-pasting redesign needed.** Separately, and still true regardless: **account-owned tokens** (`cfat_` prefix, Cloudflare's purpose-built "durable integration" token type) require **Super Administrator** account-level permission to create — a full member role, not a grantable token permission — so if this feature is ever revisited toward that mechanism instead of user-owned tokens, that's a materially higher privilege bar; not needed given the above already works via user-owned tokens. Per the global CLAUDE.md security/access-control gate, dispatch the opus-reviewer subagent to sanity-check the actual provisioning-flow plan before writing code — this finding only clears the feasibility question, not the design-review gate. No auto-matching across stores, no combined totals — unchanged from the master spec, V1 shows matching rows side by side. |

## Component 5 installer provisioning — SDD progress, DONE 2026-08-29

Executing `docs/superpowers/plans/2026-08-28-component5-cloudflare-auto-provisioning.md`
via `superpowers:subagent-driven-development`, in a git worktree at
`.claude/worktrees/component5-cloudflare-provisioning` on branch
`worktree-component5-cloudflare-provisioning` — **pushed to origin**, not
yet merged to `main`. Ledger:
`.superpowers/sdd/2026-08-28-component5-cloudflare-auto-provisioning/progress.md`
(read this first when resuming — it has the full per-task detail this
summary compresses).

**Done (Tasks 1-6 of 8), all reviewed clean, all committed:**
- Task 1: live read-only Cloudflare Access-app shape audit — found the
  real Access-app JSON shape to replicate (`docs/superpowers/specs/2026-08-29-store-access-app-shapes.md`),
  and separately found the hub's own live Access app is missing the
  wildcard scoping (id `a972fabb-67e2-4217-ab16-29c885892857`) — a real,
  still-open, out-of-plan-scope gap flagged to the user, not yet fixed.
- Tasks 2-3: proved the Inno Setup env-var-passing mechanism for real
  (`SetEnvironmentVariableW` + `ExecAndCaptureOutput` crosses the process
  boundary, verified via a throwaway `EnvTestSetup.exe` build) and built
  `poslib/provision.py`'s Cloudflare API helper functions.
- Task 4: `poslib/provision.py`'s `provision_store`/`ProvisionResult`
  orchestrator — the full sequence (verify token → create Pages project →
  mint watcher token → patch config/.env → push placeholder site → create
  both Access apps → verify reachability → flip `remote.enabled: true` →
  write provision record). Commits `2a81ba5`, `2f92c1d`.
- Task 5: `main.py --provision-cloudflare` CLI dispatch, reads the
  one-time token from `POS_TOOL_PROVISION_TOKEN` env var only, never argv.
  Commit `028876c`.
- Task 6 (Steps 1/2/3/5 only — Step 4 deliberately deferred, see below):
  `packaging/setup.iss` gained an optional wizard page for entering the
  one-time provisioning token + account ID + project slug + owner email.
  Commit `3d1c5e9`. Before this was dispatched, an opus-reviewer
  plan-sanity-check (required by the global CLAUDE.md installer/elevation +
  security-config gate) caught and fixed 6 real issues in the brief before
  any code was written: taskkill must run **before**, not after, the
  provisioning call (`provision_store` pushes a placeholder site to the new
  Pages project before either Access app exists, so a still-running watcher
  with stale cached config could race a real push into that ungated
  window); the watcher must be relaunched via
  `schtasks /run /tn "Shop Analysis - Watcher"`, not a direct `Exec` of the
  exe (a direct `Exec` from this elevated installer process would launch
  the watcher at the installer's own elevated token, defeating the
  scheduled task's own `/rl limited`); a `MsgBox` must show on failure (the
  log-only rationale inherited from Task 2 doesn't apply — this block can
  only run after a human just typed a token interactively, so silence on
  failure just hides an unrecoverable half-state); the three free-text
  fields must reject a literal `"` character (they're interpolated
  unescaped into a command line); a status-label message during the
  multi-minute `Exec`; and capturing `StdErr` alongside `StdOut`. Also used
  this pass to close out a previously-open item: re-confirmed (both by
  direct reading and by the opus-reviewer's own independent grep across
  every `ProvisionError(` raise site) that no `ProvisionResult`/
  `ProvisionError` message anywhere in the now-real `provision_store` ever
  embeds the powerful token or the minted watcher token value — safe to
  persist to the plaintext `cloudflare_provision_log.txt` this wizard page
  writes.

**Resumed and finished on store #1's own till PC, 2026-08-29 (same day, a
later session)** — this machine turned out to be the actual shop till
computer, not a second dev PC; the branch's own "push to origin, resume on
the store PC" plan worked exactly as intended. (This is exactly the kind of
identity ambiguity — inferred per-session instead of checked against a
fixed record — that the "Machine identity" table near the top of this file,
added 2026-08-31, now exists to prevent; if resuming work on that PC again,
check its hostname against that table first instead of re-deriving this the
same way.)

- **Task 6 Step 4 + Task 7, combined as planned.** Real go-ahead asked and
  given (a fresh disposable Cloudflare token, `Pages:Edit` + `Access: Apps
  and Policies:Edit` + `User API Tokens:Edit`, scoped to the real account).
  First live run — via a direct scripted `provision_store()` call, isolated
  from this machine's real config.yaml/.env with `SHOP_ANALYSIS_DATA_DIR`
  — caught two real bugs no review had found: `get_pages_edit_permission_group_id`'s
  substring match false-positived on unrelated Cloudflare "Custom Pages"
  Access permission groups (fixed to an exact-name match); both Access-app-
  creation calls were missing the required `"type": "self_hosted"` field,
  rejected by Cloudflare with a 400 (fixed). Commit `98e32d7`. After both
  fixes, a full live run succeeded end-to-end and a same-args re-run
  correctly refused instead of duplicating anything - confirmed by a full
  account-wide listing showing zero leftover resources after teardown.
- **Then the actual Setup.exe/wizard-page/Pascal-Script smoke test itself**
  (this session cannot self-elevate past UAC, so the user drove the
  clicking while Claude verified results and cleaned up): built a real
  `Setup.exe`, ran it through actual Windows UAC elevation. This surfaced a
  **third** real bug: `create_broad_access_app`/`create_bypass_access_app`
  calling `session.post` back-to-back for the same project can transiently
  400 with `access.api.error.invalid_request: domain does not belong to
  zone` (error 12130) for a few seconds — Cloudflare's own domain index
  lagging behind the broad app having just registered the bare domain.
  Reproduced directly (a domain with no real Pages project behind it 400s
  the same way, confirming this is genuinely about domain/zone
  registration, not a payload defect). Fixed with `_post_access_app`, a
  bounded retry (5 attempts, 5s backoff) that only retries this exact
  error code+message. Commit `2ba2acb`.
- **Rebuilt and ran a fourth time, end to end, through real UAC
  elevation — clean success**, no error dialog: `cloudflare_provision_log.txt`
  recorded "Cloudflare setup finished" with the live store URL, and both
  `GET /` (302, gated) and `GET /stock-<token>.json` (200, `[]`) were
  independently confirmed with a raw `curl`. This is the first fully clean
  run of the actual shipped installer mechanism, not just `provision_store()`
  in isolation.
- **Full cleanup after every run this session**: all Cloudflare resources
  created across every test (4 disposable Pages projects, 7 Access apps, 4
  minted watcher tokens, both one-time provisioning tokens) were deleted or
  revoked; every local test install (`Program Files\Shop Analysis`, both
  scheduled tasks, `%LOCALAPPDATA%\Shop Analysis`) was fully uninstalled
  and removed. Nothing from this verification was left on the real account
  or this machine.
- **One unrelated but real bug found and fixed along the way**: the
  installer's `[Run]` section launches both the auto-started watcher and
  the interactive dashboard at install time; both hit `ETL.refresh()`
  against the same real database close together and raced on the same
  `cache.building` temp file, crashing with a visible `PermissionError`
  dialog. Fixed in `poslib/etl.py` with a cross-process advisory lock
  (`msvcrt`, matching this module's existing Windows-only API use) held for
  the whole `refresh()` call — a second caller blocked on the lock just
  sees the first caller's already-finished rebuild instead of racing a
  second one. Commit `f840460`. Not scoped to Component 5, but caught live
  during this session's testing and worth fixing rather than leaving a
  known crash in place.
- **Task 1's out-of-plan-scope gap — FIXED separately the same day,
  2026-08-29**: the hub's own live Access app (id
  `a972fabb-67e2-4217-ab16-29c885892857`) was missing the wildcard scoping
  found during Task 1's audit (every `<hash>.promakeupmihoubi-hub.pages.dev`
  preview deployment was reachable without logging in). Fixed with a `PUT`
  mirroring store #1's already-correct shape: `self_hosted_domains` and
  `destinations[]` both gained the `*.promakeupmihoubi-hub.pages.dev`
  wildcard entry, the existing owner-only policy sent back unchanged
  (same email, same `reusable: false`) so nothing else about the app's
  behavior changed. The `aud` tag (what ties an existing login session to
  this app) stayed identical across the `PUT`, confirmed in the response,
  so the owner's existing session was not invalidated. Verified live
  immediately after (no propagation delay needed this time): a bare,
  cookie-less `curl -sI` against the bare hub URL and both
  previously-ungated preview hashes from Task 1's own findings
  (`5dc8a56e...`, `e786f12d...`) all returned `302` (gated) - the exact
  same two URLs that returned `200` (ungated) before the fix. This was a
  live edit to the real, in-use hub's own Access config (not a disposable
  test resource), done with a narrowly-scoped disposable token
  (`Access: Apps and Policies:Edit` only, no `Pages`/`User API Tokens`
  needed since nothing else was created), revoked immediately after.
- **Task 8 (docs)**: this CLAUDE.md update plus the `.env.example` note
  below.

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

## Store #1 migration to the packaged installer — PAUSED, STUCK, 2026-08-29 (dev-PC fixes applied same day, live re-verification still outstanding)

**Status: RESOLVED 2026-08-31 — see "Cross-store hub auto-registration +
installer reliability fixes" below.** The IPv4/timeout/watchdog fix round
described here turned out to be necessary but not sufficient; the actual
remaining stuck-install cause (found 2026-08-31, on this same real till
PC) was unrelated to networking at all — see that section for the full
story, including two more real installer bugs found and fixed the same
session. Left the rest of this section intact as the historical record of
what was tried and ruled out.

**Status (as originally written, 2026-08-29): a full round of fixes for
this incident is written, tested, and committed on the dev PC — but per
this file's own repeated "don't call it fixed until it's confirmed on the
real thing" rule (see the `_redirects` section further down, which says
"don't repeat that a third time"), this is NOT yet confirmed to fix the
actual hang. Read the "Dev-PC fix round" subsection below before
attempting another real install.**

### What this was

This machine (the real till PC, referred to elsewhere in this file as "the
dev PC" from a different session's perspective — see the till-PC-identity
memory note) is store #1, real name **Pro Makeup Boumati** (the owner's 3
stores are Boumati/this one, Setif, Eulma — "promakeupmihoubipos" was an
earlier placeholder project name from before the real store names were
known). Store #1 has run since the original build via a plain `git clone`
+ `install-startup.bat` (tasks "Shop Analysis - Dashboard"/"- Digest"),
predating the packaged installer entirely. The owner wants all 3 real
stores standardized on the same installer + Cloudflare auto-provisioning
flow, so this session attempted migrating store #1 first, as a real
(non-test) install — not a disposable verification run like the ones in
the SDD progress section above.

**Decisions already made and acted on:**
- New, permanent Cloudflare project name: `promakeupboumati` (not reusing
  `promakeupmihoubipos` — the owner hadn't bookmarked any link yet, so no
  disruption either way; `promakeupmihoubipos` was kept, unchanged, to
  become the **kept git-clone dev copy's** own staging/dev target, since
  its `config.yaml`/`.env` already point there and need no reconfiguring).
- Owner-only Access email: `rachadm23@gmail.com` for now (can be changed
  later the same way the hub's own Access app was corrected earlier).
- **The old git-clone setup's scheduled tasks are currently DISABLED** —
  the user ran `schtasks /change /tn "Shop Analysis - Dashboard" /disable`
  and same for `"- Digest"` from an elevated prompt, and the running
  `watcher.py` process was killed. **The git-clone folder itself is
  untouched** (kept intentionally for dev work — the user will explicitly
  prompt for that mode when wanted) but **it is not currently pushing to
  Cloudflare and nothing is currently keeping this store's remote
  dashboard fresh.** `promakeupmihoubipos.pages.dev` will keep serving
  whatever it last had cached until either the old tasks are manually
  re-enabled or the new install finishes successfully and takes over.

### What's actually broken

Three real installer runs in a row got stuck on the "Setting up Cloudflare
remote access" step, needing an elevated `taskkill` to recover from each
(the process runs at the installer's own elevated token, so a non-admin
session/Task Manager can't end it — confirmed twice):

1. **First stuck run**: ~28+ minutes, zero new log output the whole time.
   Recovered by killing the process from an elevated prompt.
2. **Second run** (after deleting the orphaned watcher token from the
   first): failed *cleanly* this time, in a few minutes, with `Connection
   aborted... The write operation timed out` during the placeholder push.
   Fixed with `_push_placeholder_with_retry` (3 attempts, 10s apart) -
   commit `ff86064`, published as `v1.0.3`.
3. **Third run** (after deleting that run's token too, *and* the user
   switched to a different, better internet connection specifically to
   rule out a one-off connection quality issue): **stuck again**, 11+
   minutes, zero new log output - the same symptom as run 1, surviving
   the retry fix and the network change both. Diagnosed as a real,
   reproducible bug: `nslookup api.cloudflare.com` returns IPv6 (AAAA)
   addresses before IPv4 on **both** networks tested, and Python's
   `requests`/urllib3 has no happy-eyeballs fallback - it tries addresses
   in DNS order, so an IPv6 address that's unroutable but not actively
   refused (common) silently stalls each connection attempt for the OS's
   own TCP connect timeout before ever trying IPv4. Across the ~10
   sequential Cloudflare calls one provisioning run makes, that compounds
   into a many-minute hang. Fixed by forcing IPv4-only DNS resolution
   process-wide (`urllib3.util.connection.allowed_gai_family`, patched
   from `poslib/remote.py` since `poslib/provision.py` already imports
   it) - commit `2409f85`, published as `v1.0.4`, verified directly with a
   live request under the patched resolver before shipping.
4. **Fourth attempt, using `v1.0.4`: still reported stuck.** This is the
   open, unresolved problem - the IPv4 fix was verified to work in a
   direct Python check but **has not been confirmed to actually fix a
   real installer run**. The user asked to stop debugging live and pick
   this back up from the dev PC instead, before that confirmation
   happened. **Open questions for whoever resumes this:**
   - Was the user's 4th attempt actually running the new `v1.0.4` build
     (freshly downloaded), or could a stale `Setup.exe`/cached download
     have been reused? Worth confirming explicitly before assuming the
     IPv4 fix itself is insufficient.
   - If it really was `v1.0.4` and it still hung: the IPv4 patch forces
     `urllib3`'s address family, but `poslib/provision.py`'s own
     `requests.Session()` instances are separate from `remote.py`'s - the
     patch is process-global (`urllib3.util.connection.allowed_gai_family`
     is monkeypatched at the module level, which should affect every
     `requests`/urllib3 call in the process regardless of which module
     created the session) so this *should* cover `provision.py`'s direct
     Cloudflare calls too, but this reasoning has not been verified
     against a real stuck run the way the fix itself was verified against
     a live *working* one. Re-confirm this assumption first.
   - Consider adding real, visible progress logging inside
     `provision_store()` (a `log.info` before/after each major step -
     `verify_token`, `create_pages_project`, `mint_watcher_token`,
     `push_remote`, each Access app, each `verify_reachable` call) so a
     future stuck run's *exact* stall point shows up in
     `%LOCALAPPDATA%\Shop Analysis\logs\pos-tool.log` immediately, instead
     of having to infer it indirectly from `config.yaml`/`.env` write
     timestamps the way this session had to.
   - Consider a hard, sane upper bound (e.g. a few minutes total) on the
     *whole* `provision_store()` call, so a still-unknown future hang
     fails loudly with a clear "took too long, giving up" error instead of
     silently consuming the installer session for 10-30+ minutes again.

### Dev-PC fix round (2026-08-29) — implemented and tested, NOT live-verified

An opus-reviewer subagent (per the global CLAUDE.md's mandatory
installer/elevation gate) reviewed a fix plan against this section's own
"Open questions" list above before any code was written, and gave this
priority order: (1) split the request timeout into a `(connect, read)`
tuple, since a single float is re-armed per DNS-resolved address and an
unroutable IPv6 address can cost the full timeout before urllib3 falls
back to IPv4 — this compounds with the IPv4-forcing monkeypatch already
shipped as `2409f85`, it doesn't replace it; (2) add per-attempt/per-step
elapsed-time logging in the actual silent windows; (3) a catch-all
exception handler so `provision_store` can never raise past its own
boundary; (4) only last, and only once a killed run is safe to retry, a
watchdog thread with a hard timeout. All four are now implemented in
`poslib/provision.py`, `poslib/remote.py`, and `main.py`, with new tests
in `tests/test_provision.py`/`tests/test_main.py` (54 and 14 tests
respectively) and the full suite green (381 passed, `test_export_static.py`
deselected per its own documented ~3.9-min real-database cost).

**What's confirmed** (dev-PC test coverage, not a real installer run):
- Every Cloudflare call in `provision.py`/`remote.py` now carries the
  bounded `(10, 30)` connect/read timeout tuple (`_REQUEST_TIMEOUT_SECONDS`
  in both files).
- Per-step (`provision_store`'s own major steps) and per-attempt
  (`verify_reachable`, `_post_access_app`, `_push_placeholder_with_retry`,
  `push_remote`) elapsed times are now logged to
  `%LOCALAPPDATA%\Shop Analysis\logs\pos-tool.log` — directly answering
  this section's own "consider adding real, visible progress logging"
  open question, so a future stuck run's exact stall point shows up in
  the log immediately instead of having to be inferred from file-write
  timestamps the way this incident's first three attempts had to be.
- A preflight log line at the very top of `provision_store` records the
  build's `current_version()`, whether `urllib3`'s `allowed_gai_family()`
  is actually forced to IPv4 in this process (confirms the `2409f85`
  monkeypatch really took effect — the exact thing attempt 4's "open
  questions" couldn't confirm), and a live DNS resolution of
  `api.cloudflare.com` showing every address the resolver actually
  returned.
- `provision_store` now has a final `except Exception` catch-all
  (`log.exception` + a `ProvisionResult(False, ...)` return) so it can
  never raise past its own boundary — before this fix, an unexpected
  `OSError`/`ValueError`/`ConfigError` could have escaped unhandled into
  the installer's windowed dialog.
- **A killed run is now safe to retry** — the load-bearing precondition
  for the watchdog below. `try_reuse_existing_watcher_token` verifies
  (live against Cloudflare) whether the token id `.env` already has
  matches the token Cloudflare says exists under this store's name, and
  reuses it instead of refusing outright; `patch_env_secrets` is now
  called immediately after minting/reusing the watcher token — before
  anything else that could fail or be killed — so that value is never
  the reason a retry gets refused; `_atomic_write_text` (temp file +
  `os.replace`) means a process killed mid-write can never leave
  `config.yaml`/`.env` truncated.
- `main.py` now runs `provision_store` on a daemon thread with a 40-minute
  join timeout (`_run_provisioning_with_watchdog`), and on timeout prints
  a message and calls `os._exit(1)` rather than hanging the installer
  session forever. The thread's own target function wraps the call in
  `try/except BaseException` so even a crash *inside* the catch-all above
  (this project has a documented, recurring log-rotation `PermissionError`
  when the watcher process holds `logs/pos-tool.log` open — see the
  `deploy_hub.py` note further down this file) still produces a
  `ProvisionResult` rather than leaving nothing for the watchdog to read.

**What's reasoning, not measurement**: that unbounded `socket.getaddrinfo()`
(called once per connection attempt inside urllib3, before `requests`'
timeout tuple even applies) is a plausible remaining cause of a hang that
survives the IPv4 fix is a correct general fact about Python's stdlib, but
that it actually caused *this* incident's attempts 1 and 3 is unproven —
the new DNS preflight log line is what will actually settle that on the
next real run, not this write-up. The 40-minute watchdog timeout is
arithmetic (~31-minute worst case across every retry/backoff constant in
`verify_reachable`/`_post_access_app`/`_push_placeholder_with_retry`,
doubled for the two Access-app verification calls), not a measurement of a
healthy run's real cost — **note this bound would not have fired on any
of the four recorded attempts above (11–28 minutes each)**; it's a
backstop against a still-longer future hang, not a fix for the specific
hangs already seen. The new per-step timing logs will show what a healthy
run actually costs on the next real attempt, and that measurement — not
this arithmetic — is the better basis for tightening the bound later if
wanted.

**What's still unverified — needs the real till PC and the owner's
go-ahead for live Cloudflare use, same as the standing rule elsewhere in
this file:**
- Whether any of this actually fixes the hang. Nothing here has been run
  against a real stuck installer.
- Whether the DNS preflight log line, on the next real attempt, actually
  shows an unroutable IPv6 address (confirming the working theory) or
  shows something else entirely (meaning the real cause is still
  undiagnosed).
- **Checked directly this session (2026-08-29), not assumed**: both old
  git-clone scheduled tasks ("Shop Analysis - Dashboard", "Shop Analysis
  - Digest") are now `Scheduled Task State: Enabled` with a `Last Run
  Time` of today and `Last Result: 0` — someone re-enabled them since the
  "PAUSED, STUCK" section above was written (that section had them
  disabled). So **store #1's remote dashboard is currently being kept
  fresh by the old mechanism**, not left stale. This fix round did not
  touch that state either way — re-check it's still true before assuming
  it going forward. Also checked: no `ShopAnalysis.exe` process is
  currently running and `C:\Program Files\Shop Analysis` does not exist
  (no stuck provisioning process, no lingering packaged install), but
  `%LOCALAPPDATA%\Shop Analysis` still exists — leftover data dir from a
  prior attempt, harmless but worth knowing it's there. Separately (and
  not part of this incident): this machine has four stray `pythonw.exe`
  processes running (two from `.venv\Scripts`, two from the global
  `Python312` install) — the same duplicate-watcher pattern already
  flagged in this file's "Weighted-average cost" section above; not
  touched here since it's unrelated to this fix.

Related commits: `dd1a462` (timeout tuples, step logging, catch-all
handler, idempotent token reuse, watchdog — this whole subsection).

### Cleanup needed before/when resuming

- **Multiple orphaned `pos-tool watcher - promakeupboumati` API tokens**
  likely exist on the real account by now (one per stuck attempt whose
  token wasn't deleted before the next retry) - list and clean up
  `/user/tokens` for anything named that before the next attempt, or
  `provision_store`'s own refusal-on-duplicate check will block it anyway
  (which is itself the signal to go clean up).
- The **`promakeupboumati` Pages project** itself is fine to keep/reuse -
  every attempt's `create_pages_project` call is idempotent.
- **No Access applications exist yet** for `promakeupboumati` - every
  attempt failed before reaching that step, so there is nothing to clean
  up there.
- This machine's own install state (`Program Files\Shop Analysis`,
  `%LOCALAPPDATA%\Shop Analysis`, whether a `ShopAnalysis.exe
  --provision-cloudflare` process is still running) was **not
  re-verified** after the user's last "still stuck" report, per their
  explicit "don't do anything, just commit this" - check it fresh before
  touching anything.
- **The old git-clone setup's scheduled tasks are still disabled** (see
  above) - re-enabling them (`schtasks /change /tn "..." /enable`, both
  task names) restores store #1's remote sync via the *old* mechanism if
  a quick stopgap is wanted while this installer issue gets sorted out,
  independent of finishing the migration.

Related commits this session: `98e32d7`, `2ba2acb`, `f840460` (earlier,
unrelated Component 5 fixes, all already verified working), then
`ff86064` (push retry), `2409f85` (IPv4 fix) for this specific incident.

## Cross-store hub auto-registration + installer reliability fixes (2026-08-31) — built, live-verified, two real installer bugs found and fixed

### What was built

Per the owner's request ("finish the store to hub automatically"), adding
a newly provisioned store to the shared multi-store hub — previously a
fully manual step (old `INSTALL_GUIDE.md` Step 6: hand-edit
`hub-site/stores.json` on the dev PC, `tools/deploy_hub.py`, commit) — is
now automatic, as the final step of `provision_store()`
(`poslib/provision.py`'s new `register_store_with_hub()`).

**Design was reviewed by an Opus subagent before any code was written**
(per this file's standing installer/Access-config gate). The reviewer
rejected the first draft — reading the hub's current store list through a
temporary create-then-delete Cloudflare Access bypass app — because this
exact provisioning code path has a real history of being killed mid-run
(the three stuck-install attempts in the section above), and a delete
that never runs would leave the hub's entire store list (every store's
`stock-<token>.json` URL, in one place) permanently public. The shipped
design instead reuses the same accepted tradeoff already live for each
store's own `stock-<token>.json`: the hub's store list now lives at a
**permanent** unguessable filename,
`hub-site/stores-41582b721adbd68e4fb50f5245f0e56b.json` (renamed from the
old plain `stores.json`; `hub-site/app.js`'s `STORES_JSON` constant must
match this exactly), behind a permanent bypass Access app created once
and left in place — no create/delete churn, no ungated window.
`HUB_VERSION` (currently `1`, in both `poslib/provision.py` and inside the
registry file itself) guards against a stale installer build's bundled
`hub-site/` silently rolling back the live hub's design — a provisioning
run refuses to touch the hub at all if the live `hub_version` is newer
than what it knows. A hub-registration failure never fails or rolls back
the store's own already-successful provisioning (loud `MsgBox` +
`HUB REGISTRATION FAILED` marker in the log instead — see
`register_store_with_hub`'s own docstring in `poslib/provision.py` for
the full design rationale). New wizard field: "Store's display name on
the hub" (blank = skip). `hub-site/{index.html,app.js,style.css}` are now
bundled into the installer (`packaging/pos-tool.spec`); the tokenized
registry file itself is deliberately never bundled (see the spec's own
comment) — `register_store_with_hub` always writes a freshly-fetched-and-
merged copy of that one file. Commit `a6869cd`.

**One-time live cutover already done this session**: the real hub
(`promakeupmihoubi-hub.pages.dev`) now serves the new `app.js` reading the
new registry filename, seeded with Boumati's pre-existing entry — done
using a disposable owner-supplied token (`Pages:Edit` +
`Access: Apps and Policies:Edit`), revoked immediately after, same pattern
as every other live-account change in this project's history. Verified
directly: `GET /` → 302 (still gated), `GET /stores-<token>.json` → 200
with correct JSON (one propagation-lag blip on the very first check,
resolved after ~15s — same class of transient already documented
elsewhere in this file, not a bug).

### Two real, pre-existing installer bugs found by live testing tonight (unrelated to the hub feature itself)

1. **The "Shop Analysis - Updater" scheduled task was silently failing to
   be created on every fresh install** — found because this session did
   several genuine from-scratch reinstalls of store #1 to test the hub
   feature, and `schtasks /query`/`Get-ScheduledTask` kept showing no
   Updater task at all (only Watcher). The task's `schtasks /create`
   command lived in a passive `[Run]` entry (`packaging/setup.iss`),
   whose exit code Inno Setup never checks by default — so a real failure
   there was invisible. The exact command line was extracted
   programmatically from the compiled `.iss` source (not hand-traced —
   hand-tracing the nested `\"`-escaping was error-prone) and verified
   correct by typing it into an elevated Command Prompt, which succeeded
   immediately — ruling out a syntax bug. Fixed by moving it into a new
   `CreateUpdaterTask` procedure, called from `CurStepChanged(ssPostInstall)`
   using `ExecAndCaptureOutput` (same pattern as the Cloudflare
   provisioning call), so a failure now writes
   `updater_task_log.txt` and shows a clear `MsgBox` instead of vanishing.
   **Confirmed fixed on a real install**: the next real reinstall's
   `updater_task_log.txt` read `Updater task created successfully`, with
   `schtasks`'s own "Opération réussie" in the captured output. The exact
   underlying reason the passive `[Run]` version failed while this
   `Exec`-based version (using the byte-identical command) succeeds is
   still not fully understood — plausibly something about `[Run]`
   entries' own execution context vs. an explicit `Exec()` call — but the
   fix works, is now instrumented for next time either way, and this is a
   defensible place to stop chasing it further. Published as `v1.0.7`,
   commit `73b416d`.
2. **A packaging self-inflicted bug, caught and corrected the same
   session**: `v1.0.5` was built by running PyInstaller *before* bumping
   `VERSION` to 1.0.5, so that release's installer-level `AppVersion`
   correctly said 1.0.5 but the internal `VERSION` file bundled inside it
   (read by `poslib/updater.py`'s `current_version()`, logged at the top
   of every `provision_store()` run) still said 1.0.4 — caught directly
   from a real install's own log line (`build version (1, 0, 4)`).
   Corrected in `v1.0.6` (commit `b21ed23`) by rebuilding cleanly with
   `VERSION` bumped first and confirming the bundled copy before
   compiling — worth remembering as a build-order gotcha for any future
   release: **always bump `VERSION` before running PyInstaller, and
   verify `dist/ShopAnalysis/VERSION` before compiling the installer**,
   not after.

### A red herring investigated at length, correctly ruled out

Repeated `push_remote()` failures during tonight's testing (`Connection
aborted... write operation timed out`, and once `SSLError(SSLWantWriteError)`)
led to an extended live investigation: connection speed test (fine, 21.6
Mbps up), Path MTU Discovery blackhole test via `ping -f -l <size>` at
increasing sizes up to 1500 (clean, no blackhole), then found Kaspersky +
**Kaspersky VPN** installed and running (`KSDE5.7` service) — a strong,
well-documented candidate for exactly this symptom (SSL inspection/VPN
tunnel interfering with uploads while small requests and speed tests look
fine). Quit Kaspersky, then had to separately stop the VPN service itself
(quitting the tray app doesn't stop the underlying Windows service — Defender-
style self-protection also blocked `net stop`, had to go through the
Kaspersky VPN app's own Disconnect/Exit). **None of this was actually the
cause** — pushes kept failing identically afterward. A `curl` test to the
exact same endpoint succeeded instantly, as did several hand-written
Python `requests` reproductions using the real account credentials in the
real sequence (`_get_upload_token` then `_upload_assets`, including with
`poslib.remote`'s own IPv4-only monkeypatch active) — all of which
succeeded fast, while calling the real `push_remote(cfg)` kept failing
seconds apart on the same machine. **The actual cause, found by finally
checking what was actually in the export directory**: `remote-site/` had
**263 MB across 12,626 files** — a full leftover export from an earlier
test cycle that a manual "wipe `%LOCALAPPDATA%\Shop Analysis`" step
hadn't actually cleared. My own hand-written reproductions all used a
single tiny fake file, which is why they never reproduced the failure.
Clearing the stale directory and retrying with the real ~2-file
placeholder succeeded in ~5 seconds. **Lesson for next time this comes
up**: check the actual size/file-count of what's being pushed *before*
investigating the network stack — Kaspersky/VPN/Defender were a genuine,
reasonable-looking lead that cost real time chasing, for a cause that
turned out to be mundane. (Windows Defender was also checked along the
way — found a real detection-log entry for a `Setup.exe` download from
`release-assets.githubusercontent.com` on 2026-08-29, which is a
plausible reason to eventually look at code-signing the installer, but a
Defender exclusion added during this investigation did not fix the actual
symptom either, consistent with the stale-directory explanation being the
real one.)

### Still open — a real, currently unresolved reliability gap, found by this same testing (not caused by tonight's changes)

On the cleanest full reinstall of the night (v1.0.7, genuinely empty
`%LOCALAPPDATA%\Shop Analysis`), provisioning itself succeeded end-to-end
in 40.3s including hub registration — confirming the hub feature works
correctly on a true first-time install. But the watcher's own **first
real** export (the full remote-parity product/customer drill-down export
— see the "Remote product/customer drill-downs now exported in full"
section above — 4,205 pages × 3 languages, 12,625 files, ~263 MB for this
store) **also failed to push**, with the identical
`Connection aborted... write operation timed out` symptom. This is a
different situation from the red herring above: this is a genuinely
large, real, current export, not stale leftover data. `_run_remote_push()`
already retries automatically on the next real database change (see
`watcher.py`), but if pushing an export this size is unreliable on this
connection via the current mechanism (many sequential small HTTP calls,
each carrying base64-encoded file content — see `poslib/remote.py`'s
`_upload_assets`), it may keep failing the same way indefinitely with
**no visible indication to the shop owner** that anything is wrong (a
separately-noted gap — see below). Not fixed this session; options worth
evaluating next time this is picked up: smaller upload batches, more
retry attempts with backoff specifically for the bulk-asset-upload step,
or reconsidering whether the full product/customer catalog needs to be
re-pushed on every regular watcher cycle (vs. only when it actually
changes, or on a slower cadence) given its size relative to
tickets/purchases. As of session end, `promakeupboumati.pages.dev` is
still showing the placeholder page ("This store is being set up") for
this reason — `remote.enabled` was manually flipped true and the hub
registration is correct, but real content has not yet landed; the watcher
will keep retrying on its own on the next database change.

### Other real findings from this session, not yet acted on

- **Silently-failing remote pushes have no visible indication to the shop
  owner at all** — confirmed directly tonight (multiple failed pushes,
  nothing surfaced anywhere a non-technical owner would see). Worth
  adding some visible signal (a dashboard banner, a digest note) if this
  becomes a recurring real-world problem — flagged, not built.
- At session start, **this machine had zero "Shop Analysis" scheduled
  tasks running at all** (neither the old git-clone Dashboard/Digest
  tasks nor any packaged-install Watcher/Updater task), despite a fully
  configured, previously-working packaged install being present in
  `Program Files`. Root cause not investigated (likely just: the process
  that had been running manually was closed and nothing was scheduled to
  restart it) — resolved as a side effect of this session's several real
  reinstalls, each of which now correctly creates both tasks (Watcher
  confirmed every time; Updater confirmed on the `v1.0.7` run specifically).

Related commits this session: `a6869cd` (hub auto-registration),
`719ac12` (bump to 1.0.5 — superseded, wrong internal VERSION),
`b21ed23` (bump to 1.0.6 — corrects that), `73b416d` (Updater task fix,
bump to 1.0.7). Published to GitHub Releases: `v1.0.5` (superseded, do
not use), `v1.0.6`, `v1.0.7` (current recommended build — includes both
the hub feature and the Updater task fix).

## Full-export push reliability fix (2026-08-31, later autonomous session) — implemented + unit-tested, NOT live-verified; plus a bigger separate finding

Continuing from the "Still open" gap logged the same day (store #1's
first real full-catalog export, ~263 MB / 12,625 files, failed to push
with a write-timeout): root-caused via `superpowers:systematic-debugging`
and the research-before-implementing gate (verified against wrangler's
own real source, `cloudflare/workers-sdk` on GitHub, via `gh`, not
training-data recall) rather than guessed at.

**Two real gaps found by reading `poslib/remote.py` and wrangler's
source side by side:**
1. `_upload_assets()` had zero per-batch retry and batched only by file
   count (500), never by bytes — a batch's total payload size was
   unbounded, and any single transient failure aborted the *entire*
   multi-minute push with no resume, forcing a full restart next time.
2. The codebase never called Cloudflare's `POST /pages/assets/
   check-missing` endpoint at all (undocumented by Cloudflare; confirmed
   from `packages/wrangler/src/pages/upload.ts`) — every push, including
   a retry of one that mostly-succeeded, re-uploaded literally every file
   from scratch.

**Fixed in `poslib/remote.py`**, TDD (failing tests written first,
`tests/test_remote.py`'s new `TestCheckMissingHashes`/`TestUploadBatching`
classes, 12 new tests):
- `_check_missing_hashes()` — calls check-missing before uploading;
  `push_remote()` now only uploads the subset Cloudflare reports missing,
  but always upserts the *full* hash set regardless. Fails safe: any
  rejection or exhausted-retry failure here falls back to uploading
  everything, never silently skips a real upload.
- `_batches()` — caps upload batches by both file count (500, unchanged)
  and total bytes (`_MAX_BATCH_BYTES = 40MB`, matching wrangler's own
  `MAX_BUCKET_SIZE`), so a handful of large aggregate pages in a big
  export can no longer produce one unpredictably large POST body.
- `_post_with_retry()` — shared retry/backoff (`_MAX_UPLOAD_ATTEMPTS = 5`,
  exponential 1s/2s/4s/8s) + JWT-refresh-on-401/403 wrapper, now used by
  both the upload and upsert-hashes steps. A batch failure is retried in
  place instead of aborting the whole push; a JWT that expires mid-push
  (plausible on a genuinely large, slow export) is refreshed and the
  batch retried with the new token, which the caller keeps using for
  every subsequent JWT-authed call.

Full suite re-run clean: `tests/test_remote.py` 41/41,
`pytest tests -q --deselect tests/test_export_static.py` 404 passed, no
regressions. `test_export_static.py` itself doesn't touch `remote.py`'s
internals (confirmed by grep) so its ~4-minute real-database cost wasn't
worth re-paying for this change.

**What this does NOT yet establish**: whether it actually fixes store
#1's real hang — same "don't declare a live Cloudflare deploy fixed until
confirmed against the real thing" discipline as the `_redirects` bug
history above. That real-world confirmation needs an actual retry against
the real store, which runs into the bigger finding below.

### A bigger, unrelated finding from the same session: this machine currently has no packaged install at all

Before touching `remote.py`, this session checked the machine state this
file's own 2026-08-31 (earlier) session had left it in, since that
session's own write-up says its exact next step was uncertain
("`promakeupboumati.pages.dev` was still showing the placeholder page at
session end"). What was actually found, checked twice (once at
investigation start, re-confirmed just before this write-up):

- `C:\Program Files\Shop Analysis` does **not** exist.
- No `Shop Analysis - Watcher` or `Shop Analysis - Updater` scheduled
  task exists — only the **old** `Shop Analysis - Dashboard` /
  `Shop Analysis - Digest` tasks (the git-clone mechanism), both
  `Ready`/enabled.
- The repo-root `config.yaml` (what those old tasks run against) has
  `remote.enabled: true` pointing at `promakeupmihoubipos` — the old
  placeholder/dev project, not `promakeupboumati`. This matches the
  documented intentional decision to keep this dev-PC copy pointed at
  the old project, but it also means **nothing on this machine is
  currently pushing to `promakeupboumati` at all**, packaged install or
  otherwise.
- `%LOCALAPPDATA%\Shop Analysis\config.yaml` is stale (Aug 26-27
  timestamps) with `remote.enabled: false` and no project name set - not
  the live `remote.enabled: true` / `promakeupboumati` state the earlier
  same-day session documented as the result of its own work.

**Read together, this means**: whatever packaged, `promakeupboumati`-
targeting install the earlier 2026-08-31 session finished (hub
registration succeeded, `remote.enabled` flipped true, per its own
write-up above) no longer exists on this machine now. Either it was
uninstalled since, or something about that session's final state didn't
persist the way its own notes describe. **Not investigated further or
acted on this session** - reinstalling on the real till PC is a
hard-to-reverse, live-production, elevation-requiring action (the
installer/elevation gate) that also needs a live Cloudflare token only
the owner can provide, so it needs the owner's/user's direction before
anyone touches it again, not an autonomous next step. Whoever picks this
up next should treat "is a packaged install even present, and does its
config match what the last session that touched it says it should" as a
first check before assuming any earlier write-up in this file still
describes the live state - two sessions in the same day already disagreed
on this exact machine's state once.

## Hub registration retry-on-stale-read fix (2026-08-31, same session) — a real hub-registration failure caught by a live installer run

The same real 2026-08-31 dev-PC install (`v1.0.7`, see
`%LOCALAPPDATA%\Shop Analysis\cloudflare_provision_log.txt` for the actual
run) that exercised the hub auto-registration feature for the first time
hit a genuine failure: the store itself provisioned and went live
correctly, but `register_store_with_hub` raised
`"The hub was pushed but this store does not appear in the re-fetched
store list"` even though the push had, in fact, succeeded — confirmed by
the very next line of the same log showing
`push_remote(promakeupmihoubi-hub): created deployment in 1.7s`. Root
cause, found by reading `_fetch_hub_registry_with_retry`'s own old
behavior against this timeline: the post-push verification read got back
a valid `200` less than a second after the deployment finished, but
Cloudflare's production-domain routing hadn't caught up to serving that
new deployment yet — so the `200` was real but stale, still showing the
store list from *before* the push. The old retry logic only retried on
exceptions/non-200s, so a stale-but-valid `200` was accepted as final on
the very first attempt, and the retry budget (built for exactly this kind
of propagation lag — see `verify_reachable`'s own comment on 1-2 minute
propagation) never actually got used.

**Fixed in `poslib/provision.py`**, reviewed by an opus-reviewer pass
before shipping (per the global CLAUDE.md's financial/access-config gate —
this touches the hub's shared registry, a write that can wipe every other
store's entry if handled wrong):
- `_fetch_hub_registry_with_retry` gained an optional `until` predicate —
  a fetch that succeeds but whose *content* doesn't satisfy `until` is now
  retried the same as a transient failure, not accepted as final. If every
  attempt's content still fails `until`, the last fetched result is
  returned (not raised) — a successful fetch, just not what the caller
  wanted; the caller decides what a still-missing entry means.
- The **pre-push** read now also retries via `until=lambda r: bool(r.get
  ("stores"))`. Without this, a transient blip on the pre-push read could
  be misread as "the hub file doesn't exist yet" (`fetch_hub_registry`
  maps any 404 to an empty registry, and a 404 isn't an exception this
  function already retried on) — since a Pages deployment always fully
  replaces the prior file set, that misread would have pushed a registry
  containing only the store being provisioned, silently wiping every
  previously-registered store. A genuinely empty hub (or one still empty
  after retrying) falls through unchanged — this only guards against
  mistaking a transient miss for a real one.
- The post-push `until` predicate (`_entry_live`) matches the exact entry
  just written — **name AND url**, not just the store's hostname. Matching
  on hostname alone would be satisfied by a stale pre-push-shaped read too,
  whenever the store already had a hub entry (any re-provision, e.g. a
  rotated `stock_json_token` changing the filename) — since the merge
  updates that entry in place, a hostname-only check can't tell "still
  showing the old url" from "showing the new one," which would have
  defeated the whole fix for the single most common real case
  (re-running provisioning on an already-registered store).
- The failure message (both `register_store_with_hub`'s own raise and
  `provision_store`'s `HUB REGISTRATION FAILED` log/MsgBox note) was
  reworded to match the actually-correct manual recovery: fetch the hub's
  live registry, merge this store's entry into the local hub-site
  registry file, then redeploy with
  `tools/deploy_hub.py` — **never** hand-push a registry containing only
  this one store, since `deploy_hub.py` pushes `hub-site/` verbatim and
  that would drop every other store. The original message's "add it by
  hand" phrasing didn't say this and could have led to exactly that
  mistake if followed literally.

12 new/updated tests in `tests/test_provision.py` cover the retry-on-
content-mismatch behavior, the pre-push empty-registry guard, and the
exact name+url matching (a hostname-only match would have falsely
"verified" a re-provision that actually still showed the old url). Full
suite green: `pytest tests -q --deselect tests/test_export_static.py`,
406 passed. **Not yet re-verified against a real live hub push** — same
standing "confirm against the real thing" discipline as everywhere else
in this file; the next real provisioning run (or hub redeploy) is what
actually confirms this fixes the failure mode seen in the 2026-08-31 log,
this write-up only establishes the fix is logically sound and unit-tested.

## Hub search shows cost, not price (2026-08-27/28) — and why it's an unguessable filename, not a real login

The owner asked for two changes to the hub's cross-store search after his
first live phone test: match on `Item.Reference` (the shop's own product
code, e.g. "Q0130") instead of the internal `ItemNo` (e.g. "AR0002"), and
show cost instead of selling price. The reference swap was simple —
`Metrics.items`/`catalog()` now select `i.Reference AS reference` (with
R.Lynx's own `"."` placeholder for "not set" normalized to null,
`poslib/metrics.py`), and `export_static.py`/`hub-site` use that field
instead of `item_no`.

Cost was a real security decision, not a simple swap — `/stock.json` is
reachable with **no login at all** (Cloudflare Access Bypass policy, see
the Component 5 row above), so putting cost there would make margin data
public to anyone with the URL. The owner's first instinct was "keep
everything private," which surfaced two infeasible options worth recording
so they aren't re-attempted: (1) a real server-checked secret (a header or
Cloudflare Access service token validated per-request) doesn't work
because Cloudflare Pages **Direct Upload** — this project's whole deploy
mechanism, chosen specifically so customer installs don't need Node.js/
wrangler — does not support Pages Functions at all, and browser `fetch()`
can't reliably carry Access service-token headers cross-origin either;
(2) the actually-correct fix (put every store + the hub on subdomains of
one real domain so Cloudflare Access can share one login session across
them, removing the need for any Bypass policy) needs the owner to own a
domain and was deferred, not rejected.

**What shipped instead**: `remote.stock_json_token` (per-store, in
`config.yaml`, blank by default) switches `export_static.py`'s output from
the public `stock.json` (price, never cost) to `stock-<token>.json` (cost,
never price) — a second file whose *name itself* is the only thing gating
it, since nothing server-side actually checks it (Cloudflare Pages static
hosting can't). The token is only known to `hub-site/stores.json`, which
is itself only reachable by whoever can log into the hub (the existing
owner-only Access application) — so the practical exposure is "anyone who
can already log into the hub could extract this URL from the page source
and re-share it," not "public to the internet." The owner explicitly
accepted this exact tradeoff after it was spelled out, rather than having
it silently implemented — see the two `AskUserQuestion` exchanges in this
session's history. **Missing/blank token fails safe**: a store with no
token configured keeps exactly today's behavior (public `stock.json`,
price only, no cost) rather than ever exposing cost on the well-known
filename. This dev PC's real `config.yaml` already has a generated token;
`hub-site/stores.json`'s URL was updated to match. **Both remaining steps
are now done (2026-08-28)**: the live Cloudflare Access application for
`promakeupmihoubipos.pages.dev` (app id `c5d4cdc6-afc4-43bd-984b-fd86454df55d`)
was repointed from the literal `.../stock.json` path to
`.../stock-f1cab0dac3a8e273d6293d71c808c877.json` via the Access
Management API (`domain`, `self_hosted_domains`, and `destinations[].uri`
all updated together — a `PUT` with only `domain` changed fails with
error 12130 "domain not included in destinations"; the endpoint also only
accepts `PUT`, not `PATCH`, which returns 405), and the store was
redeployed (`export_static.export(cfg)` + `poslib.remote.push_remote(cfg)`
run directly, the same manual-redeploy step used twice earlier in this
component, since the watcher only auto-pushes on new till activity). The
one-time Cloudflare API token used for the Access-app edit was pasted in
by the owner for this single use and should be revoked now that it's no
longer needed.

**Follow-up bug, found and fixed same day (2026-08-28)**: the owner
reported the hub's button-to-store-dashboard was fixed (see the
`_redirects` section below) but cross-store search now returned "no
results at all." Root cause: `hub-site/stores.json` was edited locally in
commit `95f79f9` to point at the new `stock-<token>.json` URL, but
**`tools/deploy_hub.py` was never re-run afterward** — the hub project is
pushed manually, separately from the store's own watcher-driven push, and
nothing in this fix's own work actually redeployed it. Once the store's
Access Bypass app was repointed away from the plain `/stock.json` path
(the step right above), that URL started 302-ing to the Cloudflare Access
login page instead of serving JSON — so the *still-live* hub was fetching
a URL that had just become access-gated out from under it. A cross-origin
fetch() to a redirect-to-login response fails in the browser, `allItems`
never populated, and the search box had nothing to filter. This is the
same class of bug as the earlier "stock.json initially 404'd" note above
(committed code that never actually got pushed to the live deployment) —
worth remembering any time `hub-site/` or a store's `remote.*` config
changes: a git commit is not a deploy for either of these two ad hoc
CLI-driven pushes.

Fixed by running `python tools/deploy_hub.py --project
promakeupmihoubi-hub` directly. **Verified without needing the owner's
login**: a Cloudflare Pages Direct Upload deployment's unique per-deploy
subdomain (e.g. `https://5dc8a56e.promakeupmihoubi-hub.pages.dev/`, printed
by the push itself) is NOT covered by the hub's Access application (which
is scoped to the literal `promakeupmihoubi-hub.pages.dev` hostname only),
so it's reachable without logging in — a reusable trick for verifying any
future hub/store deploy from this dev machine without needing the owner's
phone. Confirmed on that URL via `gstack browse`: `stores.json` now serves
the tokenized URL, the page loads with no console errors and "Pro Makeup
Mihoubi: 1599 items", typing "2530" into the search box returns the
matching row with correct cost (35) and box count (14 (+0)), and the
store-link button resolves to `https://promakeupmihoubipos.pages.dev/`
correctly. **Owner-confirmed 2026-08-28**: checked from his own phone
(the production `promakeupmihoubi-hub.pages.dev` URL, behind his real
Access login) — both the store-link button and cross-store search now
work. Both the button/`_redirects` bug and this stale-hub-deploy bug are
closed.

## Wholesale box/"colis" stock view (2026-08-28)

The owner sells wholesale in boxes of 24+ pieces, not individual units, and
asked the tool to show stock that way. `Item.QtyPerParcel` ("Colis" in
R.Lynx's own UI) turned out to already be a well-populated field in the
real database (1,512 of 1,599 items have a real box size; most common
values 24/12/6/36 pcs) — no schema gap like discovery #5/#6 above, just an
unsurfaced column. `Metrics.items` (`poslib/metrics.py`) now derives
`stock_boxes = floor(stock / qty_per_parcel)` and
`stock_remainder = stock - stock_boxes * qty_per_parcel`, only when
`qty_per_parcel > 1` (0/1/NaN means "no box packaging tracked" — both
derived columns stay NaN rather than showing a misleading 1-piece "box").
`catalog()` exposes both; the local Stock catalog page, `stock.json`/
`stock-<token>.json` (both variants — box counts aren't sensitive the way
cost is), and the hub's cross-store search table all show a "Boxes" column
(`24 (+6)` style: full boxes plus any partial remainder). `qty_per_parcel`
itself stays internal-only — never leaks into the exported JSON. Committed
as `8d7f94b`.

## Hub store-link button 404'd on every store's root — real bug in
`poslib/remote.py`, not the hub (2026-08-28)

While shipping the box feature above, the owner reported the hub's
"Pro Makeup Mihoubi" button led to `https://promakeupmihoubipos.pages.dev/`
showing Cloudflare's own custom 404 ("Not available remotely..."). First
fix attempt: `hub-site/app.js`'s `renderStoreLinks` collapsed a store's
`stock.json` URL back to its dashboard root via
`s.url.replace(/\/stock\.json$/, "/")` — a regex that only matched the
plain filename, not the new tokenized `stock-<token>.json` (see the hub
cost/token section above), so for this store the replace was a silent
no-op and the button linked straight at the raw JSON file's path. Fixed to
`/\/stock(-[0-9a-f]+)?\.json$/`, tested, redeployed (`f63e718`) — but the
owner reported the *exact same* symptom afterward, which meant the first
diagnosis was incomplete.

**Real root cause, finally nailed down empirically (not guessed)**:
`poslib/remote.py`'s `_IGNORED_FILE_NAMES` had excluded `_redirects` from
every Cloudflare Pages upload since Component 4 shipped (2026-08-26),
lumped in with `_worker.js`/`_routes.json` on the mistaken assumption all
three are "Pages Functions source, not static assets." A first fix
(`d9a08e0`) just removed `_redirects` from that ignore list so it uploaded
as a normal manifest asset, same as `_headers` — logical, tested, deployed,
and **still didn't work**, because it was solving the wrong half of the
problem. Cloudflare's Direct Upload API treats `_headers` and `_redirects`
asymmetrically and nothing in Cloudflare's own docs says so: `_headers` is
read straight out of the normal uploaded asset set (confirmed — the
`stock.json` CORS header really does work this way), but `_redirects` is
**only** honored when it is excluded from the asset manifest entirely and
sent as its own separate multipart file field on the deployment-create
call (filename `"_redirects"`, content-type `text/plain`) — exactly how
`wrangler pages deploy` does it internally, which is not documented
anywhere in Cloudflare's public API reference. Proved this by scripting
disposable throwaway Cloudflare Pages projects (same pattern Components 4
and 5 used) and testing both forms directly: a manifest-only `_redirects`
deploys with `success: true` and then serves a bare 404 on `/` forever,
while the separate-file-field form returns a real `302`. `_create_deployment`
now takes an optional `redirects_content` string and adds it as
`files["_redirects"] = ("_redirects", content, "text/plain")` only when
present; `_IGNORED_FILE_NAMES` has `_redirects` back in it (correctly, this
time — excluded from the manifest walk, not dropped from the deploy).
Tests updated: `test_ignores_wrangler_reserved_names` now also covers
`_redirects`, and a new `test_sends_redirects_as_separate_deployment_field`
/`test_no_redirects_field_when_no_redirects_file` replace the old
(disproven) `test_uploads_redirects_file`. Full suite green
(`tests/test_remote.py`, 30 passed). Store redeployed with this fix.

**Why the owner's phone check couldn't be skipped even after this**:
Cloudflare Access sits in front of the entire deployment and intercepts
every unauthenticated request — including a plain `curl` from this dev
machine — before Cloudflare Pages' own `_redirects` logic ever runs, so an
unauthenticated `curl` to `/` cannot distinguish "root redirect works" from
"root redirect is still broken" (both come back as a 302 to the Access
login page). What *can* be confirmed from here, and was: (1) the
mechanism itself works, proven against a disposable project with no Access
in front of it at all; (2) the real store's actual generated `_redirects`
file (`/  /fr/today  302` plus one line per language) has the same syntax
that disposable-project test used successfully; (3) the real deployment
went out with this fix. What still can't be confirmed from a dev machine:
whether Access correctly hands the *authenticated* browser back to Pages'
post-Access routing in a way that still applies the redirect — needs the
owner's own phone, logged in, hitting the bare domain root. Two prior
"fixed" claims in this file turned out to be wrong or unverified before
the owner tested; don't repeat that a third time — say what's confirmed
vs. what only the owner's phone can confirm, and wait for that check
before calling this closed.

## Weighted-average cost (AVCO) + last purchase cost (2026-08-28) — built, both figures shown side by side

The owner asked for weighted-average cost back on 2026-08-27 (deferred
until Component 5 shipped, per the sequencing noted throughout this file)
and, once Component 5 was phone-verified, gave the concrete requirement:
**both** the weighted-average cost **and** the last price actually paid
for the product, shown next to each other — not just one. Per the global
CLAUDE.md financial-decision gate (any change to cost/reconciliation
logic needs an Opus subagent plan sanity-check before code is written,
same rule that would have caught discovery #11's fabricated supplier
figure earlier), the design below was reviewed by a dedicated Opus
subagent before any code was written, then independently re-verified
against the real database.

**Two real discoveries about `PurchaseEntry`, neither previously
documented anywhere in this codebase:**

1. **`Item.Cost` is NOT reliably "last purchase cost"** — it looked like
   it should be, but it is also overwritten by `PricingUpdateEntry`
   (manual price edits in the POS UI), not just by purchases. Checked
   against the full population: 18 of 1,567 items with purchase history
   have an `Item.Cost` that diverges from their own last purchase's
   `NewCost`, one cluster of 12 items exactly 2x off via a single bulk
   `PricingUpdateID` 217 operation (2026-06-04). This means "last purchase
   cost" has to be computed fresh from `PurchaseEntry`, never by
   relabeling `Item.Cost` — an early instinct that a small 5-item sample
   check seemed to confirm, until the subagent checked all 1,567 and found
   the exceptions. **Correction, 2026-08-28** (see the section below):
   the direction of that PricingUpdateID 217 story was backwards.
   `PurchaseEntry.NewCost` itself was corrupted (understated ~2x) on
   those 12 items by a separate bug (`OldCost = 0` on a line with
   pre-existing stock — see point 2 below); PricingUpdate 217 was R.Lynx
   *correcting* `Item.Cost` back to the right value, not damaging it. The
   practical conclusion this discovery reached — compute a purchase-based
   cost fresh, never by trusting `Item.Cost` — still holds, it just
   holds for a different reason than originally written down here.
2. **`PurchaseEntry.NewStock` is R.Lynx's own snapshot of the item's total
   stock level immediately *after* that purchase line was applied** — not
   the quantity received in that delivery. This is the key fact that made
   a correct AVCO accumulator cheap to build: `NewStock - Qty` is the
   stock on hand the instant *before* this delivery landed, which already
   nets out every sale, return and stock adjustment (`ItemAdjustment`, a
   real table with more rows than `PurchaseEntry` itself — a naive
   purchases+sales-only interleave would have silently ignored it) that
   happened since the previous purchase. This is why the accumulator
   needs no purchase dates and no sales-table interleaving at all — an
   initial framing of the problem (interleave purchases and sales
   chronologically, blocked by ~34% of purchases having no real date) was
   the wrong approach even with perfect dates, and was dropped in favor of
   this one on the subagent's advice. `PurchaseEntry.ID` (`entry_id`) is a
   **very reliable but not perfect** ordering key *within one item's own
   purchase lines*: cross-checked against `Item.LastPurchasePrice`
   (R.Lynx's own record of the last price paid) on 1,574 items, it agrees
   on 1,570 and disagrees on 4 — good enough to build the accumulator on,
   but not an absolute guarantee, and it is NOT a clean global
   chronological key across the whole table regardless (confirmed:
   `PurchaseID` runs backwards against it at 20 points system-wide) —
   never rely on it beyond per-item ordering. **Corrected, 2026-08-28**:
   `PurchaseEntry.Cost` is NOT `NewCost * Qty` as originally written
   here — that was a wrong guess this file itself shipped with. `Cost` is
   actually `Amount - TotalItemDiscount`, the line's real *net* total
   cost after the supplier's own per-line discount; `Cost / Qty` is the
   correct net per-unit figure. The genuine naming trap is the other way
   around: `NewCost` *looks* like the obvious per-unit cost to use and is
   even a real per-unit figure, but it is R.Lynx's own already-computed
   running weighted-average (fed by the exact same `NewStock - Qty`
   recursion this codebase independently builds) — using it as a raw
   per-delivery input double-averages every purchase. See the correction
   section below for how this was found and fixed.

**What shipped**: `Metrics.item_purchase_costs` (`poslib/metrics.py`), a
new standalone `cached_property` — deliberately not folded into the
existing `items` property, to keep this from touching `stock_value`,
`markup_pct`, `dead_stock`, `stockout_risk`, or anything else already
built on `cost`. It walks each item's own purchase lines ordered by
`entry_id`, computing each line's raw per-unit cost as `Cost / Qty` (net
of the supplier's own discount — never `NewCost`, never gross `Price`;
see the correction section below for why both of those are wrong),
skipping a line with non-positive qty or a missing/non-positive raw cost
for costing purposes (but still resyncing the running quantity to that
line's own `new_stock`, so a later good line isn't compared against a
stale quantity), and produces `avg_cost` (weighted-average, clamped so
the "old" quantity carried forward never exceeds what was actually on
hand or goes negative) and `last_purchase_cost` (the most recent valid
line's own raw `Cost / Qty`) per item. Items never purchased get no
row — blank on the catalog screen, never guessed from `Item.Cost`.
`catalog()` merges this in by `item_id` (left join, so row count and
sort order are unchanged); `cost` (`Item.Cost`) stays exactly as it was,
still the only field every existing diagnostic reads — this is a
display-only addition. Both figures now show as adjacent "Avg cost" /
"Last cost" columns on the Stock catalog screen
(`templates/catalog.html`, replacing the single "Cost" column; new
`catalog.col_avg_cost`/`catalog.col_last_cost` keys in all three
`locales/*.json` files), and in the hub's cross-store search
(`hub-site/index.html`/`app.js`, two columns replacing one, falling back
to `price` in both cells for a store with no `stock_json_token`
configured). In `export_static.py`, both new fields are added **only**
inside the existing `if stock_token:` branch of the stock-record loop —
same discipline as the plain `cost` field (see the "Hub search shows
cost, not price" section above): they can never appear on the public,
unguessable-filename-only `stock.json`, only on `stock-<token>.json`.

**Materiality — told to the owner up front so it doesn't read as
broken**: as of the 2026-08-28 correction below, 1,576 items have a
purchase-cost row; 47 of those show *any* difference between avg cost and
last purchase cost (>0.01 DZD); 26 currently-in-stock items diverge by
more than 1%. Stock valued at `avg_cost` totals ~63,990,732 DZD. In other
words, on roughly 97% of catalog rows the two new columns still show the
identical number — a real, correctly-working feature that mostly looks
like it does nothing, because most products in this catalog have only
ever been bought at one net price. (These figures differ slightly from
the numbers this section first shipped with — 48/23/~64.5M — because the
correction below changed the actual per-line cost the accumulator reads;
re-run the query in that section any time this needs re-checking, don't
treat either set of numbers as permanently frozen.) Verified via the test
suite (`tests/test_metrics.py`'s `TestCatalog` —
`test_last_purchase_cost_matches_last_valid_purchase_entry`,
`test_avg_cost_equals_last_cost_for_single_purchase_items`, and
`test_discounted_single_purchase_uses_net_cost_not_gross_price`, the last
one a regression test specifically guarding against the gross-`Price`
mistake described below ever being reintroduced) plus a new
`TestProductPurchaseHistory` class covering the raw-purchase-lines
transparency view. `pytest tests -q` passing is the re-verification step
to re-run after any future change to `PurchaseEntry` handling, same as
every other rule in this file.

### Correction (2026-08-28) — "last cost" was reading the POS's own running average, not a raw delivery price

The owner caught this himself from the numbers, not from a bug report:
he pointed out that three products he buys often (M308, M377, M2041) got
"last cost" figures with lots of decimal digits, when in his own real
purchasing experience their cost is always a round number (rounded to the
nearest 5 DZD, "only in extreme rare cases" not). His read was exactly
right: **`item_purchase_costs` had been reading `PurchaseEntry.NewCost`
as if it were "the raw price paid on the last delivery," but `NewCost` is
R.Lynx's own already-blended running weighted-average cost** — the same
number this codebase's own accumulator independently recomputes. Treating
it as a raw per-delivery input double-averages every purchase, which is
exactly the kind of drift that produces non-round numbers on an item
whose real invoiced price never actually changes.

Per the global CLAUDE.md financial-decision gate (this counts as
"reconciliation logic... a number already shipped," so the gate applied
even though the shipped number was only hours old), an Opus subagent was
dispatched to investigate `PurchaseEntry`'s actual columns before any fix
was written — and it caught a second problem my own first hypothesis
would have missed. My first instinct was to switch from `NewCost` to
`PurchaseEntry.Price` (which does look round, and matches the user's
expectation on M308/M377/M2041 specifically, since none of those three
happen to carry a supplier discount). The subagent's deeper read of the
table found `Price` is the **gross list price before the supplier's own
per-line discount** — wrong on the ~13% of lines that do carry one
(`TotalItemDiscount != 0` on 335 of 2,615 lines, across 284 of 1,576
items). The actually-correct raw per-unit input is
`PurchaseEntry.Cost / Qty`, where `Cost` is `Amount - TotalItemDiscount`
(net of that discount) — confirmed to reproduce R.Lynx's own `NewCost`
(anchored against the also-newly-found `OldCost` column) on 2,606 of
2,606 valid lines, a perfect match, versus 2,271/2,606 for `Price` alone.
Fixed in `poslib/metrics.py`'s `item_purchase_costs` accordingly (see the
"What shipped" description above, now current); the two pre-existing
tests that referenced `new_cost` were updated to re-derive from
`cost/qty` instead, and a new regression test
(`test_discounted_single_purchase_uses_net_cost_not_gross_price`) locks
in the discount case specifically so the gross-`Price` mistake can't
silently come back. This is also where the `OldCost = 0` finding in
discovery #1 above came from, and where the `entry_id`-ordering
guarantee in discovery #2 above got softened (checked against
`Item.LastPurchasePrice` for the first time).

The owner also asked, separately and explicitly, for a way to check any
of this himself rather than trust a number he can't see behind: "because
i can't see what is inside the database my self i can't judge or review
the number you are providing." The product drill-down page
(`templates/product_detail.html`) gained a "Purchase history" table
showing each item's raw `PurchaseEntry` lines — date, supplier, quantity,
the gross price paid, R.Lynx's own running cost, and resulting stock —
completely unaltered, no calculation applied, sourced from a new
`purchase_history` key on `Metrics.product_profile()`. The Stock catalog
page's reference column and item name are now both links into this page
(`templates/catalog.html`), so the owner can search for a product (by the
same reference code he already uses, e.g. "308") and click straight
through to its own purchase history to check any figure by eye.

**Deployed and dev-machine-verified 2026-08-28**: the store's own
export+push (`export_static.export(cfg)` + `poslib.remote.push_remote(cfg)`,
run directly since the watcher only auto-pushes on new till activity) and
the hub's redeploy (`python tools/deploy_hub.py --project
promakeupmihoubi-hub`) both succeeded. Confirmed via the same
disposable-per-deployment-subdomain trick as the earlier hub-button fix
(`https://e786f12d.promakeupmihoubi-hub.pages.dev/`, printed by the deploy
itself, not covered by the hub's Access application so it's reachable
without logging in): the results table header shows "Avg cost"/"Last
cost" as two separate columns, no console errors, and a divergent-cost
item (`DB786`) renders two distinct real numbers (2777.82 vs. 2725.53)
matching the local export exactly — not a display bug collapsing both
columns to the same value. The store-link button was also re-checked and
still correctly targets `https://promakeupmihoubipos.pages.dev/` (`target=
"_blank"`), confirming the earlier `_redirects`/button fix is undisturbed.
One cosmetic-only hiccup during the hub redeploy: `tools/deploy_hub.py`
logged a `PermissionError` from Python's log-rotation handler
*after* printing its own success line, because the always-running local
watcher had `logs/pos-tool.log` open for writing at the same moment the
script tried to roll it over — the push itself completed and returned
success before this happened; if this collision recurs it may be worth
a defensive fix (e.g. delay-rotate or a separate log file per script), but
it isn't blocking anything. **Only the owner's own phone confirmation is
still outstanding** — same "a fixed/built claim only really lands once he
confirms it himself" pattern as every other feature in this file; not
blocking calling the implementation itself done.

The local Stock catalog page (`http://127.0.0.1:8777/catalog`) was also
checked, and initially 500'd (`jinja2.exceptions.UndefinedError: 'dict
object' has no attribute 'stock_boxes'`) — not a code bug: this dev PC had
**four** stray `pythonw.exe` processes left over from past sessions (two
duplicate watcher+dashboard pairs, one set from `.venv\Scripts\pythonw.exe`
and one from the global `Python312\pythonw.exe` on PATH — only one of the
two app.py processes actually held port 8777), all started 2026-08-26,
before today's `stock_boxes`/AVCO columns existed in `catalog()`. A process
that imports `poslib/metrics.py` once at startup never sees later edits to
that file — restarting is required, same as any long-running Python
service. Verified this was the only issue by rendering `/catalog` through
Flask's test client in a **fresh** process (no port conflict, no need to
touch the stale ones): 200 OK, "Avg cost"/"Dernier coût" columns present,
and `DB786` again showing the correct two distinct values. Actually
killing/restarting the stale port-8777 processes was blocked by this
session's own permission classifier (`taskkill` denied as a risky action)
and the owner was asleep to ask — so the live dashboard at :8777 itself
was **not** restarted this session and will keep 500ing on `/catalog`
until it is. Next time anyone is at this PC: restart it by killing the
`pythonw.exe` processes (`watcher.py` and `app.py --no-browser` — check
`tasklist`/`Get-CimInstance Win32_Process` for the `.venv` vs global-Python
duplicates and clear all four) and re-running `start-quiet.bat`, or just
rebooting.

## Remote product/customer drill-downs now exported in full (2026-08-28) — reversed an earlier deliberate exclusion

The owner clicked a product from the Stock catalog on the remote (phone)
dashboard and hit the static export's own "not available remotely" 404
page. That page's existence was intentional — the original remote-parity
design (see discovery #10 above) explicitly excluded `/products/<id>` and
`/customers/<id>` from the static export, reasoning "no natural recency
cutoff for a customer or product," unlike a ticket. First response was a
narrow, defensible fix: gate the product/customer links in `catalog.html`,
`products.html`, `receivables.html`, `customers.html` behind
`is_static_export` so they render as plain text (not dead links) when
viewed remotely. That shipped, tested, and worked as designed.

The owner's immediate follow-up made the actual ask explicit: **"why not
include this remotely? I want the full view of the local dashboard on my
phone remotely!!"** — full feature parity, not a hidden link. Revisiting
the original exclusion rationale found it was wrong on its own terms: it
conflated *unbounded* growth (tickets, which accumulate forever) with
*catalog/roster-sized* growth (products, customers) — the real counts are
1,599 products and 671 customers (walk-in excluded), the same order of
magnitude as purchases (~500-600), which the export already ships in full
with no windowing. There was never a real reason to treat products/
customers differently from purchases.

**What shipped**: the four templates' links were reverted back to plain,
unconditional `<a href>`s (no `is_static_export` gate — full parity means
they just work). `export_static.py` gained two more full-population export
loops, following the exact same `app.test_request_context()` +
`render_template()` pattern already proven for tickets/purchases (shared
`Metrics` instance, no per-page Flask request overhead): one over every
`catalog()` item (`item_ids`, inactive items included — a product's history
doesn't stop mattering because it's no longer sold), one over every real
customer (`customer_ids`, excluding `Metrics.walkin_id` — the anonymous
till account has no profile, `customer_profile()` returns `None` for it,
same as it always has locally). Output lands at
`<lang>/products/<item_id>.html` and `<lang>/customers/<customer_id>.html`.

**Real numbers, verified against the live database**: 1,599 products +
671 customers × 3 languages = 6,810 new files, on top of the existing
~6,457 → **~13,267 files total**. Checked against Cloudflare Pages' actual
published limit (fetched from `developers.cloudflare.com/pages/platform/
limits/`, not assumed from memory): the Free plan allows **20,000 files
per deployment** — the new total is about 66% of that ceiling. Real
margin, not a razor's edge, but also not huge if the catalog/customer
roster keeps growing — worth re-checking this file count again if either
count roughly doubles.

**Real export time cost**: `pytest tests/test_export_static.py -q`
(17 of its 19 tests each call `export_static.export(cfg)` fresh against
the real database) took 3,962s total, or **~3.9 minutes per single
export**, up from the ~40 seconds the tickets/purchases-only version took
(see discovery #10's perf note). No `RuntimeError` ("vanished mid-export")
fired for any real item or customer ID — all 1,599 products and 671
customers rendered cleanly. This cost lands on the watcher's own
background push (`watcher.py`'s `_run_remote_push`, triggered only when
the ETL detects new till activity, on a 90-second-minimum interval) — it
runs synchronously with no timeout, so a ~4-minute export just delays that
one push cycle, not anything user-facing on the local dashboard. Acceptable
tradeoff, not silently absorbed: worth knowing if a future change makes
export time matter more (e.g. an even lower push interval).

Full `pytest tests -q` suite re-run and store redeployed after this change
(`export_static.export(cfg)` + `poslib.remote.push_remote(cfg)`, run
directly — the same manual-redeploy step used repeatedly throughout this
file, since the watcher only auto-pushes on new till activity). Hub
redeploy not needed — this change doesn't touch `hub-site/` or
`stock.json`.

**Verified — and the disposable-subdomain trick is now broken, don't rely
on it without re-checking.** The established "check a Cloudflare Pages
deployment via its own unguarded per-deploy subdomain" trick (used
repeatedly earlier in this file) no longer works: `curl` with no
cookies/JS to `https://baf18b7c.promakeupmihoubipos.pages.dev/en/catalog`
got a 302 straight to the Cloudflare Access login, same as the production
hostname. Most likely cause: the Access application's
`self_hosted_domains` got broadened (to a wildcard covering
`*.promakeupmihoubipos.pages.dev`) during the stock-token repoint work
earlier on 2026-08-28 (see "Hub search shows cost, not price" above,
where `domain`/`self_hosted_domains`/`destinations[].uri` were all
updated together) — plausible but not confirmed by inspecting the Access
app config directly. **Don't assume any future per-deploy subdomain is
unguarded** — check with a bare `curl` first, the same way this was
caught.

Verification was done directly against the exported static files on disk
instead (no Cloudflare, no login needed): file counts matched exactly
(1,599 files in `remote-site/en/products/`, 671 in
`remote-site/en/customers/`); `catalog.html` contains exactly 1,599
`<a href="/en/products/...">` links; sampled pages
(`products/1.html`, `products/595.html` = item DB786,
`customers/10.html`) had no `Traceback`/`Undefined`/`werkzeug` error
markers, real headings (`Article divers`, an Arabic customer name), and
DB786's page correctly showed its known divergent purchase cost
(`2725.53`) under a real "Purchase history" section. This confirms the
exported HTML itself is correct. **What it does not confirm**: whether
Cloudflare Access, once authenticated, correctly serves these same files
on the real gated domain — that still needs the owner's own phone,
logged in, per this file's established "don't declare a deploy fully
verified until the owner's phone confirms it" practice (see the
`_redirects` bug history above, which burned this exact shortcut twice).

**Owner-confirmed on his own phone, 2026-08-28: both product and customer
drill-down pages work.** This closes out the feature — full remote parity
for product/customer detail pages is done, deployed, and verified both
mechanically (file-level checks above) and by the owner directly.

### The actual product goal (owner's own words, 2026-08-26) — read this before prioritizing anything

The owner does not want a local dashboard. He already has R.Lynx's own POS
screen at the till for local use — **the entire reason this tool exists is
so he (or each store's owner) can check that store's numbers on his phone
when he isn't physically at the shop.** This reframes priority for
everything not yet built:

1. **Install must need zero technical steps** — no terminal, no typed
   config, ideally not even the DB path. **Built and verified**
   (Component 2, 2026-08-26): the installer's wizard page auto-detects a
   `.dblx` file in common R.Lynx locations or lets the owner click Browse
   for a native file picker, then writes it into `config.yaml` itself —
   see the component table above. The auto-detect/Browse page itself
   wasn't click-through-exercised on this dev PC (it already had a
   configured `config.yaml`, so the page was correctly skipped) — a small
   residual gap, not a known bug.
2. **The background watcher must start itself, silently, on its own, the
   moment the store PC is turned on — invisible to the till workers.**
   This is the one piece the approved spec assumed rather than designed:
   Component 3 ("Silent, automatic updates") talks about the watcher
   "already running continuously via the existing Task Scheduler entries"
   as a given, but nothing in `packaging/setup.iss` used to create that
   entry — today it only existed on this dev PC because `install-startup.bat`
   was run by hand. **Built** (Component 2 above,
   `docs/superpowers/plans/2026-08-26-db-autodetect-watcher-autostart.md`):
   `packaging/setup.iss` now bakes in the exact same
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

Component 2 is now fully done, code and interactive verification both.
Component 3 (auto-update) is now fully done too — both known gaps fixed
and the whole mechanism verified end-to-end with a real installer on real
hardware (2026-08-27, see the component table above and
`docs/superpowers/specs/2026-08-27-update-elevation-fix.md`). `update.enabled`
can reasonably be flipped on for a real store now; the one open item (the
already-up-to-date no-op path against a real second release) can be
checked the first time a real update is actually shipped, not before. Read
the component table before touching `poslib/updater.py`,
`packaging/setup.iss`, or `main.py`'s `--apply-update` dispatch. **Component
5 is now fully done** — both halves: the hub (live at
`https://promakeupmihoubi-hub.pages.dev/`, phone-verified 2026-08-28 per
`docs/superpowers/plans/2026-08-27-component5-hub-page.md`) and the
installer-driven Cloudflare provisioning flow (verified end-to-end on a
real till PC with real UAC elevation, 2026-08-29, see "Component 5
installer provisioning — SDD progress" above). **All 5 components of the
customer-distribution build are now done.** Weighted-average cost (the
feature the owner explicitly sequenced after Component 5) is also already
built — see "Weighted-average cost (AVCO) + last purchase cost" below.

## What's left (optional, not blocking)

- **DONE 2026-08-31 — adding a newly provisioned store to the cross-store
  hub is now automatic**, no longer a manual step. See "Cross-store hub
  auto-registration + installer reliability fixes" above for the full
  design, the live cutover, and two real installer bugs (Updater
  scheduled task, a v1.0.5 version-mismatch) found and fixed the same
  session. Current recommended build: `v1.0.7`.
- **Fix implemented and unit-tested 2026-08-31 (a later, separate
  autonomous session), NOT yet live-verified** — see "Full-export push
  reliability fix" below for the full detail and, critically, a bigger
  unrelated finding from the same session: this machine currently has
  **no packaged install at all**, so `promakeupboumati.pages.dev` has no
  live content source right now regardless of this fix.
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
