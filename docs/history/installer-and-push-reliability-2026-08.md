> Moved verbatim out of CLAUDE.md on 2026-09-14 (docs/slim-claude-md). Nothing edited.

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

