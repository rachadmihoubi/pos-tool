> Moved verbatim out of CLAUDE.md on 2026-09-14 (docs/slim-claude-md). Nothing edited.

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

