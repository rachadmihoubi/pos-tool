# Shop Analysis — project brief for Claude

Read this before touching anything. It captures decisions and discoveries from
the build sessions that are not obvious from the code alone.

## What this is

A local analysis tool for a wholesale cosmetics distributor in Algiers. Reads
their POS's Access (Jet 4) database directly — no ODBC, no mdbtools, nothing
installed but Python — and produces a dashboard, an Excel report, a daily
digest in English/French/Arabic, automatic daily backups, and an optional
password-gated remote view on Cloudflare Pages.

**Status: feature-complete through Patch #3** (2026-08-10). Original build +
the cash/P&L, drill-down and date-range foundation ("Patch #2") + seven new
Patch #3 features + silent background launch + remote viewing are all built,
tested (169 tests) and — for the background launch and remote viewing —
actually deployed and verified live on this machine. See "What's left" below.

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

## What was built this session (Patch #2 foundation + Patch #3)

- **Cash/P&L page** (`/cash`) — income statement by month, cash/cheque/
  transfer/credit split, till-session reconciliation, working capital.
- **Date-range picker** on `/trend` and `/cash` (`?start=&end=`, whole-day
  boundaries, degrades gracefully on bad input — never a 500).
- **Customer & product drill-down pages** (`/customers/<id>`, `/products/<id>`)
  — linked from the list pages; 404 for unknown IDs and for the walk-in
  customer (not a real customer).
- **New arrivals feed** — copyable plain-text list on `/products`, config
  `catalog.new_arrivals_days` (default 7).
- **Item photos** — best-effort, see discovery #7 above.
- **Till/session reconciliation** — see discovery #4, surfaced on `/cash`.
- **Inventory shrinkage** — event-level, see discovery #5, surfaced on
  `/inventory`.
- **Customer credit-risk tiers** (low/medium/high) — `thresholds.customers.
  credit_risk_*` in config.yaml, surfaced on the customer profile and
  `/receivables`.
- **Family-level margin benchmarking** — `family_margin_outliers()`, new
  diagnostic rule `family_margin_benchmark`, config `thresholds.margin.
  family_benchmark_pp` (default 15pp).
- **Competitor price log** — `poslib/ownerdata.py`, the first *write* path in
  the app. Owns `data/owner.db`, a separate SQLite file `poslib/etl.py` never
  touches (an ETL refresh fully replaces `cache.db` every time — anything
  owner-entered stored there would be destroyed). Form + table on the product
  drill-down page.
- **Automatic daily backup** — `poslib/backup.py`, copies source `.dblx` +
  `cache.db` + `data/owner.db` into `backups/YYYY-MM-DD/`, keeps 30 days.
  Wired into `watcher.py` the same way the daily digest is.
- **Silent background launch** — turned out to already be built from the
  original session (`start-quiet.bat`, `install-startup.bat` using
  `pythonw.exe` + `schtasks /sc onlogon`, no stored password). Added the one
  missing piece, `stop-background.bat`. **`install-startup.bat` has been run
  on this machine** — Task Scheduler entries "Shop Analysis - Dashboard" and
  "Shop Analysis - Digest" are live.
- **Remote viewing** — `poslib/present.py` + `templates/remote_dashboard.html`
  + `export_static.py` build a lean, self-contained static snapshot (today's
  KPIs, a trend chart, top customers/products, diagnostics alerts, cash
  position — never the full receipt history). `poslib/remote.py` pushes it via
  `wrangler pages deploy`. Wired into `watcher.py`, pushed after every cache
  rebuild, gated by `remote.push_interval_seconds` (90s). **Live and
  deployed**: Cloudflare Pages project `promakeupmihoubipos`, gated by
  Cloudflare Access (email-allowlist policy "owner only", owner's email only).
  `remote.enabled: true` in config.yaml.

## Cloudflare setup on this machine (already done — for reference on a new PC)

- `wrangler` installed globally via `npm install -g wrangler` (Node.js was
  already present). **On Windows, `subprocess` must call the fully-resolved
  `wrangler.CMD` path**, not the bare string `"wrangler"` — `shutil.which()`
  finds it fine but `subprocess.run(["wrangler", ...])` raises `WinError 2`
  regardless; `poslib/remote.py:_wrangler_path()` handles this. Also:
  `subprocess.run` needs explicit `encoding="utf-8"` — wrangler prints emoji
  that crash the default-codepage decode on Windows otherwise (this crashes
  a background reader thread, not the main call, so it doesn't fail the
  push, but it does spam a traceback into the logs).
- Authenticated via `wrangler login` (OAuth, browser-based — cannot be
  scripted; the account is `rachadm23@gmail.com`).
- Cloudflare Pages project `promakeupmihoubipos` created via
  `wrangler pages project create`.
- Cloudflare Access application configured through the Zero Trust dashboard
  (no CLI/API path was used) — Applications → Self-hosted → destination
  `promakeupmihoubipos.pages.dev` → policy "owner only" (Allow, Include:
  Emails). **Gotcha hit while setting this up**: Cloudflare's current UI adds
  an empty "Private IP" destination row by default that fails validation if
  left as-is — delete it, keep only the public-hostname destination. Also
  watch for browser autofill polluting a hostname-shaped field with a street
  address — remove any destination that isn't the actual `.pages.dev`
  hostname. **Access enforcement takes a minute or two to propagate** after
  saving — don't conclude it's broken from an immediate check; wait, then
  re-check for the redirect to `<team>.cloudflareaccess.com/cdn-cgi/access/login/...`.

## Cross-machine sync is automated

`.claude/settings.json` (committed, travels with `git clone`) has a `SessionStart`
hook: every time Claude Code opens in this folder, it runs `git pull --ff-only`
before doing anything else — silently, no-op if that fails (no upstream, offline,
non-fast-forward) so it never surprises you with a merge or overwrites local
work. The same hook is also mirrored in the user's global `~/.claude/settings.json`.

`.claude/settings.local.json` is gitignored on purpose — it holds
machine-specific permission allowlists, not meant to travel.

**What this does NOT sync:** the raw conversation/session transcript, and
(deliberately) `data/owner.db`, `backups/`, `remote-site/`, `.wrangler/` — see
`.gitignore`. This file (`CLAUDE.md`) plus the git history are the actual
continuity mechanism.

## What's left (optional, not blocking)

- **`.env` is empty on every machine** (gitignored, by design). Email and
  Telegram digest channels are wired up but need real credentials. WhatsApp
  additionally needs Meta template approval — see
  `poslib/channels/whatsapp_channel.py` before turning it on.
- **Arabic web font not fetched** (`tools/get_fonts.py` — harmless, falls
  back to Windows' own Arabic font).
- **Patch #1 (expiry stock) was explicitly dropped** — see discovery #6.
- **This session's work is not yet committed/pushed** — run
  `.\tools\sync.ps1` when ready (or ask Claude Code to do it) so the other PC
  picks it up on its next session start.
- Nothing else from Patch #2/#3 is outstanding. New metrics belong in
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
