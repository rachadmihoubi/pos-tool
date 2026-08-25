---
name: cloudflare-remote-debug
description: Cloudflare Pages/Access setup notes and gotchas for pos-tool's remote viewing feature (poslib/remote.py, export_static.py). Use when touching the remote push code or debugging a failed/broken remote deploy.
---

# Cloudflare setup on this machine (already done — for reference on a new PC)

- `wrangler` installed globally via `npm install -g wrangler` (Node.js was
  already present). **On Windows, `subprocess` must call the fully-resolved
  `wrangler.CMD` path**, not the bare string `"wrangler"` — `shutil.which()`
  finds it fine but `subprocess.run(["wrangler", ...])` raises `WinError 2`
  regardless; `poslib/remote.py:_wrangler_path()` handles this. Also:
  `subprocess.run` needs explicit `encoding="utf-8"` — wrangler prints emoji
  that crash the default-codepage decode on Windows otherwise (this crashes
  a background reader thread, not the main call, so it doesn't fail the
  push, but it does spam a traceback into the logs).
- Authenticated via a `CLOUDFLARE_API_TOKEN` in `.env`, scoped to the
  `Pages:Edit` permission only (created at
  https://dash.cloudflare.com/profile/api-tokens — see `.env.example` for
  the exact steps). `poslib/remote.py:push_remote()` sets this on the
  `wrangler` subprocess's environment when the secret is configured; it
  falls back to a `wrangler login` OAuth session otherwise. Cloudflare does
  not support scoping a Pages token to a single project — this token can
  edit every Pages project on the account (`rachadm23@gmail.com`), not
  DNS/Workers/zones/billing.
- Cloudflare Pages project `promakeupmihoubipos` created via
  `wrangler pages project create`.
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
