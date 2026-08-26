# Component 3: Silent Auto-Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the packaged app a self-updating watcher: once at startup it checks GitHub Releases for a newer version, and if one exists, downloads it, verifies its checksum, and installs it silently with no dialog and no click.

**Architecture:** A new `poslib/updater.py` module owns the whole flow (check → download → verify → launch) as a set of small, never-raising functions, mirroring the existing style of `poslib/remote.py`/`poslib/backup.py`. `watcher.py` calls one entry point (`check_and_apply_update`) as the very first thing in `Watcher.run()`, before the file observer starts — this ordering is what guarantees the update check can never overlap an in-progress `rebuild()`. A new `packaging/publish_release.py` script is the dev-side tool that actually cuts a release (bump `VERSION`, build, checksum, `gh release create`).

**Tech Stack:** Python 3.12 (existing `.venv`), `requests` (already a dependency), `hashlib` (stdlib), `subprocess` (stdlib), GitHub REST API (`api.github.com`, unauthenticated — public repo), GitHub CLI (`gh`, dev-machine only), Inno Setup (`iscc`, dev-machine only).

## Global Constraints

- **Gate every update check on `poslib.paths.is_frozen()`.** This dev PC runs `watcher.py` directly via Python (not a frozen build — see `install-startup.bat`'s "Shop Analysis - Dashboard" task, which runs `start-quiet.bat`), and per `CLAUDE.md` it "stays on the existing git-based setup unchanged." Without this gate, the dev PC's own watcher would attempt to download and silently run `Setup.exe` on itself the first time a real release is published — the exact opposite of what the project brief says about this machine. `poslib/paths.py`'s own docstring already establishes this convention: "gated on `sys.frozen` so this dev PC's behavior is provably unchanged when not running from a frozen build." Follow it here.
- **Never raise from a public entry point.** Every function `watcher.py` calls directly (`check_and_apply_update`) must catch its own failures and return a value, never propagate an exception — same rule `poslib/remote.py`'s module docstring states for `push_remote`, and the same reason: a failed check must never crash the watcher.
- **No GitHub token.** The repo is public (`gh repo view` confirmed `"visibility":"PUBLIC"`); all GitHub API calls in `poslib/updater.py` are unauthenticated.
- **Version format is always `MAJOR.MINOR.PATCH`** (three dot-separated integers), both in the `VERSION` file and in release tags (tags use a `v` prefix, e.g. `v1.2.0`; the `VERSION` file does not).
- **A downloaded installer only ever runs after its SHA256 matches the published `Setup.exe.sha256`.** No exceptions to this — an unverified silent install is the one irreversible mistake this whole feature has to avoid.

---

### Task 1: Version file and `current_version()`

**Files:**
- Create: `VERSION`
- Create: `poslib/updater.py`
- Modify: `packaging/pos-tool.spec:16-25` (datas list)
- Test: `tests/test_updater.py`

**Interfaces:**
- Produces: `poslib.updater.current_version() -> tuple[int, int, int]`, `poslib.updater._parse_version(text: str) -> tuple[int, int, int] | None`

- [ ] **Step 1: Create the VERSION file**

Content (exactly, no trailing content beyond the newline):
```
1.0.0
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_updater.py
"""
Tests for poslib/updater.py - the silent auto-update check.

Entirely mocked - never makes a real network call, never spawns a real
process. See docs/superpowers/specs/2026-08-26-component3-auto-update-design.md
for the design this implements.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from poslib import updater


def test_current_version_reads_and_parses_the_version_file(monkeypatch, tmp_path):
    version_file = tmp_path / "VERSION"
    version_file.write_text("2.5.11\n", encoding="utf-8")
    monkeypatch.setattr(updater, "app_root", lambda: tmp_path)

    assert updater.current_version() == (2, 5, 11)


def test_current_version_falls_back_to_zero_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "app_root", lambda: tmp_path)

    assert updater.current_version() == (0, 0, 0)


@pytest.mark.parametrize("text,expected", [
    ("1.2.3", (1, 2, 3)),
    ("v1.2.3", (1, 2, 3)),
    ("V1.2.3", (1, 2, 3)),
    (" 1.2.3 \n", (1, 2, 3)),
    ("1.2", None),
    ("1.2.3.4", None),
    ("not-a-version", None),
    ("", None),
])
def test_parse_version(text, expected):
    assert updater._parse_version(text) == expected
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv\Scripts\pytest.exe tests/test_updater.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'poslib.updater'` (or collection error) — the module doesn't exist yet.

- [ ] **Step 4: Write the implementation**

```python
# poslib/updater.py
"""
updater.py - checks GitHub Releases for a newer version and, if found,
downloads, verifies, and silently installs it.

Only ever active in a packaged build (poslib.paths.is_frozen()) - this dev
PC runs watcher.py directly via Python and must never try to download and
run a Windows installer on itself. Every public function here follows
poslib/remote.py's rule: never raise. A failed check, download, or install
attempt logs and gives up until the next watcher startup - it must never
crash the watcher.

See docs/superpowers/specs/2026-08-26-component3-auto-update-design.md for
the full design.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .paths import app_root

log = logging.getLogger(__name__)


def _parse_version(text: str) -> tuple[int, int, int] | None:
    text = text.strip()
    if text[:1] in ("v", "V"):
        text = text[1:]
    parts = text.split(".")
    if len(parts) != 3:
        return None
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def current_version() -> tuple[int, int, int]:
    """
    This build's own version, read from the bundled VERSION file. Returns
    (0, 0, 0) if the file is missing or unparseable, which safely never
    compares as newer than a real release.
    """
    version_file = app_root() / "VERSION"
    if not version_file.is_file():
        return (0, 0, 0)
    parsed = _parse_version(version_file.read_text(encoding="utf-8"))
    return parsed or (0, 0, 0)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv\Scripts\pytest.exe tests/test_updater.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Bundle VERSION into the frozen build**

Edit `packaging/pos-tool.spec`, in the `datas` list (currently lines 16-25), add one entry so the frozen exe can read its own version the same way `app_root()` already resolves `config.template.yaml`:

```python
datas = [
    (str(PROJECT_ROOT / "templates"), "templates"),
    (str(PROJECT_ROOT / "static" / "style.css"), "static"),
    (str(PROJECT_ROOT / "locales" / "en.json"), "locales"),
    (str(PROJECT_ROOT / "locales" / "fr.json"), "locales"),
    (str(PROJECT_ROOT / "locales" / "ar.json"), "locales"),
    (str(PROJECT_ROOT / "config.template.yaml"), "."),
    (str(PROJECT_ROOT / ".env.example"), "."),
    (str(PROJECT_ROOT / "VERSION"), "."),
]
```

No test for this step — it only matters for a frozen build, verified when Component 3's install/update flow is tested end to end per the design doc's testing plan, not by `pytest`.

- [ ] **Step 7: Commit**

```bash
git add VERSION poslib/updater.py tests/test_updater.py packaging/pos-tool.spec
git commit -m "feat(update): add VERSION file and current_version() reader"
```

---

### Task 2: `check_for_update()` — GitHub Releases check

**Files:**
- Modify: `poslib/updater.py`
- Modify: `tests/test_updater.py`

**Interfaces:**
- Consumes: `current_version() -> tuple[int, int, int]` (Task 1), `poslib.paths.is_frozen() -> bool`, `poslib.config.Config` (`.get(dotted, default)`)
- Produces: `poslib.updater.ReleaseInfo` (dataclass: `version: tuple[int,int,int]`, `tag_name: str`, `installer_url: str`, `checksum_url: str`), `poslib.updater.check_for_update(cfg: Config) -> ReleaseInfo | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_updater.py`:

```python
import requests


class FakeConfig:
    def __init__(self, enabled=True, repo="rachadmihoubi/pos-tool"):
        self._enabled = enabled
        self._repo = repo

    def get(self, key, default=None):
        if key == "update.enabled":
            return self._enabled
        if key == "update.github_repo":
            return self._repo
        return default


class FakeResponse:
    def __init__(self, json_data=None, status_code=200):
        self._json_data = json_data or {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._json_data


def _release_json(tag_name, with_assets=True):
    assets = []
    if with_assets:
        assets = [
            {"name": "Setup.exe", "browser_download_url": "https://example.test/Setup.exe"},
            {"name": "Setup.exe.sha256",
             "browser_download_url": "https://example.test/Setup.exe.sha256"},
        ]
    return {"tag_name": tag_name, "assets": assets}


class TestCheckForUpdate:

    def test_skips_when_not_frozen(self, monkeypatch):
        monkeypatch.setattr(updater, "is_frozen", lambda: False)

        def _fail_if_called(*a, **k):
            raise AssertionError("must not make a network call when not frozen")
        monkeypatch.setattr(updater.requests, "get", _fail_if_called)

        assert updater.check_for_update(FakeConfig()) is None

    def test_skips_when_disabled_in_config(self, monkeypatch):
        monkeypatch.setattr(updater, "is_frozen", lambda: True)

        def _fail_if_called(*a, **k):
            raise AssertionError("must not make a network call when disabled")
        monkeypatch.setattr(updater.requests, "get", _fail_if_called)

        assert updater.check_for_update(FakeConfig(enabled=False)) is None

    def test_returns_release_info_when_newer_version_available(self, monkeypatch):
        monkeypatch.setattr(updater, "is_frozen", lambda: True)
        monkeypatch.setattr(updater, "current_version", lambda: (1, 0, 0))
        monkeypatch.setattr(updater.requests, "get",
                            lambda url, **k: FakeResponse(_release_json("v1.2.0")))

        result = updater.check_for_update(FakeConfig())

        assert result == updater.ReleaseInfo(
            version=(1, 2, 0), tag_name="v1.2.0",
            installer_url="https://example.test/Setup.exe",
            checksum_url="https://example.test/Setup.exe.sha256")

    def test_returns_none_when_not_newer(self, monkeypatch):
        monkeypatch.setattr(updater, "is_frozen", lambda: True)
        monkeypatch.setattr(updater, "current_version", lambda: (1, 2, 0))
        monkeypatch.setattr(updater.requests, "get",
                            lambda url, **k: FakeResponse(_release_json("v1.2.0")))

        assert updater.check_for_update(FakeConfig()) is None

    def test_returns_none_when_equal_version(self, monkeypatch):
        monkeypatch.setattr(updater, "is_frozen", lambda: True)
        monkeypatch.setattr(updater, "current_version", lambda: (1, 2, 0))
        monkeypatch.setattr(updater.requests, "get",
                            lambda url, **k: FakeResponse(_release_json("v1.1.9")))

        assert updater.check_for_update(FakeConfig()) is None

    def test_returns_none_on_request_failure(self, monkeypatch):
        monkeypatch.setattr(updater, "is_frozen", lambda: True)

        def _raise(*a, **k):
            raise requests.ConnectionError("offline")
        monkeypatch.setattr(updater.requests, "get", _raise)

        assert updater.check_for_update(FakeConfig()) is None

    def test_returns_none_on_unparseable_tag(self, monkeypatch):
        monkeypatch.setattr(updater, "is_frozen", lambda: True)
        monkeypatch.setattr(updater, "current_version", lambda: (1, 0, 0))
        monkeypatch.setattr(updater.requests, "get",
                            lambda url, **k: FakeResponse(_release_json("not-a-version")))

        assert updater.check_for_update(FakeConfig()) is None

    def test_returns_none_when_assets_missing(self, monkeypatch):
        monkeypatch.setattr(updater, "is_frozen", lambda: True)
        monkeypatch.setattr(updater, "current_version", lambda: (1, 0, 0))
        monkeypatch.setattr(
            updater.requests, "get",
            lambda url, **k: FakeResponse(_release_json("v1.2.0", with_assets=False)))

        assert updater.check_for_update(FakeConfig()) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests/test_updater.py -v`
Expected: FAIL — `AttributeError: module 'poslib.updater' has no attribute 'ReleaseInfo'` / `'check_for_update'`

- [ ] **Step 3: Write the implementation**

Add to `poslib/updater.py` (update the imports at the top and add the new code):

```python
import dataclasses
import logging
from pathlib import Path

import requests

from .config import Config
from .paths import app_root, is_frozen

log = logging.getLogger(__name__)

_GITHUB_API_TIMEOUT_SECONDS = 15


@dataclasses.dataclass
class ReleaseInfo:
    version: tuple[int, int, int]
    tag_name: str
    installer_url: str
    checksum_url: str


def _fetch_latest_release(repo: str) -> dict | None:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        resp = requests.get(url, timeout=_GITHUB_API_TIMEOUT_SECONDS,
                            headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("Could not check for updates: %s", exc)
        return None


def _asset_url(release: dict, name: str) -> str | None:
    for asset in release.get("assets", []):
        if asset.get("name") == name:
            return asset.get("browser_download_url")
    return None


def check_for_update(cfg: Config) -> ReleaseInfo | None:
    """
    Returns a ReleaseInfo if GitHub Releases has a version newer than this
    build's own VERSION file, else None. A no-op (returns None without any
    network call) unless running from a frozen build - see this module's
    docstring for why. Never raises.
    """
    if not is_frozen():
        return None
    if not bool(cfg.get("update.enabled", True)):
        return None

    repo = str(cfg.get("update.github_repo", "")).strip()
    if not repo:
        return None

    release = _fetch_latest_release(repo)
    if release is None:
        return None

    tag_name = str(release.get("tag_name", ""))
    remote_version = _parse_version(tag_name)
    if remote_version is None:
        log.warning("Could not parse a version from release tag %r", tag_name)
        return None

    if remote_version <= current_version():
        return None

    installer_url = _asset_url(release, "Setup.exe")
    checksum_url = _asset_url(release, "Setup.exe.sha256")
    if not installer_url or not checksum_url:
        log.warning("Release %s is missing Setup.exe or Setup.exe.sha256 - skipping.", tag_name)
        return None

    return ReleaseInfo(version=remote_version, tag_name=tag_name,
                        installer_url=installer_url, checksum_url=checksum_url)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests/test_updater.py -v`
Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
git add poslib/updater.py tests/test_updater.py
git commit -m "feat(update): check GitHub Releases for a newer version"
```

---

### Task 3: `download_and_verify()` — download and checksum

**Files:**
- Modify: `poslib/updater.py`
- Modify: `tests/test_updater.py`

**Interfaces:**
- Consumes: `ReleaseInfo` (Task 2)
- Produces: `poslib.updater.download_and_verify(release: ReleaseInfo, dest_dir: Path) -> Path | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_updater.py`:

```python
import hashlib


class TestDownloadAndVerify:

    def _release(self):
        return updater.ReleaseInfo(
            version=(1, 2, 0), tag_name="v1.2.0",
            installer_url="https://example.test/Setup.exe",
            checksum_url="https://example.test/Setup.exe.sha256")

    def test_downloads_and_returns_installer_path_on_matching_checksum(
            self, monkeypatch, tmp_path):
        installer_bytes = b"fake installer bytes"
        digest = hashlib.sha256(installer_bytes).hexdigest()

        def _fake_get(url, **kwargs):
            if url.endswith(".sha256"):
                return FakeResponse2(f"{digest}  Setup.exe\n".encode("ascii"))
            return FakeResponse2(installer_bytes)
        monkeypatch.setattr(updater.requests, "get", _fake_get)

        result = updater.download_and_verify(self._release(), tmp_path)

        assert result == tmp_path / "Setup.exe"
        assert result.read_bytes() == installer_bytes

    def test_returns_none_and_cleans_up_on_checksum_mismatch(self, monkeypatch, tmp_path):
        installer_bytes = b"fake installer bytes"
        wrong_digest = "0" * 64

        def _fake_get(url, **kwargs):
            if url.endswith(".sha256"):
                return FakeResponse2(f"{wrong_digest}  Setup.exe\n".encode("ascii"))
            return FakeResponse2(installer_bytes)
        monkeypatch.setattr(updater.requests, "get", _fake_get)

        result = updater.download_and_verify(self._release(), tmp_path)

        assert result is None
        assert not (tmp_path / "Setup.exe").exists()
        assert not (tmp_path / "Setup.exe.sha256").exists()

    def test_returns_none_when_checksum_download_fails(self, monkeypatch, tmp_path):
        def _fake_get(url, **kwargs):
            if url.endswith(".sha256"):
                raise requests.ConnectionError("offline")
            return FakeResponse2(b"unused")
        monkeypatch.setattr(updater.requests, "get", _fake_get)

        assert updater.download_and_verify(self._release(), tmp_path) is None

    def test_returns_none_when_installer_download_fails(self, monkeypatch, tmp_path):
        digest = hashlib.sha256(b"x").hexdigest()

        def _fake_get(url, **kwargs):
            if url.endswith(".sha256"):
                return FakeResponse2(f"{digest}  Setup.exe\n".encode("ascii"))
            raise requests.ConnectionError("offline")
        monkeypatch.setattr(updater.requests, "get", _fake_get)

        assert updater.download_and_verify(self._release(), tmp_path) is None


class FakeResponse2:
    """Like FakeResponse, but carries raw bytes for downloads instead of JSON."""

    def __init__(self, content: bytes, status_code=200):
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests/test_updater.py -v`
Expected: FAIL — `AttributeError: module 'poslib.updater' has no attribute 'download_and_verify'`

- [ ] **Step 3: Write the implementation**

Add to `poslib/updater.py` (add `hashlib` to the imports):

```python
import hashlib
```

```python
_DOWNLOAD_TIMEOUT_SECONDS = 120


def _download(url: str, dest: Path) -> bool:
    try:
        resp = requests.get(url, timeout=_DOWNLOAD_TIMEOUT_SECONDS)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        return True
    except (requests.RequestException, OSError) as exc:
        log.warning("Could not download %s: %s", url, exc)
        return False


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download_and_verify(release: ReleaseInfo, dest_dir: Path) -> Path | None:
    """
    Downloads Setup.exe and its .sha256 into dest_dir and verifies the
    hash. Returns the path to the verified installer, or None on any
    failure (download error or checksum mismatch) - the caller must not
    run an installer this returns None for. Never raises.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    installer_path = dest_dir / "Setup.exe"
    checksum_path = dest_dir / "Setup.exe.sha256"

    if not _download(release.checksum_url, checksum_path):
        return None
    if not _download(release.installer_url, installer_path):
        return None

    expected = checksum_path.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = _sha256_of(installer_path)
    if actual.lower() != expected:
        log.error("Checksum mismatch for %s: expected %s, got %s",
                   release.tag_name, expected, actual)
        installer_path.unlink(missing_ok=True)
        checksum_path.unlink(missing_ok=True)
        return None

    return installer_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests/test_updater.py -v`
Expected: PASS (19 tests)

- [ ] **Step 5: Commit**

```bash
git add poslib/updater.py tests/test_updater.py
git commit -m "feat(update): download and verify the installer's checksum"
```

---

### Task 4: `launch_silent_install()` and `check_and_apply_update()`

**Files:**
- Modify: `poslib/updater.py`
- Modify: `tests/test_updater.py`

**Interfaces:**
- Consumes: `check_for_update(cfg) -> ReleaseInfo | None` (Task 2), `download_and_verify(release, dest_dir) -> Path | None` (Task 3)
- Produces: `poslib.updater.launch_silent_install(installer_path: Path) -> bool`, `poslib.updater.check_and_apply_update(cfg: Config) -> bool`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_updater.py`:

```python
class TestLaunchSilentInstall:

    def test_launches_installer_with_silent_flags(self, monkeypatch, tmp_path):
        installer = tmp_path / "Setup.exe"
        installer.write_bytes(b"fake")
        seen = {}

        class FakePopen:
            def __init__(self, cmd, **kwargs):
                seen["cmd"] = cmd
                seen["kwargs"] = kwargs
        monkeypatch.setattr(updater.subprocess, "Popen", FakePopen)

        assert updater.launch_silent_install(installer) is True
        assert seen["cmd"][0] == str(installer)
        assert "/VERYSILENT" in seen["cmd"]
        assert "/NORESTART" in seen["cmd"]
        assert "/SUPPRESSMSGBOXES" in seen["cmd"]

    def test_returns_false_when_spawning_fails(self, monkeypatch, tmp_path):
        installer = tmp_path / "Setup.exe"
        installer.write_bytes(b"fake")

        def _raise(*a, **k):
            raise OSError("cannot launch")
        monkeypatch.setattr(updater.subprocess, "Popen", _raise)

        assert updater.launch_silent_install(installer) is False


class TestCheckAndApplyUpdate:

    def test_returns_false_when_no_update_available(self, monkeypatch):
        monkeypatch.setattr(updater, "check_for_update", lambda cfg: None)

        assert updater.check_and_apply_update(FakeConfig()) is False

    def test_returns_false_when_download_fails(self, monkeypatch):
        release = updater.ReleaseInfo(version=(9, 9, 9), tag_name="v9.9.9",
                                       installer_url="https://x/Setup.exe",
                                       checksum_url="https://x/Setup.exe.sha256")
        monkeypatch.setattr(updater, "check_for_update", lambda cfg: release)
        monkeypatch.setattr(updater, "download_and_verify", lambda release, dest_dir: None)

        assert updater.check_and_apply_update(FakeConfig()) is False

    def test_returns_true_and_launches_installer_on_success(self, monkeypatch, tmp_path):
        release = updater.ReleaseInfo(version=(9, 9, 9), tag_name="v9.9.9",
                                       installer_url="https://x/Setup.exe",
                                       checksum_url="https://x/Setup.exe.sha256")
        installer_path = tmp_path / "Setup.exe"
        installer_path.write_bytes(b"fake")
        launched = []

        monkeypatch.setattr(updater, "check_for_update", lambda cfg: release)
        monkeypatch.setattr(updater, "download_and_verify",
                            lambda release, dest_dir: installer_path)
        monkeypatch.setattr(updater, "launch_silent_install",
                            lambda path: launched.append(path) or True)

        assert updater.check_and_apply_update(FakeConfig()) is True
        assert launched == [installer_path]

    def test_returns_false_when_launch_fails(self, monkeypatch, tmp_path):
        release = updater.ReleaseInfo(version=(9, 9, 9), tag_name="v9.9.9",
                                       installer_url="https://x/Setup.exe",
                                       checksum_url="https://x/Setup.exe.sha256")
        installer_path = tmp_path / "Setup.exe"
        installer_path.write_bytes(b"fake")

        monkeypatch.setattr(updater, "check_for_update", lambda cfg: release)
        monkeypatch.setattr(updater, "download_and_verify",
                            lambda release, dest_dir: installer_path)
        monkeypatch.setattr(updater, "launch_silent_install", lambda path: False)

        assert updater.check_and_apply_update(FakeConfig()) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests/test_updater.py -v`
Expected: FAIL — `AttributeError: module 'poslib.updater' has no attribute 'launch_silent_install'`

- [ ] **Step 3: Write the implementation**

Add to `poslib/updater.py` (add `subprocess` and `tempfile` to the imports):

```python
import subprocess
import tempfile
```

```python
def launch_silent_install(installer_path: Path) -> bool:
    """
    Spawns the installer detached and returns immediately without waiting
    for it to finish - the caller must stop and exit right after this so
    the installer can replace this process's own files once the OS
    releases the lock. Returns True if the process was launched, False if
    spawning itself failed. Never raises.
    """
    try:
        subprocess.Popen(
            [str(installer_path), "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"],
            close_fds=True,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        return True
    except OSError as exc:
        log.error("Could not launch the installer: %s", exc)
        return False


def check_and_apply_update(cfg: Config) -> bool:
    """
    The one entry point watcher.py calls. Checks for a newer release and,
    if everything checks out (found, downloaded, checksum verified,
    installer launched), returns True - the caller must stop and exit
    immediately so the installer can replace the running files. Returns
    False if there's no update or any step failed; the next attempt is the
    next watcher startup. Never raises.
    """
    release = check_for_update(cfg)
    if release is None:
        return False

    log.info("Update found: %s - downloading and verifying.", release.tag_name)
    dest_dir = Path(tempfile.mkdtemp(prefix="shop-analysis-update-"))
    installer_path = download_and_verify(release, dest_dir)
    if installer_path is None:
        log.warning("Update download/verification failed - will try again next login.")
        return False

    if not launch_silent_install(installer_path):
        return False

    log.info("Installer launched for %s - stopping so it can replace running files.",
              release.tag_name)
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests/test_updater.py -v`
Expected: PASS (25 tests)

- [ ] **Step 5: Commit**

```bash
git add poslib/updater.py tests/test_updater.py
git commit -m "feat(update): launch the silent install and wire the full check-apply flow"
```

---

### Task 5: Wire into the watcher and config

**Files:**
- Modify: `watcher.py:57-115` (add `_check_for_update`, call it first in `run()`)
- Modify: `config.template.yaml` (add an `update:` section)
- Test: `tests/test_watcher_update.py`

**Interfaces:**
- Consumes: `poslib.updater.check_and_apply_update(cfg: Config) -> bool` (Task 4)
- Produces: `Watcher._check_for_update(self) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_watcher_update.py
"""
Tests for Watcher._check_for_update - the thin wrapper around
poslib.updater.check_and_apply_update. Constructs a bare Watcher instance
(bypassing __init__, which needs a real ETL/database) since this method
only touches self.cfg.
"""

from __future__ import annotations

import poslib.updater as updater_module
from watcher import Watcher


class FakeConfig:
    pass


def _bare_watcher(cfg) -> Watcher:
    watcher = Watcher.__new__(Watcher)
    watcher.cfg = cfg
    return watcher


def test_check_for_update_delegates_to_updater_and_returns_true(monkeypatch):
    seen = {}

    def _fake_check_and_apply_update(cfg):
        seen["cfg"] = cfg
        return True
    monkeypatch.setattr(updater_module, "check_and_apply_update", _fake_check_and_apply_update)

    cfg = FakeConfig()
    watcher = _bare_watcher(cfg)

    assert watcher._check_for_update() is True
    assert seen["cfg"] is cfg


def test_check_for_update_returns_false_when_no_update(monkeypatch):
    monkeypatch.setattr(updater_module, "check_and_apply_update", lambda cfg: False)

    assert _bare_watcher(FakeConfig())._check_for_update() is False


def test_check_for_update_never_raises_on_unexpected_error(monkeypatch):
    def _raise(cfg):
        raise RuntimeError("boom")
    monkeypatch.setattr(updater_module, "check_and_apply_update", _raise)

    assert _bare_watcher(FakeConfig())._check_for_update() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\pytest.exe tests/test_watcher_update.py -v`
Expected: FAIL — `AttributeError: 'Watcher' object has no attribute '_check_for_update'`

- [ ] **Step 3: Write the implementation**

In `watcher.py`, add a new method to the `Watcher` class, placed right after `mark_dirty` (around line 79), matching the lazy-import + broad-except style already used by `_run_digest`/`_run_backup`/`_run_remote_push`:

```python
    # -- silent auto-update --------------------------------------------------

    def _check_for_update(self) -> bool:
        """
        Returns True if an update was found and its silent install was
        launched - the caller must stop and exit immediately so the
        installer can replace the running files. A failure here must
        never crash the watcher, same as the digest/backup/remote jobs.
        """
        try:
            from poslib.updater import check_and_apply_update
            return check_and_apply_update(self.cfg)
        except Exception:                                # noqa: BLE001
            log.exception("The update check failed")
            return False
```

Then in `Watcher.run()` (currently starting at line 236), add the check as the very first thing, before the folder existence check, so it can never overlap a `rebuild()`:

```python
    def run(self) -> None:
        if self._check_for_update():
            log.info("Stopping for the update to install.")
            return

        folder = self.source.parent
        if not folder.is_dir():
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\pytest.exe tests/test_watcher_update.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Add the config section**

In `config.template.yaml`, after the `watcher:` section (currently ending around line 335), add:

```yaml
# -----------------------------------------------------------------------------
#  AUTO-UPDATE - checks GitHub Releases once, at startup, for a newer
#  version and installs it silently if found. Only ever active in a
#  packaged install - has no effect when run from a git clone.
# -----------------------------------------------------------------------------
update:
  enabled: true

  # The GitHub repo releases are published to.
  github_repo: "rachadmihoubi/pos-tool"
```

No test for this step - it's a template comment/default, exercised by `check_for_update`'s existing tests via `FakeConfig`.

- [ ] **Step 6: Run the full test suite**

Run: `.venv\Scripts\pytest.exe tests -q`
Expected: PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
git add watcher.py config.template.yaml tests/test_watcher_update.py
git commit -m "feat(update): check for updates first thing at watcher startup"
```

---

### Task 6: Inno Setup — safe file replacement and relaunch

**Files:**
- Modify: `packaging/setup.iss`

No unit test — this only matters for a frozen build; verified by rebuilding the installer and testing the update flow end to end per the design doc's testing plan (Task 7 below produces the tool to actually exercise this).

- [ ] **Step 1: Add close-and-relaunch settings to `[Setup]`**

In `packaging/setup.iss`, in the `[Setup]` section (currently lines 10-18), add:

```ini
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
CloseApplications=force
CloseApplicationsFilter={#MyAppExeName}
RestartApplications=no
```

`CloseApplications=force` + the filter is the safety net: if a prior `ShopAnalysis.exe --watcher` process hasn't fully released its file lock by the time Inno starts copying (the update flow spawns the installer and exits, but a slow OS handle release is possible), Inno force-closes it rather than failing the silent install outright. `RestartApplications=no` because the app is relaunched explicitly below, not via Inno's own AppMutex-based restart (which the app doesn't register for).

- [ ] **Step 2: Add the unconditional watcher relaunch**

In the `[Run]` section (currently one line), add a second line with no `skipifsilent`, so it fires during both interactive and `/VERYSILENT` installs:

```ini
[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Open Shop Analysis now"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{#MyAppExeName}"; Parameters: "--watcher"; Flags: nowait runhidden
```

The first line is unchanged (still skipped on silent installs — the dashboard must never pop up unattended). The new second line always relaunches the watcher right after install completes, so an auto-update's new version is back up within seconds rather than waiting for the next login. This also closes a small pre-existing gap: previously, a fresh interactive install didn't start the watcher until the *next* login either — this line covers both cases.

- [ ] **Step 3: Rebuild and confirm it still compiles**

Run: `iscc packaging\setup.iss`
Expected: exits 0, produces `dist-installer\Setup.exe`. (Do not run the resulting installer yet — full end-to-end verification happens after Task 7, once there's a real release to update *to*.)

- [ ] **Step 4: Commit**

```bash
git add packaging/setup.iss
git commit -m "feat(packaging): close/relaunch the watcher cleanly around a silent update install"
```

---

### Task 7: Release publishing script

**Files:**
- Create: `packaging/publish_release.py`

No unit test — this is a dev-machine-only script that shells out to `pyinstaller`, `iscc`, and `gh` to actually build and publish a real release; it is verified by running it for real (per the design doc's testing plan: publish a dummy version bump and confirm a running watcher picks it up), the same way Task 7 of the packaging-installer plan verified `setup.iss` by actually building and installing rather than a unit test.

- [ ] **Step 1: Write the script**

```python
# packaging/publish_release.py
"""
publish_release.py - cuts a new release: bumps VERSION, builds the
installer, computes its checksum, and publishes both to GitHub Releases.

Run by hand on this dev PC when rachad wants to ship an update. Not part
of the shipped app - store PCs never see this file. Requires pyinstaller
(.venv), Inno Setup's `iscc` on PATH, and the `gh` CLI already logged in.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = PROJECT_ROOT / "VERSION"
SETUP_ISS = PROJECT_ROOT / "packaging" / "setup.iss"
INSTALLER_PATH = PROJECT_ROOT / "dist-installer" / "Setup.exe"
CHECKSUM_PATH = PROJECT_ROOT / "dist-installer" / "Setup.exe.sha256"
REPO = "rachadmihoubi/pos-tool"


def _read_version() -> tuple[int, int, int]:
    text = VERSION_FILE.read_text(encoding="utf-8").strip()
    major, minor, patch = (int(p) for p in text.split("."))
    return (major, minor, patch)


def _bump_patch(version: tuple[int, int, int]) -> tuple[int, int, int]:
    major, minor, patch = version
    return (major, minor, patch + 1)


def _write_version(version: tuple[int, int, int]) -> str:
    text = "{}.{}.{}".format(*version)
    VERSION_FILE.write_text(text + "\n", encoding="utf-8")
    return text


def _update_setup_iss(version_text: str) -> None:
    content = SETUP_ISS.read_text(encoding="utf-8")
    updated = re.sub(r"AppVersion=\S+", f"AppVersion={version_text}", content)
    if updated == content:
        raise SystemExit("Could not find an AppVersion= line in setup.iss to update.")
    SETUP_ISS.write_text(updated, encoding="utf-8")


def _run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)


def _build() -> None:
    _run([str(PROJECT_ROOT / ".venv" / "Scripts" / "pyinstaller.exe"),
          "packaging/pos-tool.spec", "--distpath", "dist", "--workpath", "build",
          "--noconfirm"])
    _run(["iscc", "packaging/setup.iss"])


def _write_checksum() -> str:
    digest = hashlib.sha256(INSTALLER_PATH.read_bytes()).hexdigest()
    CHECKSUM_PATH.write_text(f"{digest}  Setup.exe\n", encoding="utf-8")
    return digest


def _publish(version_text: str) -> None:
    tag = f"v{version_text}"
    _run(["gh", "release", "create", tag,
          str(INSTALLER_PATH), str(CHECKSUM_PATH),
          "--repo", REPO, "--generate-notes"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version",
                        help="Explicit version, e.g. 1.2.0. Default: bump the patch number.")
    args = parser.parse_args()

    current = _read_version()
    if args.version:
        parts = args.version.split(".")
        if len(parts) != 3:
            parser.error("--version must be MAJOR.MINOR.PATCH, e.g. 1.2.0")
        new_version = (int(parts[0]), int(parts[1]), int(parts[2]))
    else:
        new_version = _bump_patch(current)

    version_text = _write_version(new_version)
    _update_setup_iss(version_text)
    print(f"Version: {'.'.join(map(str, current))} -> {version_text}")

    _build()
    digest = _write_checksum()
    print(f"Setup.exe sha256: {digest}")

    _publish(version_text)
    print(f"Published v{version_text} to {REPO}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Commit**

```bash
git add packaging/publish_release.py
git commit -m "feat(packaging): add publish_release.py to cut and publish a versioned release"
```

- [ ] **Step 3: Exercise the full update flow end to end — ask for confirmation first**

This step publishes a real (if throwaway) GitHub Release to the public `rachadmihoubi/pos-tool` repo and runs a real silent install on this dev PC. Confirm with the user before running it.

Once confirmed:
1. Run `.venv\Scripts\python.exe packaging\publish_release.py` — bumps `VERSION` to `1.0.1`, builds, and publishes `v1.0.1`.
2. Confirm on GitHub (`gh release view v1.0.1`) that both `Setup.exe` and `Setup.exe.sha256` are attached.
3. Install `dist-installer\Setup.exe` once manually (interactive) to get a "currently installed v1.0.0-equivalent" baseline running as a real watcher — or, simpler, run `ShopAnalysis.exe --watcher` from `dist\ShopAnalysis\` directly after temporarily editing its bundled `VERSION` to `1.0.0` so it's older than the just-published `v1.0.1`.
4. Watch the log (`logs/pos-tool.log` under `%LOCALAPPDATA%\Shop Analysis` for a real install, or the console if run unfrozen-but-with-`is_frozen`-patched for a dry run) and confirm: the update is detected, downloaded, checksum-verified, installed silently (no dialog), and the watcher is back up within roughly a minute.
5. Deliberately corrupt the published checksum (`gh release delete-asset v1.0.1 Setup.exe.sha256`, re-upload a wrong one) and confirm a fresh check refuses to install and logs the mismatch instead.
6. Clean up: delete the throwaway release (`gh release delete v1.0.1 --yes`) and the tag (`git push --delete origin v1.0.1` if it was pushed, or just leave `VERSION` at whatever it bumped to — the next real release will bump forward from there, which is fine).

## Self-Review Notes

- **Spec coverage:** version tracking (Task 1), the check itself incl. frozen/config gating (Task 2), download+verify (Task 3), silent install+relaunch orchestration (Task 4), watcher wiring + config (Task 5), Inno Setup close/relaunch safety net (Task 6), release publishing (Task 7) — all four design sections and the failure-handling table are covered.
- **Placeholder scan:** no TBD/TODO; every step has real code or an exact command.
- **Type consistency:** `ReleaseInfo` fields (`version`, `tag_name`, `installer_url`, `checksum_url`) match across Tasks 2-4; `check_and_apply_update(cfg: Config) -> bool` and `_check_for_update(self) -> bool` signatures match between Task 4's implementation and Task 5's consumer.
- **The `is_frozen()` gate (added mid-design after re-reading `watcher.py`'s actual startup path via `install-startup.bat`)** is the one correction versus the original brainstormed design doc, which didn't explicitly call this out. It's now stated in Global Constraints and Task 2's implementation/tests.
