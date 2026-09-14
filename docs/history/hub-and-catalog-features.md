> Moved verbatim out of CLAUDE.md on 2026-09-14 (docs/slim-claude-md). Nothing edited.

## Hub search shows cost, not price (2026-08-27/28) — and why it's an unguessable filename, not a real login

The owner asked for two changes to the hub's cross-store search after his
first live phone test: match on `Item.Reference` (the shop's own product
code, e.g. "Q0130") instead of the internal `ItemNo` (e.g. "AR0002"), and
show cost instead of selling price. The reference swap was simple —
`Metrics.items`/`catalog()` now select `i.Reference AS reference` (with
R.Lynx's own `"."` placeholder for "not set" normalized to null,
`poslib/metrics.py`), and `export_static.py`/`hub-site` use that field
instead of `item_no`.

Cost was a real security decision, not a simple swap — `/stock.json` is
reachable with **no login at all** (Cloudflare Access Bypass policy, see
the Component 5 row above), so putting cost there would make margin data
public to anyone with the URL. The owner's first instinct was "keep
everything private," which surfaced two infeasible options worth recording
so they aren't re-attempted: (1) a real server-checked secret (a header or
Cloudflare Access service token validated per-request) doesn't work
because Cloudflare Pages **Direct Upload** — this project's whole deploy
mechanism, chosen specifically so customer installs don't need Node.js/
wrangler — does not support Pages Functions at all, and browser `fetch()`
can't reliably carry Access service-token headers cross-origin either;
(2) the actually-correct fix (put every store + the hub on subdomains of
one real domain so Cloudflare Access can share one login session across
them, removing the need for any Bypass policy) needs the owner to own a
domain and was deferred, not rejected.

**What shipped instead**: `remote.stock_json_token` (per-store, in
`config.yaml`, blank by default) switches `export_static.py`'s output from
the public `stock.json` (price, never cost) to `stock-<token>.json` (cost,
never price) — a second file whose *name itself* is the only thing gating
it, since nothing server-side actually checks it (Cloudflare Pages static
hosting can't). The token is only known to `hub-site/stores.json`, which
is itself only reachable by whoever can log into the hub (the existing
owner-only Access application) — so the practical exposure is "anyone who
can already log into the hub could extract this URL from the page source
and re-share it," not "public to the internet." The owner explicitly
accepted this exact tradeoff after it was spelled out, rather than having
it silently implemented — see the two `AskUserQuestion` exchanges in this
session's history. **Missing/blank token fails safe**: a store with no
token configured keeps exactly today's behavior (public `stock.json`,
price only, no cost) rather than ever exposing cost on the well-known
filename. This dev PC's real `config.yaml` already has a generated token;
`hub-site/stores.json`'s URL was updated to match. **Both remaining steps
are now done (2026-08-28)**: the live Cloudflare Access application for
`promakeupmihoubipos.pages.dev` (app id `c5d4cdc6-afc4-43bd-984b-fd86454df55d`)
was repointed from the literal `.../stock.json` path to
`.../stock-f1cab0dac3a8e273d6293d71c808c877.json` via the Access
Management API (`domain`, `self_hosted_domains`, and `destinations[].uri`
all updated together — a `PUT` with only `domain` changed fails with
error 12130 "domain not included in destinations"; the endpoint also only
accepts `PUT`, not `PATCH`, which returns 405), and the store was
redeployed (`export_static.export(cfg)` + `poslib.remote.push_remote(cfg)`
run directly, the same manual-redeploy step used twice earlier in this
component, since the watcher only auto-pushes on new till activity). The
one-time Cloudflare API token used for the Access-app edit was pasted in
by the owner for this single use and should be revoked now that it's no
longer needed.

**Follow-up bug, found and fixed same day (2026-08-28)**: the owner
reported the hub's button-to-store-dashboard was fixed (see the
`_redirects` section below) but cross-store search now returned "no
results at all." Root cause: `hub-site/stores.json` was edited locally in
commit `95f79f9` to point at the new `stock-<token>.json` URL, but
**`tools/deploy_hub.py` was never re-run afterward** — the hub project is
pushed manually, separately from the store's own watcher-driven push, and
nothing in this fix's own work actually redeployed it. Once the store's
Access Bypass app was repointed away from the plain `/stock.json` path
(the step right above), that URL started 302-ing to the Cloudflare Access
login page instead of serving JSON — so the *still-live* hub was fetching
a URL that had just become access-gated out from under it. A cross-origin
fetch() to a redirect-to-login response fails in the browser, `allItems`
never populated, and the search box had nothing to filter. This is the
same class of bug as the earlier "stock.json initially 404'd" note above
(committed code that never actually got pushed to the live deployment) —
worth remembering any time `hub-site/` or a store's `remote.*` config
changes: a git commit is not a deploy for either of these two ad hoc
CLI-driven pushes.

Fixed by running `python tools/deploy_hub.py --project
promakeupmihoubi-hub` directly. **Verified without needing the owner's
login**: a Cloudflare Pages Direct Upload deployment's unique per-deploy
subdomain (e.g. `https://5dc8a56e.promakeupmihoubi-hub.pages.dev/`, printed
by the push itself) is NOT covered by the hub's Access application (which
is scoped to the literal `promakeupmihoubi-hub.pages.dev` hostname only),
so it's reachable without logging in — a reusable trick for verifying any
future hub/store deploy from this dev machine without needing the owner's
phone. Confirmed on that URL via `gstack browse`: `stores.json` now serves
the tokenized URL, the page loads with no console errors and "Pro Makeup
Mihoubi: 1599 items", typing "2530" into the search box returns the
matching row with correct cost (35) and box count (14 (+0)), and the
store-link button resolves to `https://promakeupmihoubipos.pages.dev/`
correctly. **Owner-confirmed 2026-08-28**: checked from his own phone
(the production `promakeupmihoubi-hub.pages.dev` URL, behind his real
Access login) — both the store-link button and cross-store search now
work. Both the button/`_redirects` bug and this stale-hub-deploy bug are
closed.

## Wholesale box/"colis" stock view (2026-08-28)

The owner sells wholesale in boxes of 24+ pieces, not individual units, and
asked the tool to show stock that way. `Item.QtyPerParcel` ("Colis" in
R.Lynx's own UI) turned out to already be a well-populated field in the
real database (1,512 of 1,599 items have a real box size; most common
values 24/12/6/36 pcs) — no schema gap like discovery #5/#6 above, just an
unsurfaced column. `Metrics.items` (`poslib/metrics.py`) now derives
`stock_boxes = floor(stock / qty_per_parcel)` and
`stock_remainder = stock - stock_boxes * qty_per_parcel`, only when
`qty_per_parcel > 1` (0/1/NaN means "no box packaging tracked" — both
derived columns stay NaN rather than showing a misleading 1-piece "box").
`catalog()` exposes both; the local Stock catalog page, `stock.json`/
`stock-<token>.json` (both variants — box counts aren't sensitive the way
cost is), and the hub's cross-store search table all show a "Boxes" column
(`24 (+6)` style: full boxes plus any partial remainder). `qty_per_parcel`
itself stays internal-only — never leaks into the exported JSON. Committed
as `8d7f94b`.

## Hub store-link button 404'd on every store's root — real bug in
`poslib/remote.py`, not the hub (2026-08-28)

While shipping the box feature above, the owner reported the hub's
"Pro Makeup Mihoubi" button led to `https://promakeupmihoubipos.pages.dev/`
showing Cloudflare's own custom 404 ("Not available remotely..."). First
fix attempt: `hub-site/app.js`'s `renderStoreLinks` collapsed a store's
`stock.json` URL back to its dashboard root via
`s.url.replace(/\/stock\.json$/, "/")` — a regex that only matched the
plain filename, not the new tokenized `stock-<token>.json` (see the hub
cost/token section above), so for this store the replace was a silent
no-op and the button linked straight at the raw JSON file's path. Fixed to
`/\/stock(-[0-9a-f]+)?\.json$/`, tested, redeployed (`f63e718`) — but the
owner reported the *exact same* symptom afterward, which meant the first
diagnosis was incomplete.

**Real root cause, finally nailed down empirically (not guessed)**:
`poslib/remote.py`'s `_IGNORED_FILE_NAMES` had excluded `_redirects` from
every Cloudflare Pages upload since Component 4 shipped (2026-08-26),
lumped in with `_worker.js`/`_routes.json` on the mistaken assumption all
three are "Pages Functions source, not static assets." A first fix
(`d9a08e0`) just removed `_redirects` from that ignore list so it uploaded
as a normal manifest asset, same as `_headers` — logical, tested, deployed,
and **still didn't work**, because it was solving the wrong half of the
problem. Cloudflare's Direct Upload API treats `_headers` and `_redirects`
asymmetrically and nothing in Cloudflare's own docs says so: `_headers` is
read straight out of the normal uploaded asset set (confirmed — the
`stock.json` CORS header really does work this way), but `_redirects` is
**only** honored when it is excluded from the asset manifest entirely and
sent as its own separate multipart file field on the deployment-create
call (filename `"_redirects"`, content-type `text/plain`) — exactly how
`wrangler pages deploy` does it internally, which is not documented
anywhere in Cloudflare's public API reference. Proved this by scripting
disposable throwaway Cloudflare Pages projects (same pattern Components 4
and 5 used) and testing both forms directly: a manifest-only `_redirects`
deploys with `success: true` and then serves a bare 404 on `/` forever,
while the separate-file-field form returns a real `302`. `_create_deployment`
now takes an optional `redirects_content` string and adds it as
`files["_redirects"] = ("_redirects", content, "text/plain")` only when
present; `_IGNORED_FILE_NAMES` has `_redirects` back in it (correctly, this
time — excluded from the manifest walk, not dropped from the deploy).
Tests updated: `test_ignores_wrangler_reserved_names` now also covers
`_redirects`, and a new `test_sends_redirects_as_separate_deployment_field`
/`test_no_redirects_field_when_no_redirects_file` replace the old
(disproven) `test_uploads_redirects_file`. Full suite green
(`tests/test_remote.py`, 30 passed). Store redeployed with this fix.

**Why the owner's phone check couldn't be skipped even after this**:
Cloudflare Access sits in front of the entire deployment and intercepts
every unauthenticated request — including a plain `curl` from this dev
machine — before Cloudflare Pages' own `_redirects` logic ever runs, so an
unauthenticated `curl` to `/` cannot distinguish "root redirect works" from
"root redirect is still broken" (both come back as a 302 to the Access
login page). What *can* be confirmed from here, and was: (1) the
mechanism itself works, proven against a disposable project with no Access
in front of it at all; (2) the real store's actual generated `_redirects`
file (`/  /fr/today  302` plus one line per language) has the same syntax
that disposable-project test used successfully; (3) the real deployment
went out with this fix. What still can't be confirmed from a dev machine:
whether Access correctly hands the *authenticated* browser back to Pages'
post-Access routing in a way that still applies the redirect — needs the
owner's own phone, logged in, hitting the bare domain root. Two prior
"fixed" claims in this file turned out to be wrong or unverified before
the owner tested; don't repeat that a third time — say what's confirmed
vs. what only the owner's phone can confirm, and wait for that check
before calling this closed.

