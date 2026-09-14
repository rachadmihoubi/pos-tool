> Moved verbatim out of CLAUDE.md on 2026-09-14 (docs/slim-claude-md). Nothing edited.

## Weighted-average cost (AVCO) + last purchase cost (2026-08-28) — built, both figures shown side by side

The owner asked for weighted-average cost back on 2026-08-27 (deferred
until Component 5 shipped, per the sequencing noted throughout this file)
and, once Component 5 was phone-verified, gave the concrete requirement:
**both** the weighted-average cost **and** the last price actually paid
for the product, shown next to each other — not just one. Per the global
CLAUDE.md financial-decision gate (any change to cost/reconciliation
logic needs an Opus subagent plan sanity-check before code is written,
same rule that would have caught discovery #11's fabricated supplier
figure earlier), the design below was reviewed by a dedicated Opus
subagent before any code was written, then independently re-verified
against the real database.

**Two real discoveries about `PurchaseEntry`, neither previously
documented anywhere in this codebase:**

1. **`Item.Cost` is NOT reliably "last purchase cost"** — it looked like
   it should be, but it is also overwritten by `PricingUpdateEntry`
   (manual price edits in the POS UI), not just by purchases. Checked
   against the full population: 18 of 1,567 items with purchase history
   have an `Item.Cost` that diverges from their own last purchase's
   `NewCost`, one cluster of 12 items exactly 2x off via a single bulk
   `PricingUpdateID` 217 operation (2026-06-04). This means "last purchase
   cost" has to be computed fresh from `PurchaseEntry`, never by
   relabeling `Item.Cost` — an early instinct that a small 5-item sample
   check seemed to confirm, until the subagent checked all 1,567 and found
   the exceptions. **Correction, 2026-08-28** (see the section below):
   the direction of that PricingUpdateID 217 story was backwards.
   `PurchaseEntry.NewCost` itself was corrupted (understated ~2x) on
   those 12 items by a separate bug (`OldCost = 0` on a line with
   pre-existing stock — see point 2 below); PricingUpdate 217 was R.Lynx
   *correcting* `Item.Cost` back to the right value, not damaging it. The
   practical conclusion this discovery reached — compute a purchase-based
   cost fresh, never by trusting `Item.Cost` — still holds, it just
   holds for a different reason than originally written down here.
2. **`PurchaseEntry.NewStock` is R.Lynx's own snapshot of the item's total
   stock level immediately *after* that purchase line was applied** — not
   the quantity received in that delivery. This is the key fact that made
   a correct AVCO accumulator cheap to build: `NewStock - Qty` is the
   stock on hand the instant *before* this delivery landed, which already
   nets out every sale, return and stock adjustment (`ItemAdjustment`, a
   real table with more rows than `PurchaseEntry` itself — a naive
   purchases+sales-only interleave would have silently ignored it) that
   happened since the previous purchase. This is why the accumulator
   needs no purchase dates and no sales-table interleaving at all — an
   initial framing of the problem (interleave purchases and sales
   chronologically, blocked by ~34% of purchases having no real date) was
   the wrong approach even with perfect dates, and was dropped in favor of
   this one on the subagent's advice. `PurchaseEntry.ID` (`entry_id`) is a
   **very reliable but not perfect** ordering key *within one item's own
   purchase lines*: cross-checked against `Item.LastPurchasePrice`
   (R.Lynx's own record of the last price paid) on 1,574 items, it agrees
   on 1,570 and disagrees on 4 — good enough to build the accumulator on,
   but not an absolute guarantee, and it is NOT a clean global
   chronological key across the whole table regardless (confirmed:
   `PurchaseID` runs backwards against it at 20 points system-wide) —
   never rely on it beyond per-item ordering. **Corrected, 2026-08-28**:
   `PurchaseEntry.Cost` is NOT `NewCost * Qty` as originally written
   here — that was a wrong guess this file itself shipped with. `Cost` is
   actually `Amount - TotalItemDiscount`, the line's real *net* total
   cost after the supplier's own per-line discount; `Cost / Qty` is the
   correct net per-unit figure. The genuine naming trap is the other way
   around: `NewCost` *looks* like the obvious per-unit cost to use and is
   even a real per-unit figure, but it is R.Lynx's own already-computed
   running weighted-average (fed by the exact same `NewStock - Qty`
   recursion this codebase independently builds) — using it as a raw
   per-delivery input double-averages every purchase. See the correction
   section below for how this was found and fixed.

**What shipped**: `Metrics.item_purchase_costs` (`poslib/metrics.py`), a
new standalone `cached_property` — deliberately not folded into the
existing `items` property, to keep this from touching `stock_value`,
`markup_pct`, `dead_stock`, `stockout_risk`, or anything else already
built on `cost`. It walks each item's own purchase lines ordered by
`entry_id`, computing each line's raw per-unit cost as `Cost / Qty` (net
of the supplier's own discount — never `NewCost`, never gross `Price`;
see the correction section below for why both of those are wrong),
skipping a line with non-positive qty or a missing/non-positive raw cost
for costing purposes (but still resyncing the running quantity to that
line's own `new_stock`, so a later good line isn't compared against a
stale quantity), and produces `avg_cost` (weighted-average, clamped so
the "old" quantity carried forward never exceeds what was actually on
hand or goes negative) and `last_purchase_cost` (the most recent valid
line's own raw `Cost / Qty`) per item. Items never purchased get no
row — blank on the catalog screen, never guessed from `Item.Cost`.
`catalog()` merges this in by `item_id` (left join, so row count and
sort order are unchanged); `cost` (`Item.Cost`) stays exactly as it was,
still the only field every existing diagnostic reads — this is a
display-only addition. Both figures now show as adjacent "Avg cost" /
"Last cost" columns on the Stock catalog screen
(`templates/catalog.html`, replacing the single "Cost" column; new
`catalog.col_avg_cost`/`catalog.col_last_cost` keys in all three
`locales/*.json` files), and in the hub's cross-store search
(`hub-site/index.html`/`app.js`, two columns replacing one, falling back
to `price` in both cells for a store with no `stock_json_token`
configured). In `export_static.py`, both new fields are added **only**
inside the existing `if stock_token:` branch of the stock-record loop —
same discipline as the plain `cost` field (see the "Hub search shows
cost, not price" section above): they can never appear on the public,
unguessable-filename-only `stock.json`, only on `stock-<token>.json`.

**Materiality — told to the owner up front so it doesn't read as
broken**: as of the 2026-08-28 correction below, 1,576 items have a
purchase-cost row; 47 of those show *any* difference between avg cost and
last purchase cost (>0.01 DZD); 26 currently-in-stock items diverge by
more than 1%. Stock valued at `avg_cost` totals ~63,990,732 DZD. In other
words, on roughly 97% of catalog rows the two new columns still show the
identical number — a real, correctly-working feature that mostly looks
like it does nothing, because most products in this catalog have only
ever been bought at one net price. (These figures differ slightly from
the numbers this section first shipped with — 48/23/~64.5M — because the
correction below changed the actual per-line cost the accumulator reads;
re-run the query in that section any time this needs re-checking, don't
treat either set of numbers as permanently frozen.) Verified via the test
suite (`tests/test_metrics.py`'s `TestCatalog` —
`test_last_purchase_cost_matches_last_valid_purchase_entry`,
`test_avg_cost_equals_last_cost_for_single_purchase_items`, and
`test_discounted_single_purchase_uses_net_cost_not_gross_price`, the last
one a regression test specifically guarding against the gross-`Price`
mistake described below ever being reintroduced) plus a new
`TestProductPurchaseHistory` class covering the raw-purchase-lines
transparency view. `pytest tests -q` passing is the re-verification step
to re-run after any future change to `PurchaseEntry` handling, same as
every other rule in this file.

### Correction (2026-08-28) — "last cost" was reading the POS's own running average, not a raw delivery price

The owner caught this himself from the numbers, not from a bug report:
he pointed out that three products he buys often (M308, M377, M2041) got
"last cost" figures with lots of decimal digits, when in his own real
purchasing experience their cost is always a round number (rounded to the
nearest 5 DZD, "only in extreme rare cases" not). His read was exactly
right: **`item_purchase_costs` had been reading `PurchaseEntry.NewCost`
as if it were "the raw price paid on the last delivery," but `NewCost` is
R.Lynx's own already-blended running weighted-average cost** — the same
number this codebase's own accumulator independently recomputes. Treating
it as a raw per-delivery input double-averages every purchase, which is
exactly the kind of drift that produces non-round numbers on an item
whose real invoiced price never actually changes.

Per the global CLAUDE.md financial-decision gate (this counts as
"reconciliation logic... a number already shipped," so the gate applied
even though the shipped number was only hours old), an Opus subagent was
dispatched to investigate `PurchaseEntry`'s actual columns before any fix
was written — and it caught a second problem my own first hypothesis
would have missed. My first instinct was to switch from `NewCost` to
`PurchaseEntry.Price` (which does look round, and matches the user's
expectation on M308/M377/M2041 specifically, since none of those three
happen to carry a supplier discount). The subagent's deeper read of the
table found `Price` is the **gross list price before the supplier's own
per-line discount** — wrong on the ~13% of lines that do carry one
(`TotalItemDiscount != 0` on 335 of 2,615 lines, across 284 of 1,576
items). The actually-correct raw per-unit input is
`PurchaseEntry.Cost / Qty`, where `Cost` is `Amount - TotalItemDiscount`
(net of that discount) — confirmed to reproduce R.Lynx's own `NewCost`
(anchored against the also-newly-found `OldCost` column) on 2,606 of
2,606 valid lines, a perfect match, versus 2,271/2,606 for `Price` alone.
Fixed in `poslib/metrics.py`'s `item_purchase_costs` accordingly (see the
"What shipped" description above, now current); the two pre-existing
tests that referenced `new_cost` were updated to re-derive from
`cost/qty` instead, and a new regression test
(`test_discounted_single_purchase_uses_net_cost_not_gross_price`) locks
in the discount case specifically so the gross-`Price` mistake can't
silently come back. This is also where the `OldCost = 0` finding in
discovery #1 above came from, and where the `entry_id`-ordering
guarantee in discovery #2 above got softened (checked against
`Item.LastPurchasePrice` for the first time).

The owner also asked, separately and explicitly, for a way to check any
of this himself rather than trust a number he can't see behind: "because
i can't see what is inside the database my self i can't judge or review
the number you are providing." The product drill-down page
(`templates/product_detail.html`) gained a "Purchase history" table
showing each item's raw `PurchaseEntry` lines — date, supplier, quantity,
the gross price paid, R.Lynx's own running cost, and resulting stock —
completely unaltered, no calculation applied, sourced from a new
`purchase_history` key on `Metrics.product_profile()`. The Stock catalog
page's reference column and item name are now both links into this page
(`templates/catalog.html`), so the owner can search for a product (by the
same reference code he already uses, e.g. "308") and click straight
through to its own purchase history to check any figure by eye.

**Deployed and dev-machine-verified 2026-08-28**: the store's own
export+push (`export_static.export(cfg)` + `poslib.remote.push_remote(cfg)`,
run directly since the watcher only auto-pushes on new till activity) and
the hub's redeploy (`python tools/deploy_hub.py --project
promakeupmihoubi-hub`) both succeeded. Confirmed via the same
disposable-per-deployment-subdomain trick as the earlier hub-button fix
(`https://e786f12d.promakeupmihoubi-hub.pages.dev/`, printed by the deploy
itself, not covered by the hub's Access application so it's reachable
without logging in): the results table header shows "Avg cost"/"Last
cost" as two separate columns, no console errors, and a divergent-cost
item (`DB786`) renders two distinct real numbers (2777.82 vs. 2725.53)
matching the local export exactly — not a display bug collapsing both
columns to the same value. The store-link button was also re-checked and
still correctly targets `https://promakeupmihoubipos.pages.dev/` (`target=
"_blank"`), confirming the earlier `_redirects`/button fix is undisturbed.
One cosmetic-only hiccup during the hub redeploy: `tools/deploy_hub.py`
logged a `PermissionError` from Python's log-rotation handler
*after* printing its own success line, because the always-running local
watcher had `logs/pos-tool.log` open for writing at the same moment the
script tried to roll it over — the push itself completed and returned
success before this happened; if this collision recurs it may be worth
a defensive fix (e.g. delay-rotate or a separate log file per script), but
it isn't blocking anything. **Only the owner's own phone confirmation is
still outstanding** — same "a fixed/built claim only really lands once he
confirms it himself" pattern as every other feature in this file; not
blocking calling the implementation itself done.

The local Stock catalog page (`http://127.0.0.1:8777/catalog`) was also
checked, and initially 500'd (`jinja2.exceptions.UndefinedError: 'dict
object' has no attribute 'stock_boxes'`) — not a code bug: this dev PC had
**four** stray `pythonw.exe` processes left over from past sessions (two
duplicate watcher+dashboard pairs, one set from `.venv\Scripts\pythonw.exe`
and one from the global `Python312\pythonw.exe` on PATH — only one of the
two app.py processes actually held port 8777), all started 2026-08-26,
before today's `stock_boxes`/AVCO columns existed in `catalog()`. A process
that imports `poslib/metrics.py` once at startup never sees later edits to
that file — restarting is required, same as any long-running Python
service. Verified this was the only issue by rendering `/catalog` through
Flask's test client in a **fresh** process (no port conflict, no need to
touch the stale ones): 200 OK, "Avg cost"/"Dernier coût" columns present,
and `DB786` again showing the correct two distinct values. Actually
killing/restarting the stale port-8777 processes was blocked by this
session's own permission classifier (`taskkill` denied as a risky action)
and the owner was asleep to ask — so the live dashboard at :8777 itself
was **not** restarted this session and will keep 500ing on `/catalog`
until it is. Next time anyone is at this PC: restart it by killing the
`pythonw.exe` processes (`watcher.py` and `app.py --no-browser` — check
`tasklist`/`Get-CimInstance Win32_Process` for the `.venv` vs global-Python
duplicates and clear all four) and re-running `start-quiet.bat`, or just
rebooting.

