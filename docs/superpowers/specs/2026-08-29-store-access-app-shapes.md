# Store Access app shapes — live-account findings (2026-08-29)

Produced by Task 1 of
`docs/superpowers/plans/2026-08-28-component5-cloudflare-auto-provisioning.md`.
Read-only investigation against the real, live Cloudflare account
(`GET /accounts/{id}/access/apps`, a disposable `Access: Apps and Policies:Read`
token, revoked immediately after this task). No writes were made to the
live account. Task 4's `create_broad_access_app`/`create_bypass_access_app`
must build on the confirmed-working shape below, not a guess.

## The three existing Access applications

### 1. Store #1 broad app (`promakeupmihoubipos.pages.dev`) — id `c698ba31-9b92-4cf6-a409-466325e25098`

**This is the shape Task 4 must reproduce.** It already correctly covers
per-deployment preview subdomains — CLAUDE.md's "wildcard broadening" note
is confirmed real, not a hypothesis:

```json
{
    "domain": "promakeupmihoubipos.pages.dev",
    "self_hosted_domains": [
        "promakeupmihoubipos.pages.dev",
        "*.promakeupmihoubipos.pages.dev"
    ],
    "destinations": [
        {"type": "public", "uri": "promakeupmihoubipos.pages.dev"},
        {"type": "public", "uri": "*.promakeupmihoubipos.pages.dev"}
    ],
    "policies": [
        {
            "decision": "allow",
            "include": [{"email": {"email": "rachadm23@gmail.com"}}],
            "name": "owner only",
            "reusable": true,
            "precedence": 1
        }
    ],
    "session_duration": "24h"
}
```

Both `self_hosted_domains` and `destinations[]` carry the bare hostname AND
the `*.<project>.pages.dev` wildcard — this is the field pair CLAUDE.md's
"Hub search shows cost" note says must be updated together (a `PUT` that
only updates `domain` without also updating `destinations[]` produces error
12130). `create_broad_access_app` should send both fields, both entries,
for every new store.

**Verification caveat:** I confirmed the *configuration* includes the
wildcard, but could not live-test a real store #1 preview-subdomain URL the
way I did for the hub (no known store #1 preview-deployment hash exists in
CLAUDE.md's history to try, and this task's token was scoped read-only —
no `Pages:Read` to list deployments and find one). The negative case (hub,
below) is fully proven; the positive case (store #1) rests on config
inspection plus the fact that it uses the identical Access mechanism the
negative case demonstrably enforces correctly when configured this way.

### 2. Hub broad app (`promakeupmihoubi-hub.pages.dev`) — id `a972fabb-67e2-4217-ab16-29c885892857`

**Confirmed live gap — does NOT cover preview subdomains today:**

```json
{
    "domain": "promakeupmihoubi-hub.pages.dev",
    "self_hosted_domains": ["promakeupmihoubi-hub.pages.dev"],
    "destinations": [
        {"type": "public", "uri": "promakeupmihoubi-hub.pages.dev"}
    ]
}
```

No wildcard entry in either field. Live-tested with a bare, cookie-less
`curl -sI` against the two known preview-deployment hashes recorded in
CLAUDE.md's own history:

| URL | Result |
|---|---|
| `https://5dc8a56e.promakeupmihoubi-hub.pages.dev/` | `200 OK` — **ungated** |
| `https://e786f12d.promakeupmihoubi-hub.pages.dev/` | `200 OK` — **ungated** |
| `https://promakeupmihoubi-hub.pages.dev/` (bare, sanity check) | `302 Found` — gated, as expected |

This reproduced exactly the bypass CLAUDE.md documents being used
deliberately in the past. This was a live gap on the hub's own Access app,
separate from and not fixed by this plan (this plan only touches per-store
provisioning, not the hub).

**FIXED 2026-08-29, same day, separately from this plan** (needed write
scope this task's own token deliberately didn't have): a `PUT` to app id
`a972fabb-67e2-4217-ab16-29c885892857` added `*.promakeupmihoubi-hub.pages.dev`
to both `self_hosted_domains` and `destinations[]`, mirroring store #1's
already-correct shape above exactly, with the existing owner-only policy
sent back unchanged. Verified immediately after with a bare `curl -sI`:
both preview URLs in the table above now return `302` instead of `200`.
See `CLAUDE.md`'s Component 5 SDD progress section for the full record.

### 3. Store #1 narrow stock.json bypass app — id `c5d4cdc6-afc4-43bd-984b-fd86454df55d`

```json
{
    "domain": "promakeupmihoubipos.pages.dev/stock-f1cab0dac3a8e273d6293d71c808c877.json",
    "self_hosted_domains": [
        "promakeupmihoubipos.pages.dev/stock-f1cab0dac3a8e273d6293d71c808c877.json"
    ],
    "destinations": [
        {"type": "public", "uri": "promakeupmihoubipos.pages.dev/stock-f1cab0dac3a8e273d6293d71c808c877.json"}
    ],
    "policies": [
        {
            "decision": "bypass",
            "include": [{"everyone": {}}],
            "name": "public bypass",
            "reusable": false,
            "precedence": 1
        }
    ]
}
```

Path-scoped to the exact `stock-<token>.json` filename (not a wildcard
path), single `bypass` policy with `everyone`. This is the shape
`create_bypass_access_app` should reproduce per-store: `domain`/
`self_hosted_domains`/`destinations[].uri` all set to
`<project>.pages.dev/stock-<token>.json` (no wildcard — this app is
deliberately narrow to one exact file, unlike the broad app above).

## What Task 4 should do

`create_broad_access_app(project, ...)` — build with:
- `domain`: `<project>.pages.dev`
- `self_hosted_domains`: `[<project>.pages.dev, *.<project>.pages.dev]`
- `destinations`: both entries, `type: public`, matching URIs
- one `allow` policy, `include: [{email: {email: <owner_email>}}]`,
  `reusable: true`

`create_bypass_access_app(project, stock_token, ...)` — build with:
- `domain`/`self_hosted_domains`/`destinations[].uri`: all
  `<project>.pages.dev/stock-<stock_token>.json` (no wildcard)
- one `bypass` policy, `include: [{everyone: {}}]`, `reusable: false`

Both fields (`self_hosted_domains` and `destinations[]`) must be sent
together on create — store #1's broad app proves this is the shape
Cloudflare actually enforces correctly when both are present; the hub's
gap proves what happens when the wildcard is omitted from either field.

## Token disposal

The disposable `Access: Apps and Policies:Read` token used for this
investigation was used for exactly this one read-only listing call plus
the follow-on `curl -sI` checks (which needed no auth at all). User asked
to revoke it immediately after this task, per project convention.
