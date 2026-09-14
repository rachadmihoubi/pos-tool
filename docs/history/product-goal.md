> Moved verbatim out of CLAUDE.md on 2026-09-14 (docs/slim-claude-md). Nothing edited.

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
`packaging/setup.iss`, or `main.py`'s `--apply-update` dispatch. **Component
5 is now fully done** — both halves: the hub (live at
`https://promakeupmihoubi-hub.pages.dev/`, phone-verified 2026-08-28 per
`docs/superpowers/plans/2026-08-27-component5-hub-page.md`) and the
installer-driven Cloudflare provisioning flow (verified end-to-end on a
real till PC with real UAC elevation, 2026-08-29, see "Component 5
installer provisioning — SDD progress" above). **All 5 components of the
customer-distribution build are now done.** Weighted-average cost (the
feature the owner explicitly sequenced after Component 5) is also already
built — see "Weighted-average cost (AVCO) + last purchase cost" below.

