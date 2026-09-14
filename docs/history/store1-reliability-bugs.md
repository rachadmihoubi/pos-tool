> Moved verbatim out of CLAUDE.md on 2026-09-14 (docs/slim-claude-md). Nothing edited.

## Store #1 watcher outage + timeout bug (2026-09-05) — one real bug fixed, one systemic gap found and NOT yet fixed

Picked up mid-way through the product/customer JSON-replatform plan
(`docs/superpowers/plans/2026-09-01-product-customer-json-replatform.md`,
Tasks 1-4 done on branch `product-customer-json-replatform` in
`.claude/worktrees/product-customer-json-replatform` — see that branch's
own SDD ledger for the full per-task detail) — Task 5 (live verification)
needed a real Cloudflare push, which surfaced a real, still-live
production incident on this same machine (confirmed via hostname to be
`DESKTOP-94UHGGD`, store #1 "Pro Makeup Boumati" per the machine-identity
table above): **the real store's dashboard (`promakeupboumati.pages.dev`)
had been stale since 2026-09-03**, silently, with nothing telling the
owner.

### Bug #1 — FIXED, committed to `main` (`8e2b170`): large uploads were truncated to a 10-second window, not 120

Root-caused by reading urllib3's own source (installed version 2.7.0):
in a `requests`/urllib3 `(connect, read)` timeout tuple, **the connect
half, not the read half, governs how long sending the request body is
allowed to take** — `HTTPConnectionPool._make_request` sets the socket
timeout to the connect value, writes the *entire* body under it, and
only swaps in the read timeout afterwards for the response. So the old
`_UPLOAD_TIMEOUT_SECONDS = (10, 120)` in `poslib/remote.py` actually meant
"finish sending this batch within 10 seconds" - the 120 was never
reached on a failing push. This explains the confusing pattern seen both
in the real store's log (`%LOCALAPPDATA%\Shop Analysis\logs\pos-tool.log`,
every push from 2026-09-03 07:xx onward failing identically during asset
upload) and in a disposable-project diagnostic built this session (sizes
from 1MB to 35MB all failed at a suspiciously *constant* ~10-15s,
regardless of size - the exact signature of a fixed-window cutoff, not a
bandwidth ceiling).

Fixed: a dedicated `_LARGE_BODY_TIMEOUT_SECONDS = (180, 180)` now covers
the three large-body Cloudflare calls (asset upload, check-missing,
upsert-hashes); `_MAX_BATCH_BYTES` dropped 40MB -> 8MB (wrangler's own
40MB assumes a developer-grade uplink, not a real store's connection).
Raising the timeout is safe specifically because `_force_ipv4_only()`
(already shipped 2026-08-29) means only IPv4 addresses are ever tried -
the multi-address stall the original short bound was defending against
cannot happen once AAAA records are never attempted. `tests/test_remote.py`
41/41 pass. **Deliberately committed to `main` directly, not the
replatform branch** - that plan's own Global Constraints mark
`poslib/remote.py`'s upload mechanics out of scope; the replatform branch
will pick this fix up via a future rebase/merge, not a duplicate fix.

### The network scare that turned out to be mostly a red herring - re-verify before trusting either reading

An extensive live diagnostic (dispatched to an Opus subagent, since the
network-vs-code question needed real experimentation, not more reading)
initially found a consistent ~9-10 KB/s upload ceiling to *both*
Cloudflare and an unrelated host (httpbin.org), with zero packet loss
anywhere (ping/pathping clean at every hop) - pointing at the Wi-Fi
extender's backhaul specifically (PC<->extender wireless hop flawless,
extender<->router hop carrying all the latency/jitter). Kaspersky was
fully quit (service confirmed `Stopped`, not just the tray app) and the
same slow reading persisted, ruling Kaspersky out.

**But a follow-up retest minutes later, prompted by the owner pointing
out a live speedtest showing 18 Mbps down / 33 Mbps up (utterly
inconsistent with ~10 KB/s), found healthy throughput instead**
(650-900 KB/s via plain Python `requests` to httpbin.org) - and **two
real live pushes to `promakeupmihoubipos` both succeeded** immediately
after (cold: 486.3s; warm, re-exported with zero data change seconds
later: 722.2s - slower, likely because the "Synced {when}" badge embeds
a live timestamp into every page, so nearly every file's hash changes on
every export regardless of real data change, defeating the
check-missing-hashes optimization for a full export - not a bug, just a
real limit on how much that optimization can help here).

**Net honest conclusion: the connection is real but intermittent, not
a fixed hardware fault** - the extender-backhaul theory may still be
correct as a *contributing* factor (worth trying an Ethernet cable to
the DSL router if this recurs, bypassing the extender entirely - owner
confirmed the extender is otherwise the only realistic option), but it
does not explain a 400x swing in measured throughput within the same
session. **Don't treat this as fully diagnosed** - if push failures
recur, re-run the same diagnostic (disposable Cloudflare project,
size-ladder upload test - see git history around this date for the
exact script) before assuming either the network or the timeout fix is
to blame; the honest state is "reproducibly worked twice after the code
fix, on a connection that tested badly once and well twice."

### Bug #2 - found, NOT fixed: the real watcher process died and nothing brought it back (this is the part that could hit any store)

Investigated *why* the real store's dashboard was stale for 2 full days,
per the owner's explicit ask to find root cause rather than just patch
the symptom - and the answer is a real, systemic architectural gap:

- This machine's last boot was 2026-09-03 07:14 - no reboot, no new
  logon since (confirmed via `Get-CimInstance Win32_OperatingSystem`).
  The `Shop Analysis - Watcher` scheduled task's `LastRunTime` matches
  that boot exactly - its trigger is `onlogon`, a **one-shot** trigger,
  not recurring.
- The real watcher's own log shows it working correctly for hours on
  2026-09-03 (cache rebuilding every ~10-15 min as real till activity
  happened, matching `watcher.py`'s `rebuild()`/`_run_remote_push()`
  cadence) - then the log simply **stops** at 16:08 that day. No
  `ShopAnalysis.exe`/`python` crash entry exists anywhere in the Windows
  Application event log near that time. No process is running now.
- Read `watcher.py`'s actual loop end-to-end: `rebuild()`,
  `_run_remote_push()`, `_run_digest()`, `_run_backup()` each have their
  own `except Exception` guard - good instincts already in the code. But
  **`Watcher.run()`'s outer loop (lines 236-282) only catches
  `KeyboardInterrupt`, and `main()` calls `watcher.run()` (line 323) with
  no catch-all around it at all.** A few seams are genuinely unguarded -
  e.g. `_digest_due()`/`_backup_due()`'s own condition checks are called
  directly in the loop body, outside any try/except (only the `_run_*`
  functions they gate are wrapped). If anything at all raises there, or
  anywhere else not already wrapped, the entire process dies with **zero
  visible evidence** - no dialog (this build is `console=False`, an
  already-known, previously-accepted gap - see the packaging component
  table above), no Windows Error Reporting entry (a clean Python
  exception exits normally; WER generally only catches native faults),
  nothing beyond whatever the logger already wrote before the fatal
  instant. This is exactly why the *specific* triggering line could not
  be identified this session - that unprovability is itself the finding,
  not a gap in the investigation.
- **And nothing restarts it.** The scheduled task is one-shot-per-logon
  only, with no "restart on failure" action and no independent recurring
  "is the watcher still alive? if not, start it" check. A till PC that
  stays logged in for days or weeks without a fresh logon (this one:
  still running since the 2026-09-03 boot, no logon since) has no
  self-healing path at all once the process dies once, for any reason.

**This is a store-agnostic gap, not something specific to this
machine's hardware/network** - the same silent-death-with-no-restart
exposure exists on every store's packaged install. Proposed permanent
fix (discussed with the owner, **not yet implemented** - paused here so
the owner could pick this back up on another PC):

1. Harden `Watcher.run()`'s loop with a real `except Exception` around
   each iteration's body (not just `KeyboardInterrupt`), so no single
   unexpected exception - wherever it comes from - can kill the whole
   process.
2. Make the Scheduled Task self-healing - either Windows Task
   Scheduler's native restart-on-failure settings, or a second, separate
   recurring trigger (e.g. every 5-15 minutes) that checks whether the
   watcher process is alive and starts it if not - the standard way to
   emulate a real Windows Service's auto-restart without converting the
   whole app into one.
3. Give the non-technical owner a visible signal when sync goes stale -
   e.g. the daily digest (already emailed) could include a "remote sync
   last succeeded N hours ago" warning line when it's overdue, so a
   silent failure becomes a noticed one instead of a multi-day surprise
   (this closes the gap the "Full-export push reliability fix" section
   above already flagged and left open: "Silently-failing remote pushes
   have no visible indication to the shop owner at all").

### Session state at pause - read this before continuing on another PC

- **`main` has 2 unpushed-as-of-this-writing commits**: `1abd0e3` (Synced
  badge, predates this session) and `8e2b170` (the timeout fix above).
  Check `git log origin/main..HEAD` before assuming these landed.
- **The replatform branch (`product-customer-json-replatform`) needed a
  new SDD ledger entry for Task 5's live-push results** - see that
  branch's own `.superpowers/sdd/2026-09-01-product-customer-json-replatform/progress.md`
  for the up-to-date detail (both pushes succeeded; the interim file
  count with the old per-entity loops still present was ~12,695 files /
  ~250.7MB; Task 6 - deleting those old loops - is still not started).
- **This worktree's own local ETL cache was 2 days stale** when the
  owner did the Task 5 phone check (the "Synced" badge reflects
  `cache.parsed_at` - when the *local* cache was last rebuilt - not push
  recency; nothing in a manually-driven dev worktree refreshes it
  automatically, unlike a real running watcher). A forced
  `ETL.refresh(force=True)` + fresh export + push was kicked off before
  this session paused - **check whether that actually completed and
  succeeded before assuming the live site reflects current data.**
- **Kaspersky was fully quit on this machine during network diagnosis
  and, as of this write-up, had NOT yet been confirmed turned back
  on** - check `Get-Service` for `Kaspersky Service 21.26` before
  assuming this production till PC is protected again.
- **The real store's own dashboard (`promakeupboumati`) was not
  re-pushed this session** - the timeout fix and the (probably
  transient) network improvement both suggest a retry would likely
  succeed now, but the watcher process itself is still dead and won't
  restart on its own (see Bug #2 above) - it needs either a manual
  restart of the scheduled task, a fresh logon, or the permanent fix
  above before this store's own sync resumes unattended.
- **The 3-part permanent fix for Bug #2 was proposed but explicitly not
  implemented** - the owner asked to commit/push and continue elsewhere
  before answering whether to proceed with it.

### Bug #1's fix, reviewed and shipped as v1.0.10 (2026-09-05, same day)

Per the new hard rule above ("Every real bug gets an opus-reviewer
root-cause pass and ships as a new setup.exe release"), Bug #1's fix
(`8e2b170`) was independently reviewed before being considered closed.
**Root cause CONFIRMED** against the actually-installed urllib3 2.7.0
source (read directly, not recalled) - the connect half of a
`(connect, read)` tuple really does govern the whole body-write, and
`connection.py`'s `sock.settimeout(self.timeout)` re-applies it on every
keep-alive reuse. **But the fix itself was INCOMPLETE**, missing a real
blocker:

- `_create_deployment` (the final step of every push - sends the full
  asset manifest, ~750-800KB for a real ~13,000-file export, as a single
  multipart write) was still on the old short timeout with **zero
  retry at all** - the single most expensive place in the whole push to
  fail, since it throws away an otherwise fully successful upload. Fixed:
  now uses the large-body timeout with its own retry/backoff.
- The IPv4-forcing safety comment was factually wrong - api.cloudflare.com
  resolves to **six** IPv4 A records, not one, and each one re-arms the
  full connect timeout in urllib3's connect loop. Corrected, and replaced
  with a real bound instead of relying on that reasoning: `_MAX_PUSH_SECONDS`,
  a wall-clock deadline (25 minutes, ~2x the slowest real successful push
  measured) threaded through every large-body call in `poslib/remote.py`,
  so a persistently slow connection can't chain `attempts x timeout` per
  call into an unbounded total - this also bounds the watcher's own
  worst-case unresponsiveness, which the review flagged as having grown
  ~16x under the original fix alone.
- `poslib/provision.py`'s own `push_remote` calls (a tiny placeholder site
  or the hub registry, never a full catalog) now pass a much smaller
  `_PROVISION_PUSH_MAX_SECONDS` (150s) override, and `main.py`'s
  provisioning-watchdog comment (`_PROVISIONING_TIMEOUT_SECONDS`) was
  corrected - the review found the original fix had silently invalidated
  that comment's arithmetic without anyone noticing.
- A real regression test now asserts all four large-body call sites use
  the large-body timeout (previously only implied, never actually
  checked - reverting any one of them back to the short timeout would
  have left every existing test green).

Fixed in `2391066`. `tests/test_remote.py` 46/46, `tests/test_provision.py`
+ `tests/test_main.py` 83/83, full fast suite 410/411 (the same
pre-existing, unrelated `dead_stock_value` drift).

**Shipped as `v1.0.10`** on GitHub Releases (`Setup.exe`, built from this
fix, `VERSION`/`packaging/setup.iss`'s `AppVersion` both bumped together) -
confirmed **v1.0.9 had already been published**, so every real store
install that auto-updates was still running the broken 10-second write
window until this release. `gh` had no stored credentials on this
machine; the owner ran `gh auth login` (browser device-code flow) to
unblock the actual publish. Not yet installed/verified on any real store -
per this file's own standing "don't declare a live deploy fixed until
confirmed against the real thing" discipline, the next real store update
(automatic, if `update.enabled: true`, or a manual reinstall) is what
actually confirms this.

## Bug #3 + Bug #4 — two more real auto-update hangs found live on store #1, both fixed, v1.0.14 shipped (2026-09-07 through 2026-09-09)

Continuing the same store #1 live-verification effort as Bug #1/#2 above.
With `update.enabled: true` now flipped on for real (the user's explicit
condition: "I won't be shipping this product to other stores unless I am
fully confident in its functionality"), the actual SYSTEM-context auto-
update path got its first real exercise - and hung, twice, for two
different root causes, each found, opus-reviewed, and fixed in turn. This
section was never written up before now (a context-compaction gap) - both
bugs are real, already shipped, and this is the first record of either in
this file.

### Bug #3 (fixed in `v1.0.11`) - `CloseApplications=force` hung negotiating with the still-running watcher

The real Updater task (`schtasks /run /tn "Shop Analysis - Updater"`)
launched a real `Setup.exe` under SYSTEM twice and both times hung with
zero progress for 45+ minutes - reproduced directly, then isolated with a
controlled A/B test (same cached `Setup.exe`, run manually: with the
watcher process running, hung ~45 min; with it killed first, completed in
under 2 seconds). Root cause: Inno Setup's `CloseApplications=force`
still negotiates with a running target process before force-closing it,
and that negotiation step itself can stall indefinitely with no UI to
resolve it under SYSTEM/Session 0. Fixed with
`poslib/updater.py::_close_other_running_instances()` - a `taskkill /F
/IM ShopAnalysis.exe /FI "PID ne <self>"` called by `launch_silent_install()`
right before spawning `Setup.exe`, so nothing is left running for
`CloseApplications` to negotiate with at all. Commit `fce05ba`.

**An opus-reviewer pass on this fix found the fix would unblock a second,
previously-unreachable bug**: because auto-update had never once
completed via SYSTEM before (every real attempt had hung on Bug #3
itself), the post-install steps that only run on a real success -
recreating the Watcher scheduled task, `CreateUpdaterTask`'s own
`--data-dir` argument - had never actually executed as SYSTEM and would
self-destruct on the very first real success: `{localappdata}` resolves
to SYSTEM's empty profile, so the *next* Watcher task would get created
running as SYSTEM instead of the real user, and the *next* Updater task
would get baked with a `--data-dir` pointing at nothing real. Fixed with
`skipifsilent` on the passive Watcher-task-recreate `[Run]` line (an
auto-update never needs to recreate an already-correct task) plus
`schtasks /run` instead of a direct `Exec` (always uses the task's own
configured principal, not the caller's), and the new `GetShopDataDir()`
helper (env-var-first, `{localappdata}`-fallback) used everywhere
`CreateUpdaterTask` previously assumed `{localappdata}` was correct.
Commit `5226539`.

**A third opus-reviewer pass, on THAT fix, found a third layer**: plain
`MsgBox()` in Pascal Script is not suppressed by `/SUPPRESSMSGBOXES` -
verified directly against Inno Setup's own source - so any of the
existing failure-path `MsgBox` calls inside `CreateUpdaterTask` (and one
informational one about the watcher-account mismatch) would still hang a
SYSTEM run invisibly if they ever fired. Fixed by converting those to
`SuppressibleMsgBox(..., IDOK)`, the watcher-account one additionally
gated behind `not WizardSilent()`. Commit `d50c1b2`.

All three layers shipped together as **`v1.0.11`**. Live-verified on this
store, eventually: the real `v1.0.11` Updater-task run launched at
2026-09-08 10:16:03, took several hours longer than expected to actually
finish (likely still working through a pre-fix cached download/retry
state, not re-diagnosed in detail since it did complete), but `VERSION`
at `C:\Program Files\Shop Analysis` did confirm `1.0.11` afterward - the
three-layer fix works for a real update that reaches completion.

### Bug #4 (fixed in `v1.0.14`) - the DB-locate wizard page's own MsgBox hung the very next update cycle

The very next real update cycle on this same store (v1.0.11 -> v1.0.13,
launched automatically at 2026-09-08 14:44:29) hung again, immediately -
Inno's own `/LOG=` file
(`%LOCALAPPDATA%\Shop Analysis\logs\setup-update.log`) shows Setup opening
at `14:44:29.731`, confirming `Administrative install mode: Yes`, then
at `14:44:29.853` - nine-tenths of a second later - logging
`Message box (OK): Please click Browse and select your point-of-sale
database file (it ends in .dblx) before continuing.` and nothing further.
**Root cause**: `ConfigIsConfigured()` and `WriteDatabaseConfig()`
predate `GetShopDataDir()` (built for Bug #3 above) and were never
migrated to it - they still resolved `{localappdata}` directly, which
under SYSTEM means "not configured" is always the answer, regardless of
whether the store has a perfectly real `config.yaml`. That un-skips
`DatabasePage` (`ShouldSkipPage`), and Inno's own silent-mode page walker
(`ClickThroughPages`) then hits that page's validation, finds the never-
populated `DatabaseEdit.Text` empty, and calls the same class of
unsuppressible `MsgBox` Bug #3's third layer had just fixed three other
instances of - just not this one, since it wasn't part of that earlier
audit.

**Real cost**: this store stayed on `v1.0.11` (never reached `v1.0.13`'s
real content), `update_attempted.txt` is now permanently stuck refusing
to retry the `v1.0.13` tag (by design - the marker exists specifically to
stop a genuinely broken release from looping forever), and the watcher -
force-killed by `_close_other_running_instances()` moments before the
hang - stayed dead for **~17 hours** until the next Windows logon
(2026-09-09 07:18) self-healed it via the Watcher task's own `onlogon`
trigger. This is Bug #2's still-open self-healing gap (see the "Store #1
watcher outage" section above) manifesting again through a different
trigger - worth remembering these are two separate problems (a hang that
kills the watcher, and nothing bringing a dead watcher back) that
compound each other.

**Fix, opus-reviewer-confirmed correct and complete** (verified directly
against Inno Setup's own source, not just plausibility - see the
review's own findings: `MsgBox`/`SuppressibleMsgBox` share one internal
handler that only suppresses when *both* `/SUPPRESSMSGBOXES` is present
*and* the call site opted in, so a plain `MsgBox` is genuinely
un-suppressible, and it was the only unsuppressible dialog class in this
entire installer): `ConfigIsConfigured()`, `WriteDatabaseConfig()`, and
the Cloudflare-provisioning data-dir/log paths now all route through
`GetShopDataDir()` (moved earlier in the file so it's defined before
first use). Every remaining plain `MsgBox(...)` anywhere in
`packaging/setup.iss` was converted to `SuppressibleMsgBox(..., IDOK)` -
defense in depth against this exact bug class recurring from a call site
nobody has audited yet, confirmed to change nothing for a real
interactive user (a normal install never passes `/SUPPRESSMSGBOXES`, so
both variants take the identical real-dialog code path). On the
reviewer's own recommendation, `ShouldSkipPage` also now skips
`DatabasePage` unconditionally under `WizardSilent()` rather than relying
solely on `ConfigIsConfigured()` succeeding - a silent run has no human
to ever fill that page in regardless, so this closes the entire hang/
abort class even against a future env-var-inheritance edge case this fix
didn't anticipate. Commits `1bb6370` (fix), `fe512e4` (version bump).

`update_attempted.txt` was deliberately left alone, not cleared -
clearing it would let this store retry the still-broken `v1.0.13` build
specifically, hanging identically again. Publishing a new tag
(`v1.0.14`) is what naturally un-sticks the retry logic
(`check_for_update`'s guard compares the exact tag string, not just
"is a newer version available").

**Shipped as `v1.0.14`.** `gh release create` hit this store's
documented intermittent-connectivity failure twice in a row (`wsarecv:
An existing connection was forcibly closed`, then a bare `connectex`
timeout) before succeeding on a third attempt - both release assets
(`Setup.exe`, `Setup.exe.sha256`) confirmed present via `gh release view`
afterward, per `c0a8e74`'s own "verify assets landed" discipline.
**Live-verified 2026-09-09, same day, once the user elevated the session
so the SYSTEM-owned Updater task could actually be triggered on demand**
(the exact same non-elevated-visibility artifact documented elsewhere in
this file blocked triggering it beforehand - `schtasks /run` from a
non-elevated session returns a plain "Access Denied", not "not found").
First trigger hit a transient `connect timeout=120` downloading
`Setup.exe` from `github.com` (this store's documented intermittent
connectivity, unrelated to the fix) - no tag gets burned by a download
failure, so a second `schtasks /run /tn "Shop Analysis - Updater"`
moments later re-downloaded and installed cleanly. Confirmed by direct
inspection, not just "no error": `setup-update.log` for this run has
**zero** `Message box` lines (down from the one that killed `v1.0.13`),
ends with `Process exit code: 0` on both the Watcher-task `/end` and
`/run` steps followed by a clean `Deinitializing Setup` / `Log closed`;
`C:\Program Files\Shop Analysis\VERSION` reads `1.0.14`; no
`C:\Windows\System32\config\systemprofile\...\Shop Analysis` directory
was created; the Watcher task's principal is still `Quick Tech`
(confirmed via `schtasks /query /v`, not SYSTEM); and the watcher process
itself came back up under a fresh PID immediately after. **Bug #4 is
closed** - the DB-locate wizard page hang cannot recur through the path
that caused it, and the broader `SuppressibleMsgBox` conversion covers
every other call site in the file the same way.

### A correction to the 2026-08-31 "Updater task silently failing" entry

The opus-reviewer pass on Bug #4 also re-examined the 2026-08-31 "Cross-
store hub auto-registration" section's claim that the old passive
`[Run]`-based Updater task creation was "silently failing on every fresh
install" (which motivated moving it into `CreateUpdaterTask`/
`ExecAndCaptureOutput`). Direct evidence from this session says that
diagnosis was **very likely wrong**: `Get-ScheduledTask`/`schtasks
/query` from a non-elevated session return "access denied" for a real,
existing SYSTEM-owned task - not "not found" - and this exact confusion
already independently recurred and got caught earlier in this same
session (see "Non-elevated session masking the Updater task's existence"
in the Bug #1/#2 section above). The `CreateUpdaterTask` rewrite itself
is not wrong and doesn't need reverting - it's genuinely better
(instrumented, visible failures) regardless of whether the original
failure was real - but if the *original* passive `[Run]` version is ever
reconsidered for some other reason, don't assume it was actually broken
without re-checking from an elevated session first.

## Two real bugs found live on store #1 after merging the replatform, both fixed and shipped (2026-09-14)

Right after the replatform above landed in a real release (v1.0.15), the
owner restarted store #1's till PC to watch the auto-update happen live
(per his own request, having just learned how the mechanism works) — and
that live exercise surfaced two more genuinely real production bugs, each
root-caused, opus-reviewed, and shipped the same session.

### Bug: `static/remote-detail.js` never bundled into the installer — fixed in v1.0.16

The replatform's own Task 2 added `static/remote-detail.js` (the
client-rendered product/customer shell's JS) back on 2026-09-01, but
`packaging/pos-tool.spec`'s explicit `datas` allowlist was never updated
to bundle it — only `static/style.css` was. Invisible until v1.0.15
actually shipped code that depends on the file at export time: every
real watcher export/push attempt on store #1 crashed identically with
`FileNotFoundError` at `export_static.py`'s `shutil.copy2` call, visible
directly in `%LOCALAPPDATA%\Shop Analysis\logs\pos-tool.log`. Slipped
past the whole test suite because pytest always runs in dev mode, where
`PROJECT_ROOT` (`poslib.paths.app_root()`) resolves to the real source
tree — this failure mode only exists in a frozen build, which no
existing test exercised. Fixed by adding the missing `datas` entry, plus
a new fast regression test (`tests/test_packaging_spec.py`) that
statically cross-checks every `static/` file `export_static.py`
`copy2`'s (and every file a template references via
`url_for('static', ...)`, the other real consumer of `static/` in a
frozen build) against the spec's bundle list — no real PyInstaller build
needed, so this class of gap is now caught by the normal test suite.
Opus-reviewer-confirmed correct and complete (checked broadly for any
OTHER similarly-missing file — none found). Shipped as `v1.0.16`.

### Bug #2's proposed fix, finally built and shipped — v1.0.17

CLAUDE.md's own "Store #1 watcher outage + timeout bug" section
(2026-09-05) had proposed a 3-part fix for Bug #2 (the watcher dying
silently with nothing to bring it back) but explicitly left it
unimplemented. Built this session, all 3 parts:

1. **`Watcher.run()`'s main loop hardened** — every pass now runs
   through a new `_safe_loop_iteration()` wrapper catching any
   `Exception` (never `BaseException` — `KeyboardInterrupt` still
   propagates correctly, checked explicitly) and logging+continuing
   instead of letting a single unexpected exception kill the whole
   process silently.
2. **A heartbeat file + self-healing "Shop Analysis - Watchdog"
   scheduled task**, recurring every 10 minutes, restarting the Watcher
   task via `schtasks /run` if the heartbeat has gone stale (50 min —
   raised from an initial 15 min after review found a real busy push can
   take ~40-45 min) and Task Scheduler itself confirms the task isn't
   already running.
3. **A "remote sync last succeeded N hours ago" warning** in the daily
   digest when `remote.enabled` and the last successful push is older
   than `remote.stale_warning_hours` (default 24) — never warns before
   the very first successful push.

**Two full opus-reviewer passes, the first of which caught a bug that
would have made the entire fix a silent no-op on this exact store**:
`_watcher_task_is_running()` originally parsed `schtasks /query`'s own
text output for an English "Status: Running"/"Ready" line — but schtasks
localizes that VALUE to the OS display language, and store #1's till PC
runs **French Windows** (`Statut: En cours`/`Prêt`). Proven live on this
machine, not just plausible. Fixed by switching to PowerShell's
`(Get-ScheduledTask -TaskName '...').State.ToString()`, independently
re-verified live to return a culture-invariant `"Running"`/`"Ready"`
regardless of OS language. The same review pass also caught: the new
Watchdog task's `[Run]`-section creation in `packaging/setup.iss` was
correctly `skipifsilent` (to avoid a silent auto-update baking SYSTEM as
its principal) but that meant it would **never** be created on any
already-installed store, only a fresh interactive install — fixed by
having the watcher itself self-heal the task's existence at every
startup instead (`watcher.py::_ensure_watchdog_task_exists`, idempotent,
frozen builds only); and a missing `CREATE_NO_WINDOW` that would have
flashed a console window on the till screen every 10 minutes — fixed via
a shared `_run_hidden()` helper (which also fixed a `UnicodeDecodeError`
risk on schtasks' OEM-codepage output). A second, smaller review pass
caught that a real auto-update in progress (has taken multiple hours on
this store before) could have the new Watchdog restart the watcher
mid-install, reintroducing Bug #3's own file-lock hang class — fixed
with an `update_in_progress` marker (`poslib/updater.py`, written before
`Popen`, cleared immediately at the next real watcher startup, 10h
backstop expiry) that `ensure_watcher_running()` checks first.

**Fully live-verified, 2026-09-14, on the real store**: the owner
restarted store #1's till PC specifically to watch this happen —
`v1.0.16`'s auto-update applied cleanly (confirmed via
`C:\Program Files\Shop Analysis\VERSION` and a clean `setup-update.log`,
zero `Message box` lines), which then surfaced the `remote-detail.js`
crash live; `v1.0.17` (containing both fixes) was built, opus-reviewed
twice, shipped, and auto-updated onto the same till PC on its own next
Updater cycle — owner-confirmed: the `v1.0.17` version badge is showing
on the live remote dashboard, and the remote push (previously crashing
on every attempt) is working again. Both bugs are closed per this file's
own hard rule — root-caused, opus-reviewed, and shipped as a real
`Setup.exe` release, not just a commit on `main`.

