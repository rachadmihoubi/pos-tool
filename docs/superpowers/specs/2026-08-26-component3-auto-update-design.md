# Component 3: silent auto-update via GitHub Releases

Date: 2026-08-26
Status: approved design, not yet built

## Why

This fleshes out Component 3 from
`docs/superpowers/specs/2026-08-25-installer-updates-multistore-design.md`,
which describes the mechanism at a high level ("check GitHub Releases once
at startup, download, install unattended, restart the watcher") but leaves
several implementation questions open: how the running app knows its own
version, how a locked `.exe` gets replaced while it's the process doing the
replacing, how a download running with nobody watching gets verified before
it's executed, and how releases actually get published. Those are decided
here.

Components 1 (packaging), 2 (DB auto-detect + watcher auto-start), and 4
(Cloudflare credential scope) are done. This is next in the build order.

## Scope

**In scope:**
- A version file the running app can read, and a comparison against
  GitHub's latest Release.
- Downloading and verifying (SHA256) the new installer before running it.
- Running it silently and getting the watcher back up afterward, without a
  human ever seeing a dialog.
- A script for rachad to actually cut a release (version bump, build,
  checksum, publish) — needed to ship any update at all, and to exercise
  this feature repeatably during testing.

**Out of scope (unchanged from the master spec):**
- No license/version gating, no staged rollouts, no rollback mechanism
  beyond "don't touch prior files if the silent install fails."
- No retry loop within a single run — one check per watcher startup, next
  chance is the next login. Store PCs restart roughly daily, so this is
  frequent enough.
- No code signing / Authenticode. Not addressed here; the SHA256 checksum
  guards against a corrupted or tampered-in-transit download, not against a
  compromised GitHub account publishing a bad release under a valid
  checksum. Revisit only if that threat model becomes real.

## Design

### 1. Version tracking & the check

- A plain-text `VERSION` file at the repo root (e.g. `1.0.3`), bundled into
  the PyInstaller build as a data file. The running exe reads this directly
  — no registry lookups, no parsing Inno's own installed-version metadata.
- At the very top of `Watcher.run()`, **before** the file observer starts
  and before the first `rebuild()` call, the watcher makes one
  unauthenticated `GET
  https://api.github.com/repos/rachadmihoubi/pos-tool/releases/latest`,
  reads `tag_name` (`vX.Y.Z`), strips the `v`, and compares it to the local
  `VERSION` as a `(major, minor, patch)` tuple. Only a strictly greater
  remote version triggers an update — this is downgrade-safe.
- Placing the check first, ahead of anything that touches the database or
  cache, is what guarantees it never overlaps a `rebuild()` in progress —
  there is no code path where both run at once.
- No GitHub token — the repo is public (verified: `gh repo view` →
  `"visibility":"PUBLIC"`), consistent with Component 4's decision to avoid
  shipping any credential to a customer PC that doesn't need one.
- No retry within the run: a failed check (offline, GitHub down,
  rate-limited) logs and moves on. Next attempt is the next login.

### 2. Download, verify, install

- On finding a newer version, the watcher downloads two release assets to a
  temp folder: `Setup.exe` and `Setup.exe.sha256`.
- Computes the downloaded file's SHA256 and compares it to the published
  hash. Mismatch → log an error, delete the file, abort. No install is
  attempted; retried at the next login like any other failure.
- Match → spawns `Setup.exe /VERYSILENT /NORESTART /SUPPRESSMSGBOXES` as a
  **detached** subprocess (not waited on), then immediately calls
  `self.stop()` and returns, letting the current Python process exit. This
  releases `ShopAnalysis.exe`'s own file lock so Inno Setup can overwrite
  it moments later.
- As a safety net in case the old process hasn't fully released the file by
  the time Inno starts copying, `packaging/setup.iss`'s `[Setup]` section
  adds `CloseApplicationsFilter` covering `ShopAnalysis.exe` and
  `CloseApplications=force`.
- `setup.iss` gains one more unconditional `[Run]` line — no
  `skipifsilent` — that launches `{app}\ShopAnalysis.exe --watcher` right
  after install completes, so the new version's watcher is back up within
  seconds of the silent install finishing rather than waiting for the next
  login. The existing `schtasks /create ... /sc onlogon` line is untouched
  and keeps firing at future logins as before (idempotent re-create).
- Net effect: once a day, at login, there's roughly a 10–60 second gap
  (download + install time) where nothing is actively watching the
  database before the updated watcher takes over. Acceptable: the cache
  already holds the prior data, and `rebuild()`'s existing 60s min-gap /
  120s poll cadence already tolerates gaps this size.

### 3. Release publishing

- New script: `packaging/publish_release.py`, run by hand on this dev PC.
  1. Bumps `VERSION` (patch bump by default, or an explicit version via
     arg).
  2. Runs the existing PyInstaller (`--onedir`) and Inno Setup build steps
     (the same ones Component 1 already established) to produce
     `dist-installer/Setup.exe`.
  3. Computes `Setup.exe`'s SHA256, writes `dist-installer/Setup.exe.sha256`.
  4. Runs `gh release create vX.Y.Z dist-installer/Setup.exe
     dist-installer/Setup.exe.sha256 --generate-notes`.
- Replaces a multi-step manual process that's easy to get wrong — in
  particular, forgetting the checksum file would silently break the update
  check for every installed store, not just fail loudly.

### 4. Failure handling (summary)

| Situation | Behavior |
|---|---|
| No internet / GitHub unreachable | Log, skip, retry next login |
| Checksum mismatch | Log, discard download, skip, retry next login |
| Installer exits non-zero | Log exit code; prior install files are untouched (Inno doesn't partially apply on failure), so the old version keeps running via the relaunch `[Run]` line |
| Already up to date | Silent no-op (debug-level log only) |

## Testing plan

- Bump a dummy `VERSION`, publish a real GitHub Release via
  `publish_release.py` pointed at a throwaway tag, confirm a running
  watcher on this dev PC detects it, downloads, verifies the checksum,
  silently installs, and the watcher is back up within the expected
  window.
- Corrupt the published checksum file deliberately; confirm the watcher
  refuses to run the installer and logs the mismatch instead of silently
  proceeding.
- Kill network access mid-check; confirm the watcher logs and continues
  normally (no crash, no retry storm).
- Confirm the version-check-before-observer-start ordering actually
  prevents any overlap with `rebuild()` by reading the code path, not just
  by not observing a failure in one manual test.
- Confirm `CloseApplicationsFilter`/`CloseApplications=force` in
  `setup.iss` doesn't produce a visible dialog or prompt during a
  `/VERYSILENT` run.
