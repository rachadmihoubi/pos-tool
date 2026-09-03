# Task 3 report: `export_static.py` — write `products.json` / `customers.json`

## What I implemented

Exactly per the brief, no deviations:

1. **`export_static.py`**: added `_json_safe(value)` (new module-level helper,
   placed right before `_today_preset_ranges`) — recursively converts a
   `row_dict()`/`rows()`-cleaned structure into something `json.dumps(...,
   allow_nan=False)` can serialize: non-finite floats (`inf`/`-inf`/`nan`) become
   `None`, `datetime.datetime` becomes an ISO string (`isoformat(sep="T",
   timespec="seconds")`), `datetime.date` becomes `isoformat()`.
2. **`export_static.py`**: inside `export()`, right after the existing
   `_headers` write and right before `presets = _today_preset_ranges(today)`,
   added the `products_json`/`customers_json` build-and-write block. It
   iterates `item_ids`/`customer_ids` (the same lists the existing
   `products_dir`/`customers_dir` HTML loops further down already iterate),
   calls `m.product_profile(item_id)` / `m.customer_profile(customer_id)`
   fresh (a deliberate duplicate fetch — the brief is explicit this is
   intentional, Task 6 removes the duplication later), and writes
   `out_dir/products.json` / `out_dir/customers.json` with
   `json.dumps(..., ensure_ascii=False, allow_nan=False)`.
3. **`tests/test_export_static.py`**: added `TestProductsCustomersJson` with
   the exact 3 tests from the brief, placed between the existing `TestExport`
   class and `TestStatusPayload`.

The existing per-entity-per-language HTML loops (`products_dir`/
`customers_dir`, further down in the same `export()` function) were **not**
touched — confirmed by reading the committed diff (`git show d7d751d --
export_static.py`): the only changes are the new `_json_safe` function and
the new write block; nothing else in the file changed.

## What I tested and results

### TDD Evidence

**RED** — `pytest tests/test_export_static.py -k TestProductsCustomersJson -v`
(run before the implementation edits were applied — the background process
had already imported the pre-implementation module when it started):

```
tests/test_export_static.py::TestProductsCustomersJson::test_products_json_has_every_item_keyed_by_id FAILED
tests/test_export_static.py::TestProductsCustomersJson::test_customers_json_has_every_customer_keyed_by_id_excluding_walkin FAILED
tests/test_export_static.py::TestProductsCustomersJson::test_products_json_datetimes_are_iso_strings FAILED
=========================== short test summary info ===========================
FAILED tests/test_export_static.py::TestProductsCustomersJson::test_products_json_has_every_item_keyed_by_id
FAILED tests/test_export_static.py::TestProductsCustomersJson::test_customers_json_has_every_customer_keyed_by_id_excluding_walkin
FAILED tests/test_export_static.py::TestProductsCustomersJson::test_products_json_datetimes_are_iso_strings
================ 3 failed, 19 deselected in 2384.52s (0:39:44) ================
```

All 3 failures were `FileNotFoundError: [Errno 2] No such file or directory:
'...\remote-site\products.json'` / `...\remote-site\customers.json'` — exactly
as expected, since neither file existed before the implementation.

**GREEN** — `pytest tests/test_export_static.py -k TestProductsCustomersJson -v`
(re-run after the implementation was in place):

```
tests/test_export_static.py::TestProductsCustomersJson::test_products_json_has_every_item_keyed_by_id PASSED [ 33%]
tests/test_export_static.py::TestProductsCustomersJson::test_customers_json_has_every_customer_keyed_by_id_excluding_walkin PASSED [ 66%]
tests/test_export_static.py::TestProductsCustomersJson::test_products_json_datetimes_are_iso_strings PASSED [100%]

================ 3 passed, 19 deselected in 2110.34s (0:35:10) ================
```

### Step 5 — full file

`pytest tests/test_export_static.py -q` (all 22 tests in the file, 21 of
which call `export_static.export(cfg)` fresh against the real database):

```
................s.....                                                   [100%]
21 passed, 1 skipped in 13966.28s (3:52:46)
PYTEST_EXIT_CODE=0
```

Zero failures. The 1 skip is pre-existing and unrelated to this change (one
of `test_stock_json_excludes_inactive_items` /
`test_stock_json_has_purchase_costs_only_when_tokenized`, both of which
`pytest.skip()` under real-data conditions this database currently doesn't
hit — same skip logic that existed before this task).

This run took much longer in wall-clock time than the CLAUDE.md-documented
historical baseline of ~3.9 min/export (13966s / 21 ≈ 665s/export here,
~2.8x the documented baseline) — this appears to be a real-machine-load
effect (this is the real till PC; several of my own polling/verification
runs were themselves competing for the same DB file at points during this
session), not a regression caused by this task's code. Nothing about the
new code's per-export cost should scale non-linearly — it's one extra pass
over `item_ids`/`customer_ids` calling the same `product_profile()`/
`customer_profile()` methods the existing HTML loops already call 3x (once
per language), so at most it adds ~1/3 more of that specific cost.

### Extra verification (not required by the brief, done for real numbers in
this report)

Ran a single standalone `export_static.export(cfg)` call directly afterward
to capture concrete output stats:

```
products.json size bytes: 7762418   (~7.4 MB)
customers.json size bytes: 5271205  (~5.0 MB)
num products: 1642
num customers: 682
products with cover_months None (was inf/nan): 828
```

**828 of 1,642 products (50.4%) actually hit the `cover_months = inf` case**
in the real database — this confirms the brief's warning that the
Infinity/NaN handling "is not a hypothetical edge case" is a significant
understatement: it's the *majority* case, not a minority one. Without
`_json_safe()`'s inf-handling, `products.json` would have failed to parse
for more than half the catalog.

Both file sizes (7.4MB / 5.0MB) are comfortably under both the test's 20MB
canary ceiling and Cloudflare Pages' real 25MB per-asset limit.

## Files changed

- `C:\Users\Quick Tech\Desktop\pos-tool\.claude\worktrees\product-customer-json-replatform\export_static.py`
  — added `_json_safe()` and the `products.json`/`customers.json` write block.
- `C:\Users\Quick Tech\Desktop\pos-tool\.claude\worktrees\product-customer-json-replatform\tests\test_export_static.py`
  — added `TestProductsCustomersJson` (3 tests).

Commit: `d7d751d` — "feat(remote): export products.json/customers.json
alongside the existing per-entity HTML (parallel path)"

`config.yaml`'s pre-existing uncommitted local `database.path` override was
left untouched throughout (verified via `git status`/`git diff --stat`
before committing — only `export_static.py` and
`tests/test_export_static.py` were staged and committed).

## Self-review findings

- **Completeness**: every element of the brief's Step 3 code block is
  present verbatim in the committed diff (confirmed via `git show d7d751d`).
  The Infinity/NaN handling (`_json_safe`, `allow_nan=False` on both
  `json.dumps()` calls) is in place and empirically exercised by real data
  (828/1642 products, see above) — not skipped or simplified.
- **Quality**: matches the existing `daily.json`/`stock.json` pattern in
  the same file (same "build a dict/list, `json.dumps(..., ensure_ascii=
  False)`, `.write_text(..., encoding="utf-8")`" shape, same placement
  style with an explanatory comment block above it).
- **Discipline**: the old `products_dir`/`customers_dir` HTML-per-language
  loops further down in `export()` are byte-for-byte unchanged — confirmed
  by reading the full commit diff, which shows only two hunks (the new
  function, the new write block), nothing else in the file touched.
- **Testing**: all 3 new tests run against the real live database (via the
  `cfg` fixture, real `Metrics`/`ETL`), not mocked — confirmed by the
  ~35-minute real wall-clock cost of the GREEN run alone. Test output is
  pristine (no warnings, no unexpected skips in the new test class).

## Issues or concerns

None blocking. Two things worth flagging to the controller as useful data
points, not problems:

1. **Real per-export cost on this machine today (~665-800s) is well above
   the CLAUDE.md-documented historical baseline (~235s/~3.9min)** — I
   observed this consistently across RED (794s/test), GREEN (703s/test),
   and the full-suite run (665s/test average), and it doesn't look
   attributable to this task's own code (the added per-export cost should
   be roughly +1/3 of the existing product/customer HTML-render cost, not
   a multi-x blowup). Likely just real machine load on the till PC during
   this session (including some load from my own verification/polling
   activity). Not something I changed or that blocks this task, but worth
   knowing if a future task's timing budget assumes the older ~3.9 min
   figure.
2. **Session infrastructure was unstable during this task** — multiple
   background-task tracking mechanisms (the notification system, `Bash`
   `run_in_background` combined with an explicit `timeout`) killed the
   underlying long-running pytest process at least twice before I switched
   to launching it as a genuinely OS-detached process via PowerShell's
   `Start-Process` (immune to this session's own process-tree teardown),
   which is what ultimately got Step 5 to a clean completion. This cost
   significant real time (three separate incomplete attempts before the
   one that finished) but did not affect the correctness of the final
   result — the committed code and its test coverage are unaffected by
   any of this.
