# Packaging & Installer Implementation Plan

> **STATUS: COMPLETE.** All 7 tasks done and committed (`a66bc3d`,
> `13377a1`, `584bc26`, `817e4fd`, `d2f5560`, `ce5b8c7`, `2eb2543`,
> `3f8c69a`, `f8cb8ec`). Full task-by-task history, deviations, and
> deferred findings are in the SDD ledger at
> `.superpowers/sdd/2026-08-25-packaging-installer/progress.md` (not
> committed to git, local-machine only). Two things carried forward from
> that ledger, not yet resolved:
> 1. **A test install from Task 7's verification is still on this dev
>    PC** at `C:\Program Files\Shop Analysis\` (with `unins000.exe`) —
>    uninstall was blocked pending explicit user go-ahead (a
>    system-modifying action outside the repo). Still sitting there as of
>    2026-08-26.
> 2. **A plan-mandated risk was accepted, not fixed:** `console=False` +
>    `disable_windowed_traceback=False` in `packaging/pos-tool.spec` means
>    an unhandled exception on a customer's unattended till PC would hang
>    invisibly with no console and no crash log. User ruling: "Accept
>    as-is for now" — candidate to revisit alongside Component 3
>    (auto-update/health-check work), not forgotten.
>
> Component 1 (packaging) in CLAUDE.md's "Customer distribution" section
> refers to this plan.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package pos-tool as a normal Windows installer (`Setup.exe`) that a non-technical shop owner can run — Next → Next → Install → desktop shortcut — with no Python, Node, git, or terminal window ever visible, and reach a working dashboard on a clean machine.

**Architecture:** PyInstaller in `--onedir` mode freezes the app into a folder of files plus one `ShopAnalysis.exe` (spec explicitly rejects `--onefile`: its startup is a known source of environment-specific breakage with pandas/numpy, and undebuggable over the phone with a non-technical owner — see `docs/superpowers/specs/2026-08-25-installer-updates-multistore-design.md` lines 139-151). Inno Setup wraps that onedir folder into a normal `Setup.exe` wizard. A new `poslib/paths.py` module gives every part of the app one place to ask "where do I read the bundled app from, and where do I write my data" — gated on `sys.frozen` so this dev PC's behavior is provably unchanged when not running from a frozen build.

**Tech Stack:** PyInstaller (onedir), Inno Setup (`iscc`), Python 3.12.10 (this dev PC's `.venv`), existing Flask/pandas/PyYAML stack (`requirements.txt`).

## Global Constraints

- **Never write to the source `.dblx` file** — untouched by this plan; it only affects how the app is packaged and where it stores its own data, not how it reads the POS database (project-wide rule, `CLAUDE.md`).
- **`--onedir`, never `--onefile`** — spec mandate (`docs/superpowers/specs/2026-08-25-installer-updates-multistore-design.md:139-151`).
- **`console=False` on every packaged entry point** — no terminal window, ever, matching the "zero technical involvement" goal.
- **Every new path-resolution behavior must be gated on `getattr(sys, "frozen", False)`** — when not frozen (this dev PC, `pytest`, `start.bat`), behavior must be bit-for-bit identical to today. The existing 232-passing test suite is the proof; it must still pass unmodified after every task in this plan.
- **No secrets, no real business data, no this-shop's cached photos or owner-entered data ship in the installer.** The packaging step uses an explicit allowlist of files to bundle — never a wildcard glob of the project root. Excluded, explicitly: `.env`, `cache.db`, `data/owner.db`, `logs/`, `digests/`, `backups/`, `remote-site/`, `static/photo-cache/*` (this shop's real product photos).
- **The bundled `config.yaml` template ships with `remote.enabled: false`.** Remote viewing needs `wrangler`, which needs Node.js — PyInstaller freezes Python, not Node — so whether/how a customer PC gets remote viewing is unresolved and explicitly out of scope here, not silently broken.
- **Non-goal: process auto-start.** This plan's finish line is "the installed shortcut launches a working dashboard" — not "the dashboard and watcher start automatically at login." `install-startup.bat`'s Task Scheduler wiring is not replicated in this plan; a future plan addresses background auto-start for the packaged build (Task Scheduler entries pointed at the new exe, or a tray-icon supervisor). Opening the dashboard directly still shows current data because `app.py`'s `main()` already calls `ensure_cache_ready()` (a synchronous rebuild if the cache is stale) before serving — it just won't live-refresh on new sales without the watcher also running.
- **Non-goal: Component 2 (DB auto-detect setup screen) and Component 3 (silent auto-updates).** Both are separate future plans per the spec's own build-order sequencing. This plan's bundled `config.yaml` template ships with a deliberately-invalid placeholder `database.path`, which is enough for `Config()` to load without error (`ConfigError` only fires on an *empty* path) while `ETL` raises a clear, caught `ETLError` naming the missing file — the dashboard still starts and shows a friendly error page (`app.py:1045-1050`, already-existing behavior). That friendly error page *is* the working dashboard this plan targets on a fresh install with no real database configured yet.

---

## File Structure

| File | Responsibility |
|---|---|
| `poslib/paths.py` (new) | `is_frozen()`, `app_root()` (read-only bundle root), `user_data_dir()` (writable per-user root). The one place frozen-vs-dev path logic lives. |
| `poslib/config.py` (modify) | `PROJECT_ROOT`, `DEFAULT_CONFIG_PATH`, `DEFAULT_ENV_PATH`, and `Config.path()` resolve through `poslib/paths.py` instead of a hardcoded `Path(__file__)` parent. `Config.__init__` bootstraps a first-run `config.yaml`/`.env` from bundled templates when frozen and none exist yet. |
| `app.py` (modify, 1 line) | `Flask(...)`'s `template_folder`/`static_folder` become absolute paths from `paths.app_root()` instead of Flask's default relative-to-`__file__` resolution. |
| `poslib/i18n.py` (modify, 1 line) | `LOCALES_DIR` becomes `paths.app_root() / "locales"` instead of a hand-computed `Path(__file__)` parent — same fragility, same fix, as `app.py`'s Flask paths. |
| `config.template.yaml` (new) | The bundled, customer-facing `config.yaml` starting point — sanitized business info, placeholder `database.path`, `remote.enabled: false`. Copied to `user_data_dir()/config.yaml` on first run. |
| `main.py` (new) | The single PyInstaller entry point. Argparse dispatch: no flag → dashboard (`app.main()`); `--watcher` → watcher (`watcher.main()`), forwarding `--once`/`--digest-now`/`--backup-now`. `start.bat`/`start-quiet.bat` keep calling `app.py`/`watcher.py` directly, unaffected. |
| `requirements-build.txt` (new) | Build-only dependency (`pyinstaller`) kept out of the runtime `requirements.txt`. |
| `packaging/pos-tool.spec` (new) | PyInstaller spec: one onedir bundle, `console=False`, explicit `datas` allowlist. |
| `packaging/setup.iss` (new) | Inno Setup script wrapping the onedir output into `Setup.exe`, with a desktop + Start Menu shortcut. |

---

### Task 1: Frozen-aware path resolution (`poslib/paths.py`)

**Files:**
- Create: `poslib/paths.py`
- Test: `tests/test_paths.py`

**Interfaces:**
- Produces: `is_frozen() -> bool`, `app_root() -> Path`, `user_data_dir() -> Path`. Every later task that needs "where do I read bundled files from" or "where do I write my data" calls these two functions — nothing else computes its own frozen/dev branch.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_paths.py
"""
Tests for poslib/paths.py - where the app reads its own bundled files from,
and where it writes its data, in dev mode vs. a frozen PyInstaller build.
"""

from __future__ import annotations

from pathlib import Path

from poslib import paths


class TestIsFrozen:

    def test_false_when_sys_has_no_frozen_attribute(self, monkeypatch):
        monkeypatch.delattr(paths.sys, "frozen", raising=False)
        assert paths.is_frozen() is False

    def test_true_when_sys_frozen_is_set(self, monkeypatch):
        monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
        assert paths.is_frozen() is True


class TestAppRoot:

    def test_dev_mode_is_the_project_root(self, monkeypatch):
        monkeypatch.delattr(paths.sys, "frozen", raising=False)
        root = paths.app_root()
        # poslib/ lives directly under the project root.
        assert (root / "poslib" / "paths.py").resolve() == Path(__file__).resolve().parent.parent / "poslib" / "paths.py"

    def test_frozen_mode_is_the_exe_folder(self, monkeypatch, tmp_path):
        fake_exe = tmp_path / "ShopAnalysis.exe"
        fake_exe.touch()
        monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
        monkeypatch.setattr(paths.sys, "executable", str(fake_exe))
        assert paths.app_root() == tmp_path


class TestUserDataDir:

    def test_dev_mode_matches_app_root(self, monkeypatch):
        monkeypatch.delattr(paths.sys, "frozen", raising=False)
        assert paths.user_data_dir() == paths.app_root()

    def test_frozen_mode_is_under_localappdata(self, monkeypatch, tmp_path):
        monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        assert paths.user_data_dir() == tmp_path / "Shop Analysis"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_paths.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'poslib.paths'`

- [ ] **Step 3: Write the implementation**

```python
# poslib/paths.py
"""
paths.py - where the app reads its own bundled files from, and where it
writes its data.

Two different questions, both frozen-mode-aware:

  app_root()       Read-only. Where templates/, static/, and the app's own
                    code live. In dev, the project folder. In a packaged
                    build, the folder next to ShopAnalysis.exe.

  user_data_dir()   Writable. config.yaml, .env, cache.db, logs, digests,
                    backups, the remote export, and the owner's own typed-in
                    data all live here. In dev, the same as app_root() - this
                    machine's layout is unchanged. In a packaged build,
                    %LOCALAPPDATA%\\Shop Analysis, since a normal Windows
                    install (Program Files, or a Task Scheduler entry set to
                    /rl limited) is not reliably writable by the app itself.

Every place in the codebase that used to compute PROJECT_ROOT by hand goes
through here instead, so there is exactly one frozen/dev branch, not one per
file.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """True inside a PyInstaller-built exe, false in every dev/test run."""
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """Read-only root: the folder templates/, static/ and poslib/ live in."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def user_data_dir() -> Path:
    """Writable root: config.yaml, .env, cache.db, logs, digests, backups."""
    if is_frozen():
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
        return base / "Shop Analysis"
    return app_root()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_paths.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add poslib/paths.py tests/test_paths.py
git commit -m "feat(packaging): add frozen-aware path resolution (poslib/paths.py)"
```

---

### Task 2: Wire `poslib/config.py` through `paths.py`, with first-run bootstrap

**Files:**
- Modify: `poslib/config.py:24-27` (`PROJECT_ROOT`, `DEFAULT_CONFIG_PATH`, `DEFAULT_ENV_PATH`), `poslib/config.py:141-151` (`Config.path()`), `poslib/config.py:81-106` (`Config.__init__`)
- Test: `tests/test_config.py` (new)

**Interfaces:**
- Consumes: `poslib.paths.is_frozen()`, `poslib.paths.app_root()`, `poslib.paths.user_data_dir()` (Task 1).
- Produces: `Config(config_path=None, env_path=None)` behaves exactly as before when not frozen (proof: the full existing test suite, unmodified, still passes). When frozen and `user_data_dir()/config.yaml` doesn't exist yet, `Config()` copies `app_root()/config.template.yaml` and `app_root()/.env.example` into `user_data_dir()` before reading them.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config.py
"""
Tests for the frozen-vs-dev path wiring in poslib/config.py.

Everything else about Config (get/require/secret/_validate/...) is already
covered indirectly by every other test module using the real config.yaml -
this file is scoped to the one thing this plan changes: where Config reads
its files from, and the first-run bootstrap for a packaged build.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from poslib import paths
from poslib.config import Config, ConfigError


MINIMAL_CONFIG = """
database:
  path: "C:/CHANGE-ME/database.dblx"
interface:
  default_language: "fr"
"""


class TestPathResolutionDevMode:

    def test_relative_database_path_resolves_against_project_root(self, tmp_path, monkeypatch):
        monkeypatch.delattr(paths.sys, "frozen", raising=False)
        config_file = tmp_path / "config.yaml"
        config_file.write_text(MINIMAL_CONFIG, encoding="utf-8")
        cfg = Config(config_path=config_file)
        # cache defaults to "cache.db" (relative) -> resolved against
        # user_data_dir(), which in dev mode is the project root.
        assert cfg.cache_db == paths.user_data_dir() / "cache.db"


class TestFirstRunBootstrap:

    def test_frozen_with_no_existing_config_copies_the_template(self, tmp_path, monkeypatch):
        bundle_dir = tmp_path / "bundle"
        bundle_dir.mkdir()
        (bundle_dir / "config.template.yaml").write_text(MINIMAL_CONFIG, encoding="utf-8")
        (bundle_dir / ".env.example").write_text("SMTP_PASSWORD=\n", encoding="utf-8")

        data_dir = tmp_path / "userdata"

        monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
        monkeypatch.setattr(paths, "app_root", lambda: bundle_dir)
        monkeypatch.setattr(paths, "user_data_dir", lambda: data_dir)

        cfg = Config()

        assert (data_dir / "config.yaml").is_file()
        assert (data_dir / ".env").is_file()
        assert cfg.get("database.path") == "C:/CHANGE-ME/database.dblx"

    def test_frozen_with_existing_config_does_not_overwrite_it(self, tmp_path, monkeypatch):
        bundle_dir = tmp_path / "bundle"
        bundle_dir.mkdir()
        (bundle_dir / "config.template.yaml").write_text(MINIMAL_CONFIG, encoding="utf-8")
        (bundle_dir / ".env.example").write_text("SMTP_PASSWORD=\n", encoding="utf-8")

        data_dir = tmp_path / "userdata"
        data_dir.mkdir()
        already_there = data_dir / "config.yaml"
        already_there.write_text(
            'database:\n  path: "D:/real/shop.dblx"\ninterface:\n  default_language: "fr"\n',
            encoding="utf-8",
        )

        monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
        monkeypatch.setattr(paths, "app_root", lambda: bundle_dir)
        monkeypatch.setattr(paths, "user_data_dir", lambda: data_dir)

        cfg = Config()
        assert cfg.get("database.path") == "D:/real/shop.dblx"

    def test_not_frozen_never_bootstraps(self, tmp_path, monkeypatch):
        monkeypatch.delattr(paths.sys, "frozen", raising=False)
        with pytest.raises(ConfigError):
            Config(config_path=tmp_path / "does-not-exist.yaml")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_config.py -v`
Expected: FAIL — `test_relative_database_path_resolves_against_project_root` fails because `Config.path()` still resolves against the old hardcoded `PROJECT_ROOT`, not `paths.user_data_dir()` (same value in dev mode today, but the assertion targets the new source of truth); the bootstrap tests fail with `TypeError`/`AttributeError` since no bootstrap code exists yet.

- [ ] **Step 3: Write the implementation**

Replace `poslib/config.py:14-27`:

```python
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any

import yaml

from poslib import paths

# Kept for existing importers (export_static.py uses PROJECT_ROOT to find
# bundled read-only assets like static/style.css) - now backed by
# poslib.paths.app_root() so it resolves correctly in a frozen build too.
PROJECT_ROOT = paths.app_root()

DEFAULT_CONFIG_PATH = paths.user_data_dir() / "config.yaml"
DEFAULT_ENV_PATH = paths.user_data_dir() / ".env"
```

Replace `Config.__init__` (`poslib/config.py:81-106`):

```python
    def __init__(self, config_path: str | Path | None = None,
                 env_path: str | Path | None = None):
        self.config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self.env_path = Path(env_path) if env_path else DEFAULT_ENV_PATH

        if config_path is None and env_path is None:
            self._bootstrap_if_frozen()

        if not self.config_path.is_file():
            raise ConfigError(
                f"Cannot find the settings file: {self.config_path}\n"
                "It should sit next to start.bat. If it is missing, copy it "
                "back from the original download."
            )

        try:
            raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ConfigError(
                f"config.yaml could not be read - there is a typo in it.\n{exc}"
            ) from exc

        if not isinstance(raw, dict):
            raise ConfigError("config.yaml is empty or malformed.")

        self._data: dict[str, Any] = raw
        self._env: dict[str, str] = _load_env(self.env_path)

        self._validate()

    def _bootstrap_if_frozen(self) -> None:
        """
        First run of a packaged build: copy the bundled config template and
        .env.example into the writable user-data folder if nothing is there
        yet. Never touches an existing config.yaml/.env - a customer's real
        settings are never overwritten by an update.
        """
        if not paths.is_frozen():
            return

        data_dir = paths.user_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)

        if not self.config_path.is_file():
            template = paths.app_root() / "config.template.yaml"
            if template.is_file():
                shutil.copy2(template, self.config_path)

        if not self.env_path.is_file():
            example = paths.app_root() / ".env.example"
            if example.is_file():
                shutil.copy2(example, self.env_path)
```

Replace `Config.path()` (`poslib/config.py:141-151`):

```python
    def path(self, dotted: str, default: str = "") -> Path:
        """
        Read a setting that is a file or folder path and turn it into a full
        path. Relative paths are taken as relative to the writable user-data
        folder, so the tool works the same however it is started, and every
        piece of data it writes lands somewhere it's actually allowed to
        write in a packaged install.
        """
        raw = str(self.get(dotted, default) or default)
        p = Path(raw)
        if not p.is_absolute():
            p = paths.user_data_dir() / p
        return p
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_config.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full existing suite to prove dev-mode behavior is unchanged**

Run: `.venv\Scripts\python.exe -m pytest tests -q`
Expected: 242 passed (232 from before this plan, +6 from Task 1's `test_paths.py`, +4 from this task's `test_config.py`), 1 pre-existing unrelated failure — `TestConsistency::test_verification_table`'s `dead_stock_value` drift, already known per this session's earlier full run. No new failures.

- [ ] **Step 6: Commit**

```bash
git add poslib/config.py tests/test_config.py
git commit -m "feat(packaging): resolve Config paths through poslib.paths, add first-run bootstrap"
```

---

### Task 3: Frozen-safe bundled-asset paths (Flask templates/static, i18n locales)

Two places in the codebase compute a "where are my own read-only bundled
files" path by hand from `Path(__file__).resolve().parent...` instead of
going through `poslib.paths.app_root()` (confirmed by grepping the whole
codebase for that pattern — these are the only two that matter for a
packaged build; the same pattern in `tools/get_fonts.py` and the test files
is dev/test-only and unaffected by packaging):

- `app.py:44` — Flask's `template_folder`/`static_folder`.
- `poslib/i18n.py:33` — `LOCALES_DIR`, where `locales/en.json`,
  `locales/fr.json`, `locales/ar.json` are read from.

Both get the same fix: resolve from `app_root()` instead of `__file__`.
`__file__` on a module PyInstaller has compiled into its bundle archive does
not reliably behave like a normal on-disk path, so anything that derives a
directory from it is fragile in a frozen build even though it works fine
today.

**Files:**
- Modify: `app.py:44`
- Modify: `poslib/i18n.py:33`
- Test: `tests/test_app.py` (new)
- Test: `tests/test_i18n_and_app.py` (existing file — add one test to it)

**Interfaces:**
- Consumes: `poslib.paths.app_root()` (Task 1).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_app.py
"""
The one thing this plan changes about app.py: template_folder/static_folder
must resolve to an absolute path from poslib.paths.app_root(), not Flask's
own default (relative to app.py's __file__), so both still work in a
PyInstaller onedir build.
"""

from __future__ import annotations

from poslib import paths


def test_template_and_static_folders_are_under_app_root():
    import app as app_module
    root = paths.app_root()
    assert app_module.app.template_folder == str(root / "templates")
    assert app_module.app.static_folder == str(root / "static")
```

Add to the end of `tests/test_i18n_and_app.py` (it already imports `poslib.i18n` — check its existing import block and reuse it rather than re-importing):

```python
def test_locales_dir_resolves_from_app_root():
    from poslib import i18n, paths
    assert i18n.LOCALES_DIR == paths.app_root() / "locales"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py tests/test_i18n_and_app.py::test_locales_dir_resolves_from_app_root -v`
Expected: FAIL — `app.template_folder` is `"templates"` (relative, not absolute); `i18n.LOCALES_DIR` is computed from `Path(__file__)`, not `paths.app_root()` (same value today, but the wrong source — this will diverge once `poslib/i18n.py` is frozen inside a bundle).

- [ ] **Step 3: Write the implementation**

Replace `app.py:44`:

```python
from poslib.paths import app_root

app = Flask(__name__,
            template_folder=str(app_root() / "templates"),
            static_folder=str(app_root() / "static"))
```

(Add the `from poslib.paths import app_root` alongside `app.py`'s other `poslib` imports near the top of the file, not inline — check the existing import block and place it there in real code, matching the file's existing import grouping.)

Replace `poslib/i18n.py:33`:

```python
from poslib.paths import app_root

LOCALES_DIR = app_root() / "locales"
```

(Same note: place the import alongside `poslib/i18n.py`'s existing imports at the top of the file, matching its existing style, not inline at line 33.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py tests/test_i18n_and_app.py -v`
Expected: PASS — both new tests, plus every pre-existing test in `test_i18n_and_app.py` still passing (this file's other tests exercise real translation lookups against the same `locales/*.json` files, now reached via `app_root()` instead of `Path(__file__)`, so a regression here would show up as translation-loading failures across that whole file, not just the new test).

- [ ] **Step 5: Run the full suite again**

Run: `.venv\Scripts\python.exe -m pytest tests -q`
Expected: 244 passed (242 from Task 2 Step 5, +2 from this task's new tests), 1 pre-existing unrelated failure (same as Task 2 Step 5).

- [ ] **Step 6: Commit**

```bash
git add app.py poslib/i18n.py tests/test_app.py tests/test_i18n_and_app.py
git commit -m "fix(packaging): resolve Flask template/static and i18n locales from app_root(), not __file__"
```

---

### Task 4: Sanitized `config.template.yaml`

**Files:**
- Create: `config.template.yaml`

No test file — this is a data/content task. Its correctness is verified by Task 2's bootstrap tests (already passing) plus this task's own manual verification step below.

- [ ] **Step 1: Create the template**

Copy `config.yaml` to `config.template.yaml`, then change exactly these values (every other setting — thresholds, digest config, backup config, watcher config — stays identical to the real `config.yaml`, since those are sensible defaults for any shop, not this shop's private data):

```yaml
database:
  path: "C:/CHANGE-ME/point-this-at-your-database.dblx"
  cache: "cache.db"

business:
  currency: "DZD"
  currency_symbol: "DA"
  name: "My Shop"
  closed_weekdays: []
```

```yaml
remote:
  enabled: false
  cloudflare_project_name: ""
  push_interval_seconds: 90
  export_dir: "remote-site"
```

Leave every other section (`interface`, `thresholds`, `data_rules`, `catalog`, `digest`, `backup`, `watcher`, `logging`) byte-for-byte the same as the current `config.yaml` — those are the tool's tuned defaults, not this shop's identity.

- [ ] **Step 2: Verify it parses and validates on its own**

Run:
```
.venv\Scripts\python.exe -c "from poslib.config import Config; c = Config(config_path='config.template.yaml', env_path='.env.example'); print('OK', c.get('database.path'), c.get('remote.enabled'))"
```
Expected: `OK C:/CHANGE-ME/point-this-at-your-database.dblx False` — proves the template is valid YAML, passes `Config._validate()` (non-empty `database.path`, valid `default_language`, etc.), and `remote.enabled` reads as `False`.

- [ ] **Step 3: Commit**

```bash
git add config.template.yaml
git commit -m "feat(packaging): add sanitized config.template.yaml for first-run bootstrap"
```

---

### Task 5: Single entry point with argparse dispatch (`main.py`)

**Files:**
- Create: `main.py`
- Test: `tests/test_main.py` (new)

**Interfaces:**
- Consumes: `app.main() -> int` (`app.py:1013`, unchanged signature), `watcher.main() -> int` (`watcher.py:288`, unchanged signature).
- Produces: `main(argv: list[str] | None = None) -> int` — importable and unit-testable without going through `sys.argv`. `if __name__ == "__main__": raise SystemExit(main())` at module level for the packaged build to call.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_main.py
"""
Tests for main.py - the single entry point the packaged build launches.

No flag -> the dashboard. --watcher -> the watcher, forwarding its own
flags. Both app.main() and watcher.main() are mocked here; they have their
own test coverage already (or are exercised directly by start.bat/
start-quiet.bat in the dev workflow) - this file only tests the dispatch.
"""

from __future__ import annotations

import main as main_module


def test_no_flags_runs_the_dashboard(monkeypatch):
    calls = []
    monkeypatch.setattr(main_module.app, "main", lambda: calls.append("dashboard") or 0)
    monkeypatch.setattr(main_module.watcher, "main", lambda: calls.append("watcher") or 0)
    assert main_module.main([]) == 0
    assert calls == ["dashboard"]


def test_watcher_flag_runs_the_watcher(monkeypatch):
    calls = []
    monkeypatch.setattr(main_module.app, "main", lambda: calls.append("dashboard") or 0)
    monkeypatch.setattr(main_module.watcher, "main", lambda: calls.append("watcher") or 0)
    assert main_module.main(["--watcher"]) == 0
    assert calls == ["watcher"]


def test_watcher_flags_are_forwarded_to_sys_argv(monkeypatch):
    seen_argv = []

    def fake_watcher_main():
        seen_argv.append(list(main_module.sys.argv))
        return 0

    monkeypatch.setattr(main_module.watcher, "main", fake_watcher_main)
    assert main_module.main(["--watcher", "--digest-now"]) == 0
    assert seen_argv == [["watcher.py", "--digest-now"]]


def test_dashboard_flags_are_forwarded_to_sys_argv(monkeypatch):
    seen_argv = []

    def fake_app_main():
        seen_argv.append(list(main_module.sys.argv))
        return 0

    monkeypatch.setattr(main_module.app, "main", fake_app_main)
    assert main_module.main(["--no-browser"]) == 0
    assert seen_argv == [["app.py", "--no-browser"]]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_main.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'main'`

- [ ] **Step 3: Write the implementation**

```python
# main.py
"""
main.py - the single entry point PyInstaller builds into ShopAnalysis.exe.

Both app.py (the dashboard) and watcher.py (the background refresh/digest/
backup loop) stay runnable on their own for the dev workflow (start.bat,
start-quiet.bat call them directly, unchanged). This file exists only so a
packaged build has one exe with two modes, dispatched by a flag, instead of
two separate exes:

    ShopAnalysis.exe                 the dashboard (same as app.py)
    ShopAnalysis.exe --watcher ...   the watcher (same as watcher.py),
                                      any flags after --watcher are app.py's
                                      own (--once, --digest-now, --backup-now)
"""

from __future__ import annotations

import sys

import app
import watcher


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    if argv and argv[0] == "--watcher":
        sys.argv = ["watcher.py", *argv[1:]]
        return watcher.main()

    sys.argv = ["app.py", *argv]
    return app.main()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_main.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Manual smoke check that both modes still actually run**

Run: `.venv\Scripts\python.exe main.py --no-browser` — confirm it prints `Shop analysis is running at http://127.0.0.1:8777/` (same as running `app.py` directly), then Ctrl+C to stop.
Run: `.venv\Scripts\python.exe main.py --watcher --once` — confirm it rebuilds the cache and exits 0 (same as running `watcher.py --once` directly).

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_main.py
git commit -m "feat(packaging): add main.py, the single dispatch entry point for the packaged build"
```

---

### Task 6: PyInstaller build

**Files:**
- Create: `requirements-build.txt`
- Create: `packaging/pos-tool.spec`

No unit test — this task's verification is an actual local build and run on this dev PC, which is the only way to catch PyInstaller import/bundling gaps (hidden imports, missing data files) before attempting a clean VM.

- [ ] **Step 1: Add the build-only dependency**

```
# requirements-build.txt
# Only needed to build the Windows installer - never installed on a
# customer's machine, and never required to run the tool from source.
pyinstaller==6.11.1
```

- [ ] **Step 2: Install it into the dev .venv**

Run: `.venv\Scripts\pip.exe install -r requirements-build.txt`
Expected: installs cleanly (confirm with `.venv\Scripts\pip.exe show pyinstaller`).

- [ ] **Step 3: Write the PyInstaller spec**

```python
# packaging/pos-tool.spec
# -*- mode: python ; coding: utf-8 -*-
#
# Builds one onedir bundle containing ShopAnalysis.exe. main.py dispatches
# between dashboard mode (default) and watcher mode (--watcher) at runtime -
# see main.py's own docstring. console=False on purpose: no terminal window
# is ever shown, per the spec's "zero technical involvement" goal.
#
# Explicit datas allowlist only - never glob the project root. In
# particular: never bundle .env, cache.db, data/owner.db, logs/, digests/,
# backups/, remote-site/, or static/photo-cache/* - all of those either hold
# secrets or this dev machine's own shop data, and none of them belong on a
# customer's PC.

import sys
from pathlib import Path

PROJECT_ROOT = Path(SPECPATH).resolve().parent

datas = [
    (str(PROJECT_ROOT / "templates"), "templates"),
    (str(PROJECT_ROOT / "static" / "style.css"), "static"),
    (str(PROJECT_ROOT / "locales" / "en.json"), "locales"),
    (str(PROJECT_ROOT / "locales" / "fr.json"), "locales"),
    (str(PROJECT_ROOT / "locales" / "ar.json"), "locales"),
    (str(PROJECT_ROOT / "config.template.yaml"), "."),
    (str(PROJECT_ROOT / ".env.example"), "."),
]

a = Analysis(
    [str(PROJECT_ROOT / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ShopAnalysis",
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ShopAnalysis",
)
```

The `datas` list above is exhaustive, not illustrative: confirmed by grepping `templates/*.html` for `url_for('static', ...)` (only `style.css` is ever referenced — there is no separate JS file; scripts are inline `<script>` blocks in the templates) and by checking `static/`'s real contents (`style.css` plus `photo-cache/`, a folder of per-product image-cache markers for *this* shop's own products — deliberately excluded, per the Global Constraints allowlist rule, not bundled here).

- [ ] **Step 4: Build it**

Run: `.venv\Scripts\pyinstaller.exe packaging\pos-tool.spec --distpath dist --workpath build`
Expected: exits 0, produces `dist/ShopAnalysis/ShopAnalysis.exe` plus supporting files.

- [ ] **Step 5: Run the built exe on this dev PC, isolated from this machine's real user data**

Run (PowerShell, redirecting `LOCALAPPDATA` to a scratch folder so this test never touches this machine's real config/cache):
```powershell
$env:LOCALAPPDATA = "$env:TEMP\shop-analysis-build-test"
& "dist\ShopAnalysis\ShopAnalysis.exe"
```
Expected: a browser opens to `http://127.0.0.1:8777/` showing the dashboard's friendly error page (since `$env:TEMP\shop-analysis-build-test\Shop Analysis\config.yaml` was just bootstrapped from `config.template.yaml`, whose `database.path` is a deliberate placeholder). Confirm `$env:TEMP\shop-analysis-build-test\Shop Analysis\config.yaml` now exists and matches `config.template.yaml`. Close the window, then delete the scratch folder.

If it fails to start or the browser shows nothing: check the failure against PyInstaller's known gotchas for this stack before assuming the spec file above is wrong — Flask's template auto-reload, `pandas`/`numpy`'s C-extension DLLs, and `watchdog`'s Windows backend are common sources of missing-import errors that only surface at this step, not at Task 1-5's unit-test level. Add whatever `hiddenimports` the actual error names, rebuild, and retry this step before moving on.

- [ ] **Step 6: Commit**

```bash
git add requirements-build.txt packaging/pos-tool.spec
git commit -m "feat(packaging): add PyInstaller onedir build (packaging/pos-tool.spec)"
```

(`dist/` and `build/` are build output, not source — confirm they're covered by `.gitignore`; add them if not, in this same commit.)

---

### Task 7: Inno Setup installer

**Files:**
- Create: `packaging/setup.iss`

No unit test — verified by actually building and running the installer.

- [ ] **Step 1: Confirm Inno Setup is available**

Run: `where iscc`
If not found: Inno Setup needs installing on this dev PC first (download from https://jrsoftware.org/isinfo.php — this is a one-time local tool install, the same category as the Cloudflare token creation in the earlier plan: something only a human at the keyboard can do, not scriptable end-to-end). Stop and ask the user to install it before continuing this task.

- [ ] **Step 2: Write the Inno Setup script**

```ini
; packaging/setup.iss
; Wraps dist/ShopAnalysis/ (Task 6's PyInstaller onedir output) into a
; normal Windows installer wizard. No terminal, no visible Python, ever -
; just Next, Next, Install, and a desktop shortcut.

#define MyAppName "Shop Analysis"
#define MyAppExeName "ShopAnalysis.exe"

[Setup]
AppName={#MyAppName}
AppVersion=1.0.0
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist-installer
OutputBaseFilename=Setup
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\dist\ShopAnalysis\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Open Shop Analysis now"; Flags: nowait postinstall skipifsilent
```

- [ ] **Step 3: Build the installer**

Run: `iscc packaging\setup.iss`
Expected: exits 0, produces `dist-installer\Setup.exe`.

- [ ] **Step 4: Run the installer on this dev PC — ask for confirmation first**

This step installs real files into `Program Files` and creates real Start Menu/desktop shortcuts and an uninstall registry entry on this dev machine — a normal, reversible install (via the generated uninstaller), but it's still a change outside the project folder, so pause and confirm with the user before running `Setup.exe` for real rather than doing it unprompted.

Once confirmed: run `dist-installer\Setup.exe`, click through the wizard, and on the final "Open Shop Analysis now" step, confirm the dashboard opens in the browser exactly as it did in Task 6 Step 5 (friendly placeholder-database error page, since this installer's bundled `config.template.yaml` is unchanged from Task 6). Then uninstall it again via "Add or Remove Programs" to leave this dev PC clean.

- [ ] **Step 5: Commit**

```bash
git add packaging/setup.iss
git commit -m "feat(packaging): add Inno Setup script wrapping the PyInstaller build into Setup.exe"
```

(`dist-installer/` is build output — confirm it's covered by `.gitignore`; add it in this same commit if not.)

---

### Task 8: Clean-VM acceptance test (manual)

**Files:** none — this is a verification-only task, not a code change.

This is the plan's actual finish line: "a clean Windows machine with no Python/Node/git installed can run `Setup.exe` end to end and reach a working dashboard" (spec's Testing plan, and this plan's own scope boundary). It cannot be automated by an agent — it requires a human to watch a GUI installer run and a browser tab open, on a machine this session has no access to.

- [ ] **Step 1: Get a clean environment**

Windows Sandbox (built into Windows 11 Pro, already on this machine — Start menu → "Windows Sandbox") is the right tool here: a fresh, ephemeral, network-connected Windows environment with no Python/Node/git, that discards all state the moment it's closed. No separate VM to provision or maintain. (A Hyper-V/VirtualBox VM works too if Windows Sandbox isn't available for some reason, but Sandbox is the lower-effort default.)

- [ ] **Step 2: Copy `Setup.exe` in and run it**

Copy `dist-installer\Setup.exe` (Task 7) into the Sandbox window (drag-and-drop is supported), double-click it, and click through the wizard exactly as a shop owner would — no manual dependency installs, no terminal, nothing beyond what the wizard itself presents.

- [ ] **Step 3: Confirm the dashboard reaches a working state**

On the wizard's final screen, let it open the app. Confirm a browser window opens to `http://127.0.0.1:8777/` and shows the dashboard's friendly placeholder-database error page (not a crash, not a blank tab, not a console window). This confirms every dependency (Flask, pandas, numpy, PyYAML, the whole frozen bundle) actually runs standalone with nothing pre-installed on the machine — the one thing Tasks 1-7's dev-PC verification cannot prove, since this dev PC already has Python, the `.venv`, and every dependency installed.

- [ ] **Step 4: Record the result**

If it works: this plan is done. Update `CLAUDE.md`'s "Customer distribution" section with a discovery note recording that Component 1 (packaging) is built and verified on a clean VM, the same way Component 4's Task 2 will be recorded once its manual step is done.

If it fails: note exactly what broke (missing DLL, crash on startup, blank browser tab, etc.) — that detail decides whether the fix belongs in Task 6's PyInstaller spec (a missing hidden import or data file) or is a genuinely new problem this plan didn't anticipate.
