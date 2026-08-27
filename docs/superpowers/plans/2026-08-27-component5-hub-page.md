# Component 5 Hub Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the multi-store hub's own static Cloudflare Pages site - a
switcher (links to each onboarded store's own dashboard) plus a client-side
search box that fetches every store's `stock.json` and merges/filters the
results, per `docs/superpowers/specs/2026-08-27-component5-hub-design.md`.

**Architecture:** A hand-written static site (`hub-site/`: `index.html`,
`style.css`, `app.js`, `stores.json`) with no Python templating and no
per-store data of its own - it is deployed once and rarely redeployed. The
JS fetches each store's `stock.json` directly (already CORS-open, no login,
per the spec's empirically-verified Access-bypass design) via
`Promise.allSettled`, so one unreachable store never blocks the others.
Deployment reuses `poslib/remote.py`'s already-proven Cloudflare Pages
upload flow (Task 1 adds optional `project`/`export_dir` overrides to
`push_remote` so it can push an arbitrary directory to an arbitrary
project, not just a store's own `config.yaml`-configured one) via a new
one-off script, `tools/deploy_hub.py`, run by hand from this dev PC.

**Tech Stack:** Python 3.12, pytest, vanilla JS/HTML/CSS (no build step, no
framework - matches the rest of the project's static export, which has no
JS bundler either).

## Global Constraints

- Never write to the source `.dblx` file - not touched by this plan at all
  (the hub has no database access), noted only because it is this
  project's one absolute rule.
- No merged/summed numbers across stores, no auto-matching of products
  across stores - the hub shows matching rows side by side and lets the
  owner's own eyes do the matching (per the master multi-store spec,
  reaffirmed in `docs/superpowers/specs/2026-08-27-component5-hub-design.md`
  decision #4).
- `stock.json` fields are exactly `item_no`, `name`, `stock`, `price` (see
  `export_static.py:180-190`) - no cost, no margin. The hub must not invent
  or expect any other field.
- Every store's own existing `push_remote(cfg)` call (from `watcher.py`)
  must keep working unchanged - Task 1's new parameters must be optional
  and additive only.
- Task 4 in this plan touches the real, live Cloudflare account (creating
  a new Pages project and Access applications). Do not run Task 4's
  Cloudflare-facing steps without the user's explicit go-ahead at that
  point, even though the code/file tasks before it (1-3) are safe to do
  without asking.

---

### Task 1: Let `push_remote` push an arbitrary directory to an arbitrary project

**Files:**
- Modify: `poslib/remote.py:166-176` (the `push_remote` signature and its
  first two guard clauses)
- Test: `tests/test_remote.py`

**Interfaces:**
- Consumes: nothing new - reuses `poslib/remote.py`'s existing internal
  helpers (`_iter_export_files`, `_cf_hash`, `_get_upload_token`,
  `_upload_assets`, `_upsert_hashes`, `_create_deployment`) exactly as
  they are today.
- Produces: `push_remote(cfg: Config, *, project: str | None = None, export_dir: Path | None = None) -> bool`
  - `project`/`export_dir` omitted or `None` -> identical behavior to
    today (reads `remote.cloudflare_project_name` / `remote.export_dir`
    from `cfg`).
  - `project`/`export_dir` given -> those values are used instead,
    `cfg` is still the source of `CLOUDFLARE_API_TOKEN` /
    `CLOUDFLARE_ACCOUNT_ID` either way. Task 3's `tools/deploy_hub.py`
    is the first caller to pass these.

- [x] **Step 1: Write the failing tests**

Add to `tests/test_remote.py`, inside `class TestPushRemoteHappyPath`:

```python
    def test_explicit_project_overrides_config_project(self, tmp_path, monkeypatch):
        export_dir = _make_export_dir(tmp_path, {"index.html": "hi"})
        cfg = FakeConfig(project="", export_dir=export_dir)  # config has no project set
        session = FakeSession(deploy_response=_ok({"url": "https://hub-site.pages.dev"}))
        _patch_session(monkeypatch, session)

        result = remote.push_remote(cfg, project="hub-site")

        assert result is True
        deploy_call = [c for c in session.calls if c[0] == "POST" and c[1].endswith("/deployments")][0]
        assert "/pages/projects/hub-site/" in deploy_call[1]

    def test_explicit_export_dir_overrides_config_export_dir(self, tmp_path, monkeypatch):
        real_dir = _make_export_dir(tmp_path, {"index.html": "real"})
        cfg = FakeConfig(export_dir=tmp_path / "does-not-exist")  # config points nowhere
        session = FakeSession()
        _patch_session(monkeypatch, session)

        result = remote.push_remote(cfg, export_dir=real_dir)

        assert result is True
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_remote.py -k "override" -v`
Expected: FAIL - `push_remote() got an unexpected keyword argument 'project'`

- [x] **Step 3: Implement the optional overrides**

In `poslib/remote.py`, replace the `push_remote` signature and its first
two blocks (lines 166-181):

```python
def push_remote(cfg: Config, *, project: str | None = None,
                 export_dir: Path | None = None) -> bool:
    """
    Deploy a directory to Cloudflare Pages. Returns True on success, False
    on any problem at all - never raises.

    project/export_dir let a caller push an arbitrary directory to an
    arbitrary project under the same account credentials, instead of the
    store's own config.yaml-configured project - used by
    tools/deploy_hub.py to push the multi-store hub (which has no
    config.yaml/remote-site of its own) with this same proven upload code
    rather than reimplementing it. Every store's own watcher call
    (push_remote(cfg), no extra arguments) is unaffected - both default to
    None, which falls back to today's config-read behavior exactly.
    """
    if project is None:
        project = str(cfg.get("remote.cloudflare_project_name", "")).strip()
    else:
        project = project.strip()
    if not project:
        log.warning("remote.cloudflare_project_name is not set - skipping push. "
                    "See SETUP.md to create a Cloudflare Pages project.")
        return False

    if export_dir is None:
        export_dir = cfg.path("remote.export_dir", "remote-site")
    if not export_dir.is_dir():
        log.warning("Nothing to push yet - %s does not exist. Run the export first.",
                    export_dir)
        return False
```

The rest of the function (from `api_token = cfg.secret(...)` onward) is
unchanged - `project` and `export_dir` are now plain local variables
either way, so every later reference to them already works.

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_remote.py -v`
Expected: PASS - all existing tests plus the two new ones (existing tests
are unaffected since they never pass `project`/`export_dir`, so both
default to `None` and hit the same code path as before).

- [x] **Step 5: Commit**

```bash
git add poslib/remote.py tests/test_remote.py
git commit -m "feat(remote): let push_remote target an arbitrary project/directory"
```

---

### Task 2: Build the hub's static site

**Files:**
- Create: `hub-site/index.html`
- Create: `hub-site/style.css`
- Create: `hub-site/app.js`
- Create: `hub-site/stores.json`
- Test: `tests/test_hub_site.py`

**Interfaces:**
- Consumes: each store's `stock.json` at the URL listed in
  `hub-site/stores.json` - schema `[{item_no: str|null, name: str,
  stock: float|null, price: float|null}, ...]` per `export_static.py:180-190`.
- Produces: a static site directory (`hub-site/`) that Task 3's
  `tools/deploy_hub.py` pushes as-is. No other task depends on this one's
  internals beyond the directory existing.

- [x] **Step 1: Write `hub-site/stores.json`**

```json
{
  "stores": [
    {"name": "Pro Makeup Mihoubi", "url": "https://promakeupmihoubipos.pages.dev/stock.json"}
  ]
}
```

This starts with the one store already live. When a second/third store is
onboarded (Task 4's provisioning work, not yet built), add another
`{"name": ..., "url": ...}` entry here and redeploy the hub
(`tools/deploy_hub.py`) - this file is the hub's only per-store
configuration, hand-maintained on purpose (see the design spec's "rarely
redeployed" framing).

- [x] **Step 2: Write `hub-site/style.css`**

```css
:root {
  color-scheme: light;
}

body {
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  max-width: 900px;
  margin: 0 auto;
  padding: 24px 16px 64px;
  color: #1a1a1a;
}

header h1 {
  margin-bottom: 4px;
}

header p {
  color: #555;
  margin-top: 0;
}

#store-links {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 16px 0 32px;
}

.store-link {
  display: inline-block;
  padding: 8px 14px;
  border: 1px solid #ccc;
  border-radius: 6px;
  text-decoration: none;
  color: #1a1a1a;
}

.store-link:hover {
  background: #f4f4f4;
}

#search-box {
  width: 100%;
  box-sizing: border-box;
  padding: 10px 12px;
  font-size: 16px;
  border: 1px solid #ccc;
  border-radius: 6px;
}

#store-status {
  margin: 10px 0;
  font-size: 14px;
}

#store-status .ok {
  color: #1a7a1a;
  margin-right: 12px;
}

#store-status .unreachable {
  color: #a11;
  margin-right: 12px;
}

#results-table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 16px;
}

#results-table th, #results-table td {
  text-align: left;
  padding: 6px 8px;
  border-bottom: 1px solid #eee;
  font-size: 14px;
}

#results-table th {
  border-bottom: 2px solid #ccc;
}
```

- [x] **Step 3: Write `hub-site/app.js`**

```js
// app.js - hub page logic: renders store links, fetches every store's
// stock.json (CORS-open, no login needed - see
// docs/superpowers/specs/2026-08-27-component5-hub-design.md), merges the
// results client-side, and supports a live text search. A store whose
// fetch fails (offline PC, no internet, still being provisioned) is shown
// as "unreachable" instead of breaking the other stores' results -
// Promise.allSettled, not Promise.all, is what keeps one bad store from
// taking down the rest.

let allItems = [];

async function loadStores() {
  const resp = await fetch("stores.json");
  const data = await resp.json();
  return data.stores.filter(s => s.url);
}

function renderStoreLinks(stores) {
  const el = document.getElementById("store-links");
  el.innerHTML = stores.map(s => {
    const dashboardUrl = s.url.replace(/\/stock\.json$/, "/");
    return `<a class="store-link" href="${dashboardUrl}" target="_blank" rel="noopener">${s.name}</a>`;
  }).join(" ");
}

async function fetchStoreStock(store) {
  const resp = await fetch(store.url, { mode: "cors" });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const items = await resp.json();
  return items.map(item => ({ ...item, store: store.name }));
}

async function loadAllStock(stores) {
  const statusEl = document.getElementById("store-status");
  const results = await Promise.allSettled(stores.map(fetchStoreStock));

  const statusParts = [];
  const items = [];
  results.forEach((result, i) => {
    const store = stores[i];
    if (result.status === "fulfilled") {
      items.push(...result.value);
      statusParts.push(`<span class="ok">${store.name}: ${result.value.length} items</span>`);
    } else {
      statusParts.push(`<span class="unreachable">${store.name}: unreachable</span>`);
    }
  });
  statusEl.innerHTML = statusParts.join("");
  return items;
}

function renderResults(items) {
  const body = document.getElementById("results-body");
  body.innerHTML = items.map(item => `
    <tr>
      <td>${item.store}</td>
      <td>${item.item_no ?? ""}</td>
      <td>${item.name ?? ""}</td>
      <td>${item.stock ?? ""}</td>
      <td>${item.price ?? ""}</td>
    </tr>
  `).join("");
}

function applySearch() {
  const q = document.getElementById("search-box").value.trim().toLowerCase();
  if (!q) {
    renderResults([]);
    return;
  }
  const filtered = allItems.filter(item =>
    (item.name ?? "").toLowerCase().includes(q) ||
    (item.item_no ?? "").toLowerCase().includes(q)
  );
  renderResults(filtered.slice(0, 200));
}

async function init() {
  const stores = await loadStores();
  renderStoreLinks(stores);
  allItems = await loadAllStock(stores);
  document.getElementById("search-box").addEventListener("input", applySearch);
}

init();
```

- [x] **Step 4: Write `hub-site/index.html`**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Shop Analysis - Store Hub</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<header>
  <h1>Store Hub</h1>
  <p>Pick a store's own dashboard, or search stock across every store.</p>
</header>

<section id="store-links" aria-label="Store dashboards"></section>

<section id="stock-search">
  <h2>Search stock across stores</h2>
  <input id="search-box" type="search" placeholder="Item name or code...">
  <div id="store-status"></div>
  <table id="results-table">
    <thead>
      <tr><th>Store</th><th>Item #</th><th>Name</th><th>Stock</th><th>Price</th></tr>
    </thead>
    <tbody id="results-body"></tbody>
  </table>
</section>

<script src="app.js"></script>
</body>
</html>
```

- [x] **Step 5: Write the failing structural tests**

Create `tests/test_hub_site.py`:

```python
"""
Tests for hub-site/ - the multi-store hub's static site.

There is no JS test runner in this project (no Node.js at all - see
CLAUDE.md's "Environment note"), so these are structural checks only: the
files exist, stores.json is valid and shaped correctly, and the HTML/JS
reference each other and the fields stock.json actually has. Not a
substitute for opening the deployed page in a real browser once it's live.
"""

from __future__ import annotations

import json
from pathlib import Path

HUB_DIR = Path(__file__).resolve().parent.parent / "hub-site"


def test_all_expected_files_exist():
    for name in ("index.html", "style.css", "app.js", "stores.json"):
        assert (HUB_DIR / name).is_file(), f"missing hub-site/{name}"


def test_stores_json_is_valid_and_shaped_correctly():
    data = json.loads((HUB_DIR / "stores.json").read_text(encoding="utf-8"))
    assert "stores" in data
    assert isinstance(data["stores"], list)
    assert len(data["stores"]) >= 1
    for store in data["stores"]:
        assert isinstance(store["name"], str) and store["name"]
        assert isinstance(store["url"], str) and store["url"].startswith("https://")
        assert store["url"].endswith("/stock.json")


def test_index_html_references_its_own_assets():
    html = (HUB_DIR / "index.html").read_text(encoding="utf-8")
    assert 'href="style.css"' in html
    assert 'src="app.js"' in html
    assert 'id="search-box"' in html
    assert 'id="store-links"' in html
    assert 'id="results-body"' in html


def test_app_js_uses_the_real_stock_json_field_names():
    js = (HUB_DIR / "app.js").read_text(encoding="utf-8")
    for field in ("item_no", "name", "stock", "price"):
        assert field in js, f"app.js never references stock.json's {field!r} field"
    assert "stores.json" in js
    assert "Promise.allSettled" in js, \
        "must use allSettled, not Promise.all, so one unreachable store doesn't hide the rest"
```

- [x] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_hub_site.py -v`
Expected: PASS (this task writes both the files and the tests together,
so there is no red step here beyond a typo check - run the tests to
confirm none)

- [x] **Step 7: Commit**

```bash
git add hub-site/ tests/test_hub_site.py
git commit -m "feat(hub): add the multi-store hub's static site"
```

---

### Task 3: One-off deploy script for the hub

**Files:**
- Create: `tools/deploy_hub.py`
- Test: `tests/test_deploy_hub.py`

**Interfaces:**
- Consumes: `poslib.remote.push_remote(cfg, *, project=..., export_dir=...)`
  from Task 1; `poslib.config.get_config()` / `setup_logging()`.
- Produces: a CLI entry point, `python tools/deploy_hub.py --project NAME
  [--dir PATH]` (default `--dir hub-site`), exit code 0 on success, 1 on
  any failure - run by hand from this dev PC, never by the watcher or any
  scheduled task (the hub has no per-store data that changes on its own).

- [x] **Step 1: Write the failing tests**

Create `tests/test_deploy_hub.py`:

```python
"""
Tests for tools/deploy_hub.py - the hub's manual, occasional deploy script.

push_remote itself is already fully tested in tests/test_remote.py; these
tests only cover this script's own thin argument-handling/wiring layer,
with push_remote mocked out.
"""

from __future__ import annotations

import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import deploy_hub  # noqa: E402


class FakeConfig:
    pass


def test_missing_dir_fails_without_calling_push_remote(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(deploy_hub, "get_config", lambda: FakeConfig())
    monkeypatch.setattr(deploy_hub, "setup_logging", lambda cfg: None)
    called = []
    monkeypatch.setattr(deploy_hub.remote, "push_remote",
                        lambda cfg, **kw: called.append(kw) or True)

    rc = deploy_hub.main(["--project", "hub-site", "--dir", str(tmp_path / "nope")])

    assert rc == 1
    assert called == []
    assert "does not exist" in capsys.readouterr().out


def test_success_passes_project_and_resolved_dir_to_push_remote(tmp_path, monkeypatch, capsys):
    hub_dir = tmp_path / "hub-site"
    hub_dir.mkdir()
    monkeypatch.setattr(deploy_hub, "get_config", lambda: FakeConfig())
    monkeypatch.setattr(deploy_hub, "setup_logging", lambda cfg: None)
    captured = {}

    def fake_push(cfg, *, project, export_dir):
        captured["project"] = project
        captured["export_dir"] = export_dir
        return True

    monkeypatch.setattr(deploy_hub.remote, "push_remote", fake_push)

    rc = deploy_hub.main(["--project", "my-hub", "--dir", str(hub_dir)])

    assert rc == 0
    assert captured["project"] == "my-hub"
    assert captured["export_dir"] == hub_dir.resolve()
    assert "Pushed" in capsys.readouterr().out


def test_push_failure_returns_1(tmp_path, monkeypatch, capsys):
    hub_dir = tmp_path / "hub-site"
    hub_dir.mkdir()
    monkeypatch.setattr(deploy_hub, "get_config", lambda: FakeConfig())
    monkeypatch.setattr(deploy_hub, "setup_logging", lambda cfg: None)
    monkeypatch.setattr(deploy_hub.remote, "push_remote", lambda cfg, **kw: False)

    rc = deploy_hub.main(["--project", "my-hub", "--dir", str(hub_dir)])

    assert rc == 1
    assert "failed" in capsys.readouterr().out.lower()
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_deploy_hub.py -v`
Expected: FAIL - `ModuleNotFoundError: No module named 'deploy_hub'`

- [x] **Step 3: Write `tools/deploy_hub.py`**

```python
"""
tools/deploy_hub.py - one-time / occasional manual push of the multi-store
hub's static site to its own Cloudflare Pages project.

Not run by any watcher or scheduled task - the hub has no per-store data of
its own (see docs/superpowers/specs/2026-08-27-component5-hub-design.md),
so it is redeployed by hand, on rachad's own dev PC, only when hub-site/'s
files change (a new store added to stores.json, a design tweak). Reuses
poslib/remote.py's already-proven Cloudflare Pages upload flow - never
reimplements it.

Usage:
    python tools/deploy_hub.py --project promakeupmihoubi-hub

Requires CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID in this dev PC's
own .env - the same credentials already used to push the first store's
data (Pages:Edit scope covers pushing to any project on the account, not
just one).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from poslib.config import get_config, setup_logging
from poslib import remote


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True,
                        help="Cloudflare Pages project name for the hub, "
                             "e.g. promakeupmihoubi-hub")
    parser.add_argument("--dir", default="hub-site",
                        help="Directory to push (default: hub-site)")
    args = parser.parse_args(argv)

    export_dir = Path(args.dir).resolve()
    if not export_dir.is_dir():
        print(f"{export_dir} does not exist.")
        return 1

    cfg = get_config()
    setup_logging(cfg)

    ok = remote.push_remote(cfg, project=args.project, export_dir=export_dir)
    if not ok:
        print("Push failed - see the log above for the reason.")
        return 1

    print(f"Pushed {export_dir} to Cloudflare Pages project '{args.project}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_deploy_hub.py -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add tools/deploy_hub.py tests/test_deploy_hub.py
git commit -m "feat(hub): add tools/deploy_hub.py for manual hub deploys"
```

---

### Task 4: Live Cloudflare setup (manual - requires go-ahead before running)

This task touches the real, live Cloudflare account. Do not run its
Cloudflare-facing steps (4.2 onward) without the user's explicit
confirmation at that point - Tasks 1-3 above are safe to do unattended,
this one is not.

**Files:** none (operational steps only - `hub-site/stores.json` may get a
follow-up edit in a later, separate commit once real store URLs exist
beyond the one already in Task 2's Step 1).

- [ ] **Step 1: Confirm with the user before proceeding**

State plainly what's about to happen: creating a new Cloudflare Pages
project (`promakeupmihoubi-hub` or similar) and Access applications on the
live account, and - separately - adding the missing `/stock.json` Bypass
Access application to the already-live `promakeupmihoubipos` project (it
currently only has the broad owner-only application; the narrow bypass one
was only ever created on the disposable throwaway project used for
verification, per the design spec's "Empirically verified" section). Wait
for explicit go-ahead.

- [ ] **Step 2: Add the missing `/stock.json` Bypass Access application to the live store**

Via the Cloudflare Zero Trust dashboard (or one throwaway API call, same
shape as the spec's already-verified step 3): create a second Access
application scoped to `promakeupmihoubipos.pages.dev/stock.json` with a
`bypass` policy, leaving the existing broad owner-only application
untouched. Wait ~1-2 minutes for Access propagation (per the spec's own
note), then confirm `GET https://promakeupmihoubipos.pages.dev/stock.json`
returns 200 with JSON, not a 302 redirect.

- [ ] **Step 3: Create the hub's own Cloudflare Pages project**

Via the dashboard (Pages -> Create a project -> Direct Upload) or
`tools/deploy_hub.py` itself, which creates the project implicitly on
first push if it doesn't exist yet (Cloudflare's Direct Upload API creates
a project on first deployment to a new project name). Run:

```bash
python tools/deploy_hub.py --project promakeupmihoubi-hub
```

Confirm the printed URL loads the hub page and its store link/search box
render.

- [ ] **Step 4: Gate the hub itself with an owner-only Access application**

The hub aggregates all onboarded stores' stock + prices in one place -
wider exposure than any single store's `/stock.json` (which the owner
already accepted). Create one Access application on
`promakeupmihoubi-hub.pages.dev` (the whole hostname, no path restriction)
with an `allow` policy scoped to the owner's email - identical shape to
each store's existing broad application. The hub's own JS still reaches
each store's `/stock.json` without a login, since those are separate
origins already gated to `bypass` for that one path - only the hub page
itself needs a login.

- [ ] **Step 5: Verify end-to-end from a real phone**

Open the hub's URL from a phone (not this dev PC), confirm the Access
login prompt appears, log in as the owner, confirm the store link opens
the real store dashboard and the search box returns real stock rows for a
known item.
