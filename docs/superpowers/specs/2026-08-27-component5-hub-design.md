# Component 5: the multi-store hub — corrected design

Date: 2026-08-27
Status: design settled this session (with the owner), not yet built

## What this supersedes

`docs/superpowers/specs/2026-08-25-installer-updates-multistore-design.md`'s
Component 5 section described each store's watcher "pushing a small
`stock.json` ... to the hub project." That doesn't work: a Cloudflare Pages
deployment is a **full site replacement**, not an additive upload. If 3
independent stores' watchers all pushed to the *same* hub project on their
own timers, each push would completely replace the previous one — the hub
would only ever show whichever store pushed most recently, never all 3 at
once. This was caught before any code was written, not after.

It also assumed (implicitly) that the owner's Cloudflare Access login for
one store would "just work" when the hub's JS fetches that store's data.
It doesn't: `*.pages.dev` is on the public suffix list specifically so
browsers treat every project as a fully separate origin — an Access
session cookie for `store-a.pages.dev` is never sent on a request to
`hub.pages.dev` or `store-b.pages.dev`, full stop. Not a risk to verify;
a hard no from ordinary cookie scoping rules that no Cloudflare-specific
behavior can override without a shared custom root domain (out of scope -
would need the owner to own and configure a real domain).

## Decisions made with the owner (2026-08-27)

1. **Each store keeps pushing only to its own existing Cloudflare Pages
   project**, on the existing timer, unchanged. No shared write target,
   no multi-writer clobbering.
2. **Each store's own export additionally includes one file,
   `stock.json`**, and that one path (only that path — everything else on
   the store's domain stays gated to the owner's email exactly as today)
   is made reachable without an Access login, via a Cloudflare Access
   **path-scoped Bypass policy** on that store's existing Access
   application. This is what makes a plain, unauthenticated,
   CORS-permitted `fetch()` from the hub's JS actually work, with no
   cross-domain session-sharing problem to solve.
3. **`stock.json` includes price, not just name + quantity** — the
   owner explicitly asked for this. Retail prices are visible to anyone
   in the physical store anyway, so this is a smaller exposure than, say,
   revenue or margin, but it is a real widening from the original "name +
   quantity only" framing that justified the bypass being low-risk — worth
   remembering if this decision is ever revisited.
4. **The hub itself stays purely static**: a switcher (links to each
   store's own dashboard) + a search box whose JS fetches all 3 stores'
   `stock.json` directly and merges/filters client-side. No combined
   total, no auto-matching across stores (unchanged from the master
   spec - product identity isn't reliable across 3 independent R.Lynx
   databases). No new Cloudflare product (no Workers, no KV) - it's
   another Pages project, same mechanism as everything else, built once
   and rarely redeployed since it has no per-store data of its own.
5. **Full provisioning automation** — the owner's explicit goal: "the
   installer should do everything automatically." Setting up a new
   store's Cloudflare Pages project + Access application + owner-only
   policy + the new `stock.json` bypass policy should not require rachad
   to click through the Cloudflare Zero Trust dashboard by hand for every
   store, the way the *existing single store's* Access app was set up.

### How full automation is done without shipping a dangerous credential

Cloudflare's Access Management API (creating Access applications and
policies, not just Pages deployments) needs a token with real
organization-level Zero Trust permissions — a categorically more powerful
credential than the existing `Pages:Edit`-only one, since it can create,
modify, or delete the login gate protecting every property in the account.
Shipping *that* permanently into every store PC's installed app would be a
real security regression from Component 4's whole reason for existing
(narrow the credential a compromised store PC could expose).

The resolution: the powerful provisioning token is only ever used
**transiently, during the one-time interactive install rachad himself runs
on a new store's PC** — never written to `config.yaml`, never bundled into
the installer binary, never persisted anywhere after setup completes:

1. The installer's existing DB-detection wizard page (Component 2) gains
   one more optional field: a Cloudflare API token, pasted in by rachad
   only when setting up a *new* store (left blank on every ordinary
   customer-facing repeat-install/update, where nothing Cloudflare-side
   needs to change).
2. If provided, the installer calls Cloudflare's REST API, in order: create
   a new Pages project for this store -> create an Access application
   scoped to that project's `*.pages.dev` hostname -> add the owner-only
   Identity policy (same "Allow, Include: Emails" shape already used for
   `promakeupmihoubipos`) -> add the `/stock.json` Bypass policy -> push
   the first deployment (reusing the exact upload flow already built in
   `poslib/remote.py`) -> mint a **new, narrow** `Pages:Edit`-only token
   scoped to this account (the same account-wide constraint Component 4
   already documented - Cloudflare has no finer per-project scoping) and
   write *that* one into `config.yaml`/`.env` for the watcher's ongoing
   use.
3. The one-time powerful token itself is used only for the duration of
   that install run and is never written to disk - it lives only in the
   installer process's own memory for the few seconds this takes, the
   same "enter a secret, use it once, never persist it" pattern
   `packaging/publish_release.py` already uses for GitHub release
   credentials on rachad's own dev PC.

This is new, unverified ground - Cloudflare's Access Management API
(distinct from the Pages Direct Upload API already reverse-engineered and
documented in `poslib/remote.py`) has not been used from this codebase
before. It needs its own research-and-verify pass (most likely against a
disposable throwaway project + Access app, created and torn down via the
API, the same precedent Component 4 already established) before being
trusted to run unattended against a real store's setup - not assumed
correct from reading Cloudflare's docs alone.

## Deferred to after this component: weighted-average cost

The owner also asked for a real inventory-accounting feature, explicitly
**not** part of this component and not to be started until Component 5
is done:

> if I bought at a high price and sold half, then bought at a lower
> price, calculate the average price of the new + remaining stock

This is the standard **weighted-average cost (AVCO)** inventory costing
method. Today, `Metrics.items`/`catalog()`'s `cost` column is read
straight from R.Lynx's own `Item.Cost` field - a single point-in-time
value R.Lynx itself maintains, not something this tool computes. The new
feature would instead reconstruct each item's own purchase/sale history
(`purchases()` already has the purchase-line data; `lines`/`is_sale`
already has the sale-line data) in chronological order and run it through
a stateful accumulator: a purchase updates the weighted-average cost
(`new_avg = (old_qty * old_avg + purchased_qty * purchase_unit_cost) /
(old_qty + purchased_qty)`); a sale reduces quantity without changing the
average (standard AVCO - the average only moves on a purchase, never a
sale). The result should surface **both** on the Stock catalog screen
(replacing or sitting alongside the existing `cost` column - which to do
needs its own small design decision when this is picked up) **and** in
`stock.json`/the hub's cross-store view, since the owner asked for it in
both places. Not started. When picked up, this needs its own careful
design pass (where exactly in `metrics.py` this lives, how "purchase
price" is defined when a `PurchaseEntry` line's own unit cost is missing
or zero, whether a floor of zero applies, how this interacts with the
existing `stock_value`/`markup_pct`/`dead_stock`/`stockout_risk`
calculations that already read the old single `cost` column) - not
attempted here.

## What's next (build order for this component)

1. Extend `poslib/metrics.py`'s stock catalog data + `export_static.py` to
   emit a `stock.json` (item name, quantity, price - cost/margin stays out
   of this public file) into each store's own export directory. Low risk,
   no Cloudflare-API research needed, useful regardless of how the
   provisioning-automation piece turns out.
2. Research and verify Cloudflare's Access Management API (create
   application, create Identity policy, create Bypass policy) against a
   disposable throwaway project - before wiring it into the real
   installer flow.
3. Build the one-time provisioning flow into the installer wizard
   (Component 2's existing DB-detection page gains the optional Cloudflare
   token field).
4. Build the hub's own static page (switcher + search JS, CORS-aware
   fetch with graceful "store unreachable" handling per store, matching
   the master spec's existing testing-plan requirement).
5. Weighted-average cost - after all of the above, per the owner's own
   sequencing.
