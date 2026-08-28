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
| 1 | Packaging: PyInstaller onedir + Inno Setup `Setup.exe` | **DONE** | `docs/superpowers/plans/2026-08-25-packaging-installer.md` (status banner + SDD ledger at `.superpowers/sdd/2026-08-25-packaging-installer/progress.md`). One carried-forward item remains: the `console=False` crash-visibility gap was accepted as-is by the user, candidate to revisit alongside Component 3. (The leftover test install at `C:\Program Files\Shop Analysis\` was manually removed 2026-08-26.) |
| 2 | DB auto-detect wizard page + silent watcher auto-start (`schtasks /sc onlogon`) | **DONE, interactively verified 2026-08-26** | `docs/superpowers/plans/2026-08-26-db-autodetect-watcher-autostart.md` (status banner has the full detail). Both tasks committed (`eb22bfb`..`10deb48`, 5 commits incl. two real fixes: placeholder-config detection, admin/user-account mismatch warning). Real install on this dev PC confirmed: no-overwrite guard, correct scheduled-task target/user/trigger, headless watcher run (no window, correct DB file watched, digest job ran), clean `/VERYSILENT` uninstall (task + folder both removed). One residual gap: the DB wizard page's own auto-detect/Browse flow wasn't click-through-exercised (this dev PC already had a configured `config.yaml` so the page was skipped by design) — the underlying code did go through a review + fix cycle already. |
| 3 | Silent auto-update via GitHub Releases | **CODE-COMPLETE; elevation gap fixed 2026-08-27, still NOT customer-rollout-ready** | `docs/superpowers/plans/2026-08-26-component3-auto-update.md` for the original build; `docs/superpowers/specs/2026-08-27-update-elevation-fix.md` for the elevation fix. Detect/download/checksum-verify/reject all verified 2026-08-27 against a real, now-deleted throwaway `v1.0.1` GitHub release. **Known blocking gap #1 — FIXED 2026-08-27**: the watcher's scheduled task stays de-elevated (`setup.iss`, unchanged, per commit `10deb48`) and no longer calls `check_and_apply_update()` at all. A second scheduled task, "Shop Analysis - Updater" (`packaging/setup.iss`), created at install time running as `SYSTEM`/`/rl highest`/`/sc onlogon`, owns the whole check→download→verify→install flow instead — SYSTEM tasks never hit an interactive UAC prompt (same mechanism as Windows' own built-in `SilentCleanup` task), so no stored credentials and no dialog nobody is there to click. It runs `ShopAnalysis.exe --apply-update --data-dir "<installing user's %LOCALAPPDATA%\Shop Analysis>"` (new `main.py` dispatch + new `SHOP_ANALYSIS_DATA_DIR` override in `poslib/paths.py::user_data_dir()`), because SYSTEM's own `%LOCALAPPDATA%` is not the shop's — solved the same way `setup.iss`'s existing `WriteDatabaseConfig` already captures `{localappdata}` at install time, not by having the elevated process guess. The two cheap fixes previously ruled out (per-user install; a `/CURRENTUSER` override) stay ruled out; this is the "second always-elevated helper task" option the table used to describe as not-yet-built. `check_and_apply_update()`/`check_for_update()`/etc. in `poslib/updater.py` are unchanged — only which process, running as what account, calls them changed. `update.enabled` stays defaulted to `false` in both `config.template.yaml` and `poslib/updater.py`'s own fail-safe default — this fix removes the *reason* it was off, but flipping it on for a real store still needs the last item below, not done as part of this fix. **Known gap #2 — FIXED 2026-08-27**: `poslib/updater.py` now writes a small marker (`update_attempted.txt` in `user_data_dir()`, holding the release tag) right after successfully launching an installer; `check_for_update()` refuses to retry the *same* tag again (logs an error instead - "publish a corrected release to resume") so a genuinely mis-cut release (bundled `VERSION` not actually bumped) can no longer loop download→install→relaunch forever - a launch that fails outright is *not* marked attempted, so it still retries normally next login. **Fully real-machine-verified 2026-08-27, both the mechanism and the real installer** (see `docs/superpowers/specs/2026-08-27-update-elevation-fix.md` for the full log of both passes): first, a dev-Python substitute test (`SYSTEM`/`/rl highest` task running `main.py --apply-update --data-dir <repo>`) confirmed **zero UAC prompt** and exit code `0`. Then Inno Setup was installed (`winget install --id JRSoftware.InnoSetup`) and the *actual* `packaging/pos-tool.spec` + `packaging/setup.iss` were built into a real `Setup.exe` and installed on this machine: `schtasks /query` confirmed both tasks created exactly as designed (`"Shop Analysis - Watcher"` as the installing user, `"Shop Analysis - Updater"` as `Système` with `--data-dir` correctly baked to the real `%LOCALAPPDATA%`); `schtasks /run` on the Updater task showed no prompt and the install's own log recorded `poslib.updater  Auto-update disabled via config - skipping check.` at INFO level — direct proof the real frozen build ran elevated end-to-end. Full manual cleanup afterward (process killed, Program Files/`%LOCALAPPDATA%\Shop Analysis`/build output all removed, both tasks confirmed gone); the real git-clone watcher kept pushing to Cloudflare throughout, completely undisturbed. **One minor finding from this pass, fixed and re-verified same session**: the uninstaller initially couldn't remove every file in one pass because the install's own `--watcher` process (started unconditionally by `setup.iss`'s `[Run]` section, even on a silent install) was still holding some files open — `CloseApplications=force` covers the *installer*'s file-copy phase but not the *uninstaller*'s file-removal phase. Added a `taskkill /F /IM ShopAnalysis.exe` `[UninstallRun]` entry ordered before file removal; reproduced the exact failure and re-ran the uninstall — process killed automatically, `Program Files` fully removed in one pass, both tasks confirmed gone. **Still open**: exercising the already-up-to-date no-op path against a real newer release (needs an actual second GitHub release to publish and compare against) — low priority, can be checked the first time a real update actually ships. |
| 4 | Cloudflare Pages push over direct REST API (no `wrangler`/Node.js) | **DONE, committed, phone-verified** | `poslib/remote.py`, commit `5df4d73` (2026-08-26), superseding the scoped-token approach in `docs/superpowers/plans/2026-08-25-cloudflare-token-auth.md` (see that file's status banner). Verified by pushing to a disposable throwaway Cloudflare Pages project (created and deleted via the API — the real store project `promakeupmihoubipos` was never touched) and confirming it loaded correctly from an actual phone, not just a "success" API response. 25 unit tests passing (`tests/test_remote.py`). |
| 5 | Multi-store hub page + cross-store stock search | **Hub is LIVE at `promakeupmihoubi-hub.pages.dev` and phone-verified working 2026-08-27/28 (reference-based search confirmed by the owner). Cost-instead-of-price + reference-field fix is CODE-COMPLETE (2026-08-28, see "Hub search shows cost, not price" section below) but NOT YET LIVE — the Cloudflare Access app for `stock.json` still needs repointing to the new `stock-<token>.json` path and the store needs redeploying before the owner's phone actually sees cost. Installer-driven provisioning NOT built, blocked on one unverified step** | `docs/superpowers/specs/2026-08-27-component5-hub-design.md` for the design; `docs/superpowers/plans/2026-08-27-component5-hub-page.md` for the build and live-deploy log (all of Tasks 1-4 done; Task 4's Step 5, a real-phone check, is the one thing left — see that plan file). Corrects a real gap found in the original master spec (a Cloudflare Pages deployment fully replaces a project's site, so 3 stores can't share one hub project as a write target the way the master spec assumed) and a hard blocker (Access login sessions can't cross `*.pages.dev` origins — cookie scoping, not a Cloudflare quirk). Settled design: each store keeps pushing only its own project; `export_static.py` now emits one extra file, `stock.json` (item code, name, quantity, price — price inclusion was the owner's explicit call, a real widening from "low-sensitivity" but accepted), excluding inactive items. Making that one path reachable without login needs **two** Access applications per store, not one edited application with a path-scoped policy — Cloudflare has no such thing; a policy can't be scoped to a path within an app. The fix: a second, narrower Access application scoped to just `.../stock.json` with a Bypass policy, alongside the existing broad owner-only application — Cloudflare evaluates the most specific matching application first. **Empirically verified twice now**: first against a disposable throwaway project (2026-08-27, deleted afterward, same precedent Component 4 held itself to), then for real against the live `promakeupmihoubipos` project and a brand-new live `promakeupmihoubi-hub` project (2026-08-27) — `GET https://promakeupmihoubipos.pages.dev/` → 302 to Access login (broad app untouched); `GET .../stock.json` → 200 with real item rows, no redirect; `GET https://promakeupmihoubi-hub.pages.dev/` → 302 to Access login (new owner-only app on the hub). Built and pushed 2026-08-27, with two real corrections found while going live (both logged in the plan file): (1) the live store's `stock.json` initially 404'd even with the Bypass app working, because this dev PC's watcher only re-pushes when the ETL detects new source rows — the `export_static.py` code adding `stock.json` had landed on `main` with no new till activity since, so nothing had re-exported it; fixed by running `export(cfg)` + `push_remote(cfg)` directly once, bypassing that gate. (2) Cloudflare's Direct Upload API does **not** auto-create a project on first push (contrary to this component's own original assumption) — `POST /accounts/{id}/pages/projects` has to be called explicitly first; `tools/deploy_hub.py` does not yet have this fallback built in (a one-off manual step for now, not blocking). All three live Access applications (`promakeupmihoubipos.pages.dev` broad, `promakeupmihoubipos.pages.dev/stock.json` bypass, `promakeupmihoubi-hub.pages.dev` broad) created with a temporary Cloudflare token (`Pages:Edit` + `Access: Apps and Policies:Edit`) the user pasted in for one-time use, held in-memory only, never written to disk — user was asked to revoke it once done, same disposable-credential pattern as Component 4 and the original verification pass. **Built 2026-08-27**: `hub-site/` (static switcher + client-side cross-store search page, `Promise.allSettled`-based so one unreachable store doesn't hide the rest), `poslib/remote.py::push_remote` gained optional `project`/`export_dir` overrides (backward compatible — every existing watcher call is unaffected) so it can push a directory that isn't a store's own `config.yaml`-configured export, and `tools/deploy_hub.py`, a one-off manual CLI reusing that override to push `hub-site/` to its own Cloudflare Pages project. 34 new/updated unit tests passing (`tests/test_remote.py`, `tests/test_hub_site.py`, `tests/test_deploy_hub.py`); full suite (`pytest tests -q`) green at 310 passed, 1 skipped. Owner's explicit goal for the *other* half of this component: **the installer should provision new stores' Cloudflare setup automatically**, no manual Zero Trust dashboard clicking — resolved as a one-time-use powerful Cloudflare token (permission group "Access: Apps and Policies", Edit — confirmed distinct from `Pages:Edit`), pasted in only during a *new* store's interactive install (never persisted), that creates the project + both Access applications via Cloudflare's Access Management API and then mints the narrow ongoing `Pages:Edit`-only token for the watcher's permanent use. **This is genuinely blocked, not just "not built yet"**: the last step (minting that narrow token programmatically) is Cloudflare's *user*-scoped `POST /user/tokens` endpoint, never called from this codebase and unconfirmed whether a token can even be granted permission to create another token — if it can't, "the installer does everything automatically" degrades to "rachad also pastes the narrow token in by hand," a real design change for the owner to decide, not something to assume. Verify against a real disposable token before writing that task's code. No auto-matching across stores, no combined totals — unchanged from the master spec, V1 shows matching rows side by side. |

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
`hub-site/stores.json`'s URL was updated to match — **but the live
Cloudflare Access application for `promakeupmihoubipos.pages.dev`, still
scoped to the literal path `.../stock.json`, has NOT been updated to the
new `.../stock-<token>.json` path yet**, and the live site has not been
redeployed with this change. Both need a one-time Cloudflare Access API
token (`Access: Apps and Policies:Edit`, same disposable-credential
pattern as the rest of Component 5) or a manual dashboard edit before this
reaches the real hub — see the Component 5 row in the table above.

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
`packaging/setup.iss`, or `main.py`'s `--apply-update` dispatch. Component
5's hub is now live at `https://promakeupmihoubi-hub.pages.dev/`, deployed
and mechanically verified per
`docs/superpowers/plans/2026-08-27-component5-hub-page.md` (2026-08-27) —
only a real-phone click-through (that plan's Task 4 Step 5) is left, not
something verifiable from a dev session. Next build-order step after that:
the installer-driven provisioning flow (build-order step 4 in the design
spec), currently blocked on verifying whether a Cloudflare token can mint
another token programmatically — see the component table's Component 5
row. The weighted-average-cost feature below stays after all of Component
5, per the owner's own explicit sequencing.

## What's left (optional, not blocking)

- **Weighted-average cost, requested by the owner 2026-08-27, deferred
  until Component 5 is done.** Today `cost` (in `Metrics.items`/`catalog()`
  and everything built on it - `stock_value`, `markup_pct`, `dead_stock`,
  `stockout_risk`, etc.) is read straight from R.Lynx's own `Item.Cost`
  field, a single point-in-time value. The owner wants a real weighted-
  average cost instead: if stock was bought at a high price, half sold,
  then more bought at a lower price, the tool should track the average
  cost of the *remaining* stock (standard AVCO inventory costing - a
  purchase moves the average, a sale doesn't), surfaced on both the Stock
  catalog screen and in Component 5's `stock.json`/hub view. Full
  reasoning and the open design questions (where in `metrics.py`, how a
  missing/zero purchase-line unit cost is handled, whether to replace or
  sit alongside the existing `cost` column, interaction with every metric
  already built on that column) are in
  `docs/superpowers/specs/2026-08-27-component5-hub-design.md`'s "Deferred
  to after this component" section. Not started - explicitly sequenced by
  the owner to come after Component 5, not before.
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
