# Task 4 report — `templates/product_shell.html` + `templates/customer_shell.html`, wired into `export_static.py`, links updated

## What was implemented

Followed the brief (`.superpowers/sdd/2026-09-01-product-customer-json-replatform/task-4-brief.md`) closely — transcribed the exact HTML/JS given, verified every detail against the real source templates first.

1. **`templates/product_shell.html`** (new) — thin shell extending `base.html`, with `id="rd-*"` DOM hooks in place of Jinja value interpolation, `fetch("../products.json")` client-side lookup by `?id=` query param, and `RemoteDetail.renderProduct(...)` doing the DOM fill. Includes:
   - Photo, tiles, never-sold banner, family-compare section, competitor price table (with add/delete form — `action` built client-side since the shell doesn't know `item_id` at render time), sales history table, purchase history table — same section order as `templates/product_detail.html`, confirmed by reading that file in full before writing this one.
   - `competitorErrors` lookup object (`missing_name`/`invalid_price`/`invalid_date`) — the exact three codes `app.py:705-718`'s `product_competitor_price_add` route can produce, confirmed by reading that route directly.
   - Lang-switch fix: appends `window.location.search` to every `.lang-switch a.lang` link so switching language on a detail page keeps `?id=...`.

2. **`templates/customer_shell.html`** (new) — mirrors `templates/customer_detail.html`'s structure (page-head with segment pill → tiles incl. balance/credit-risk pill → at-risk banner → never-bought banner → purchases panel → payments panel), `fetch("../customers.json")`, `RemoteDetail.renderCustomer(...)`. Includes the `segmentLabels` (7 codes) and `creditRiskLabels` (3 codes) lookup objects built via Jinja loops, and the identical lang-switch-search-preserving fix.
   - Verified: the balance tile's credit-risk pill sits inside `.value` (`#rd-balance-amount` + `#rd-credit-risk-pill`), not in `.sub` (`#rd-balance-sub`, which is only the "owes X, Y days" note) — matches `customer_detail.html:42-50` exactly.

3. **`export_static.py` wiring**:
   - Added `shutil.copy2(PROJECT_ROOT / "static" / "remote-detail.js", static_dir / "remote-detail.js")` right after the existing `style.css` copy, inside the per-language static-dir block.
   - Added a block right after the `for slug in NESTED_PAGES:` loop (still inside `for lang in LANGUAGES:`), rendering `product_shell.html`/`customer_shell.html` directly via `render_template` inside `app.test_request_context(...)` (same pattern as the ticket/purchase/product/customer per-entity loops further down) and writing to `lang_dir / "product.html"` / `lang_dir / "customer.html"` — one shell per language, not per entity.

4. **Four link-template updates** (`is_static_export` branch, `page_product`/`page_customer` `url_for` else-branch unchanged):
   - `templates/catalog.html:33`
   - `templates/products.html:85, 128, 170, 212, 259, 299` (all six — verified via grep after edit, no `page_product` occurrences left unwrapped, no double-wrapping)
   - `templates/receivables.html:78`
   - `templates/customers.html:155`

5. **Tests** (`tests/test_export_static.py`, new `TestProductCustomerShells` class, verbatim from the brief's Step 5): one-shell-per-language check (plus confirms the old per-entity trees still exist, parallel path per Task 6's future removal), static-labels-and-fetch-call check, catalog-links-point-at-shell check.

## Pre-work verification (before writing any code)

- Read `templates/product_detail.html` and `templates/customer_detail.html` in full to confirm section order and structure the brief's shells needed to reproduce.
- Confirmed `static/remote-detail.js` (Task 2) already exports exactly the `RemoteDetail.renderProduct`/`renderCustomer`/`renderTable` shape the shells' scripts call — no changes needed there.
- Confirmed `poslib/i18n.py`'s `js_format()` (Task 1) already returns all fields the shells' `fmt` variable needs (`thousands`, `decimal`, `currency`, `money_format`, `percent_format`, `date_format`, `datetime_format`, `dash`).
- Confirmed every translation key referenced by both shells (`app.loading`, `app.no_findings`, `common.not_measurable`, all `products.competitor_error_*`, all `segments.*`, all `customers.credit_risk_*`, `customers.detail_owes_note`, `customers.detail_no_balance`, etc.) exists in all three `locales/*.json` files via a script check — no missing keys.
- Read `app.py:688-731` (`product_competitor_price_add`) directly and confirmed the exact three error codes (`missing_name`, `invalid_price`, `invalid_date`) match the brief and the shell's `competitorErrors` lookup.
- Confirmed `config.yaml`'s `database.path` is still this machine's own value (`E:/Base de données4.dblx`, matching CLAUDE.md's machine-identity table) before running any real-DB test — the diff was pre-existing (uncommitted, expected per CLAUDE.md), not touched by this task.

## Testing — TDD evidence

### RED (export_static.py wiring stashed via `git stash push -m "task4-red-check" -- export_static.py`; templates/link-changes/tests left in place)

Command: `python -m pytest tests/test_export_static.py -k "TestProductCustomerShells" -v`

```
tests/test_export_static.py::TestProductCustomerShells::test_writes_one_shell_per_language_not_per_entity FAILED [ 33%]
tests/test_export_static.py::TestProductCustomerShells::test_shell_contains_static_labels_and_fetch_call FAILED [ 66%]
tests/test_export_static.py::TestProductCustomerShells::test_catalog_links_point_at_shell_when_static_export PASSED [100%]

FAILED tests/test_export_static.py::TestProductCustomerShells::test_writes_one_shell_per_language_not_per_entity
  AssertionError: assert False
   +  where False = is_file()
   +    where is_file = (.../remote-site/en/product.html).is_file
FAILED tests/test_export_static.py::TestProductCustomerShells::test_shell_contains_static_labels_and_fetch_call
  FileNotFoundError: [Errno 2] No such file or directory: '...remote-site\en\product.html'
=========== 2 failed, 1 passed, 22 deselected in 2077.20s (0:34:37) ===========
EXIT_CODE=1
```

Failed for the right reason (missing `product.html`/`customer.html`, since the wiring was stashed out). The third test passed independently since it only depends on the already-applied link-template change, not the wiring — expected, not a bug.

Restored the wiring with `git stash pop`.

### GREEN (wiring restored)

Command: `python -m pytest tests/test_export_static.py -k "TestProductCustomerShells" -v`

```
tests/test_export_static.py::TestProductCustomerShells::test_writes_one_shell_per_language_not_per_entity PASSED [ 33%]
tests/test_export_static.py::TestProductCustomerShells::test_shell_contains_static_labels_and_fetch_call PASSED [ 66%]
tests/test_export_static.py::TestProductCustomerShells::test_catalog_links_point_at_shell_when_static_export PASSED [100%]

================ 3 passed, 22 deselected in 2079.91s (0:34:39) ================
EXIT_CODE=0
```

### Full-file regression (Step 6) — `pytest tests/test_export_static.py -q`

STATUS AT TIME OF WRITING: launched, still running. Given this machine's measured export cost tonight (~650-800s/export) and this file's own ~22 export-calling tests, this run could take multiple hours. Per the task instructions' explicit guidance for long-running real-DB tests on this session, this was launched as a genuinely OS-detached process (PowerShell `Start-Process` running `cmd.exe /c ... pytest ... > log 2>&1 & echo EXIT_CODE=%ERRORLEVEL% >> log`), independent of this session's own tooling lifetime, rather than relying solely on the Bash tool's `run_in_background` for a run this long.

- Log file: `.superpowers/sdd/2026-09-01-product-customer-json-replatform/logs/task4-full-regression.log`
- To check status later: `Get-Content` that log file, or check for the trailing `EXIT_CODE=N` line to know it finished, or `tail -f` it.
- **This report will be updated with the final result once it completes.** If this report still shows this run as "still running" when read later, the full-suite regression has not yet been confirmed and should be re-checked/re-run before treating this task as fully verified.

## Files changed

- `templates/product_shell.html` (new)
- `templates/customer_shell.html` (new)
- `export_static.py` (remote-detail.js copy + shell render wiring)
- `templates/catalog.html` (1 link)
- `templates/products.html` (6 links)
- `templates/receivables.html` (1 link)
- `templates/customers.html` (1 link)
- `tests/test_export_static.py` (new `TestProductCustomerShells` class)

## Self-review findings

- Verified `templates/products.html`'s 6 link occurrences all correctly transformed via `grep -n "page_product\|product.html?id="` — all 6 lines show the `is_static_export` branch, none left as bare `url_for`, none double-wrapped.
- Verified locale keys exist in all 3 languages before writing the templates (see "Pre-work verification" above), avoiding a Jinja `t()` KeyError surprise mid-export.
- Verified the exact competitor-error codes against `app.py`'s route source rather than trusting the brief's citation blindly.
- No stray debug output, no TODOs left in the new templates.
- Did not touch `config.yaml` (pre-existing uncommitted diff, this machine's own correct value per CLAUDE.md's machine-identity table).
- Did not run any destructive git command; used `git stash push -- export_static.py` / `git stash pop` (non-destructive, scoped to one file) to get a genuine RED run without hand-reverting code.

## Concerns

- The Step 6 full-file regression run had not finished at the time this report section was first written — see the note above under "Full-file regression (Step 6)". This report / the final status message will reflect the actual outcome once available; if not available, this is reported as DONE_WITH_CONCERNS per the task instructions' own guidance for this situation, not as unqualified DONE.
- Commit (Step 7) is intentionally **not yet created** — waiting on the full regression result per the brief's own ordering ("Run the full file regression test... before committing").
