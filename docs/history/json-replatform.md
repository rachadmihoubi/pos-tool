> Moved verbatim out of CLAUDE.md on 2026-09-14 (docs/slim-claude-md). Nothing edited.

## Product/customer JSON replatform — Tasks 5 and 6 done, file count cut ~55% (2026-09-12)

Branch `product-customer-json-replatform`
(`.claude/worktrees/product-customer-json-replatform`), plan
`docs/superpowers/plans/2026-09-01-product-customer-json-replatform.md` —
see that branch's own
`.superpowers/sdd/2026-09-01-product-customer-json-replatform/progress.md`
for full per-task detail; this is just the numbers the plan's own Task 6
Step 5 asks to be logged here.

**Task 5 (live verification)**: passed 2026-09-12 — the owner confirmed
`https://promakeupmihoubipos.pages.dev/en/product.html?id=595` renders
correctly from his own device (DB786's name, two distinct purchase
costs, family comparison all correct). File count/size at this point
(old per-entity HTML loops still present): **12,935 files, 284 MB**.

**Task 6 (delete the old per-entity HTML export loops)**: done same
session. `export_static.py`'s old `products_dir`/`customers_dir`
per-language, per-entity HTML loops (one `.html` file per product per
customer per language) were removed entirely — `products.json`/
`customers.json` (Task 3) plus the `product.html`/`customer.html`
client-rendered shells (Task 4) fully replace them now.
`templates/product_detail.html`/`customer_detail.html` and `app.py`'s
own local `/products/<id>`/`/customers/<id>` routes are untouched (they
serve the local live dashboard, unrelated to this export path).

**Post-cleanup "after" figure** (clean re-export, confirmed no stale
per-entity directories left over from before the code change):
**5,864 files, 89.2 MB** — down from 12,935 files / 284 MB, a ~55%
file-count and ~69% size reduction. Pushed live
(`push_remote` → `True`, 398.8s); since a Cloudflare Pages Direct Upload
fully replaces a deployment's file set on every push, the old
per-entity URLs are now gone from the live site (a stale bookmark to one
falls through to the existing `404.html` "not available remotely"
page). **Owner-confirmed 2026-09-12** ("everything sees fine") — Task 6
Step 6 and this whole plan are complete. Branch merged to `main`
(fast-forward, `a138fff`), worktree removed.

