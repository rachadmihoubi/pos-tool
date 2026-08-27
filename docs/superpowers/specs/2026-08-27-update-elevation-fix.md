# Component 3 follow-up: fixing the update elevation gap

Date: 2026-08-27
Status: built and real-machine verified this session (see "Real-machine
verification" at the bottom)

## The gap this closes

`docs/superpowers/specs/2026-08-26-component3-auto-update-design.md` built
the full check/download/verify/install flow, but it runs inside the same
process as the regular watcher - and that watcher's scheduled task
("Shop Analysis - Watcher") was deliberately made to run de-elevated
(`/rl limited`, commit `10deb48`) so the common case (creating the task at
all) works without fighting UAC every login. That leaves no admin rights
available at the one moment the flow actually needs them: launching
`Setup.exe`, which writes into `{autopf}\Shop Analysis` (Program Files).
Unattended, a de-elevated launch either fails outright or stalls on a UAC
prompt nobody is there to click - this is why `update.enabled` currently
defaults to `false` everywhere (`config.template.yaml`, `poslib/updater.py`)
and CLAUDE.md marks Component 3 "do not ship to a real store PC."

Two cheap fixes were already tried and rejected (see CLAUDE.md's component
table): a per-user install, and a `/CURRENTUSER` override on just the update
run. Both either break Component 2's already-verified scheduled-task
creation or install a second, parallel copy of the app.

## Decision: a second, narrow, always-elevated helper task

Split the one job in two, by privilege, the same way Chrome/Edge separate
their unprivileged browser process from a privileged updater service:

- **"Shop Analysis - Watcher"** (existing, unchanged): stays de-elevated,
  keeps doing everything that doesn't need admin rights - DB parsing,
  digest/backup, Cloudflare push. No longer touches update-checking at all.
- **"Shop Analysis - Updater"** (new): a second scheduled task created at
  install time (while `Setup.exe` is already running elevated, so creating
  it costs nothing extra), configured to run as **`SYSTEM`** with
  `/rl highest`, triggered `/sc onlogon` (fires once per login/reboot, same
  cadence the design doc already accepted as "good enough"). Its whole job
  is one call: `ShopAnalysis.exe --apply-update --data-dir "<path>"`, which
  runs the existing, unchanged `check_and_apply_update()`.

### Why SYSTEM, and why this needs no UAC prompt at all

A task registered to run as `SYSTEM` never goes through interactive UAC
consent - there is no user token being elevated, so there is no consent
dialog to show or fail to click. This is exactly the mechanism Windows'
own built-in `SilentCleanup` scheduled task relies on (`RunLevel=Highest`,
runs as SYSTEM, any standard user can trigger it via
`schtasks /run` with no prompt). It also needs no stored password -
`/ru SYSTEM` never prompts `schtasks /create` for one, unlike a real user
account would.

Creating a `/ru SYSTEM` task itself requires admin rights, which
`Setup.exe` already has - no different from the existing watcher-task
creation that commit `10deb48` found requires elevation. No new admin
requirement is introduced at install time; it only fixes what happens
*after* install, unattended, with nobody at the keyboard.

### Why the whole flow runs elevated, not just the final `Setup.exe` launch

An earlier version of this idea downloaded the installer with the
de-elevated watcher and only handed the *launch* step to an elevated
helper, via a shared folder both processes could reach. That was rejected:
making a folder writable by a standard (non-admin) user so a `SYSTEM` task
can later read from it creates a local-privilege-escalation hole - any
other standard-user process on the machine could swap in its own `.exe` and
matching checksum before the elevated task runs it, turning a normal user
account into SYSTEM. Nothing here needs that risk: `check_and_apply_update()`
already does its own GitHub check, its own download, its own SHA256
verification, and its own install-launch as one self-contained call, entirely
using `tempfile.mkdtemp()` (which, run as SYSTEM, lands somewhere only
SYSTEM/admins can reach - not a shared folder at all). So the elevated
helper just runs that whole existing function itself, unchanged, and no
process ever has to trust a file another process wrote.

### The one new problem this creates: SYSTEM's `%LOCALAPPDATA%` is not the shop's

`poslib/paths.py`'s `user_data_dir()` resolves `%LOCALAPPDATA%` from the
*calling process's own* environment. Under the normal watcher (running as
the shop's own Windows user), that's correct - it's where `config.yaml`,
`cache.db`, and everything else actually live. Under `SYSTEM`, `%LOCALAPPDATA%`
resolves to `SYSTEM`'s own profile
(`C:\Windows\System32\config\systemprofile\AppData\Local`), which has no
`config.yaml` at all - `update.enabled`/`update.github_repo` would never be
found, and the elevated task would silently no-op forever.

Fixed the same way `setup.iss`'s existing `WriteDatabaseConfig` already
solves an identical problem: `Setup.exe` captures `{localappdata}` of the
*installing* user (the one real daily user this single-till-PC assumption,
already documented in Component 2, depends on) at install time, and bakes
that literal path into the new task's command line as `--data-dir`. A new
`SHOP_ANALYSIS_DATA_DIR` environment-variable override in
`poslib/paths.py::user_data_dir()` - checked before the existing frozen/dev
branches, set by `main.py`'s new `--apply-update` handler from `--data-dir`
- makes every path the elevated process resolves (`config.yaml`, `cache.db`,
the log file) point at the real shop data instead of SYSTEM's empty profile.
No parameter-threading through `Config`/`ETL`/etc. call chains needed - one
override, one place, matching `paths.py`'s own stated goal of "exactly one
frozen/dev branch, not one per file."

### What happens to the file-in-use conflict

Both scheduled tasks run the same `ShopAnalysis.exe`. When the elevated
helper's `check_and_apply_update()` launches `Setup.exe /VERYSILENT`, that
installer's existing `CloseApplicationsFilter=ShopAnalysis.exe` +
`CloseApplications=force` (already in `setup.iss` from the original
Component 3 build) force-closes *any* running `ShopAnalysis.exe` process by
image name before copying files - this already had to cover the regular
watcher and a manually-launched dashboard instance, so it transfers
unchanged to also covering the short-lived elevated-helper process itself.
No new self-stop choreography is needed on the elevated side beyond what
`check_and_apply_update()` already does (launch the installer, return,
let the caller exit).

## What is NOT changing

- `check_and_apply_update()`, `check_for_update()`, `download_and_verify()`,
  `launch_silent_install()` in `poslib/updater.py` - all unchanged. This is
  purely about *which process, running as what account*, calls the existing
  function.
- The existing "Shop Analysis - Watcher" task's `/rl limited` - still
  correct, per commit `10deb48`.
- `update.enabled` stays defaulted to `false` in both
  `config.template.yaml` and `poslib/updater.py`'s own fail-safe default.
  This fix removes the *reason* it was turned off, but flipping it on for
  a real store still deserves the human-at-the-keyboard verification the
  original Component 3 status row already called for (watching a real
  silent install actually happen with no dialog, and confirming the
  already-up-to-date no-op path). Not done as part of this change.
- Known gap #2 (no persisted "last version attempted" marker, so a
  mis-cut release could loop) - separate, smaller, still not built.

## Testing plan

- Unit tests for `poslib/paths.py::user_data_dir()`'s new env-var override
  (present vs. absent, frozen vs. dev).
- Unit tests for `main.py`'s new `--apply-update` dispatch: sets the env
  var from `--data-dir` before loading config, calls
  `check_and_apply_update()` exactly once, never raises on a `ConfigError`.
- `tests/test_watcher_update.py` (tests for the now-removed
  `Watcher._check_for_update`) deleted - that responsibility no longer
  lives on `Watcher` at all.
- `packaging/setup.iss` changes are Inno Setup script, not Python - not
  unit-testable. Same as Component 2's residual gap, real verification
  (does the SYSTEM task actually get created, does `schtasks /run` on it
  actually launch with no prompt, does `--data-dir` actually resolve to the
  real config) needs a human at the keyboard on a real Windows machine, not
  claimed here as done from a code read alone.

## Real-machine verification (2026-08-27, this machine - the actual shop
till PC, not a disposable dev box)

Rather than building the full PyInstaller/Inno Setup installer (Inno Setup
isn't currently installed on this machine, and installing new dev tooling
onto a production till PC is its own decision worth a separate ask), the
core claim - a `SYSTEM`/`/rl highest` scheduled task runs elevated with no
UAC prompt, and `--data-dir` correctly redirects `poslib/paths.py` to the
real config - was verified directly against the same code path (`main.py
--apply-update`) run via the dev virtualenv's Python instead of a frozen
exe. This exercises identical logic - `is_frozen()` only affects whether
`check_for_update()` makes a network call, not the config/path resolution
being tested.

Because creating a `/ru SYSTEM` task itself requires admin rights (same
finding as commit `10deb48`), and this session's own tool shell was not
elevated (confirmed via `net session`), the owner ran the commands
themselves in a separately-opened elevated PowerShell window - this
session's shell tools are a distinct, non-elevated process even when an
elevated terminal is open on screen, so commands had to be handed over
rather than run directly.

Steps taken and results:

1. Created a uniquely-named test task (`Shop Analysis - Updater TEST`, not
   the real task name, to avoid any collision) via
   `schtasks --% /create /f /tn "Shop Analysis - Updater TEST" /tr "\"<venv
   python>\" \"<repo>\main.py\" --apply-update --data-dir \"<repo>\""
   /sc onlogon /rl highest /ru SYSTEM` (the `--%` stop-parsing token was
   needed because PowerShell's own quote handling otherwise mangles a
   command line containing embedded escaped quotes - `schtasks.exe` itself
   expects the classic Win32 `\"`-escaped convention, same as
   `packaging/setup.iss`'s `[Run]` Parameters already use). Succeeded.
2. `schtasks /query /tn "Shop Analysis - Updater TEST" /v /fo list`
   confirmed `Exécuter en tant qu'utilisateur: Système` and
   `Type de planification: À l'ouverture de session` - the task was created
   exactly as designed.
3. `schtasks /run /tn "Shop Analysis - Updater TEST"` - the owner watched
   the screen and confirmed **no UAC dialog, no visible window** appeared
   at all.
4. The expected log line (`check_for_update()`'s "Not a frozen build -
   skipping update check.") did not appear in `logs/pos-tool.log` - not a
   failure, but a reminder that message logs at DEBUG while this config's
   `logging.level` is INFO, so it's correctly filtered. Exit code is the
   real signal here, not that specific line.
5. Re-querying showed `Dernier résultat: 0` (Last Result: 0, success).
   Since `main._apply_update()` returns 1 (via `sys.exit`) on a
   `ConfigError`, a `0` here specifically proves `get_config()` succeeded
   under the `--data-dir` override reading the real `config.yaml` - not
   just that *some* process launched.
6. Deleted the test task
   (`schtasks /delete /f /tn "Shop Analysis - Updater TEST"`); confirmed
   gone via a subsequent query returning "file not found". The real
   watcher's own log activity (Cloudflare pushes at 09:52 and 09:56/10:01)
   continued undisturbed throughout - this test never touched the DB, the
   live Cloudflare project, or the production scheduled tasks.

Net result: the elevation mechanism (SYSTEM task -> no UAC -> correct
config resolution via `--data-dir`) is now verified on real hardware, not
just unit-tested. Still not verified: an actual frozen-build silent
install end-to-end (that part's underlying download/verify/checksum logic
was already verified in the prior Component 3 session against a real
throwaway GitHub release - see the original design doc - so this was
intentionally scoped to just the new elevation mechanism, not a re-test of
already-proven logic).
