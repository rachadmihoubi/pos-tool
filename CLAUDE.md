# Shop Analysis — project brief for Claude

Read this before touching anything. It captures decisions and discoveries from
the build session that are not obvious from the code alone.

## What this is

A local analysis tool for a wholesale cosmetics distributor in Algiers. Reads
their POS's Access (Jet 4) database directly — no ODBC, no mdbtools, nothing
installed but Python — and produces a dashboard, an Excel report, and a daily
digest in English/French/Arabic. Full spec is in `claude-code-prompt.md`.

**Status: feature-complete per the original spec.** All stages built, 84 tests
passing, verified against the live database. See "What's left" below for the
only things not yet done.

## The one rule that overrides everything else

**Never write to the source `.dblx` file.** It is opened read-only and always
copied to a temp folder before parsing (`poslib/etl.py:copy_database_readonly`).
Every change must preserve this. If you're ever tempted to open the source path
directly for anything other than a read-only copy, stop.

## Machine-specific thing to fix on a new PC

`config.yaml` → `database.path` is currently:
```
E:/Base de données4.dblx
```
This is **this machine's** path to the live POS database. On a different PC the
POS database will be at a different location (different drive letter, different
folder — possibly not present at all if this is a dev machine rather than the
shop's actual till computer). **Check this path is correct before running
anything** — `poslib/etl.py` will raise a clear `ETLError` naming the missing
file if it's wrong, so this fails loudly, not silently.

## Three non-obvious discoveries — do not "fix" these back

These look like they could be bugs if you're not aware of the history. They are
deliberate, each verified against the real data:

1. **Boolean columns: a SET bit means TRUE.** (`poslib/jet4.py`, the
   `TYPE_BOOL` branch in `_parse_row`.) The original spec said the opposite
   ("clear bit = TRUE"). I verified empirically: under "set = true", all 42
   customers carrying a balance have `AllowAccount=True` (0 contradictions),
   and items sold today show `Inactive=False`. Under the spec's rule, all
   1,570 products come out inactive and the anonymous walk-in account is the
   only one with credit — both nonsensical. If you ever see products
   appearing inactive that shouldn't be, this is the first place to check,
   but the current code is correct — verified, not guessed.

2. **`Item.LastSold` is stale on ~60 products** (worst case: 370 days stale on
   a product that sold last week with 432 units in stock). `metrics.py`'s
   `item_movement` property uses the most recent *ticket* date
   (`last_sale_effective`) instead of the POS's own field, falling back to the
   field only when there's no ticket history at all. Trusting the raw field
   wrongly condemns ~584,340 DZD of live stock as dead. This is quantified on
   the Data Quality page (`stale_last_sold` in `data_quality()`).

3. **Purchase line totals don't reconcile** — they sum to roughly 2x the cost
   of goods sold plus current stock value, which can't be right (likely a POS
   export quirk, not investigated further). `purchase_coverage()` in
   `metrics.py` flags this (`value_reconciles: False`) and every supplier
   money figure is built from cost-of-goods/stock-value instead, never from
   summed purchase lines. Don't wire supplier "amount spent" back to raw
   purchase-line sums without re-checking this.

Also worth knowing: `Receipt.TotalCost` is zero on exactly the 9 `ReceiptType=1`
("DV") tickets — cost of goods is always computed from `ReceiptEntry` lines,
never trusted from the ticket header.

## Verified numbers (as of the last full check, 2026-08-10)

These are floors for all-time totals (they only grow with new sales) and were
confirmed to reconcile exactly against three new receipts written during the
build session. Full table and tolerances: `tests/conftest.py` fixtures
`expected_counts` / `expected_totals`, exercised by `tests/test_metrics.py`.

| Check | Value |
|---|---|
| Revenue, all time (excl. `ItemID<=0`) | 266,299,322 DZD |
| Gross profit, all time | 26,686,300 DZD |
| Account payments ("Paiement de règlement") | 30,420,753 DZD |
| Stock at cost | 59,168,540 DZD |
| Receivables (positive balances only) | 18,035,898 DZD |
| `Receipt` / `ReceiptEntry` / `Item` / `Customer` rows | 7,858 / 39,736 / 1,570 / 662 |

Run `python -m pytest tests -q` to re-verify against whatever the database says
now — it should never fail on the "grows" checks (that would mean rows or
money are being lost) and should stay within tolerance on the point-in-time
ones (stock value, receivables — these genuinely drift as trade happens).

## What's left (optional, not blocking)

- **`.env` is empty on every machine** (gitignored, by design). Email and
  Telegram digest channels are wired up but need real credentials pasted in
  before they'll send. WhatsApp additionally needs Meta template approval —
  see the long comment in `poslib/channels/whatsapp_channel.py` before anyone
  asks to "just turn it on."
- **Arabic web font not fetched.** `tools/get_fonts.py` downloads Cairo from
  Google Fonts; it wasn't run in this session (`static/fonts/` is empty in the
  repo). Harmless — the CSS falls back to Windows' own Arabic font — but run
  it if you want the branded look. `setup.bat` runs it automatically anyway.
- **Task Scheduler entries not installed on this machine.** `install-startup.bat`
  exists and works but hasn't been run — the tool currently only starts via
  manual `start.bat`.
- Nothing else from the original spec is outstanding. If asked to add a new
  metric or diagnostic rule, it belongs in `poslib/metrics.py` /
  `poslib/diagnostics.py` respectively — see the "How it is put together" /
  architecture rules at the bottom of `README.md`.

## Cross-machine sync is automated

`.claude/settings.json` (committed, travels with `git clone`) has a `SessionStart`
hook: every time Claude Code opens in this folder, it runs `git pull --ff-only`
before doing anything else — silently, no-op if that fails (no upstream, offline,
non-fast-forward) so it never surprises you with a merge or overwrites local
work. That's the mechanism that makes "switch PCs mid-project" actually work:
whichever machine has the latest push, the other one picks it up automatically
on next session start. The same hook is also mirrored in the user's global
`~/.claude/settings.json` so it applies to every future project on a given
machine, not just this one — but the project-level copy here is what makes it
work with **zero setup** after a fresh `git clone` on a brand new PC.

`.claude/settings.local.json` is gitignored on purpose — it holds
machine-specific permission allowlists, not meant to travel.

**What this does NOT sync:** the raw conversation/session transcript. That's
local per machine by design. This file (`CLAUDE.md`) plus the git history are
the actual continuity mechanism — keep it current at natural stopping points
rather than relying on transcript memory.

## Environment note

This machine had neither Python nor Git on PATH at session start — both are
installed now (Python via winget at
`%LOCALAPPDATA%\Programs\Python\Python312`, Git at
`C:\Program Files\Git\bin\git.exe`) but a fresh PowerShell may still need the
full path if a new session's PATH hasn't picked it up. `.venv` itself is
gitignored and must be rebuilt with `setup.bat` on any new machine.
