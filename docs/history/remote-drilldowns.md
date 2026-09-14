> Moved verbatim out of CLAUDE.md on 2026-09-14 (docs/slim-claude-md). Nothing edited.

## Remote product/customer drill-downs now exported in full (2026-08-28) — reversed an earlier deliberate exclusion

The owner clicked a product from the Stock catalog on the remote (phone)
dashboard and hit the static export's own "not available remotely" 404
page. That page's existence was intentional — the original remote-parity
design (see discovery #10 above) explicitly excluded `/products/<id>` and
`/customers/<id>` from the static export, reasoning "no natural recency
cutoff for a customer or product," unlike a ticket. First response was a
narrow, defensible fix: gate the product/customer links in `catalog.html`,
`products.html`, `receivables.html`, `customers.html` behind
`is_static_export` so they render as plain text (not dead links) when
viewed remotely. That shipped, tested, and worked as designed.

The owner's immediate follow-up made the actual ask explicit: **"why not
include this remotely? I want the full view of the local dashboard on my
phone remotely!!"** — full feature parity, not a hidden link. Revisiting
the original exclusion rationale found it was wrong on its own terms: it
conflated *unbounded* growth (tickets, which accumulate forever) with
*catalog/roster-sized* growth (products, customers) — the real counts are
1,599 products and 671 customers (walk-in excluded), the same order of
magnitude as purchases (~500-600), which the export already ships in full
with no windowing. There was never a real reason to treat products/
customers differently from purchases.

**What shipped**: the four templates' links were reverted back to plain,
unconditional `<a href>`s (no `is_static_export` gate — full parity means
they just work). `export_static.py` gained two more full-population export
loops, following the exact same `app.test_request_context()` +
`render_template()` pattern already proven for tickets/purchases (shared
`Metrics` instance, no per-page Flask request overhead): one over every
`catalog()` item (`item_ids`, inactive items included — a product's history
doesn't stop mattering because it's no longer sold), one over every real
customer (`customer_ids`, excluding `Metrics.walkin_id` — the anonymous
till account has no profile, `customer_profile()` returns `None` for it,
same as it always has locally). Output lands at
`<lang>/products/<item_id>.html` and `<lang>/customers/<customer_id>.html`.

**Real numbers, verified against the live database**: 1,599 products +
671 customers × 3 languages = 6,810 new files, on top of the existing
~6,457 → **~13,267 files total**. Checked against Cloudflare Pages' actual
published limit (fetched from `developers.cloudflare.com/pages/platform/
limits/`, not assumed from memory): the Free plan allows **20,000 files
per deployment** — the new total is about 66% of that ceiling. Real
margin, not a razor's edge, but also not huge if the catalog/customer
roster keeps growing — worth re-checking this file count again if either
count roughly doubles.

**Real export time cost**: `pytest tests/test_export_static.py -q`
(17 of its 19 tests each call `export_static.export(cfg)` fresh against
the real database) took 3,962s total, or **~3.9 minutes per single
export**, up from the ~40 seconds the tickets/purchases-only version took
(see discovery #10's perf note). No `RuntimeError` ("vanished mid-export")
fired for any real item or customer ID — all 1,599 products and 671
customers rendered cleanly. This cost lands on the watcher's own
background push (`watcher.py`'s `_run_remote_push`, triggered only when
the ETL detects new till activity, on a 90-second-minimum interval) — it
runs synchronously with no timeout, so a ~4-minute export just delays that
one push cycle, not anything user-facing on the local dashboard. Acceptable
tradeoff, not silently absorbed: worth knowing if a future change makes
export time matter more (e.g. an even lower push interval).

Full `pytest tests -q` suite re-run and store redeployed after this change
(`export_static.export(cfg)` + `poslib.remote.push_remote(cfg)`, run
directly — the same manual-redeploy step used repeatedly throughout this
file, since the watcher only auto-pushes on new till activity). Hub
redeploy not needed — this change doesn't touch `hub-site/` or
`stock.json`.

**Verified — and the disposable-subdomain trick is now broken, don't rely
on it without re-checking.** The established "check a Cloudflare Pages
deployment via its own unguarded per-deploy subdomain" trick (used
repeatedly earlier in this file) no longer works: `curl` with no
cookies/JS to `https://baf18b7c.promakeupmihoubipos.pages.dev/en/catalog`
got a 302 straight to the Cloudflare Access login, same as the production
hostname. Most likely cause: the Access application's
`self_hosted_domains` got broadened (to a wildcard covering
`*.promakeupmihoubipos.pages.dev`) during the stock-token repoint work
earlier on 2026-08-28 (see "Hub search shows cost, not price" above,
where `domain`/`self_hosted_domains`/`destinations[].uri` were all
updated together) — plausible but not confirmed by inspecting the Access
app config directly. **Don't assume any future per-deploy subdomain is
unguarded** — check with a bare `curl` first, the same way this was
caught.

Verification was done directly against the exported static files on disk
instead (no Cloudflare, no login needed): file counts matched exactly
(1,599 files in `remote-site/en/products/`, 671 in
`remote-site/en/customers/`); `catalog.html` contains exactly 1,599
`<a href="/en/products/...">` links; sampled pages
(`products/1.html`, `products/595.html` = item DB786,
`customers/10.html`) had no `Traceback`/`Undefined`/`werkzeug` error
markers, real headings (`Article divers`, an Arabic customer name), and
DB786's page correctly showed its known divergent purchase cost
(`2725.53`) under a real "Purchase history" section. This confirms the
exported HTML itself is correct. **What it does not confirm**: whether
Cloudflare Access, once authenticated, correctly serves these same files
on the real gated domain — that still needs the owner's own phone,
logged in, per this file's established "don't declare a deploy fully
verified until the owner's phone confirms it" practice (see the
`_redirects` bug history above, which burned this exact shortcut twice).

**Owner-confirmed on his own phone, 2026-08-28: both product and customer
drill-down pages work.** This closes out the feature — full remote parity
for product/customer detail pages is done, deployed, and verified both
mechanically (file-level checks above) and by the owner directly.

