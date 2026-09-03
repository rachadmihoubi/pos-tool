## Task 3: `export_static.py` — write `products.json` / `customers.json` (parallel path, old loops untouched)

**Files:**
- Modify: `export_static.py`
- Test: `tests/test_export_static.py`

**Interfaces:**
- Consumes: `Metrics.product_profile(item_id)` / `Metrics.customer_profile(customer_id)` (existing, unmodified — see `poslib/metrics.py:1351` and `poslib/metrics.py:2001`), `app.row_dict`/`app.rows` (existing, unmodified — `app.py:199-236`), `ownerdata.competitor_prices_for_item(cfg, item_id)` (existing, unmodified).
- Produces: `out_dir / "products.json"` — a JSON object keyed by `str(item_id)`, each value shaped `{"summary": {...}, "family": {...}|null, "sales_history": [...], "purchase_history": [...], "competitor_prices": [...]}`. `out_dir / "customers.json"` — a JSON object keyed by `str(customer_id)`, each value shaped `{"summary": {...}, "receivable": {...}|null, "purchases": [...], "payments": [...]}`. Every value inside is JSON-safe (no `NaN`, no Python `datetime` objects — see Step 1). Task 4 consumes these two files by `fetch()`.

- [ ] **Step 1: Write the failing test**

In `tests/test_export_static.py`, add a new test class (near the existing product/customer export tests — search the file for `products_dir`/`customers_dir` to find the right neighborhood):

```python
class TestProductsCustomersJson:

    def test_products_json_has_every_item_keyed_by_id(self, cfg, monkeypatch, tmp_path):
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)
        data = json.loads((out_dir / "products.json").read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert len(data) > 0
        some_id = next(iter(data))
        entry = data[some_id]
        assert set(entry.keys()) == {"summary", "family", "sales_history",
                                      "purchase_history", "competitor_prices"}
        assert "item_name" in entry["summary"]
        # JSON round-trips cleanly with no NaN/Infinity tokens (json.loads
        # would already have raised on those with default settings, but
        # assert explicitly so a future change to allow_nan doesn't silently
        # let one back in).
        raw = (out_dir / "products.json").read_text(encoding="utf-8")
        assert "NaN" not in raw
        assert "Infinity" not in raw
        # Cloudflare Pages rejects any single asset over 25MB
        # (poslib/remote.py's _MAX_FILE_SIZE_BYTES - a rejection that fails
        # SILENTLY, only a log.warning, see remote.py:395-398). Reviewed
        # 2026-09-01: today's real data lands around 9-10MB, but
        # sales_history/purchase_history are capped at 200 rows/product
        # (not today's actual row count), so the bounded worst case is
        # materially higher - this assertion is a canary for growth, not a
        # today-only sanity check. If this ever fails, split products.json
        # into per-entity files rather than raising the cap blindly.
        assert len(raw.encode("utf-8")) < 20 * 1024 * 1024

    def test_customers_json_has_every_customer_keyed_by_id_excluding_walkin(
            self, cfg, monkeypatch, tmp_path):
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)
        data = json.loads((out_dir / "customers.json").read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert len(data) > 0
        some_id = next(iter(data))
        entry = data[some_id]
        assert set(entry.keys()) == {"summary", "receivable", "purchases", "payments"}
        assert "customer_name" in entry["summary"]
        raw = (out_dir / "customers.json").read_text(encoding="utf-8")
        assert len(raw.encode("utf-8")) < 20 * 1024 * 1024
        from poslib.metrics import Metrics
        from poslib.etl import ETL
        etl = ETL(cfg)
        conn = etl.connect()
        try:
            m = Metrics(conn, cfg)
            assert str(int(m.walkin_id)) not in data
        finally:
            conn.close()

    def test_products_json_datetimes_are_iso_strings(self, cfg, monkeypatch, tmp_path):
        _cfg_with_export_dir(monkeypatch, cfg, tmp_path)
        out_dir = export_static.export(cfg)
        data = json.loads((out_dir / "products.json").read_text(encoding="utf-8"))
        # Find any entry with at least one sales_history row and check its
        # ticket_time is a plain ISO-ish string, not a dict/list (which is
        # what an un-cleaned Timestamp would serialize to via a bad default).
        for entry in data.values():
            if entry["sales_history"]:
                ts = entry["sales_history"][0]["ticket_time"]
                assert isinstance(ts, str)
                assert ts[4] == "-" and ts[7] == "-"
                break
        else:
            pytest.fail("no product with sales_history found to check date serialization")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_export_static.py -k TestProductsCustomersJson -v`
Expected: FAIL — `products.json`/`customers.json` don't exist yet (`FileNotFoundError`).

- [ ] **Step 3: Write minimal implementation**

In `export_static.py`, add near the top (after the existing imports, before `PAGES`):

```python
def _json_safe(value):
    """
    Recursively convert a row_dict()/rows()-cleaned structure (which may
    still contain Python datetime objects, per app.py's rows()) into
    something json.dumps can serialize with allow_nan=False, matching the
    same "None for missing" convention every other JSON export in this
    file (daily_records, stock_records) already uses.

    row_dict()/rows() (app.py:199-236) already turn NaN into None, but NOT
    +/-inf - isnan(inf) is False, so it passes their cleaning untouched.
    metrics.py's item_movement() sets cover_months = np.inf for any item
    with no recent sale (metrics.py:1527-1531), which is a large share of
    a real catalog's dead stock - json.dumps would otherwise emit the bare
    token "Infinity", which is not valid JSON and makes every consumer's
    JSON.parse() throw. Caught in review before this was ever run for
    real - see this plan's Task 3 Step 4 test, which asserts against it.
    """
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    if isinstance(value, datetime.datetime):
        return value.isoformat(sep="T", timespec="seconds")
    if isinstance(value, datetime.date):
        return value.isoformat()
    return value
```

Then, inside `export()`, right after the existing `stock_records`/`stock_filename` block (after the `(out_dir / "_headers").write_text(...)` call, before `presets = _today_preset_ranges(today)`), add:

```python
        # Products/customers detail, replatformed from per-entity-per-
        # language pre-rendered HTML (see the old products_dir/customers_dir
        # loops further down, still present in parallel - see
        # docs/superpowers/plans/2026-09-01-product-customer-json-replatform.md)
        # to two JSON payloads consumed by templates/product_shell.html and
        # templates/customer_shell.html client-side, the same "one JSON
        # file, all entities, no per-language variant" shape as stock.json
        # above. Every value here is exactly what row_dict()/rows() already
        # produce for the live local dashboard - only the datetime -> ISO
        # string conversion (_json_safe) differs, since JSON has no native
        # datetime type.
        products_json: dict[str, dict] = {}
        for item_id in item_ids:
            profile = m.product_profile(item_id)
            if profile is None:
                raise RuntimeError(f"item {item_id} vanished mid-export")
            competitor_prices = ownerdata.competitor_prices_for_item(cfg, item_id)
            products_json[str(item_id)] = _json_safe({
                "summary": row_dict(profile["summary"]),
                "family": row_dict(profile["family"]),
                "sales_history": rows(profile["sales_history"], limit=200),
                "purchase_history": rows(profile["purchase_history"], limit=200),
                "competitor_prices": rows(competitor_prices),
            })
        (out_dir / "products.json").write_text(
            json.dumps(products_json, ensure_ascii=False, allow_nan=False), encoding="utf-8")

        customers_json: dict[str, dict] = {}
        for customer_id in customer_ids:
            profile = m.customer_profile(customer_id)
            if profile is None:
                raise RuntimeError(f"customer {customer_id} vanished mid-export")
            customers_json[str(customer_id)] = _json_safe({
                "summary": row_dict(profile["summary"]),
                "receivable": row_dict(profile["receivable"]),
                "purchases": rows(profile["purchases"], limit=200),
                "payments": rows(profile["payments"], limit=100),
            })
        (out_dir / "customers.json").write_text(
            json.dumps(customers_json, ensure_ascii=False, allow_nan=False), encoding="utf-8")
```

This duplicates the per-entity data fetch the existing `products_dir`/`customers_dir` loops (further down in the same function) already do — deliberately, per the Global Constraints' migration-order rule (old loops stay untouched and working until Task 6). Task 6 removes this duplication by deleting the old loops, not this new block.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_export_static.py -k TestProductsCustomersJson -v`
Expected: PASS. This is a real-database test (per the file's own module docstring) — expect it to take real time (the baseline single `export()` call already measured at several minutes on this machine's current data volume).

- [ ] **Step 5: Run the full export test file**

Run: `pytest tests/test_export_static.py -q`
Expected: all PASS, including the pre-existing tests (this task is purely additive — nothing existing was removed or changed).

- [ ] **Step 6: Commit**

```bash
git add export_static.py tests/test_export_static.py
git commit -m "feat(remote): export products.json/customers.json alongside the existing per-entity HTML (parallel path)"
```

---

