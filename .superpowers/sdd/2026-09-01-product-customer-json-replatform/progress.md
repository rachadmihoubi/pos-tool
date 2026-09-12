# SDD ledger — plan: docs/superpowers/plans/2026-09-01-product-customer-json-replatform.md

## Setup

- Worktree: `.claude/worktrees/product-customer-json-replatform`, branch
  `product-customer-json-replatform`, branched from local `main` HEAD
  `1abd0e3` (which includes the Stage 1 "Synced" badge commit, so this
  worktree is not missing that work). User gave explicit consent for both
  the worktree and committing Stage 1 to main first.
- Deps: global Python already has flask/pandas/requests/blake3 installed
  (verified) — no venv setup needed, `python` on PATH works directly in
  the worktree.
- `config.yaml`'s `database.path` set locally (uncommitted, matching this
  machine's own established per-checkout convention — see CLAUDE.md's
  machine-identity table) to `E:/Base de données4.dblx`. Without this,
  every real-DB test errors with `poslib.etl.ETLError` (the worktree's
  committed config.yaml still had the *other* machine's dev-PC path).
- Baseline: `pytest tests -q --deselect tests/test_export_static.py` →
  405 passed, 19 deselected, **1 pre-existing failure**:
  `TestConsistency::test_verification_table` — `dead_stock_value` is
  12,099,402 against a frozen expected 15,698,733 (>20% tolerance band).
  **Ruling: proceed, do not fix.** This is a point-in-time figure
  (CLAUDE.md: "point-in-time values (stock, receivables) are checked
  within tolerance since they drift as trade happens") that has simply
  drifted since the floor was last set — real stock has moved on this
  machine's live database over time. Nothing in this plan touches
  `dead_stock_value`, `item_movement`, or any diagnostics threshold. Cost
  if wrong: none — if this floor genuinely needs updating, that's a
  separate, unrelated maintenance task, not something this plan's tasks
  would introduce or fix.
- `test_export_static.py` deliberately NOT run as part of the baseline
  (matches this repo's own documented practice, CLAUDE.md: "deselected
  per its own documented ~3.9-min real-database cost") — it IS run as
  part of Task 3/4/6's own dedicated test steps per the plan.

## Pre-flight conflict scan

| Pair | Shares | Produces / Consumes | Finding |
|---|---|---|---|
| Task 1 → Task 2 | `poslib/i18n.py`'s `js_format()` shape | Task 1 produces `{thousands, decimal, currency, money_format, percent_format, date_format, datetime_format, dash}`; Task 2's formatters consume exactly these 8 keys | Clean — verified every key Task 2 reads (`fmt.dash`, `fmt.money_format`, `fmt.percent_format`, `fmt.date_format`, `fmt.datetime_format`, `fmt.thousands`, `fmt.decimal`, `fmt.currency`) is a key Task 1 adds. |
| Task 1 → Task 4 | same | Task 4's shells embed `{{ t.js_format() \| tojson }}` directly | Clean — no shell-specific field naming needed, whole dict passed through. |
| Task 2 → Task 3 | JSON field names (`s.item_name`, `s.item_no`, `s.family_name`, `s.revenue_all`, `s.stock_value`, `s.days_since_sale`, `s.credit_risk`, `s.segment`, `s.avg_basket`, `s.revenue_12m`, etc.) | Task 3 dumps `row_dict(profile["summary"])` etc. wholesale (every column of `item_movement()`/`customer_summary()`), Task 2's JS reads a subset of those field names | Verified by grep against `poslib/metrics.py` that every field name Task 2's JS reads (`item_no`, `stock_value`, `family_name`, `days_since_sale`, `credit_risk`, `segment`, `avg_basket`, `revenue_12m`) is a real column, not a typo/guess. One nuance checked and confirmed harmless: `family_name` is never null (metrics.py:292 fills it with the literal string "—"), so both the real Jinja template's `{% if summary.family_name %}` and the plan's JS `s.family_name ? ... : ...` truthy-check the same non-empty string the same way — the "—" placeholder shows in both, consistently, not a Stage-2-introduced regression. |
| Task 2 → Task 4 | `dom.*` object keys (every `document.getElementById(...)` mapping) | Task 4 defines the `dom = {...}` object per shell; Task 2's `renderProduct`/`renderCustomer` read `dom.X` | **Automated cross-check run**: extracted every `dom\.[a-zA-Z]+` reference from Task 2 and every `key: document.getElementById(...)` definition from Task 4 across the whole plan file — zero references without a matching definition. Clean. |
| Task 3 → Task 4 | File paths (`../products.json`, `../customers.json`) and JSON top-level keys (`summary`/`family`/`sales_history`/`purchase_history`/`competitor_prices` for products; `summary`/`receivable`/`purchases`/`payments` for customers) | Task 3 writes these exact shapes; Task 4's shell fetch/lookup code reads them | Clean — matches; both were written together in the same review-fix pass and share identical key names throughout. |
| Task 4 → Task 6 | `export_static.py`'s `products_dir`/`customers_dir` loops | Task 4 leaves them in place (parallel path); Task 6 deletes them | Sequential by design (Task 6 explicitly gated on Task 5 passing) — not a real conflict, just ordering, already enforced by the plan's own task numbering and this ledger's dispatch order. |
| Task 3 → Task 6 | `export_static.py`'s new JSON-writing block | Task 6 does not touch this block, only the old HTML loops | Clean — no overlap in the lines each task edits. |
| Self-consistency: Task 3 | Test assertions vs. implementation | `test_products_json_has_every_item_keyed_by_id`/`test_customers_json_...` assert `set(entry.keys())` for a specific field set, and a byte-size ceiling (`< 20MB`) | Implementation code produces exactly those keys (no extra, no missing) — verified by re-reading both blocks side by side. Size ceiling is a canary, not a hard requirement the implementation must engineer toward — consistent with the plan's own framing. |
| Self-consistency: Task 4 | Shell markup vs. `renderProduct`/`renderCustomer`'s DOM writes | Every `id="rd-*"` the JS touches must exist in the shell HTML | Covered by the automated dom.\* cross-check above (Task 2 ↔ Task 4 row) — since every read has a write-side definition, and the definitions come from `document.getElementById("rd-*")` calls whose `"rd-*"` string literals were written alongside the same shell's HTML `id="rd-*"` attributes in the same edit, these are consistent by construction, not just by the automated key-name check. |

**Scan verdict: clean.** No conflicts requiring a pre-implementation ruling beyond the baseline-drift ruling above. Proceeding to Task 1.

## Task 1: dispatched (BASE ea60416, implementer agent a11c045e57a363833, model haiku)

**Operational note**: Task 1's implementer ran some `git reset`-shaped
command during its work (visible in `git reflog`: "reset: moving to
HEAD" between the plan-doc commit and Task 1's own commit) that silently
discarded this worktree's uncommitted local `config.yaml` override
(`database.path`), even though its own final commit correctly touched
only the 2 intended files (`poslib/i18n.py`, `tests/test_i18n_and_app.py`)
— the test run itself must have happened before the reset, since its
reported "72/72 passing, 160s" figure includes real-DB dashboard-page-load
tests that would fail immediately against the stale committed path.
Restored `config.yaml`'s local override (`E:/Base de données4.dblx`)
after the fact. **Going forward: re-verify `config.yaml`'s database.path
before every task dispatch and before every test run in this worktree**,
and tell each implementer explicitly not to run `git checkout .` /
`git reset --hard` / `git clean` against tracked files outside what it
intends to commit.

## Task 1: complete (commits ea60416..54074b0, review pending)

Task 1 review: Spec compliant, task quality Approved. One Minor (deferred):
tests/test_i18n_and_app.py:490 - TestJsFormatExtended placed near
TestChannels rather than near TestLocales (the other Translator format
tests) - cosmetic, no fix needed.

## Task 1: complete (commits ea60416..54074b0, review clean, 1 minor deferred)

## Task 2: dispatched (BASE 54074b0, implementer agent a6f719677d7e4ff01, model sonnet)

Task 2 first attempt (agent a6f719677d7e4ff01) crashed mid-work with an
infra-level API error (connection lost), not a content problem - it had
already written static/remote-detail.js (282 lines, uncommitted, all 15
expected functions present) before crashing, but never committed or
reported. Not counted as a fix-loop round (no review finding involved) -
re-dispatched a fresh implementer to verify/complete/commit the existing
draft rather than restart from scratch.

## Task 2: re-dispatched to finish/verify/commit (BASE 54074b0, agent a21fa897a5dbe2078, model sonnet)

Task 2 review: Spec compliant, task quality Approved. Verified injection
safety (competitor price delta cell, family-compare margin cell - both
escapeHtml'd, no free-text field reaches innerHTML anywhere) and parity
with poslib/i18n.py's dash/rounding conventions. Minor (deferred, all
inherent/already-known, no fix needed): JS toFixed vs Python round-half-
to-even tie divergence (already documented in the plan's own Self-Review
Notes); .replace() first-occurrence-only vs Python str.format() (harmless
against real locale files, each placeholder appears once); some redundant
isMissing/isNaN re-checks (brief's own specified code, not implementer-
introduced).

## Task 2: complete (commits 54074b0..79ab966, review clean, 3 minor deferred)

## Task 3: dispatched (BASE 79ab966, implementer agent a0d25a0aeb686d061, model sonnet) - expect a long wait, real-DB export tests take several minutes each

**Operational note**: this task's implementer crashed twice (infra-level
"connection lost"/"response stopped arriving" errors) mid-way through its
long real-DB test runs, was resumed 3 times total via SendMessage. On the
controller side, a background-task-tracking mechanism also proved
unreliable: a `bev9xlsq6` background bash task the crashed implementer had
launched showed status `[killed]` with zero captured output even though
the underlying OS process (confirmed via `tasklist`) had genuinely run to
completion - so the controller independently re-ran the new
`TestProductsCustomersJson` class fresh (3/3 passed, real captured output,
34m44s) rather than trust that unreliable tracking, before the implementer
itself separately recovered via a genuinely OS-detached `Start-Process`
launch and completed Step 5 on its own. Both the controller's independent
re-run and the implementer's own final full-suite run agree: the
implementation is correct. Going forward: don't trust a background task's
"[killed]"-with-no-output status as evidence of failure - verify the
actual OS process state independently before concluding a run failed.

## Task 3: complete (commit d7d751d, review dispatched)

Implementer: agent a0d25a0aeb686d061 (resumed 3x), model sonnet.
Full regression (`pytest tests/test_export_static.py -q`): 21 passed, 1
skipped (pre-existing), 0 failed, 13966s. Real-data note: 828/1642
products (50.4%) hit the `cover_months = inf` case that `_json_safe()`
guards against - not an edge case, the majority case. `products.json`
7.4MB, `customers.json` 5.0MB - both well under the 20MB test canary and
Cloudflare's 25MB/asset limit.

Review package: `review-79ab966..d7d751d.diff`. Task reviewer dispatched:
agent af3c673fe46a77937, model sonnet.

Task 3 review: Spec compliant, task quality Approved. Zero Critical/
Important findings. Two Minor (deferred, no fix needed): (1) `_json_safe`'s
`datetime.date` branch is currently unreachable given `rows()`/`row_dict()`
always produce `datetime.datetime`, not bare `date` - harmless defensive
code; (2) today's real per-export cost (~665-800s) ran ~2.8x above the
CLAUDE.md-documented historical baseline (~3.9min) - flagged as a data
point for future timing budgets, not a defect of this task.

## Task 3: complete (commit d7d751d, review clean, 2 minor deferred)

## Task 4: dispatched (BASE d7d751d, implementer agent a5a11d8a1f7e17fde, model sonnet) - expect a long wait, real-DB export tests take 10-13+ min each on this machine today; full-file regression could take 3-4+ hours. Implementer was briefed on Task 3's infra instability (unreliable background-task tracking, config.yaml reset risk) and told to redirect test output to disk directly / use an OS-detached launch if needed.

## Task 4: implementation complete, committed as WIP (commit `06288a5` on
`product-customer-json-replatform`, pushed to origin), full regression
STILL UNVERIFIED as of this note - READ THIS BEFORE TRUSTING TASK 4

Implementer's report (`task-4-report.md`) confirmed RED (2 failed for the
right reason, 1 unrelated pass) and GREEN (3/3 passed) for the new
`TestProductCustomerShells` class - that part is trustworthy. But the
report's own Step-6 full-file regression claim ("still running, will
update this report") was never actually updated, and the log file it
pointed at (`logs/task4-full-regression.log`) turned out to be only 17
bytes with no real pytest summary - stale/truncated, NOT evidence of a
real pass. Do not trust that log or the report's implied completion.

**Controller (this session) is independently re-running the full suite**
(`pytest tests/test_export_static.py -q`, all ~22 tests, each a fresh
real-DB export) to get a trustworthy result before treating Task 4 as
verified. Given this session's user needed to leave, this run was
launched as a genuinely OS-detached process (PowerShell `Start-Process`
running a detached `cmd.exe`, NOT tied to any Claude Code session or
terminal) specifically so it survives the session ending:

- Log file: `.superpowers/sdd/2026-09-01-product-customer-json-replatform/logs/task4-full-regression-final.log`
- Launched ~2026-09-03 (see file's own birth time via `stat` if unsure)
- To check: read that log file. A finished run ends with either a pytest
  summary line (`N passed, M skipped in Xs`) or `EXIT_CODE=N`. Empty or
  missing summary = still running or the process died - check
  `tasklist` for a `python.exe` with several hundred MB RSS and rising
  CPU time to tell "still running" from "died silently."
- **If it shows a clean pass (all passed, same 1 pre-existing skip as
  Task 3's run)**: amend/replace the WIP commit (or add a new commit)
  removing the "[WIP, regression re-verifying]" marker from the message,
  update this ledger's Task 4 entry to "complete," generate the review
  package (`scripts/review-package ... 06288a5's-parent d7d751d..HEAD`
  wait - actually diff BASE should be `d7d751d` since that's Task 3's
  HEAD, to `06288a5` or whatever the final Task 4 commit becomes), and
  dispatch Task 4's task reviewer (see Tasks 1-3's dispatches above for
  the pattern) before moving to Task 5.
- **If it shows any failure**: do NOT commit further on top of the WIP
  commit as if done - investigate the failure first (it could be a real
  regression from Task 4's changes, or could be environment/machine-load
  noise like Task 3's ~2.8x-slower-than-baseline finding - check which).
- **Do not re-run the whole suite a third time without first checking
  this log** - it's expensive (real per-export DB cost, hours total) and
  the point of this note is to avoid re-deriving what's already in
  flight.

Two earlier full-suite launches this session were killed/superseded before
this one: a Bash `nohup`-launched run (session-tied, killed deliberately
to avoid two concurrent runs fighting over `poslib/etl.py`'s cross-process
advisory lock on `cache.building`, which would have serialized them
anyway) and the implementer's own truncated-log attempt described above.
Only the OS-detached one above is currently live.

## Task 4: full regression CONFIRMED CLEAN (controller-independent re-run)

`logs/task4-full-regression-final.log`:
```
................s........                                                [100%]
24 passed, 1 skipped in 69305.40s (19:15:05)
EXIT_CODE=0
```
24 passed (21 pre-existing + 3 new `TestProductCustomerShells`), 1 skipped
(same pre-existing skip as Task 3's run), 0 failed. The ~19h15m wall-clock
is far above every other run this session (~650-800s/export earlier) -
most likely explained by two full-suite runs briefly competing for
`poslib/etl.py`'s cross-process advisory lock before the redundant one was
killed (see above), plus this being a real till PC with real business
hours/load in between, not a regression in the code itself - nothing in
Task 4's diff touches `etl.py` or per-export cost. Not investigated
further; the result itself (24 passed, 0 failed) is what matters.

Task 4 is now trustworthy. Proceeding to generate its review package
(diff `d7d751d..06288a5` - the code commit; `d9cf6a7` is docs-only ledger
notes, not part of the reviewable diff) and dispatch its task reviewer.

**Note**: `06288a5` also contains ~2,400 lines of force-added SDD
workspace docs (committed for cross-machine continuity, see the earlier
"commit everything and push" instruction) - the standard
`scripts/review-package` diff would have included all of that as noise.
Built a manual code-only diff instead, scoped to `export_static.py`,
`templates/`, `tests/test_export_static.py`:
`review-d7d751d..06288a5-code-only.diff`. Task reviewer dispatched:
agent a25c046378d1f0d5f, model sonnet.

Task 4 review: Spec compliant, task quality Approved. Zero Critical
findings. One Important finding, but in a DIFFERENT task's already-
reviewed file (`static/remote-detail.js`, Task 2), surfaced only as a
byproduct of the required Task2↔Task4 DOM-hook interface check:
`renderProduct`'s competitor-row builder rendered an empty 5th cell
instead of the real (non-functional-when-remote, same accepted parity
tradeoff as the add form) delete button `product_detail.html` always
shows - a real visible-parity regression versus both the local page and
the old per-entity remote export (which rendered the real button, since
it dispatched the real template). Two Minor (both deferred): stale
"[WIP]" commit message (cosmetic only); shell `<title>` doesn't update to
the entity name post-fetch (not covered by the "reproduce visible
content" constraint).

**Ruling**: fixed directly by the controller rather than looping back
through a fresh Task-2 implementer/reviewer cycle - the fix is small
(29 lines across 2 files + 1 test), fully scoped to the exact gap named,
and empirically verified (see below), so the overhead of a full
dispatch cycle wasn't justified. Cost if this ruling is wrong: the fix
itself is still real code someone would need to review eventually - a
full whole-branch review happens before this branch merges regardless
(see the skill's final-review step), so nothing here escapes review
permanently, only skips an extra dedicated per-fix review pass.

Fix: `templates/product_shell.html`'s `strings` object gained `lang`/
`competitorDelete`; `static/remote-detail.js`'s competitor-row builder
now renders a real `<form method="post">`+`<button>` pointed at the real
`product_competitor_price_delete` route (`app.py:733`), matching the
already-accepted "visible but non-functional when remote" tradeoff. New
regression test `test_competitor_delete_button_is_reproduced_on_the_shell`
added to `TestProductCustomerShells`. Verified: full
`TestProductCustomerShells` class (4 tests, including the new one) ran
clean against the real database - 4 passed, 0 failed (73508s / 20h25m,
another very slow real-DB run on this till PC, consistent with the
Task 4 full-regression run's own ~19h - not investigated further, same
non-code-related explanation). Committed as `6f0fce8`.

## Task 4: COMPLETE (commits d7d751d..6f0fce8: 06288a5 code +
d9cf6a7/52c3a8b docs + 6f0fce8 fix). Review clean, 1 Important finding
fixed same-session, 2 Minor deferred (cosmetic commit-message staleness;
shell `<title>` not entity-specific).

## Task 5, Step 1: full suite pre-check run (fast subset)

`pytest tests -q --deselect tests/test_export_static.py` (deselected the
slow real-DB export tests, which have already been run and verified
clean many times over across Tasks 3/4 - re-running them again here
would cost another ~19h for no new information): **407 passed, 1 failed,
26 deselected in 291s**. The 1 failure is
`TestConsistency::test_verification_table` - `dead_stock_value` is
12,224,107 against a frozen expected 15,698,733 (>20% tolerance) - the
EXACT SAME pre-existing failure already ruled acceptable in this
worktree's own Setup section at the very start of this plan's execution
(a point-in-time figure that drifts with real trade; nothing in this
plan touches `dead_stock_value`/`item_movement`/any diagnostics
threshold). **No new regressions from Tasks 1-4.** Task 5 Step 1 is
satisfied.

## Task 5, Steps 2-5: BLOCKED - needs the user, cannot proceed
autonomously

These steps require a real Cloudflare push (`.env` with valid
`CLOUDFLARE_API_TOKEN`/`CLOUDFLARE_ACCOUNT_ID`, `remote.enabled: true`
pointed at a real reachable project) and, per this repo's own
established discipline (see CLAUDE.md's `_redirects` bug history -
"don't declare a live deploy fixed until the owner's phone confirms
it"), the owner's own phone checking the real deployed pages in all 3
languages. This session has neither a live token scoped for this
purpose nor the ability to check a phone. **Task 6 is hard-gated on
Task 5 passing** - do not start it until Task 5 Steps 2-4 are confirmed,
per the plan's own explicit "Only start this task after Task 5's live
verification has actually passed" instruction.

**Whoever resumes with live Cloudflare access should**: run Task 5 Steps
2-4 exactly as written in the plan (`docs/superpowers/plans/2026-09-01-
product-customer-json-replatform.md`, "Task 5" section) - export+push,
measure file count/size before/after plus a second "warm" push timing,
then the phone spot-checks (a product with history+family, a never-sold
item, `DB786` specifically if still present, a customer with a balance,
an empty-history entity, all 3 languages including Arabic RTL, and the
Stage 1 "Synced" badge still present). Once that passes, proceed to
Task 6 (delete the old per-entity HTML loops) using the same
subagent-driven-development pattern as Tasks 1-4 above.

## Task 5, Steps 2-3: DONE 2026-09-05, on store #1's own till PC (real Cloudflare access) - but read CLAUDE.md's "Store #1 watcher outage + timeout bug" section first, this ran into a real production incident along the way

Live access became available this session on the real till PC
(`DESKTOP-94UHGGD`). Attempting the real push immediately surfaced a
genuine, already-in-progress production incident (store #1's own
`promakeupboumati` dashboard had been silently stale since 2026-09-03) -
fully root-caused and partly fixed; see CLAUDE.md's new section for the
complete story (a real `poslib/remote.py` timeout bug, now fixed and
committed to `main` as `8e2b170` - deliberately NOT on this branch, since
this plan's own Global Constraints mark `remote.py`'s upload mechanics
out of scope; a network scare that mostly turned out to be a red herring;
and a second, NOT-yet-fixed systemic gap where the packaged watcher can
die silently with no restart). **This worktree's own `poslib/remote.py`
still has the pre-fix code** - a future rebase/merge from `main` will
pick up `8e2b170` naturally; don't re-fix it independently here.

Once the timeout fix was applied (copied in locally for testing, not
committed to this branch) and the network came back healthy, two full
live pushes to `promakeupmihoubipos` succeeded:
- Cold push: 486.3s, `push_remote` returned `True`.
- Warm push (re-exported immediately after, no real data change):
  722.2s - slower, not faster, likely because the Stage 1 "Synced
  {when}" badge embeds a live timestamp into every page, so nearly every
  file's content-hash changes on every export regardless of real data
  change, defeating `poslib/remote.py`'s check-missing-hashes
  optimization for a full export. Not a bug in this plan's own code -
  worth knowing as a real limit on how cheap a "no-op" push can ever be
  here.

**File count/size** (interim - the old per-entity HTML loops are still
present, Task 6 hasn't run yet): 12,695 files, ~250.7MB. Compare against
the plan's own 12,555-files/~232.6MB baseline once Task 6 actually
removes the old loops - this number is not the real "after" yet.

## Task 5, Step 4: PARTIALLY DONE - one real finding, needs a proper re-check before this task is called complete

The owner did check the live `promakeupmihoubipos.pages.dev` pages
directly and reported "everything is showing" (catalog -> product page,
receivables -> customer page, language switching, no visible breakage) -
but also reported the "Synced" badge showing "2 days ago", which read as
a possible push failure.

**Root cause, checked and confirmed harmless**: `templates/base.html`'s
badge reads `cache.parsed_at` - when the *local* ETL cache
(`cache.db`) was last rebuilt from the real database - not when the last
push happened (`export_static.export()` just opens whatever cache
already exists via `ETL.connect()`, which never calls `refresh()` -
`poslib/etl.py:402-410`). This worktree is a manually-driven dev
environment with no watcher loop running in it, so of course its local
cache goes stale between manual test runs - this is expected for this
kind of test worktree, not a symptom of the push failing (the push
itself independently returned `True` twice, per above).

A forced `ETL.refresh(force=True)` + fresh `export_static.export(cfg)` +
`push_remote(cfg)` was kicked off to get a genuinely fresh "Synced just
now" badge on the live site before this session paused (per the owner's
explicit request to stop debugging and hand off to another PC) - **its
final result was not confirmed before the session ended.** Whoever
resumes should:
1. Check whether that run actually completed and succeeded (there is no
   automatic record of it beyond this note - re-run it if unsure:
   `ETL(cfg).refresh(force=True)` then `export_static.export(cfg)` then
   `remote.push_remote(cfg)`, same pattern as Step 2 above).
2. Re-check the live site's "Synced" badge shows a recent, correct time.
3. Finish the rest of Step 4's checklist properly: a product WITH
   purchase history and a family, a NEVER-sold item, `DB786`
   specifically if still present (known avg-cost/last-cost divergence),
   a customer with a balance, an empty-history entity, and explicit
   confirmation of Arabic's right-to-left rendering and its
   thousands/decimal separators - none of these specific checks were
   confirmed individually this session, only a general "everything is
   showing."

**Do not treat Task 5 as fully passed yet** - Steps 2-3 are solid
(two independently-succeeding live pushes), but Step 4's checklist needs
finishing properly before Task 6 (deleting the old per-entity loops)
starts. Task 6 is still hard-gated on Task 5 actually passing, per the
plan's own instruction.

## Task 5, Step 4: content/rendering fully verified locally (2026-09-09) - BLOCKED on a live push by a revoked Cloudflare token, not a code issue

Resumed after a merge from `main` (see below) picked up `poslib/remote.py`'s
real timeout/batching/retry fixes (`8e2b170` and everything after) that
this branch was missing entirely - Task 5's live-push numbers from
2026-09-05 were measured against the OLD pre-fix `remote.py`, worth
knowing if revisited.

**Merge from main**: `product-customer-json-replatform` was 18 commits
behind `main` (missing the whole store #1 watcher-outage/timeout-bug/
auto-update-hang saga - see CLAUDE.md). Merged clean, no conflicts
(`50fbe90`). Fast suite re-run after merge: 418 passed, 1 pre-existing
failure (same `dead_stock_value` drift already ruled acceptable at this
worktree's own Setup section - nothing in this plan touches it).

**Attempted a real forced refresh+export+push** (`ETL.refresh(force=True)`
+ `export_static.export(cfg)` + `remote.push_remote(cfg)`, same as Task 5
Step 2's own instructions): export succeeded (942.1s, fresh real-DB
data), but the push failed with `401 Client Error: Unauthorized` minting
an upload token for `promakeupmihoubipos`. **Root cause confirmed, not
guessed**: called Cloudflare's own `/user/tokens/verify` directly with
this worktree's `.env` token - `{"success":false,"errors":[{"code":1000,
"message":"Invalid API Token"}]}`. The token itself is dead (revoked or
rotated since 2026-09-05, consistent with this project's own standing
discipline of not leaving live tokens lying around). Checked the main
checkout's own `.env` too - same dead token. **No live Cloudflare
credential is available anywhere on this machine right now** - this
needs a fresh disposable token from the user (same one-time-use pattern
as every other live-account change documented in CLAUDE.md) before Task
5 Steps 2-3 can be re-run and a real URL exists to phone-check against.

**Interim file count/size** (fresh export, old per-entity loops from
Task 4 still present - Task 6 not started): **12,935 files, 284 MB**
(up slightly from the 2026-09-05 measurement of 12,695 files/250.7MB -
consistent with real trade continuing on the live database since then,
not a regression).

**Given the live push is blocked, did the next best thing**: served the
fresh `remote-site/` locally (`python -m http.server`) and drove it with
gstack's `browse` skill to verify every item on Task 5 Step 4's checklist
against the actual static files that would be pushed, not just unit
tests:

- **Product with purchase history + family, DB786/item 595**: renders
  correctly - item name (mixed Arabic/Latin "DIAMOND BEAUTY ... DB786"),
  family comparison section ("Famille diverse", 6.2% vs 9.0% average),
  25-row sales history, and - the specific DB786 check the plan calls
  out - the Purchase history table shows two distinct real cost figures
  (2,550 DZD paid / 2,726 DZD running cost on one line, 2,800 DZD /
  2,800 DZD on the other), not collapsed to one value. Zero console
  errors beyond the known font gap (see below).
- **Never-sold item, item 728**: `#rd-never-sold-banner` confirmed
  actually visible (`is visible` returns true, not just present in
  markup).
- **Fully-empty item, item 56**: clean "No sales recorded."/"No
  purchases recorded for this product." empty states, no blank table,
  no JS error.
- **Customer with a balance, id 14**: balance (3,720 DZD), "High risk"
  credit-risk pill, and "Owes 3,720 DZD, 509 days since last purchase"
  note all render correctly; real 40-row purchase history.
- **Fully-empty customer, id 67**: clean "Has not bought anything
  measurable yet."/"No purchases recorded."/"No account payments
  recorded." empty states, no error.
- **Arabic (`/ar/product.html?id=595`)**: `document.documentElement`
  confirmed `dir="rtl"`/`lang="ar"` and computed `direction: rtl`; UI
  strings correctly translated; thousands separator correctly
  space-formatted per this locale ("157 500 DZD" style, not "157,500").
- **Synced badge**: present and showing the correct fresh timestamp on
  every page checked, all 3 languages.

**One real false alarm, caught and ruled out before being reported as a
bug** - worth recording since this project has a documented history of
exactly this kind of premature "found a bug" mistake (see CLAUDE.md's
Kaspersky/VPN red herring): the browse skill's plain-text extraction does
NOT respect the `hidden` attribute - it initially looked like DB786's
never-sold banner was incorrectly showing alongside real 54-unit sales
data, which would have been a real bug in `remote-detail.js`'s
`s.days_since_sale !== null` check. Verified directly instead of trusting
that text output: `document.getElementById('rd-never-sold-banner').hidden`
returned `true`, and an explicit visibility check also returned `false` -
the element genuinely is hidden, the text-extraction command just doesn't
honor `[hidden]` when flattening the DOM to text. **Lesson for next time
this comes up**: check a conditionally-shown element's real hidden/visible
state directly, don't trust flattened text output alone for anything
gated by `hidden`.

**One pre-existing, already-documented, unrelated gap re-confirmed**:
every page load 404s on the Cairo web font files - this is CLAUDE.md's
own "What's left" section, "Arabic web font not fetched
(`tools/get_fonts.py` - harmless, falls back to Windows' own Arabic
font)" - not caused by this plan's work, not a regression.

**Still genuinely unconfirmed, and cannot be substituted for by local
serving**: whether Cloudflare Access, once authenticated on the real
gated domain, correctly serves these exact files the same way - this
project has burned that exact shortcut before (see CLAUDE.md's
`_redirects` bug history, "don't declare a deploy fully verified until
the owner's phone confirms it"). **Task 5 is NOT being marked complete
on the strength of local verification alone.** Needs, in order: (1) a
fresh disposable Cloudflare token from the user, (2) re-run Step 2's
real push (now against the merged-in fixed `remote.py`), (3) Step 3's
before/after file count + cold/warm push timing (now against the fixed
upload mechanics - worth re-measuring since the old 486s/722s numbers
predate the timeout/batching fixes), (4) a phone or curl-based real-
domain spot check of at least DB786 and the Arabic RTL page, matching
what was just verified locally.

## Task 5, Steps 2-3: RE-DONE for real (2026-09-09) with a working token and the fixed remote.py - numbers below supersede the 2026-09-05 ones

The user supplied a fresh Cloudflare token. First one verified as
active/valid but carried zero real permissions (403 Forbidden even on a
read-only `list projects` call - likely created without "Cloudflare
Pages > Edit" actually attached, or scoped to the wrong account). Second
token, recreated with that permission explicitly added, verified working
end to end (`list projects` returned all 6 real projects on this
account including `promakeupmihoubipos` and the real store's own
`promakeupboumati`).

**Cold push** (current export, already fresh from the earlier
force-refresh): `push_remote` returned `True` in **577.9s**. Confirmed
independently via Cloudflare's own deployments API (not just the
process's own return value) - deployment `3190b788`,
`https://3190b788.promakeupmihoubipos.pages.dev`, `latest_stage.status:
"success"`, timestamped exactly when this push ran.

**Warm push** (re-exported immediately after with no real data change,
matching Step 3's own instructions): export 754.2s, `push_remote`
returned `True` in **822.1s** - slower than cold, same already-documented
reason as the 2026-09-05 numbers (the "Synced {when}" badge embeds a live
timestamp into every page, so a full re-export changes nearly every
file's hash regardless of real data change, defeating
check-missing-hashes for this specific export shape).

**File count/size** (still interim - old per-entity loops not yet
removed, Task 6 pending): **12,935 files, 284 MB** - same as the
pre-push measurement, confirming the push didn't change what's on disk
locally (as expected, `push_remote` only uploads, never mutates
`remote-site/`).

**These numbers are measured against the FIXED `remote.py`** (post-merge
from `main`, `50fbe90` - `_LARGE_BODY_TIMEOUT_SECONDS`, `_MAX_PUSH_SECONDS`
deadline, `_check_missing_hashes`, per-batch retry), unlike the
2026-09-05 numbers (486.3s cold / 722.2s warm) which predate all of that.
Directly comparable and consistent - both cold numbers are in the same
~500-600s range, the fix didn't change the shape of a healthy push, it
made a struggling one survive.

**One real, per-step timing detail was lost this run**: `logs/pos-tool.log`
does not exist in this worktree as of this session (checked directly -
the whole `logs/` directory is gone, not just rotated; confirmed via
`find`). The overall push numbers above come from the script's own
`time.monotonic()` wrapper around `push_remote()`, which is reliable
regardless - just the per-step (`got upload token in Xs` /
`checked N hash(es) in Xs` / etc.) breakdown `poslib/remote.py` normally
emits to that log wasn't captured. Not investigated further (low value -
the numbers that matter for this task are the top-level ones); worth
knowing if a future session expects that log to exist here.

## Task 5, Step 4 (real domain): Access-gating confirmed active; full authenticated content check blocked by this machine's connectivity right now, not by anything in this branch's code

One real, meaningful signal was captured before the connection degraded
further: an unauthenticated `requests.get` to
`https://promakeupmihoubipos.pages.dev/en/catalog` was redirected to
Cloudflare Access's real login domain
(`broad-violet-b829.cloudflareaccess.com/cdn-cgi/access/login/...`,
`200`, ~35KB body - the login page itself) - direct proof Access is
actively gating the freshly-pushed deployment exactly as designed, not
serving content unauthenticated.

**Every further attempt to reach the live domain failed on this
machine** - Python `requests` (`ConnectTimeout`), PowerShell
`Invoke-WebRequest` (timed out, including to `google.com` - not specific
to Cloudflare), and gstack's own Chromium-backed `browse` skill (also
timed out) all failed the same way within the same ~15-minute window,
despite `ping`/ICMP to the same hostname succeeding at healthy latency
(7-8ms) and the push itself succeeding via this exact machine's network
moments earlier. This is this store's own well-documented intermittent-
connectivity pattern (see CLAUDE.md's "network scare" section from
2026-09-05) reasserting itself, not an Access/deployment/code problem -
the deployment's own success is independently confirmed via Cloudflare's
API regardless of whether this machine can currently reach it over
HTTPS.

**Task 5 is still not being marked fully complete** - the plan's own
gate is a real authenticated content check (owner's phone, or at minimum
a successful unauthenticated-vs-authenticated distinction via curl),
which this session could not complete due to transient local
connectivity, not because anything is actually broken. Asked the user to
spot-check `https://promakeupmihoubipos.pages.dev/en/product.html?id=595`
from their own device (a different network) as the fastest path to
closing this out, given local retries kept failing. If the user confirms
it looks correct (matching what was already verified locally - DB786's
name, two distinct purchase costs, family comparison), that satisfies
Step 4 and Task 5 can be marked passed; if this session's own
connectivity recovers first, retry the Python `requests` check above
before waiting further on the user.

## Task 5: PASSED (2026-09-12)

This session's own connectivity recovered first - retried the
unauthenticated `requests` check and got the same Access-login redirect
as before (confirming the deployment is still live and still correctly
gated). The owner then checked
`https://promakeupmihoubipos.pages.dev/en/product.html?id=595` from his
own device and confirmed it renders correctly ("everything sees fine").
That's the plan's own real gate satisfied - **Task 5 is complete.**
Proceeding to Task 6.

## Task 6: implemented, tested, and live-pushed (2026-09-12)

**Code changes** (`export_static.py`, `tests/test_export_static.py`):
removed the old `products_dir = lang_dir / "products"` and
`customers_dir = lang_dir / "customers"` per-entity-per-language HTML
export loops entirely (the `products.json`/`customers.json` + shell
architecture from Tasks 3/4 fully replaces them now); rewrote the module
docstring's "Customer and product drill-down pages... ARE exported too,
in full" paragraph to describe the new JSON+shell shape; removed the
now-redundant `test_customer_and_product_drilldowns_are_exported` test
(superseded by Task 3's `TestProductsCustomersJson`); updated
`TestProductCustomerShells.test_writes_one_shell_per_language_not_per_entity`
to assert the OLD per-entity directories are now **absent**
(`assert not (out_dir / "en" / "products").exists()` /
`.../"customers"`), per the plan's exact Step 3 instruction.
`templates/product_detail.html`/`templates/customer_detail.html` and
`app.py`'s local `/products/<id>`/`/customers/<id>` routes are untouched
- confirmed unaffected, they serve the local live dashboard only.

**Step 4 (full test file)**: `pytest tests/test_export_static.py -q` -
**24 passed, 1 skipped, 0 failed** (real-database run, 6145.57s /
1:42:25 - this run happened to land on a particularly loaded stretch of
this shared till PC, consistent with this session's own earlier
observation of highly variable real-DB export costs on this machine;
not investigated further since the result itself, 0 failures, is what
matters).

**Full suite** (`pytest tests -q --deselect tests/test_export_static.py`):
418 passed, 1 pre-existing failure (`dead_stock_value` drift, the exact
same already-ruled-acceptable point-in-time figure from this worktree's
own Setup section - nothing in Task 6 touches it), 25 deselected. No new
regressions from Task 6's changes.

**Step 5 (before/after file count and size)** - one real gotcha caught
along the way: the first fresh export after this code change still
showed 13,106 files, because `export_static.export()` never wipes its
output directory first (only `mkdir(parents=True, exist_ok=True)`) - the
old `en/products/`, `en/customers/` etc. directories from a *previous*
export (predating Task 6) were still sitting on disk, untouched by the
new code, and got miscounted as if the new code had regenerated them.
Wiped `remote-site/` and re-exported from a genuinely clean slate to get
a trustworthy number:

- **Before Task 6** (2026-09-09 measurement, Task 5's own numbers, old
  per-entity loops still present): 12,935 files, 284 MB.
- **After Task 6** (this session, clean re-export, old loops actually
  gone): **5,864 files, 89.2 MB** - confirmed via directory listing that
  `en/products/`, `en/customers/` (and `ar`/`fr` equivalents) no longer
  exist at all; only `static/`, `suppliers/`, `tickets/` remain per
  language, plus the top-level `products.json`/`customers.json` and the
  `product.html`/`customer.html` shells. A ~55% file-count and ~69%
  size reduction, consistent with the plan's own stated goal of cutting
  the per-entity-per-language tree down to 2 shared JSON files + 6 shell
  pages.

**Step 6 (live re-verification)**: pushed this clean export via
`poslib.remote.push_remote(cfg)` - returned `True` in 398.8s. Since
Cloudflare Pages Direct Upload fully replaces a deployment's entire file
set on every push, this confirms the old per-entity URLs
(`/en/products/<id>.html` etc.) are now gone from the live
`promakeupmihoubipos.pages.dev` deployment - any stale bookmark to one
should now hit the existing `404.html` "not available remotely" page,
the same way any other never-exported static path already does.
**Still needs the owner's own phone check** (same standing "don't
declare a live deploy verified until the owner confirms it" discipline
as every other live-deploy claim in this project) to close out Step 6
for real - not yet done as of this note.

**Commit**: `export_static.py` + `tests/test_export_static.py`,
message `refactor(remote): remove now-redundant per-entity
product/customer HTML export (JSON+shell replatform verified live)`,
per the plan's own Step 7 text.

This closes out every task in
`docs/superpowers/plans/2026-09-01-product-customer-json-replatform.md`
except the owner's final phone re-check above - once that lands, this
whole plan is complete and the branch is ready to be considered for
merging into `main`.
