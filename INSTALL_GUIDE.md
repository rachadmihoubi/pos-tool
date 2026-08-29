# Installing Shop Analysis on a new store's PC

This is the real, permanent-install guide — for putting the tool on an
actual store's till computer for the first time. It is **not** the
developer setup guide (that's `SETUP.md`, for setting up this repo on a
machine you'll keep coding on) and it is **not** a disposable test run.

Read this end to end once before starting on a real store; the Cloudflare
step in particular is easy to get wrong in a way that's annoying to undo.

---

## Who does what

- **You (rachad)** run this guide, once per new store, sitting at (or
  remoted into) that store's own PC.
- **The store owner** does nothing technical — they never see a terminal,
  never type a config value. The only thing that involves them is telling
  you their email address if it's ever different from the one already
  baked into this guide.
- **The store's till worker(s)** never see the installer at all. Once
  installed, the tool runs invisibly in the background.

---

## Before you start

- **This is only for a genuinely new store's PC.** Don't run this on the
  dev PC or on store #1's own till PC — both of those already have their
  own setup (a plain `git clone` + `install-startup.bat`, not this
  installer) and are explicitly excluded from the customer rollout. See
  `CLAUDE.md`'s "Customer distribution" section if you need the reasoning.
- Know where the store's POS database file (`.dblx`) lives on that PC. The
  installer can usually auto-detect it, but it's faster if you already
  know the path.
- Have the store owner's email address ready — this is the **only**
  account that will ever be able to view that store's dashboard remotely.
  Double-check the spelling; a typo here means the owner can't log in and
  you'll have to fix the Cloudflare Access policy by hand afterward.

---

## Step 1 — Get `Setup.exe` onto the store's PC

If you don't already have a built copy, build one on your dev PC:

```powershell
cd path\to\pos-tool
.venv\Scripts\pip install -r requirements-build.txt
.venv\Scripts\pyinstaller packaging\pos-tool.spec --noconfirm --distpath dist --workpath build
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\setup.iss
```

(Path to `ISCC.exe` varies by machine — check
`%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe` too.) The result is
`dist-installer\Setup.exe` — a single, self-contained file. Copy that one
file to the store's PC (USB stick, network share, email, whatever's
convenient). **The store's PC needs nothing pre-installed** — no Python,
no Git, nothing. That's the entire point of packaging it.

---

## Step 2 — Create the one-time Cloudflare provisioning token

This is a real, powerful credential, used exactly once during this
install and then thrown away. Create a fresh one for every store — never
reuse one across stores, never leave one lying around afterward.

1. Go to `dash.cloudflare.com` → your profile avatar (top right) → **API
   Tokens** → **Create Token**.
2. Choose **Create Custom Token** (not one of the ready-made templates —
   the templates can't grant the `User API Tokens` permission below).
3. Name it something you'll recognize, e.g. `provision - <store name>`.
4. Under **Permissions**, add all three rows:
   - `Account` → `Cloudflare Pages` → `Edit`
   - `Account` → `Access: Apps and Policies` → `Edit`
   - `Account` → `User API Tokens` → `Edit`
5. Under **Account Resources**, choose **Include → Specific account** and
   pick the real account (not "All accounts"). Confirm the account ID
   shown matches `9ced76fef875048f0517d0bf2fe7d43f`.
6. **Continue to summary** → **Create Token**, and copy the token. You'll
   paste it once into the installer in a moment; it is never written to
   disk anywhere, never logged, and you should revoke it the moment the
   install finishes (Step 5 below).

---

## Step 3 — Run the installer

On the store's PC, run `Setup.exe` and approve the UAC prompt (it needs
admin rights to register the scheduled tasks). Click through the normal
pages — destination folder is fine left at default.

**Database detection page:** let it auto-detect the `.dblx` file, or
Browse to it if auto-detect doesn't find it. This never writes to the
source file — it's always copied read-only before anything touches it.

**Cloudflare remote setup page** — this is the one that matters. Fill in:

| Field | What to put |
|---|---|
| One-time provisioning token | the token from Step 2 |
| Cloudflare account ID | `9ced76fef875048f0517d0bf2fe7d43f` |
| New store project name | a permanent, unique, lowercase-hyphenated slug — see naming below |
| Owner's email | the real store owner's email, exactly as they'd log in with |

**Naming the project:** this becomes the store's permanent public URL
(`<name>.pages.dev`) and its permanent identity in `hub-site/stores.json`
— pick something you won't want to change later, e.g. the store's own
name in slug form (`storeb-pos`, `boutique-x`). Lowercase letters, digits,
and hyphens only, must start and end with a letter or digit.

Leave the token field **blank** if you're just repeating an ordinary
install/update on a store that's already provisioned — filling it in only
matters for a store's very first setup.

Click through to **Install**. The provisioning step happens automatically
near the end and can take a minute or two (it's talking to Cloudflare's
API several times and waiting for a login gate to propagate) — there's a
status message while it runs. It does **not** pop up a success dialog; it
only shows a message box if something went wrong.

---

## Step 4 — Verify it actually worked

Read the log it just wrote:

```
%LOCALAPPDATA%\Shop Analysis\cloudflare_provision_log.txt
```

It should say `Cloudflare setup finished:` followed by the store's live
URL. If it instead says `Cloudflare setup did not finish`, read the error
message — it's written to explain what to check, not just that something
broke. Re-running the installer with the same token is usually enough to
retry a step that failed transiently; provisioning is idempotent except
for the token-minting step (a second attempt with the same store name will
refuse rather than mint a second watcher token — that's intentional).

Then confirm from a browser (or `curl`):

- `https://<project-name>.pages.dev/` should redirect to a Cloudflare
  Access login page, and only the owner's email should be able to sign in.
- The `stock-<token>.json` URL printed in the same log line should load
  directly with no login — this is what the cross-store hub uses.

Also worth a glance: `schtasks /query` should show `Shop Analysis -
Watcher` (runs as the account you installed under) and `Shop Analysis -
Updater` (runs as `SYSTEM`) both present.

---

## Step 5 — Revoke the one-time token

Back in the Cloudflare dashboard → **My Profile → API Tokens**, delete the
token you created in Step 2. Its job is done; there's no reason for it to
keep existing. The narrow token the installer minted for the watcher's
ongoing use is a completely separate, much less powerful credential — that
one stays, it's what keeps the store's dashboard pushing to Cloudflare
every day.

---

## Step 6 — Add the store to the cross-store hub

Provisioning sets up the store's *own* dashboard but doesn't touch the
shared hub page — that's a deliberate manual step so one store's install
can never accidentally overwrite the hub's own site. On your dev PC:

1. Open `hub-site/stores.json` and add an entry:
   ```json
   {"name": "<Store's display name>", "url": "https://<project-name>.pages.dev/stock-<token>.json"}
   ```
   (both the project name and the stock token are in the same
   `cloudflare_provision_log.txt` line from Step 4, or in
   `config.yaml`'s `remote:` section on the store's own PC.)
2. Push the updated hub:
   ```powershell
   python tools/deploy_hub.py --project promakeupmihoubi-hub
   ```
3. Commit `hub-site/stores.json` in this repo so the change isn't lost.

---

## If something goes wrong

**The provisioning step failed and you need to retry** — just re-run
`Setup.exe` with the token field filled in again (a fresh token if the
first one already got revoked). Every step before token-minting is safe to
repeat; nothing gets duplicated.

**You need to undo an install entirely** — run
`C:\Program Files\Shop Analysis\unins000.exe` (approve UAC). It kills the
watcher, removes both scheduled tasks, and removes the install folder in
one pass. It deliberately leaves `%LOCALAPPDATA%\Shop Analysis` (your
`config.yaml`/`.env`/logs) behind in case you're reinstalling — delete
that folder by hand too if you want a completely clean slate. You'll also
want to manually delete the Cloudflare Pages project and its two Access
applications, and revoke the watcher's minted token, from the Cloudflare
dashboard — the uninstaller has no way to reach Cloudflare.

**A store's dashboard stops showing up remotely** — check
`remote.enabled` in that store's `config.yaml` is still `true`, and that
the "Shop Analysis - Watcher" scheduled task is still present and running
(`schtasks /query /tn "Shop Analysis - Watcher"`).

---

## What this guide doesn't cover

- Setting up this repo for development work — see `SETUP.md`.
- What the finished dashboard looks like and how the shop owner uses it —
  see `README.md`.
- The full design reasoning behind any of this (why two Access
  applications, why the token never touches disk, why the hub can't share
  a write target) — see `docs/superpowers/specs/2026-08-27-component5-hub-design.md`
  and `docs/superpowers/plans/2026-08-28-component5-cloudflare-auto-provisioning.md`.
