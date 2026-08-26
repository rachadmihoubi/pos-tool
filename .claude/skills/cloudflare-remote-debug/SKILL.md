---
name: cloudflare-remote-debug
description: Cloudflare Pages/Access setup notes and gotchas for pos-tool's remote viewing feature (poslib/remote.py, export_static.py). Use when touching the remote push code or debugging a failed/broken remote deploy.
---

# Cloudflare setup on this machine (already done — for reference on a new PC)

- **`poslib/remote.py` talks to Cloudflare's Direct Upload REST API
  directly (via `requests`) — no `wrangler`, no Node.js.** This replaced
  the original `wrangler pages deploy` subprocess approach because a
  frozen customer install has no Node.js on the machine at all, so the
  CLI-based push silently could not run there. The protocol is four HTTP
  calls, not one — `_get_upload_token` → `_upload_assets` →
  `_upsert_hashes` → `_create_deployment`, all in `poslib/remote.py` — with
  a critical asymmetry: `upload-token` and `deployments` use the full
  `/accounts/{id}/pages/projects/{project}/...` path with the normal API
  token, while `upload` and `upsert-hashes` use a short-lived JWT (from
  `upload-token`'s response) and **no** `/accounts/{id}/` prefix — pasting
  one in gets a misleading 404. **The one undocumented, easy-to-get-wrong
  piece is the asset content-hash key**, reverse-engineered from
  wrangler's own source: `blake3(base64(file_bytes) +
  extension_without_dot).hex()[:32]` — implemented in `_cf_hash()`. Get
  any part of that wrong (wrong hash algorithm, hashing raw bytes instead
  of base64 text, keeping the extension's dot, taking more than 32 hex
  chars) and every stage still reports `success: true` while every asset
  404s forever — Cloudflare stores the upload under whatever key you send,
  with no validation against the content, so there is no error response
  to search for. If a deploy "succeeds" but the site 404s, this is almost
  certainly why — verify by fetching a specific asset URL, not by trusting
  the deployment response.
- Authenticated via a `CLOUDFLARE_API_TOKEN` **and** a `CLOUDFLARE_ACCOUNT_ID`
  in `.env` — the token scoped to the `Pages:Edit` permission only (created
  at https://dash.cloudflare.com/profile/api-tokens — see `.env.example`
  for the exact steps and where to find the account ID).
  `poslib/remote.py:push_remote()` requires both; it returns `False` (never
  raises) if either is missing. Cloudflare does not support scoping a
  Pages token to a single project — this token can edit every Pages
  project on the account (`rachadm23@gmail.com`), not DNS/Workers/zones/
  billing.
- Cloudflare Pages project `promakeupmihoubipos` was created via
  `wrangler pages project create` (before the wrangler dependency was
  removed) — creating a new store's project can now be done from the
  Cloudflare dashboard UI instead, since nothing here depends on wrangler
  being installed anywhere any more.
- Cloudflare Access application configured through the Zero Trust dashboard
  (no CLI/API path was used) — Applications → Self-hosted → destination
  `promakeupmihoubipos.pages.dev` → policy "owner only" (Allow, Include:
  Emails). **Gotcha hit while setting this up**: Cloudflare's current UI adds
  an empty "Private IP" destination row by default that fails validation if
  left as-is — delete it, keep only the public-hostname destination. Also
  watch for browser autofill polluting a hostname-shaped field with a street
  address — remove any destination that isn't the actual `.pages.dev`
  hostname. **Access enforcement takes a minute or two to propagate** after
  saving — don't conclude it's broken from an immediate check; wait, then
  re-check for the redirect to `<team>.cloudflareaccess.com/cdn-cgi/access/login/...`.
