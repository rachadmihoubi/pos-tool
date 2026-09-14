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

## Every real bug gets an opus-reviewer root-cause pass and ships as a new setup.exe release

**Hard rule, added 2026-09-05 after the store #1 watcher-outage/timeout-bug
session.** Whenever something in the tool actually breaks for a real
store (not a test failure, not a hypothetical - a real bug hit in
practice, the way the connect-timeout truncation bug and the
silently-dying watcher both were), two things are now mandatory before
considering it closed, not optional extras:

1. **Dispatch the opus-reviewer subagent to review the root cause and the
   proposed fix** - even when the root cause already seems obvious or has
   already been diagnosed by hand (e.g. via `systematic-debugging`, a
   live diagnostic script, or another subagent). The review's job is to
   check that the diagnosis is actually correct (not just plausible) and
   that the fix addresses the real cause rather than papering over a
   symptom - the same bar this file already holds financial-logic and
   installer/Access-config changes to, now generalized to every real bug.
2. **The fix must ship as a new `Setup.exe` published to GitHub
   Releases** - a commit sitting on `main` (or any branch) is not enough
   on its own. A store PC only ever gets a fix via
   `poslib/updater.py`'s auto-update mechanism pulling a new release (see
   the Component 3 row in the checklist below), so a real bug isn't
   actually fixed for a real store until: `VERSION` is bumped (bump it
   *before* running PyInstaller - see the `v1.0.5` build-order gotcha
   later in this file), a new build is produced from
   `packaging/pos-tool.spec`/`packaging/setup.iss`, and that installer is
   published as a GitHub Release. Skipping this step leaves every
   already-installed store exactly as broken as before, no matter how
   correct the code change is.

Why this is a hard rule and not a judgment call: this session's own
timeout-bug fix is the exact case this rule exists to prevent from
repeating - it was correctly root-caused and correctly fixed in
`poslib/remote.py`, but as of that fix landing on `main` it had not yet
been reviewed by an opus-reviewer pass or cut into a release, so store
#1's real watcher would still have shipped and run the old, broken
timeout value indefinitely even after the "fix" was "done." Treat a bug
as open - regardless of what CLAUDE.md or an SDD ledger says elsewhere -
until both of these have actually happened, not just the code change.

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

## Data discoveries — do not "fix" these back

Condensed; full reasoning, numbers and verification in `docs/history/data-discoveries.md`.

1. **Boolean columns: a SET bit means TRUE** (`poslib/jet4.py` `TYPE_BOOL`). The opposite reading is nonsensical.
2. **`Item.LastSold` is stale on ~60 products** — `item_movement` uses the latest *ticket* date instead.
3. **Purchase totals reconcile** once supplier payment lines (`ItemID=-2`) are excluded — RESOLVED by #12, not a quirk.
4. **`Batch.*Shift` columns don't track live sales** — `till_reconciliation()` recomputes from `Receipt.BatchID` tickets.
5. **No per-product stocktake detail in store #1's live DB** — `shrinkage_events()` is event-level only (`BlankDB` has `StockTakeEntry`; check per store).
6. **Expiry dates don't persist anywhere** — feature intentionally dropped.
7. **`Item.Picture` (OLE) is skipped by the ETL cache** — `poslib/photos.py` reads it on demand.
9. **Tender reconciliation is clean** (98.96% of tickets) — cash-realized split is trustworthy.
10. **Remote viewing = static export, no live tunnel** — reuse one shared `Metrics` instance (per-page rebuild took ~19 min).
11. **`Supplier.TotalPurchased` is a count, not DZD** — never derive "amount paid" from it.
12. **Supplier payments are `PurchaseEntry` lines with `ItemID=-2`** ("Paiement de règlement") — `purchases()` splits `is_purchase`/`is_payment`; no per-supplier breakdown exists.
13. **"DV" tickets (`ReceiptType==1`) are devis, not sales** — excluded via `is_devis`/`completed_tickets` (Rule 6).
14. **An account payment is real till cash** — Today/Tickets show `cash_in` = cash revenue + collections; Rule 1 stays accrual.

- `Receipt.TotalCost` is zero on DV tickets — COGS always from `ReceiptEntry` lines, never the header.
- **Purchase cost**: `Item.Cost` is overwritten by price edits and `PurchaseEntry.NewCost` is R.Lynx's running average — raw per-unit cost is `Cost / Qty` (net of discount). See `docs/history/avco-cost.md`.

## Release build-order gotcha

**Always bump `VERSION` (and `setup.iss` `AppVersion`) before running PyInstaller, and verify `dist/ShopAnalysis/VERSION` before compiling the installer.** v1.0.5 shipped with a stale bundled VERSION because of this (docs/history/installer-and-push-reliability-2026-08.md).

## History index — moved verbatim out of this file on 2026-09-14

- see docs/history/data-discoveries.md when touching jet4 parsing or metrics rules (supplier/purchase/devis/cash)
- see docs/history/deployment-and-distribution.md when touching frozen test floors, deployment state, or the 5-component distribution checklist
- see docs/history/component5-provisioning.md when touching `poslib/provision.py` or the setup.iss Cloudflare wizard page
- see docs/history/installer-and-push-reliability-2026-08.md when touching provisioning timeouts, hub auto-registration, `remote.py` upload batching, or the Updater task
- see docs/history/hub-and-catalog-features.md when touching `hub-site/`, `stock-<token>.json`, the box/colis view, or `_redirects` deploys
- see docs/history/avco-cost.md when touching `item_purchase_costs`, `PurchaseEntry` cost columns, or the purchase-history view
- see docs/history/remote-drilldowns.md when touching `export_static.py` product/customer export or Pages file-count limits
- see docs/history/product-goal.md when prioritizing any new work (owner's remote-first goal)
- see docs/history/store1-reliability-bugs.md when touching `watcher.py`'s loop/watchdog, `updater.py`, setup.iss silent-mode/MsgBox, or `remote.py` timeouts
- see docs/history/rlynx-forensics.md when considering cloning/replacing R.Lynx or reading BlankDB/DBConnect/POSParameter
- see docs/history/json-replatform.md when touching `products.json`/`customers.json` or the product/customer HTML shells

## Model routing
- Default: Sonnet main session + Opus advisor. Use for bugs, diagnosis, live till-PC investigation, small features.
- Written plan with 3+ independent tasks: run `/model opus`, then superpowers:subagent-driven-development. Dispatch each task to the `implementer` agent (pinned Sonnet), one per task, batching tiny related tasks. Read only the summaries. `/model sonnet` when the plan is done.
- opus-reviewer gate unchanged (money, installer, access, schema, every real prod bug) — it reads files, the advisor does not, so never substitute the advisor for it.
- Lookups: Explore (Haiku). `/clear` between unrelated tasks.

## What's left — audited fresh 2026-09-14, checked against the real running store, not just old notes

Everything below was verified directly this session (real config files,
real services, real log/data directories on store #1's actual till PC),
not assumed from earlier write-ups elsewhere in this file — several of
which had gone stale (e.g. an old note here claiming "no packaged install
at all" — there now is one, real, running, on `v1.0.17`).

**Real, currently-open items:**

- **The daily digest has no remote delivery channel enabled on the real
  store.** Checked directly: `email.enabled`, `telegram.enabled`, and
  `whatsapp.enabled` are all `false` in the real install's own
  `config.yaml`, and every credential in its `.env`
  (`SMTP_PASSWORD`/`SMTP_USERNAME`/`TELEGRAM_BOT_TOKEN`/
  `TELEGRAM_CHAT_ID`/`WHATSAPP_ACCESS_TOKEN`) is blank. Only the `file`
  channel is on, which just saves the digest as a local HTML/PDF on the
  till PC itself — nobody reads it there. **This directly undercuts the
  brand-new remote-sync staleness warning (v1.0.17, see above)**: the
  whole point of that warning is a visible signal reaching the
  *owner*, but right now the digest that carries it never leaves the
  till PC. Needs real SMTP or Telegram bot credentials before that
  warning can actually do its job. WhatsApp additionally needs Meta
  template approval — see `poslib/channels/whatsapp_channel.py`.
  Separately confirmed: no digest has actually run yet since the most
  recent reinstalls (`digests/` doesn't exist on the real install) —
  not a bug, it's just not 20:00 (the configured `digest.hour`) yet
  today.
- **Only 1 of the owner's 3 stores (Boumati) has ever been set up.**
  Setif and Eulma are named exactly once in this whole file (the
  "Boumati/this one, Setif, Eulma" note) — neither has a packaged
  install, a Cloudflare project, or any provisioning work done at all.
  This is the actual next major milestone for the "customer
  distribution" effort, not a bug — the installer + auto-provisioning
  flow (Component 5) exists and is proven on store #1, so the mechanism
  itself doesn't need building, just running for the other two stores
  whenever the owner is ready.
- **Hub search cost/price real security fix — deferred, not rejected**
  (see "Hub search shows cost, not price" above): the `stock-<token>.json`
  unguessable-filename gate is a real, accepted tradeoff, not a bug, but
  the *actually*-correct fix (every store + the hub on subdomains of one
  real domain, so Cloudflare Access can share one login session and the
  Bypass-policy gate isn't needed at all) needs the owner to own a
  domain. Not started because that precondition was never met, not
  because of any remaining technical blocker.
- **Arabic web font not fetched** (`tools/get_fonts.py` — harmless, falls
  back to Windows' own Arabic font). Unchanged, still true, still low
  priority.
- ~~Task 14's manual spot-check against a live POS screenshot~~ —
  **DONE 2026-09-14.** Owner supplied a real "État ventes du jour"
  screenshot (today, generated 13:35). First comparison showed a
  discrepancy (14 tickets/422,020 DA vs the report's 15/446,980 DA) —
  investigated per the plan's own "stop and investigate, don't force a
  match" instruction rather than dismissed: the local cache was stale
  (last refreshed 13:00, before a ticket at ~13:1x-13:3x landed).
  `ETL.refresh(force=True)` picked it up, and every figure then matched
  **exactly**, not just the "same ballpark" the plan itself expected:
  revenue 446,980.00, collections 106,800.00, on-account 57,700.00,
  cash-in 496,080.00, 15 tickets, gross profit 31,314.11 — all identical
  to the DZD against the real POS report. Strongest confirmation yet
  that `metrics.py`'s cash-realized/on-account/collections split reads
  the real database correctly.
- **The auto-update "already up to date, do nothing" path still hasn't
  been explicitly confirmed against a real log line** — every real
  Updater-task run observed so far has been checking-into-a-real-newer-
  release, never checking-while-already-current. Low risk (the code path
  is simple, `check_for_update` just returns `None`), will get exercised
  naturally the next time the till PC logs in already on the latest
  version — not worth chasing deliberately.

**Confirmed NOT open (checked directly this session, despite older notes
elsewhere in this file that might read as unresolved):**

- Kaspersky's core protection (`AVP21.26`) is running on the real till
  PC — re-confirmed directly, closing out the 2026-09-05 network-
  diagnostic session's own loose end about this ("had NOT yet been
  confirmed turned back on"). Its separate VPN component (`KSDE5.7`) is
  stopped, which is fine — that was a red herring even at the time, per
  that session's own write-up, not something that needs to be running.
- A real packaged install exists and is current (`v1.0.17`) — an older
  note in this file describing "no packaged install at all" describes a
  since-superseded state, not today's.
- The hub's Access-app wildcard-scoping gap (Task 1, Component 5) was
  fixed the same day it was found (2026-08-29) — an earlier "still-open"
  note above describes the moment it was *found*, not its final state.
- Bugs #1-#4 (store #1 watcher/auto-update reliability) and the
  self-healing 3-part fix Bug #2's own section had proposed but left
  unbuilt are now **all** shipped and live-verified — see "Two real bugs
  found live on store #1 after merging the replatform" above.
- **Patch #1 (expiry stock) was explicitly dropped** — see discovery #6,
  an intentional decision, not an open item.
- Nothing else from Patch #2/#3/#4 is outstanding. New metrics belong in
  `poslib/metrics.py`, new diagnostic rules in `poslib/diagnostics.py`, new
  owner-entered data in `poslib/ownerdata.py` (never in a file `etl.py`
  rebuilds) — see the architecture rules at the bottom of `README.md`.

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
