# Turnkey installer, remote updates, and a multi-store hub

Date: 2026-08-25
Status: approved design, not yet built

## Why

This tool has so far been a single-shop dev project: git clone, `setup.bat`,
a `.venv`, a `config.yaml` with a hardcoded path — all steps that assume
someone with a terminal and Python is doing the install and can pull updates
by hand. That stops working the moment it's handed to an actual customer.

The concrete case driving this: one customer owns **3 separate stores**,
each with its own physical till PC running R.Lynx and its own independent
`.dblx` database. They want:

1. To install this tool on each store's PC without any technical
   involvement — no Python, no git, no terminal, no Claude Code visible at
   any point. Just a normal Windows installer wizard.
2. To receive updates pushed from the developer (rachad) without ever
   physically visiting any of the 3 stores.
3. One remote entry point where they, personally, can check any of their 3
   stores' dashboards from their phone/laptop — but explicitly **not** a
   merged/summed view. Each store's performance is reviewed on its own; see
   "Non-goals" below.
4. A way to check a product's stock across all 3 stores at once, before
   deciding what to buy — e.g. "do I need to order more of X, or does Store
   B already have some?"

This is architectural rather than a small patch because it changes how the
tool is packaged, how it gets database access, how it learns about updates,
and adds one genuinely new kind of remote page (a hub spanning 3
independent installs) that doesn't exist today.

**This dev PC (`C:/Users/RACHAD/Desktop/Base de données4.dblx`) is not one
of the 3 customer stores.** It stays on the existing git-based dev setup
unchanged. Everything below describes what gets *built and shipped*, not a
change to how this machine works.

## Scope

**In scope:**
- Package the app as a normal Windows installer (no visible Python/git).
- A one-screen setup step that finds the customer's R.Lynx `.dblx` file
  automatically, with a manual fallback.
- A silent, automatic update mechanism — checked once daily, applied with
  no customer interaction, no dialogs.
- Replacing wrangler's broad account-wide OAuth credential (full access to
  every Cloudflare product: Pages, Workers, DNS, zones, billing) with a
  Cloudflare API token restricted to the `Pages:Edit` permission only —
  needed regardless of store count, because shipping wrangler's current
  OAuth credential to a customer PC is the actual thing that made this
  design need a rethink. **Note:** Cloudflare does not support scoping a
  Pages:Edit token to a single project — the token is account-wide across
  all Pages projects. See Component 4 for the corrected claim and the
  resulting decision.
- One Cloudflare Pages project per store (same Access-gated-by-owner-email
  pattern the tool already uses today for the dev PC), plus one small
  shared "hub" project: links to each store's dashboard, and a cross-store
  stock search.
- Cross-store stock search on the hub page: side-by-side results only, no
  computer-computed combined total (see "Why no auto-matching" below).

**Out of scope (v1):**
- License-key enforcement or payment gating. At 1 customer / 3 stores this
  is trust-based; revisit only if selling to people you don't know
  personally.
- True combined stock **totals**. Would require a reliable way to know
  "this row in Store A's database and this row in Store B's database are
  the same physical product," which does not exist today (see below) —
  would need a one-time manual linking step per product, not started.
- Support for POS software other than R.Lynx. Every part of this design
  (the `.dblx` parser, the auto-detect paths) assumes R.Lynx everywhere.
- Automated multi-tenant provisioning (signup flows, self-serve Cloudflare
  project creation). At 3 stores, setting up each Cloudflare project by
  hand is fine.
- Merging/summing sales, revenue, or margin numbers across the 3 stores.
  Confirmed explicitly with the owner: he wants to see each store's
  performance on its own, not blended together. The hub is a shared front
  door, not a data-merging layer.

### Why no auto-matching for cross-store stock

Each store runs its own independent R.Lynx database with its own item IDs.
Product codes/names are **not guaranteed to match** across stores — a
barcode might be entered slightly differently, a product might be typed in
by hand with a different name, an item might exist in one store's catalog
and not another's at all. This was confirmed directly with the owner, not
assumed.

Auto-matching by name (fuzzy string matching) or by code would sometimes
get it wrong — and a wrong match feeding a real buying decision ("we have
40 combined, don't reorder") has a real cost: under-ordering and running
out, or over-ordering and wasting money. That is the same class of mistake
CLAUDE.md discovery #11 already documents (a plausible-looking but wrong
number shipped once and had to be reverted) — the fix there was the same
principle applied here: don't let the tool assert a number it can't stand
behind. So v1 shows the owner's own eyes the matching work: type a product
name, see every matching row from all 3 stores' catalogs side by side
(store name + that store's quantity), with no combined figure computed or
implied.

## Architecture overview

```
Customer's Store A PC                Customer's Store B PC        Store C PC
┌─────────────────────┐              ┌─────────────────────┐      ┌────────┐
│ Installed app        │              │ Installed app        │      │  ...   │
│ (PyInstaller +       │              │ (same)                │      │        │
│  Inno Setup)          │              │                       │      │        │
│                       │              │                       │      │        │
│ watcher.py:           │              │ watcher.py:           │      │        │
│  - rebuild cache       │              │  - rebuild cache       │      │        │
│  - daily update check  │              │  - daily update check  │      │        │
│    (GitHub Releases)   │              │    (GitHub Releases)   │      │        │
│  - push own site  ─────┼──► Store A   │  - push own site  ─────┼──► Store B   │
│    (Cloudflare REST    │    Pages     │    (Cloudflare REST    │    Pages     │
│     API, scoped token) │    project   │     API, scoped token) │    project   │
│  - push stock.json ────┼──► hub       │  - push stock.json ────┼──► hub       │
│    to shared hub        │    Pages     │    to shared hub        │    Pages     │
│    project              │    project   │    project              │    project   │
└─────────────────────┘              └─────────────────────┘      └────────┘
                                                    │
                                                    ▼
                                    hub Pages project (Cloudflare Access,
                                    gated to owner's email only):
                                      - links to Store A / B / C dashboards
                                      - stock search across the 3 stock.json
                                        snapshots, side-by-side, no merging

GitHub Releases (public repo) ◄── rachad pushes a new release when ready
        ▲
        │ checked once/day, at startup
        │ (store PCs are only powered on ~7am–2pm)
        │
   each store's watcher.py
```

## Component 1 — Packaging & installer

**PyInstaller in `--onedir` mode** (a folder of files, not a single `.exe`),
wrapped by **Inno Setup** into a normal `Setup.exe`.

`--onedir` over `--onefile`: onefile's self-extracting startup is a known
source of environment-specific breakage with pandas/numpy-heavy apps —
exactly the kind of bug that can't be debugged over the phone with a
non-technical owner. `--onedir` is slower to build but far more predictable
at runtime. Inno Setup hides the folder-of-files reality behind a normal
installer UX: Next → Next → Install → Start Menu/Desktop shortcut, same as
any commercial Windows software. No terminal, no visible Python, ever.

## Component 2 — Linking to the customer's database

One setup screen. It scans the standard R.Lynx install locations for a
`.dblx` file automatically; if found, the field is pre-filled and the owner
clicks Next with zero input needed. If not found (non-standard install), a
single native "browse for your database file" dialog — one click, no typed
paths. Writes the result into `config.yaml`'s `database.path`. Everything
downstream (`copy_database_readonly`, the ETL) is already built for an
arbitrary path — no change needed there.

## Component 3 — Silent, automatic updates

The background watcher (already running continuously via the existing
Task Scheduler entries) gains one more periodic job. Because these store
PCs are only powered on roughly 7am–2pm, "update overnight" doesn't work —
instead, the check happens **once, right at startup**, before the
dashboard is opened by anyone:

1. On watcher startup, check GitHub Releases for this repo for a version
   newer than the currently-installed one.
2. If found: download the new installer, run it unattended
   (`/VERYSILENT` — Inno Setup's silent-install flag), let it replace the
   installed files, then restart the watcher.
3. All of this happens in the minute or two after boot, before the store
   owner has sat down. No dialog, no click, nothing to approve.

The repo is already public on GitHub (verified: `gh repo view` →
`"visibility":"PUBLIC"`), so the Releases API needs no credential embedded
in the shipped app — this was chosen specifically to avoid shipping any
GitHub token to a customer PC.

## Component 4 — Narrowing the Cloudflare credential shipped to a store PC

Today, the remote push (`poslib/remote.py`) shells out to `wrangler`, a
Node.js CLI, authenticated via `wrangler login` — an OAuth credential
capable of managing the *entire* Cloudflare account: every Pages project,
Workers, DNS, zones, billing. Shipping that to a customer PC means shipping
a master key onto a machine outside rachad's control.

**Correction (2026-08-25, after implementation research):** this section
originally proposed replacing wrangler with a direct Python `requests` call
to Cloudflare's Pages direct-upload REST API, authenticated with a token
"scoped to Pages:Edit on one project only," claiming a compromised store PC
could at worst deface that one store's own dashboard. Both parts turned out
to be wrong:

1. **Cloudflare Pages API tokens cannot be scoped to a single project.**
   Confirmed against Cloudflare's own permission-groups documentation: the
   `Pages:Edit` permission is account-wide — it grants edit access to every
   Pages project in the account, with no per-project resource restriction
   available. So even the REST rewrite would not have delivered "blast
   radius limited to one store" — a token capable of pushing to Store A's
   dashboard is equally capable of pushing to Store B's, Store C's, and the
   shared hub's.
2. **The Pages direct-upload REST flow is not officially documented.**
   Cloudflare's own docs (`developers.cloudflare.com/pages/get-started/
   direct-upload/`) state there are exactly two ways to do a direct upload:
   Wrangler, or drag-and-drop in the dashboard. The REST endpoints wrangler
   uses internally are unofficial and reverse-engineered; two third-party
   writeups found describe conflicting file-hashing schemes (MD5 vs.
   BLAKE3) and note that malformed requests can return HTTP 200 while
   silently uploading nothing, serving stale or 404 content. (Cloudflare
   Workers has a separate, officially documented direct-upload REST API —
   `workers/static-assets/direct-upload/` — but that's a different product
   from Pages and doesn't apply to what this tool deploys today.)

**Revised plan:** keep `wrangler`, but stop authenticating it with the
account-owner's OAuth login. Wrangler reads a `CLOUDFLARE_API_TOKEN`
environment variable directly, so `poslib/remote.py` can set that (sourced
via `Config.secret()`, same as every other credential in this codebase)
from a token restricted to the `Pages:Edit` permission group only — no DNS,
no Workers, no zones, no billing access. This is a genuine, real blast
radius reduction (whole account → Pages product only) even though it isn't
the "one project only" isolation originally claimed. It requires no change
to the upload mechanism, no new dependency, and no reverse-engineered API —
only a credential swap in how `wrangler` is invoked.

Node.js remains a bundled dependency of the installer as a result of
keeping wrangler. Revisit only if Cloudflare later ships per-project Pages
token scoping, or if the installer's Node.js bundle size/reliability
becomes a real problem in practice — not assumed up front.

True per-store isolation (a compromised Store A PC cannot touch Store B's
or the hub's Cloudflare project) is only achievable today with **separate
Cloudflare accounts per store** — accepted elsewhere in this spec as a
reasonable amount of manual setup at 3 stores. Whether to do this is a
decision for the owner/rachad, not assumed by this document.

This credential swap is a prerequisite for shipping to any customer PC
regardless of how many stores are involved — flagged as its own component
because it's a real deviation from how the code works today, not a detail
of the multi-store piece.

## Component 5 — The hub: store switcher + cross-store stock search

One additional, small Cloudflare Pages project (separate from the 3
per-store ones), gated by the same Cloudflare Access policy (owner's email
only). Two things live there:

- **Switcher**: a static page listing the 3 stores, linking out to each
  store's own private dashboard. No data lives here for this part.
- **Stock search**: each store's watcher additionally pushes a small
  `stock.json` (product name + current quantity — this data already exists
  locally via the Patch #4 Stock catalog tab) to the hub project, under a
  per-store path (e.g. `data/store-a-stock.json`). The hub page has a
  search box; typing a product name filters and displays matching rows
  from all 3 `stock.json` files side by side (store name + that store's
  quantity). No combined total is computed or shown — see "Why no
  auto-matching" above.

## Non-goals (recap)

- No license enforcement, no payment gating.
- No true combined stock totals (would need manual product linking; not
  built).
- No support for non-R.Lynx POS software.
- No merged/summed sales figures across stores.
- No automated customer self-service provisioning.

## Suggested build order

Each piece below is independently testable before the next depends on it:

1. **Cloudflare credential swap** — keep `wrangler`, switch it from
   `wrangler login` OAuth to a scoped `CLOUDFLARE_API_TOKEN`
   (`Pages:Edit` only). Testable entirely on this dev PC against the
   existing single Cloudflare project; no customer or new store needed yet.
2. **Packaging** — PyInstaller `--onedir` + Inno Setup, tested by
   installing on this dev PC (or a clean VM) as if it were a customer.
3. **DB auto-detect setup screen** — added to the Inno Setup wizard.
4. **Auto-update mechanism** — testable by publishing a dummy GitHub
   Release bump and confirming a running watcher picks it up at its next
   startup check.
5. **Hub project** — switcher page first (no data dependency), then
   `stock.json` push + search once at least one real store install exists
   to push from.

## Testing plan

- Installer: verify a clean Windows VM with no Python/Node/git installed
  can run `Setup.exe` end to end and reach a working dashboard.
- DB auto-detect: verify both the found-automatically path and the
  manual-browse fallback path produce a working `config.yaml`.
- Update flow: bump a dummy version, publish a GitHub Release, confirm an
  already-installed instance detects it, downloads, silently installs, and
  restarts cleanly — including the case where the machine is mid-`rebuild()`
  when the check fires.
- Cloudflare credential swap: confirm `wrangler` successfully deploys when
  authenticated via `CLOUDFLARE_API_TOKEN` (Pages:Edit only) instead of
  `wrangler login`, and confirm the token **cannot** do anything else
  (e.g. manage DNS, Workers, or other zones) — this is a security property,
  worth actually checking, not just assuming from the token's stated
  permission group. Also confirm, and document plainly for the owner, that
  this token still allows editing *every* Pages project in the account
  (not just one) — true per-project isolation is out of reach without
  separate Cloudflare accounts.
- Hub stock search: confirm it degrades sanely when one store's
  `stock.json` hasn't pushed yet (stale or missing) — should show what it
  has, clearly marked, never silently omit a store without saying so.
