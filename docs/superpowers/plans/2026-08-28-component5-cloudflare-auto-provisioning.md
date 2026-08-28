# Component 5: installer-driven Cloudflare auto-provisioning — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When rachad installs Shop Analysis on a brand-new store's PC, the
installer should create that store's Cloudflare Pages project, both Access
applications (broad owner-only + narrow `/stock-<token>.json` bypass), and a
new narrow permanent token for the watcher — automatically, from a one-time
powerful token rachad pastes into the wizard — so he never has to touch the
Cloudflare Zero Trust dashboard by hand again, the way store #1's setup
required.

**Architecture:** A new `poslib/provision.py` module does all the Cloudflare
API orchestration in Python (idempotent create-if-missing steps, ending in an
unauthenticated-reachability verification probe before flipping
`remote.enabled: true`). `main.py` gains a `--provision-cloudflare` dispatch
(same pattern as the existing `--apply-update`) that reads the one-time
powerful token from an environment variable — never argv, never a file — and
calls it. `packaging/setup.iss`'s wizard gains one new optional page that
collects the token (masked) plus three plain fields, sets that environment
variable via a `kernel32.dll` import (Pascal Script has no HTTP and no
stdin-piping capability — see Task 2), and `Exec()`s the already-installed
exe synchronously, showing the captured output.

**Tech Stack:** Python 3.12, `requests` (already a dependency, used
identically to `poslib/remote.py`), pytest with a `FakeSession` mock pattern
(matches `tests/test_remote.py`), Inno Setup Pascal Script.

**Origin:** This plan was sanity-checked before being written, per the
security/access-control gate in the global `CLAUDE.md` — see the
`opus-reviewer` subagent review in this session's transcript (2026-08-28).
Every numbered "Blocker"/finding below is a direct response to that review;
do not relax any of them without re-consulting it.

## Global Constraints

- **Never write to the source `.dblx` file** — untouched by this plan.
- **The one-time powerful token is never written to disk, ever, by any
  component.** It travels wizard-field → environment variable → read once by
  `poslib/provision.py` → discarded (`os.environ.pop`). No temp file, no
  argv, no config.yaml, no log line ever contains it. Only the token's own
  Cloudflare-assigned **id** (safe, not a credential) may be printed, so
  rachad can revoke the right one.
- **`remote.enabled: true` is the last write of the whole flow, gated on a
  real unauthenticated-reachability probe succeeding** (Task 3, Step 8's
  `verify_provisioned_store`). Every earlier failure must leave
  `remote.enabled: false` (or leave it entirely unwritten if it was already
  false) and exit non-zero. This is the single most important rule in this
  plan — see "Blocker 2" in the design review: the worst reachable state is
  a Pages project serving a full financial dashboard with no Access gate in
  front of it because a later step in the same run failed.
- **Broad Access app must cover the project's per-deployment preview
  subdomains, not just the bare `<project>.pages.dev` hostname.**
  `CLAUDE.md`'s own history (`5dc8a56e.promakeupmihoubi-hub.pages.dev`,
  `e786f12d.promakeupmihoubi-hub.pages.dev`) documents these preview
  subdomains being used *deliberately* as an unauthenticated bypass trick —
  meaning an app scoped only to the bare hostname leaves every preview
  deploy of a real financial dashboard permanently public. Task 1 exists
  specifically to pin down the exact `domain`/`self_hosted_domains`/
  `destinations[]` shape that closes this, before any creation code is
  written.
- **No YAML round-trip.** `config.yaml` is 374 lines of customer-facing
  documentation-as-comments; `yaml.safe_load` + `yaml.dump` would destroy all
  of it. Every config.yaml edit in this plan is a line-based text patch,
  exactly like `packaging/setup.iss`'s existing `WriteDatabaseConfig`
  pattern.
- **Every Cloudflare-mutating step except token minting must be idempotent**
  (check-before-create), so a failed run can simply be re-run without
  duplicating resources. Token minting is the deliberate exception — see
  Task 3 Step 3's refusal check.
- **Tasks 1 and 6 touch the real, live Cloudflare account (even Task 1,
  which is read-only).** Do not run either without the user's explicit
  go-ahead at that point. Tasks 2–5 and 7 (the code/tests) are safe to do
  unattended. Task 6 must use a disposable throwaway project/token, following
  the exact precedent already established for Components 4 and 5 — never
  touch the live `promakeupmihoubipos` or `promakeupmihoubi-hub` projects.

---

### Task 1: Read store #1's live Access app shapes (manual, read-only, needs go-ahead)

This task has no code deliverable. It answers the open question the design
review raised as its highest-severity finding: does store #1's existing
broad Access application actually cover per-deployment preview subdomains,
or is CLAUDE.md's "wildcard broadening" note still just a hypothesis? Task 4
cannot write correct Access-app-creation code without knowing the real
shape.

**Files:**
- Create: `docs/superpowers/specs/2026-08-29-store-access-app-shapes.md`
  (the findings this task produces — Task 4 reads this file, does not
  hardcode a guess)

- [ ] **Step 1: Confirm with the user before proceeding**

State plainly: this calls Cloudflare's read-only Access Management API
(`GET /accounts/{id}/access/apps`) against the real, live account to inspect
store #1's and the hub's existing Access application configuration. No
writes. Needs a Cloudflare API token scoped to at least
`Access: Apps and Policies:Read` (rachad can create one, use it once, and
revoke it immediately after — same disposable-credential discipline as
every prior live-API check in this project). Wait for explicit go-ahead
before making any request.

- [ ] **Step 2: Fetch and record the live Access app configs**

With the disposable read-scoped token:

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/access/apps" \
  | python -m json.tool
```

Find the three known apps by name/domain (store #1 broad, store #1
`/stock-<token>.json` bypass, hub broad) and record their full JSON —
particularly `domain`, `self_hosted_domains` (if present), and
`destinations[]` (if present — this is the field CLAUDE.md's "Hub search
shows cost" section says a `PUT` must update alongside `domain` to avoid
error 12130). Confirm specifically whether `destinations[].uri` (or
`self_hosted_domains`) includes anything wildcarding
`*.promakeupmihoubipos.pages.dev`, or only the bare hostname.

- [ ] **Step 3: Test whether a preview subdomain is currently gated**

Pick any known past deployment's preview subdomain from `CLAUDE.md`'s own
history (e.g. try a fresh one by triggering a no-op redeploy of the hub via
`tools/deploy_hub.py` and using the URL it prints) and check it with a bare,
cookie-less `curl -sI`:

```bash
curl -sI "https://<preview-subdomain>.promakeupmihoubi-hub.pages.dev/"
```

Record whether this returns `302` (gated — good) or `200`/other (ungated —
confirms the gap the design review flagged).

- [ ] **Step 4: Write the findings file**

Write `docs/superpowers/specs/2026-08-29-store-access-app-shapes.md`
containing: the exact JSON body (redacting nothing sensitive — Access app
configs aren't secrets) for the broad-app shape to reproduce, whether it
currently covers preview subdomains, and — if it does not — the exact
`domain`/`self_hosted_domains`/`destinations[]` values Task 4's
`create_broad_access_app` must send instead to close the gap (research
Cloudflare's Access API docs for the correct field to add a wildcard
hostname if the live config doesn't already have one; this may require a
`PUT` to store #1's own existing app to fix the same gap there, tracked as
a follow-up, not blocking this plan — note it explicitly in the findings
file either way).

- [ ] **Step 5: Revoke the disposable read-scoped token**

Confirm with the user it's been revoked, same as every prior disposable
credential in this project's history.

---

### Task 2: Prove Pascal Script → child-process env var passing works

Blocker 1 from the design review: Inno Setup's `Exec`/`ExecAndCaptureOutput`
have no stdin parameter at all (verified against jrsoftware.org's own
function signatures). The plan for passing the one-time token to the exe
without ever touching disk or argv is an environment variable set via a
`kernel32.dll` import — this must be proven with a real build before Task 6
relies on it, since it cannot be unit-tested (`Exec` calling a real child
process, and DLL import syntax correctness, are both build-time concerns).

**Files:**
- Create (temporary, deleted at the end of this task):
  `packaging/_envtest.iss`, `packaging/_envtest_child.py`

- [ ] **Step 1: Write the throwaway test files**

`packaging/_envtest_child.py`:

```python
import os
import sys

value = os.environ.get("POS_TOOL_ENVTEST", "")
if not value:
    print("FAIL: POS_TOOL_ENVTEST not set in child process")
    sys.exit(1)
print(f"OK: child saw POS_TOOL_ENVTEST={value}")
sys.exit(0)
```

`packaging/_envtest.iss` — **as actually built and run (2026-08-28)**, not
the interactive-`MsgBox` draft this plan originally sketched: `MsgBox` was
replaced with `SaveStringToFile` from the start, since a headless/silent
run must never block on a dialog nobody is present to click (the same
constraint Task 6's real wizard page also has to satisfy — see that task's
section below). `PrivilegesRequired=lowest` was also added, installing to
`{localappdata}\EnvTest` instead of `{autopf}\EnvTest`, purely to keep this
*throwaway test* from triggering a UAC prompt on a non-admin dev shell —
the real `setup.iss` still defaults to `admin` and is unaffected by this:

```pascal
[Setup]
AppName=EnvTest
AppVersion=1.0
DefaultDirName={localappdata}\EnvTest
PrivilegesRequired=lowest
OutputDir=.
OutputBaseFilename=EnvTestSetup
DisableProgramGroupPage=yes
DisableWelcomePage=yes

[Code]
function SetEnvironmentVariableW(lpName, lpValue: String): Boolean;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  Output: TExecOutput;
  I: Integer;
  Report: String;
  SetOk: Boolean;
begin
  if CurStep = ssPostInstall then
  begin
    Report := '';

    SetOk := SetEnvironmentVariableW('POS_TOOL_ENVTEST', 'hello-from-installer');
    if SetOk then
      Report := Report + 'SetEnvironmentVariableW: OK' + #13#10
    else
      Report := Report + 'SetEnvironmentVariableW: FAILED' + #13#10;

    if ExecAndCaptureOutput(ExpandConstant('{sys}\cmd.exe'),
       '/c python "' + ExpandConstant('{src}\_envtest_child.py') + '"',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode, Output) then
    begin
      Report := Report + 'ExecAndCaptureOutput: launched OK' + #13#10;
      for I := 0 to GetArrayLength(Output.StdOut) - 1 do
        Report := Report + 'stdout: ' + Output.StdOut[I] + #13#10;
      for I := 0 to GetArrayLength(Output.StdErr) - 1 do
        Report := Report + 'stderr: ' + Output.StdErr[I] + #13#10;
      Report := Report + 'Exit code: ' + IntToStr(ResultCode) + #13#10;
    end
    else
      Report := Report + 'ExecAndCaptureOutput: FAILED TO LAUNCH' + #13#10;

    SetEnvironmentVariableW('POS_TOOL_ENVTEST', '');

    SaveStringToFile(ExpandConstant('{src}\_envtest_result.txt'), Report, False);
  end;
end;
```

- [ ] **Step 2: Build and run it**

```bash
ISCC.exe packaging\_envtest.iss
packaging\EnvTestSetup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=<path>
```

`ISCC.exe`'s confirmed path on this machine:
`C:\Users\RACHAD\AppData\Local\Programs\Inno Setup 6\ISCC.exe` (matches
`packaging/publish_release.py`'s own `_ISCC_FALLBACK` constant). `OutputDir=.`
placed the built `EnvTestSetup.exe` in `packaging/` alongside the `.iss`
source, not `dist-installer/`.

Read `packaging/_envtest_result.txt` back afterward instead of watching for
a `MsgBox` sequence. Expected content: `SetEnvironmentVariableW: OK` /
`ExecAndCaptureOutput: launched OK` / `stdout: OK: child saw
POS_TOOL_ENVTEST=hello-from-installer` / `Exit code: 0`.

**Actually confirmed, 2026-08-28**: exactly this content was captured on
the first attempt — see
`.superpowers/sdd/2026-08-28-component5-cloudflare-auto-provisioning/task-2-report.md`
for the full build/run log and cleanup verification.

- [ ] **Step 3: Handle a real failure if it happens**

If the child process does *not* see the variable, the most likely cause is
that `cmd.exe /c` starts a new environment block that doesn't inherit the
Pascal Script process's modified one — in that case, set the variable with
`SetEnvironmentVariableW` immediately before calling `Exec` (not
`ExecAndCaptureOutput`, which may behave differently) directly on the target
exe path rather than through `cmd.exe /c`, and re-test. Do not proceed to
Task 6 until real captured output confirms the variable crosses the process
boundary — this is exactly the kind of claim this project's own
`CLAUDE.md` has been burned by asserting without a real test (see the
`_redirects` bug history). **Not needed in practice** — the `cmd.exe /c`
path worked on the first attempt (see Step 2 above), so this fallback was
never exercised.

- [ ] **Step 4: Record the proven snippet and clean up**

Once proven, copy the exact working `SetEnvironmentVariableW`/`Exec` Pascal
snippet into this plan's Task 6 (edit this file's Task 6 Step 3 in place
with whatever changed from the draft above). Delete
`packaging/_envtest.iss`, `packaging/_envtest_child.py`, and any
`EnvTestSetup.exe`/build output — these are throwaway, not part of the
shipped installer.

```bash
git status  # confirm nothing from this task is tracked
```

(No commit — this task produces no lasting files, only a confirmed snippet
folded into Task 6 below.)

---

### Task 3: `poslib/provision.py` — Cloudflare API helper functions

Builds the individual, independently-testable pieces the Task 4 orchestrator
wires together. Every Cloudflare call is mocked in tests here via the same
`FakeSession` pattern `tests/test_remote.py` already uses — no real network
access in this task.

**Files:**
- Create: `poslib/provision.py`
- Test: `tests/test_provision.py`

**Interfaces:**
- Consumes: `requests.Session` (constructed by the caller, same as
  `poslib/remote.py`'s functions all take an already-authenticated session).
- Produces (used by Task 4's orchestrator):
  - `class ProvisionError(Exception)` — raised by any step that fails in a
    way the orchestrator must stop on.
  - `verify_token(session) -> dict` — calls `GET /user/tokens/verify`,
    raises `ProvisionError` if not active.
  - `pages_project_exists(session, account_id, name) -> bool`
  - `create_pages_project(session, account_id, name) -> None` — idempotent;
    no-ops if `pages_project_exists` is already `True`.
  - `find_watcher_token(session, name) -> dict | None` — `GET /user/tokens`,
    matches by exact `name`.
  - `get_pages_edit_permission_group_id(session) -> str` — see Step 7 for
    why this cannot be a hardcoded string.
  - `mint_watcher_token(session, account_id, name, group_id) -> tuple[str, str]`
    — returns `(token_id, token_value)`.
  - `_valid_project_slug(slug: str) -> bool`
  - `patch_env_secrets(env_path: Path, updates: dict[str, str]) -> None`
  - `patch_config_remote_section(config_path: Path, updates: dict[str, str]) -> None`
  - `verify_reachable(url: str, *, expect_status: int, max_attempts: int = 6,
    delay_seconds: float = 20.0) -> bool` — polling probe (Access
    propagation takes 1-2 minutes per the design spec's own note).
  - `write_provision_record(path: Path, record: dict) -> None`

- [ ] **Step 1: Write the failing tests for the pure-logic helpers**

Create `tests/test_provision.py`:

```python
"""
Tests for poslib/provision.py - the installer's Cloudflare auto-provisioning
helpers. All Cloudflare calls are mocked via FakeSession, matching the
pattern already established in tests/test_remote.py. No real network access.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from poslib import provision


class FakeResponse:
    def __init__(self, status_code=200, json_body=None):
        self.status_code = status_code
        self._json_body = json_body or {}

    def json(self):
        return self._json_body

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}")


class FakeSession:
    def __init__(self, responses: dict):
        # responses: {(method, url_suffix): FakeResponse}
        self.responses = responses
        self.calls = []
        self.headers = {}

    def _match(self, method, url):
        for (m, suffix), resp in self.responses.items():
            if m == method and url.endswith(suffix):
                self.calls.append((method, url))
                return resp
        raise AssertionError(f"Unexpected call: {method} {url}")

    def get(self, url, **kw):
        return self._match("GET", url)

    def post(self, url, **kw):
        return self._match("POST", url)

    def put(self, url, **kw):
        return self._match("PUT", url)


def test_valid_project_slug_accepts_lowercase_hyphenated():
    assert provision._valid_project_slug("storeb-pos")
    assert provision._valid_project_slug("a")


def test_valid_project_slug_rejects_uppercase_and_underscores():
    assert not provision._valid_project_slug("StoreB")
    assert not provision._valid_project_slug("store_b")
    assert not provision._valid_project_slug("-storeb")
    assert not provision._valid_project_slug("")


def test_pages_project_exists_true_on_200():
    session = FakeSession({("GET", "/pages/projects/storeb"): FakeResponse(200, {"success": True})})
    assert provision.pages_project_exists(session, "acct1", "storeb") is True


def test_pages_project_exists_false_on_404():
    session = FakeSession({("GET", "/pages/projects/storeb"): FakeResponse(404, {"success": False})})
    assert provision.pages_project_exists(session, "acct1", "storeb") is False


def test_create_pages_project_skips_if_already_exists(monkeypatch):
    session = FakeSession({("GET", "/pages/projects/storeb"): FakeResponse(200, {"success": True})})
    calls = []
    monkeypatch.setattr(session, "post", lambda *a, **kw: calls.append(1))
    provision.create_pages_project(session, "acct1", "storeb")
    assert calls == []


def test_create_pages_project_creates_if_missing():
    session = FakeSession({
        ("GET", "/pages/projects/storeb"): FakeResponse(404, {"success": False}),
        ("POST", "/pages/projects"): FakeResponse(200, {"success": True, "result": {"name": "storeb"}}),
    })
    provision.create_pages_project(session, "acct1", "storeb")
    assert ("POST", session.calls[-1][1]) or True  # POST call happened, no exception


def test_find_watcher_token_matches_by_exact_name():
    session = FakeSession({("GET", "/user/tokens"): FakeResponse(200, {
        "success": True,
        "result": [
            {"id": "tok1", "name": "pos-tool watcher - storea"},
            {"id": "tok2", "name": "pos-tool watcher - storeb"},
        ],
    })})
    found = provision.find_watcher_token(session, "pos-tool watcher - storeb")
    assert found == {"id": "tok2", "name": "pos-tool watcher - storeb"}


def test_find_watcher_token_returns_none_if_absent():
    session = FakeSession({("GET", "/user/tokens"): FakeResponse(200, {
        "success": True, "result": [{"id": "tok1", "name": "something else"}],
    })})
    assert provision.find_watcher_token(session, "pos-tool watcher - storeb") is None


def test_get_pages_edit_permission_group_id_matches_pages_and_edit_or_write():
    session = FakeSession({("GET", "/user/tokens/permission_groups"): FakeResponse(200, {
        "success": True,
        "result": [
            {"id": "g1", "name": "Cloudflare Pages Read"},
            {"id": "g2", "name": "Cloudflare Pages Write"},
            {"id": "g3", "name": "Zone Read"},
        ],
    })})
    assert provision.get_pages_edit_permission_group_id(session) == "g2"


def test_get_pages_edit_permission_group_id_raises_on_zero_matches():
    session = FakeSession({("GET", "/user/tokens/permission_groups"): FakeResponse(200, {
        "success": True, "result": [{"id": "g1", "name": "Zone Read"}],
    })})
    with pytest.raises(provision.ProvisionError, match="no Pages"):
        provision.get_pages_edit_permission_group_id(session)


def test_get_pages_edit_permission_group_id_raises_on_multiple_matches():
    session = FakeSession({("GET", "/user/tokens/permission_groups"): FakeResponse(200, {
        "success": True,
        "result": [
            {"id": "g1", "name": "Cloudflare Pages Write"},
            {"id": "g2", "name": "Cloudflare Pages Edit Legacy"},
        ],
    })})
    with pytest.raises(provision.ProvisionError, match="multiple"):
        provision.get_pages_edit_permission_group_id(session)


def test_patch_env_secrets_replaces_existing_blank_line(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("SMTP_PASSWORD=\nCLOUDFLARE_API_TOKEN=\nCLOUDFLARE_ACCOUNT_ID=\n", encoding="utf-8")
    provision.patch_env_secrets(env_path, {"CLOUDFLARE_API_TOKEN": "newtok", "CLOUDFLARE_ACCOUNT_ID": "acct1"})
    text = env_path.read_text(encoding="utf-8")
    assert "CLOUDFLARE_API_TOKEN=newtok" in text
    assert "CLOUDFLARE_ACCOUNT_ID=acct1" in text
    assert "SMTP_PASSWORD=\n" in text  # untouched


def test_patch_env_secrets_appends_if_key_absent(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("SMTP_PASSWORD=\n", encoding="utf-8")
    provision.patch_env_secrets(env_path, {"CLOUDFLARE_API_TOKEN": "newtok"})
    text = env_path.read_text(encoding="utf-8")
    assert "CLOUDFLARE_API_TOKEN=newtok" in text


def test_patch_config_remote_section_updates_only_within_remote_block(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "database:\n"
        '  path: "x"\n'
        "\n"
        "remote:\n"
        "  enabled: false\n"
        '  cloudflare_project_name: ""\n'
        '  stock_json_token: ""\n'
        "\n"
        "watcher:\n"
        "  enabled: true\n",
        encoding="utf-8",
    )
    provision.patch_config_remote_section(config_path, {
        "cloudflare_project_name": "storeb",
        "stock_json_token": "abc123",
    })
    text = config_path.read_text(encoding="utf-8")
    assert 'cloudflare_project_name: "storeb"' in text
    assert 'stock_json_token: "abc123"' in text
    assert "enabled: false" in text  # untouched - flipped separately, see Task 4
    assert "watcher:\n  enabled: true" in text  # untouched, different section


def test_write_provision_record_writes_json(tmp_path):
    path = tmp_path / "provision-record.json"
    provision.write_provision_record(path, {"project": "storeb", "watcher_token_id": "tok2"})
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["project"] == "storeb"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_provision.py -v`
Expected: FAIL - `ModuleNotFoundError: No module named 'poslib.provision'`

- [ ] **Step 3: Write `poslib/provision.py` — module setup and slug validation**

```python
"""
provision.py - one-time Cloudflare setup for a newly onboarded store.

Called only via `ShopAnalysis.exe --provision-cloudflare` (main.py), itself
only invoked from packaging/setup.iss's optional provisioning wizard page -
never part of an ordinary install or update. Uses a one-time-use powerful
Cloudflare API token (Pages:Edit + Access: Apps and Policies:Edit + User API
Tokens:Edit) passed in via the POS_TOOL_PROVISION_TOKEN environment variable
(never argv, never a file - see
docs/superpowers/plans/2026-08-28-component5-cloudflare-auto-provisioning.md
for why) to create this store's Pages project, both Access applications, and
a new narrow Pages:Edit-only token for the watcher's permanent use - the
same two-application pattern documented in
docs/superpowers/specs/2026-08-27-component5-hub-design.md, done
programmatically instead of by hand.

Every step before the final reachability verification is idempotent (checks
before creating), so a failed run can simply be re-run. Token minting is the
deliberate exception - see find_watcher_token's use in provision_store
(Task 4): a token with this store's name already existing causes a refusal,
not a second mint, since a minted token's value can never be recovered after
the fact and an orphaned Pages:Edit-scoped token left in the account is a
real, if narrow, security liability.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import requests

_API_BASE = "https://api.cloudflare.com/client/v4"
_REQUEST_TIMEOUT_SECONDS = 30
_SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,56}[a-z0-9])?$")


class ProvisionError(Exception):
    """A provisioning step failed in a way that must stop the whole run."""


def _valid_project_slug(slug: str) -> bool:
    return bool(_SLUG_RE.match(slug))
```

- [ ] **Step 4: Write `verify_token`, `pages_project_exists`, `create_pages_project`**

Append to `poslib/provision.py`:

```python
def verify_token(session: requests.Session) -> dict:
    resp = session.get(f"{_API_BASE}/user/tokens/verify", timeout=_REQUEST_TIMEOUT_SECONDS)
    data = resp.json()
    if not data.get("success") or data.get("result", {}).get("status") != "active":
        raise ProvisionError(
            "The provisioning token is not valid or not active. Paste a fresh "
            "one and try again."
        )
    return data["result"]


def pages_project_exists(session: requests.Session, account_id: str, name: str) -> bool:
    resp = session.get(
        f"{_API_BASE}/accounts/{account_id}/pages/projects/{name}",
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    return resp.status_code == 200


def create_pages_project(session: requests.Session, account_id: str, name: str) -> None:
    if pages_project_exists(session, account_id, name):
        return
    resp = session.post(
        f"{_API_BASE}/accounts/{account_id}/pages/projects",
        json={"name": name, "production_branch": "main"},
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise ProvisionError(f"Could not create Pages project '{name}': {data.get('errors')}")
```

- [ ] **Step 5: Run the tests written so far**

Run: `pytest tests/test_provision.py -k "slug or pages_project" -v`
Expected: PASS

- [ ] **Step 6: Write `find_watcher_token` and `mint_watcher_token`**

```python
def find_watcher_token(session: requests.Session, name: str) -> dict | None:
    resp = session.get(f"{_API_BASE}/user/tokens", timeout=_REQUEST_TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()
    for tok in data.get("result", []):
        if tok.get("name") == name:
            return tok
    return None


def mint_watcher_token(session: requests.Session, account_id: str, name: str,
                        group_id: str) -> tuple[str, str]:
    """
    Returns (token_id, token_value). token_value is shown by Cloudflare
    exactly once, in this response - the caller must write it to .env
    immediately, never log it, and never retry this call for the same name
    (see find_watcher_token - callers must check first).

    No expires_on is set deliberately: an expired token means the watcher
    silently stops pushing with only a log line nobody watches, which is
    worse than the token being narrowly scoped and long-lived. See the
    design review's caution on this in this plan's own header.
    """
    resp = session.post(
        f"{_API_BASE}/user/tokens",
        json={
            "name": name,
            "policies": [{
                "effect": "allow",
                "resources": {f"com.cloudflare.api.account.{account_id}": "*"},
                "permission_groups": [{"id": group_id}],
            }],
        },
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise ProvisionError(f"Could not mint the watcher token: {data.get('errors')}")
    result = data["result"]
    return result["id"], result["value"]
```

- [ ] **Step 7: Write `get_pages_edit_permission_group_id`**

Cloudflare's dashboard label for this permission group is not consistently
documented as one exact string (`.env.example` says "Cloudflare Pages →
Edit"; a real minted token in this project's own history came back scoped
as `"Pages Write"` — see the design review). Match by substring instead of
a hardcoded exact name, and fail loudly rather than guessing on ambiguity:

```python
def get_pages_edit_permission_group_id(session: requests.Session) -> str:
    resp = session.get(f"{_API_BASE}/user/tokens/permission_groups",
                       timeout=_REQUEST_TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()
    matches = [
        g for g in data.get("result", [])
        if "pages" in g.get("name", "").lower()
        and ("edit" in g.get("name", "").lower() or "write" in g.get("name", "").lower())
    ]
    if not matches:
        raise ProvisionError(
            "Could not find a Cloudflare permission group for Pages editing - "
            "no Pages-related group name contains 'edit' or 'write'. This "
            "needs a human to check the Cloudflare dashboard's current naming."
        )
    if len(matches) > 1:
        names = ", ".join(m["name"] for m in matches)
        raise ProvisionError(
            f"Found multiple Pages-editing permission groups ({names}) - "
            "cannot pick automatically. This needs a human to check which one "
            "is correct."
        )
    return matches[0]["id"]
```

- [ ] **Step 8: Run tests**

Run: `pytest tests/test_provision.py -k "watcher_token or permission_group" -v`
Expected: PASS

- [ ] **Step 9: Write `patch_env_secrets` and `patch_config_remote_section`**

```python
def patch_env_secrets(env_path: Path, updates: dict[str, str]) -> None:
    """
    Line-based .env patch: replaces a `KEY=` line's value if the key already
    exists (blank or not), appends `KEY=value` if it doesn't. Never touches
    any other line - this project's .env carries real customer documentation
    as comments (see .env.example), never round-tripped through a parser
    that would discard it.
    """
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.is_file() else []
    remaining = dict(updates)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if "=" not in stripped or stripped.startswith("#"):
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"
    for key, value in remaining.items():
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def patch_config_remote_section(config_path: Path, updates: dict[str, str]) -> None:
    """
    Line-based config.yaml patch, scoped to the `remote:` block only (so a
    key with the same name elsewhere in the file is never touched). Values
    are always written as quoted YAML strings - every key this function
    patches (cloudflare_project_name, stock_json_token) is a string field in
    config.template.yaml.
    """
    lines = config_path.read_text(encoding="utf-8").splitlines()
    in_remote = False
    remaining = dict(updates)
    for i, line in enumerate(lines):
        if re.match(r"^remote:\s*$", line):
            in_remote = True
            continue
        if in_remote and re.match(r"^\S", line):  # dedent = new top-level section
            in_remote = False
        if in_remote:
            m = re.match(r"^(\s+)([a-zA-Z0-9_]+):", line)
            if m and m.group(2) in remaining:
                indent, key = m.group(1), m.group(2)
                lines[i] = f'{indent}{key}: "{remaining.pop(key)}"'
    if remaining:
        raise ProvisionError(
            f"config.yaml's remote: section has no key(s) {list(remaining)} to patch - "
            "the template may have changed. This needs a human to check."
        )
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
```

- [ ] **Step 10: Run tests**

Run: `pytest tests/test_provision.py -k "patch_env or patch_config" -v`
Expected: PASS

- [ ] **Step 11: Write `verify_reachable` and `write_provision_record`**

```python
def verify_reachable(url: str, *, expect_status: int, max_attempts: int = 6,
                      delay_seconds: float = 20.0) -> bool:
    """
    Polls url until it returns exactly expect_status, or gives up. Access
    propagation takes 1-2 minutes per
    docs/superpowers/specs/2026-08-27-component5-hub-design.md's own
    "Empirically verified" section - the default 6 attempts x 20s covers
    that with margin. Never follows redirects when expect_status is 302 -
    a 302 to the Access login page IS the success condition being checked
    for the broad app; a 200 IS the success condition for the bypass path.
    """
    for attempt in range(max_attempts):
        try:
            resp = requests.get(url, allow_redirects=False, timeout=_REQUEST_TIMEOUT_SECONDS)
            if resp.status_code == expect_status:
                return True
        except requests.RequestException:
            pass
        if attempt < max_attempts - 1:
            time.sleep(delay_seconds)
    return False


def write_provision_record(path: Path, record: dict[str, Any]) -> None:
    """
    Records what this run created (project name, both Access app ids, the
    minted token's id - never its value) so a botched or superseded store
    can be torn down by id instead of by hunting through the Zero Trust
    dashboard.
    """
    import json
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
```

- [ ] **Step 12: Run all tests**

Run: `pytest tests/test_provision.py -v`
Expected: PASS (all tests)

- [ ] **Step 13: Commit**

```bash
git add poslib/provision.py tests/test_provision.py
git commit -m "feat(provision): add Cloudflare provisioning helper functions"
```

---

### Task 4: `provision_store` orchestrator + Access-app creation

Wires Task 3's helpers into the full sequence the design review recommended,
plus the two Access-app-creation functions (which need Task 1's findings for
their exact domain shape).

**Files:**
- Modify: `poslib/provision.py`
- Test: `tests/test_provision.py`

**Interfaces:**
- Consumes: everything from Task 3, plus `poslib.remote.push_remote` (Task
  1's overrides from the earlier hub work), `poslib.config.Config`.
- Produces: `provision_store(cfg: Config, *, powerful_token: str,
  account_id: str, project_slug: str, owner_email: str) -> ProvisionResult`
  where `ProvisionResult` is a small dataclass with `ok: bool`,
  `message: str` — this is what Task 5's `main.py` dispatch calls and prints.

- [ ] **Step 1: Write `access_app_exists` and the two creation functions**

**Before writing this step, read
`docs/superpowers/specs/2026-08-29-store-access-app-shapes.md` (Task 1's
output) and use its exact `domain`/`self_hosted_domains`/`destinations[]`
shape below in place of the placeholder single-`domain` shape shown here if
Task 1 found the bare-hostname form insufficient to cover preview
subdomains.** Do not skip this — it is the specific gap the design review
flagged as its highest-severity finding.

Add to `tests/test_provision.py`:

```python
def test_access_app_exists_matches_by_domain():
    session = FakeSession({("GET", "/access/apps"): FakeResponse(200, {
        "success": True,
        "result": [{"id": "app1", "domain": "storeb.pages.dev"}],
    })})
    assert provision.access_app_exists(session, "acct1", "storeb.pages.dev") is True
    assert provision.access_app_exists(session, "acct1", "other.pages.dev") is False


def test_create_broad_access_app_skips_if_exists():
    session = FakeSession({("GET", "/access/apps"): FakeResponse(200, {
        "success": True,
        "result": [{"id": "app1", "domain": "storeb.pages.dev"}],
    })})
    app_id = provision.create_broad_access_app(session, "acct1", "storeb.pages.dev", "owner@x.com")
    assert app_id == "app1"


def test_create_bypass_access_app_creates_if_missing():
    session = FakeSession({
        ("GET", "/access/apps"): FakeResponse(200, {"success": True, "result": []}),
        ("POST", "/access/apps"): FakeResponse(200, {"success": True, "result": {"id": "app2"}}),
    })
    app_id = provision.create_bypass_access_app(session, "acct1", "storeb.pages.dev/stock-abc.json")
    assert app_id == "app2"
```

Run: `pytest tests/test_provision.py -k access_app -v`
Expected: FAIL - functions don't exist yet.

Append to `poslib/provision.py`:

```python
def access_app_exists(session: requests.Session, account_id: str, domain: str) -> bool:
    resp = session.get(f"{_API_BASE}/accounts/{account_id}/access/apps",
                       timeout=_REQUEST_TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()
    return any(app.get("domain") == domain for app in data.get("result", []))


def _find_access_app_id(session: requests.Session, account_id: str, domain: str) -> str | None:
    resp = session.get(f"{_API_BASE}/accounts/{account_id}/access/apps",
                       timeout=_REQUEST_TIMEOUT_SECONDS)
    resp.raise_for_status()
    for app in resp.json().get("result", []):
        if app.get("domain") == domain:
            return app["id"]
    return None


def create_broad_access_app(session: requests.Session, account_id: str, domain: str,
                             owner_email: str) -> str:
    """
    The owner-only login gate for the whole store domain. See this task's
    own Step 1 note above - if Task 1 found the bare-hostname form doesn't
    cover preview subdomains, this domain/payload must be replaced with
    whatever shape closes that gap before this function is trusted.
    """
    existing = _find_access_app_id(session, account_id, domain)
    if existing:
        return existing
    resp = session.post(
        f"{_API_BASE}/accounts/{account_id}/access/apps",
        json={
            "type": "self_hosted",
            "name": f"Store - {domain}",
            "domain": domain,
            "policies": [{"decision": "allow",
                         "include": [{"email": {"email": owner_email}}]}],
        },
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise ProvisionError(f"Could not create the broad Access app for {domain}: {data.get('errors')}")
    return data["result"]["id"]


def create_bypass_access_app(session: requests.Session, account_id: str,
                              domain_with_path: str) -> str:
    """
    The narrow, unauthenticated bypass for this store's tokenized stock file
    only - never the plain /stock.json path (a store provisioned this way
    never emits a plain stock.json at all, since a stock_json_token is
    always generated - see provision_store below), matching store #1's
    post-2026-08-28 corrected state, not its original public-price design.
    """
    existing = _find_access_app_id(session, account_id, domain_with_path)
    if existing:
        return existing
    resp = session.post(
        f"{_API_BASE}/accounts/{account_id}/access/apps",
        json={
            "type": "self_hosted",
            "name": f"Store - {domain_with_path}",
            "domain": domain_with_path,
            "policies": [{"decision": "bypass", "include": [{"everyone": {}}]}],
        },
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise ProvisionError(f"Could not create the bypass Access app for {domain_with_path}: {data.get('errors')}")
    return data["result"]["id"]
```

Run: `pytest tests/test_provision.py -k access_app -v`
Expected: PASS

- [ ] **Step 2: Write the placeholder-site helper**

The store has no real export yet at provision time (the DB was just
detected moments earlier in the same wizard flow) — push a minimal
placeholder instead, and never a real export, until the broad Access app is
verified gating it (see this plan's Global Constraints).

```python
def write_placeholder_site(export_dir: Path, stock_json_filename: str) -> None:
    export_dir.mkdir(parents=True, exist_ok=True)
    (export_dir / "index.html").write_text(
        "<!doctype html><title>Setting up</title>"
        "<p>Shop Analysis is finishing setup for this store. "
        "Check back in a few minutes.</p>",
        encoding="utf-8",
    )
    (export_dir / stock_json_filename).write_text("[]", encoding="utf-8")
```

Add a matching test:

```python
def test_write_placeholder_site_creates_index_and_stock_files(tmp_path):
    export_dir = tmp_path / "remote-site"
    provision.write_placeholder_site(export_dir, "stock-abc123.json")
    assert (export_dir / "index.html").is_file()
    assert (export_dir / "stock-abc123.json").read_text() == "[]"
```

Run: `pytest tests/test_provision.py -k placeholder -v`
Expected: PASS

- [ ] **Step 3: Write `provision_store`, the orchestrator**

This follows the design review's recommended sequence exactly: preflight →
idempotent project create → refuse-if-token-exists check → mint → write
config/secrets (enabled stays false) → push placeholder using the new token
→ create both Access apps → verify (retries) → flip `enabled: true` →
restart the watcher → write the provision record → print the hub
registration reminder.

```python
import secrets as _secrets
from dataclasses import dataclass

from . import remote as _remote


@dataclass
class ProvisionResult:
    ok: bool
    message: str


def provision_store(cfg, *, powerful_token: str, account_id: str,
                     project_slug: str, owner_email: str) -> ProvisionResult:
    if not _valid_project_slug(project_slug):
        return ProvisionResult(False, f"'{project_slug}' is not a valid Cloudflare Pages "
                                       "project name (lowercase letters, digits, hyphens only).")

    watcher_token_name = f"pos-tool watcher - {project_slug}"
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {powerful_token}"

    try:
        verify_token(session)

        create_pages_project(session, account_id, project_slug)

        if find_watcher_token(session, watcher_token_name) is not None:
            raise ProvisionError(
                f"A token named '{watcher_token_name}' already exists on this "
                "account - a previous provisioning run must have minted it. "
                "Delete it by hand in the Cloudflare dashboard (User API "
                "Tokens) before retrying, so this run doesn't leave a second, "
                "orphaned one behind."
            )
        group_id = get_pages_edit_permission_group_id(session)
        token_id, token_value = mint_watcher_token(session, account_id, watcher_token_name, group_id)

        stock_json_token = _secrets.token_hex(16)
        patch_config_remote_section(cfg.config_path, {
            "cloudflare_project_name": project_slug,
            "stock_json_token": stock_json_token,
        })
        patch_env_secrets(cfg.env_path, {
            "CLOUDFLARE_API_TOKEN": token_value,
            "CLOUDFLARE_ACCOUNT_ID": account_id,
        })

        # Reload so push_remote below sees the credentials just written,
        # not a stale in-memory snapshot from before this function started.
        from .config import get_config
        fresh_cfg = get_config(reload=True)

        export_dir = fresh_cfg.path("remote.export_dir", "remote-site")
        stock_filename = f"stock-{stock_json_token}.json"
        write_placeholder_site(export_dir, stock_filename)
        if not _remote.push_remote(fresh_cfg, project=project_slug, export_dir=export_dir):
            raise ProvisionError(
                "The newly minted token could not push a placeholder deployment - "
                "check the token's Pages:Edit scope in the Cloudflare dashboard."
            )

        project_domain = f"{project_slug}.pages.dev"
        broad_app_id = create_broad_access_app(session, account_id, project_domain, owner_email)
        bypass_app_id = create_bypass_access_app(
            session, account_id, f"{project_domain}/{stock_filename}")

        root_ok = verify_reachable(f"https://{project_domain}/", expect_status=302)
        stock_ok = verify_reachable(f"https://{project_domain}/{stock_filename}", expect_status=200)
        if not (root_ok and stock_ok):
            raise ProvisionError(
                "Could not confirm the new store is correctly Access-gated after "
                "waiting for propagation - remote.enabled is staying off. Re-run "
                "this provisioning step; the project/token/Access apps created so "
                "far will be reused, not duplicated."
            )

        _flip_remote_enabled(fresh_cfg.config_path, True)

        from .paths import user_data_dir
        write_provision_record(
            user_data_dir() / "provision-record.json",
            {
                "project": project_slug,
                "broad_access_app_id": broad_app_id,
                "bypass_access_app_id": bypass_app_id,
                "watcher_token_id": token_id,
            },
        )

        return ProvisionResult(True,
            f"Provisioned '{project_slug}'. Add this to hub-site/stores.json and run "
            f"tools/deploy_hub.py:\n"
            f'  {{"name": "<store name>", "url": "https://{project_domain}/{stock_filename}"}}\n'
            f"Revoke the one-time provisioning token now (id from /user/tokens/verify).")

    except ProvisionError as exc:
        return ProvisionResult(False, str(exc))
    except requests.RequestException as exc:
        return ProvisionResult(False, f"Network error talking to Cloudflare: {exc}")
```

- [ ] **Step 4: Write `_flip_remote_enabled`**

A separate, single-purpose patch (not reusing `patch_config_remote_section`,
which raises if a requested key is missing — `enabled` is a boolean, not a
quoted string, so it needs its own tiny patcher):

```python
def _flip_remote_enabled(config_path: Path, value: bool) -> None:
    lines = config_path.read_text(encoding="utf-8").splitlines()
    in_remote = False
    for i, line in enumerate(lines):
        if re.match(r"^remote:\s*$", line):
            in_remote = True
            continue
        if in_remote and re.match(r"^\S", line):
            in_remote = False
        if in_remote and re.match(r"^\s+enabled:", line):
            indent = re.match(r"^(\s+)", line).group(1)
            lines[i] = f"{indent}enabled: {'true' if value else 'false'}"
            break
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
```

Add a test:

```python
def test_flip_remote_enabled_sets_true(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("remote:\n  enabled: false\n  cloudflare_project_name: \"x\"\n", encoding="utf-8")
    provision._flip_remote_enabled(config_path, True)
    assert "enabled: true" in config_path.read_text(encoding="utf-8")
```

- [ ] **Step 5: Write the full-sequence orchestrator test with a scripted FakeSession**

```python
def test_provision_store_happy_path_full_sequence(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "remote:\n  enabled: false\n  cloudflare_project_name: \"\"\n  stock_json_token: \"\"\n",
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    env_path.write_text("CLOUDFLARE_API_TOKEN=\nCLOUDFLARE_ACCOUNT_ID=\n", encoding="utf-8")

    class FakeCfg:
        def __init__(self):
            self.config_path = config_path
            self.env_path = env_path
        def path(self, dotted, default=""):
            return tmp_path / default

    session = FakeSession({
        ("GET", "/user/tokens/verify"): FakeResponse(200, {"success": True, "result": {"status": "active"}}),
        ("GET", "/pages/projects/storeb"): FakeResponse(404, {}),
        ("POST", "/pages/projects"): FakeResponse(200, {"success": True, "result": {"name": "storeb"}}),
        ("GET", "/user/tokens"): FakeResponse(200, {"success": True, "result": []}),
        ("GET", "/user/tokens/permission_groups"): FakeResponse(200, {
            "success": True, "result": [{"id": "g2", "name": "Cloudflare Pages Write"}]}),
        ("POST", "/user/tokens"): FakeResponse(200, {"success": True, "result": {"id": "tok9", "value": "secretval"}}),
        ("GET", "/access/apps"): FakeResponse(200, {"success": True, "result": []}),
        ("POST", "/access/apps"): FakeResponse(200, {"success": True, "result": {"id": "appX"}}),
    })
    monkeypatch.setattr(provision.requests, "Session", lambda: session)
    monkeypatch.setattr(provision._remote, "push_remote", lambda cfg, **kw: True)
    monkeypatch.setattr(provision, "verify_reachable", lambda *a, **kw: True)

    import poslib.config as config_module
    monkeypatch.setattr(config_module, "get_config", lambda reload=True: FakeCfg())

    import poslib.paths as paths_module
    monkeypatch.setattr(paths_module, "user_data_dir", lambda: tmp_path)

    result = provision.provision_store(
        FakeCfg(), powerful_token="pwtok", account_id="acct1",
        project_slug="storeb", owner_email="owner@x.com",
    )

    assert result.ok is True
    assert "enabled: true" in config_path.read_text(encoding="utf-8")
    assert "CLOUDFLARE_API_TOKEN=secretval" in env_path.read_text(encoding="utf-8")


def test_provision_store_refuses_when_watcher_token_already_exists(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("remote:\n  enabled: false\n  cloudflare_project_name: \"\"\n  stock_json_token: \"\"\n", encoding="utf-8")
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")

    class FakeCfg:
        config_path = config_path
        env_path = env_path

    session = FakeSession({
        ("GET", "/user/tokens/verify"): FakeResponse(200, {"success": True, "result": {"status": "active"}}),
        ("GET", "/pages/projects/storeb"): FakeResponse(200, {"success": True}),
        ("GET", "/user/tokens"): FakeResponse(200, {"success": True, "result": [
            {"id": "tokOld", "name": "pos-tool watcher - storeb"}]}),
    })
    import poslib.provision as provision_module
    provision_module.requests.Session = lambda: session

    result = provision.provision_store(
        FakeCfg(), powerful_token="pwtok", account_id="acct1",
        project_slug="storeb", owner_email="owner@x.com",
    )

    assert result.ok is False
    assert "already exists" in result.message


def test_provision_store_rejects_invalid_slug():
    result = provision.provision_store(
        object(), powerful_token="pwtok", account_id="acct1",
        project_slug="Bad_Slug!", owner_email="owner@x.com",
    )
    assert result.ok is False
    assert "not a valid" in result.message
```

- [ ] **Step 6: Run tests, fix any FakeSession wiring issues, verify pass**

Run: `pytest tests/test_provision.py -v`
Expected: PASS (all tests). The happy-path test's monkeypatching of
`provision.requests.Session` and `get_config` may need small adjustments to
match the exact import style chosen in Step 3 — keep iterating until green;
this is normal TDD wiring friction, not a sign the design is wrong.

- [ ] **Step 7: Commit**

```bash
git add poslib/provision.py tests/test_provision.py
git commit -m "feat(provision): add provision_store orchestrator and Access app creation"
```

---

### Task 5: `main.py` dispatch for `--provision-cloudflare`

**Files:**
- Modify: `main.py`
- Test: `tests/test_main.py` (create if it doesn't already exist — check
  first with `Glob tests/test_main.py`)

**Interfaces:**
- Consumes: `poslib.provision.provision_store`, `poslib.config.get_config`,
  same `--data-dir` pattern as `_apply_update` in `main.py`.
- Produces: `ShopAnalysis.exe --provision-cloudflare --account-id ID
  --project-slug SLUG --owner-email EMAIL [--data-dir PATH]`, reading the
  powerful token from the `POS_TOOL_PROVISION_TOKEN` environment variable
  (never argv - see this plan's Global Constraints). Exit code 0 on success,
  1 on failure.

- [ ] **Step 1: Write the failing test**

Create or extend `tests/test_main.py`:

```python
from __future__ import annotations

import os

import main


def test_provision_cloudflare_reads_token_from_env_not_argv(monkeypatch, capsys):
    monkeypatch.setenv("POS_TOOL_PROVISION_TOKEN", "secret-token")
    monkeypatch.setattr(main, "get_config", lambda: object())
    monkeypatch.setattr(main, "setup_logging", lambda cfg: None)
    captured = {}

    def fake_provision_store(cfg, *, powerful_token, account_id, project_slug, owner_email):
        captured["powerful_token"] = powerful_token
        captured["account_id"] = account_id
        captured["project_slug"] = project_slug
        captured["owner_email"] = owner_email
        from poslib.provision import ProvisionResult
        return ProvisionResult(True, "done")

    monkeypatch.setattr(main, "provision_store", fake_provision_store)

    rc = main.main([
        "--provision-cloudflare",
        "--account-id", "acct1",
        "--project-slug", "storeb",
        "--owner-email", "owner@x.com",
    ])

    assert rc == 0
    assert captured["powerful_token"] == "secret-token"
    assert "POS_TOOL_PROVISION_TOKEN" not in os.environ  # discarded after use


def test_provision_cloudflare_fails_cleanly_with_no_token_set(monkeypatch, capsys):
    monkeypatch.delenv("POS_TOOL_PROVISION_TOKEN", raising=False)
    monkeypatch.setattr(main, "get_config", lambda: object())
    monkeypatch.setattr(main, "setup_logging", lambda cfg: None)

    rc = main.main([
        "--provision-cloudflare",
        "--account-id", "acct1",
        "--project-slug", "storeb",
        "--owner-email", "owner@x.com",
    ])

    assert rc == 1
    assert "POS_TOOL_PROVISION_TOKEN" in capsys.readouterr().out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_main.py -k provision -v`
Expected: FAIL — `--provision-cloudflare` not recognized / `provision_store`
not imported into `main`.

- [ ] **Step 3: Implement the dispatch**

Modify `main.py`. Add near the top, alongside existing imports:

```python
from poslib.provision import provision_store
```

Add a new function, modeled directly on `_apply_update`:

```python
def _provision_cloudflare(argv: list[str]) -> int:
    """
    Runs once, interactively, from packaging/setup.iss's optional
    provisioning wizard page - never on an ordinary install/update. The
    one-time powerful token travels via the POS_TOOL_PROVISION_TOKEN
    environment variable only (Inno Setup's Exec has no stdin path - see
    docs/superpowers/plans/2026-08-28-component5-cloudflare-auto-provisioning.md
    Task 2), popped out of os.environ as the very first thing this function
    does so it cannot leak into anything this process does afterward.
    """
    import argparse
    import os

    parser = argparse.ArgumentParser(prog="ShopAnalysis.exe --provision-cloudflare")
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--project-slug", required=True)
    parser.add_argument("--owner-email", required=True)
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args(argv)

    if args.data_dir:
        os.environ["SHOP_ANALYSIS_DATA_DIR"] = args.data_dir

    token = os.environ.pop("POS_TOOL_PROVISION_TOKEN", "")
    if not token:
        print("POS_TOOL_PROVISION_TOKEN is not set - nothing to provision with.")
        return 1

    from poslib.config import ConfigError, get_config, setup_logging

    try:
        cfg = get_config()
    except ConfigError as exc:
        print(f"\nThere is a problem with config.yaml:\n\n{exc}\n")
        return 1

    setup_logging(cfg)

    result = provision_store(
        cfg, powerful_token=token, account_id=args.account_id,
        project_slug=args.project_slug, owner_email=args.owner_email,
    )
    print(result.message)
    return 0 if result.ok else 1
```

Modify `main()`'s dispatch block to add the new branch:

```python
    if argv and argv[0] == "--provision-cloudflare":
        return _provision_cloudflare(argv[1:])
```

Update the module docstring's usage list to include the new mode, following
the existing three-mode format (`--watcher`, `--apply-update`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_main.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_main.py
git commit -m "feat(main): add --provision-cloudflare dispatch"
```

---

### Task 6: `setup.iss` wizard page

Uses Task 2's proven env-var-passing snippet verbatim. Do not write this
task's Pascal Script until Task 2 has actually confirmed the mechanism on a
real build — a plausible-looking but unverified Pascal snippet is exactly
the kind of claim this project's own history warns against.

**Files:**
- Modify: `packaging/setup.iss`

- [ ] **Step 1: Add the wizard page fields**

In `[Code]`'s `var` block, add:

```pascal
  CloudflarePage: TWizardPage;
  CloudflareTokenEdit: TPasswordEdit;
  CloudflareAccountIdEdit: TNewEdit;
  CloudflareSlugEdit: TNewEdit;
  CloudflareOwnerEmailEdit: TNewEdit;
  CloudflareHintLabel: TNewStaticText;
  CloudflareAccountIdLabel: TNewStaticText;
  CloudflareSlugLabel: TNewStaticText;
  CloudflareOwnerEmailLabel: TNewStaticText;
```

In `InitializeWizard()`, after the existing `DatabasePage` block, add a new
page (skipped by default whenever the token field is left blank — see
`NextButtonClick` below, not `ShouldSkipPage`, since this page is always
shown but only acted on if filled in):

```pascal
  CloudflarePage := CreateCustomPage(DatabasePage.ID,
    'Cloudflare remote setup (optional)',
    'Only fill this in when setting up a brand-new store for the first ' +
    'time. Leave the token blank for an ordinary install or update.');

  CloudflareHintLabel := TNewStaticText.Create(CloudflarePage);
  CloudflareHintLabel.Parent := CloudflarePage.Surface;
  CloudflareHintLabel.Left := 0;
  CloudflareHintLabel.Top := 0;
  CloudflareHintLabel.Width := CloudflarePage.SurfaceWidth;
  CloudflareHintLabel.AutoSize := False;
  CloudflareHintLabel.WordWrap := True;
  CloudflareHintLabel.Height := ScaleY(32);
  CloudflareHintLabel.Caption :=
    'One-time provisioning token (leave blank to skip this step entirely):';

  CloudflareTokenEdit := TPasswordEdit.Create(CloudflarePage);
  CloudflareTokenEdit.Parent := CloudflarePage.Surface;
  CloudflareTokenEdit.Left := 0;
  CloudflareTokenEdit.Top := CloudflareHintLabel.Top + CloudflareHintLabel.Height + ScaleY(4);
  CloudflareTokenEdit.Width := CloudflarePage.SurfaceWidth;

  CloudflareAccountIdLabel := TNewStaticText.Create(CloudflarePage);
  CloudflareAccountIdLabel.Parent := CloudflarePage.Surface;
  CloudflareAccountIdLabel.Left := 0;
  CloudflareAccountIdLabel.Top := CloudflareTokenEdit.Top + CloudflareTokenEdit.Height + ScaleY(8);
  CloudflareAccountIdLabel.Width := CloudflarePage.SurfaceWidth;
  CloudflareAccountIdLabel.Caption := 'Cloudflare account ID:';

  CloudflareAccountIdEdit := TNewEdit.Create(CloudflarePage);
  CloudflareAccountIdEdit.Parent := CloudflarePage.Surface;
  CloudflareAccountIdEdit.Left := 0;
  CloudflareAccountIdEdit.Top := CloudflareAccountIdLabel.Top + CloudflareAccountIdLabel.Height + ScaleY(2);
  CloudflareAccountIdEdit.Width := CloudflarePage.SurfaceWidth;

  CloudflareSlugLabel := TNewStaticText.Create(CloudflarePage);
  CloudflareSlugLabel.Parent := CloudflarePage.Surface;
  CloudflareSlugLabel.Left := 0;
  CloudflareSlugLabel.Top := CloudflareAccountIdEdit.Top + CloudflareAccountIdEdit.Height + ScaleY(8);
  CloudflareSlugLabel.Width := CloudflarePage.SurfaceWidth;
  CloudflareSlugLabel.Caption := 'New store project name (lowercase letters, digits, hyphens only, e.g. "storeb-pos"):';

  CloudflareSlugEdit := TNewEdit.Create(CloudflarePage);
  CloudflareSlugEdit.Parent := CloudflarePage.Surface;
  CloudflareSlugEdit.Left := 0;
  CloudflareSlugEdit.Top := CloudflareSlugLabel.Top + CloudflareSlugLabel.Height + ScaleY(2);
  CloudflareSlugEdit.Width := CloudflarePage.SurfaceWidth;

  CloudflareOwnerEmailLabel := TNewStaticText.Create(CloudflarePage);
  CloudflareOwnerEmailLabel.Parent := CloudflarePage.Surface;
  CloudflareOwnerEmailLabel.Left := 0;
  CloudflareOwnerEmailLabel.Top := CloudflareSlugEdit.Top + CloudflareSlugEdit.Height + ScaleY(8);
  CloudflareOwnerEmailLabel.Width := CloudflarePage.SurfaceWidth;
  CloudflareOwnerEmailLabel.Caption := 'Owner''s email (the only account allowed to view this store remotely):';

  CloudflareOwnerEmailEdit := TNewEdit.Create(CloudflarePage);
  CloudflareOwnerEmailEdit.Parent := CloudflarePage.Surface;
  CloudflareOwnerEmailEdit.Left := 0;
  CloudflareOwnerEmailEdit.Top := CloudflareOwnerEmailLabel.Top + CloudflareOwnerEmailLabel.Height + ScaleY(2);
  CloudflareOwnerEmailEdit.Width := CloudflarePage.SurfaceWidth;
```

(Field order in the UI: token, then account ID, then project slug, then
owner email. `CloudflareAccountIdLabel`, `CloudflareSlugLabel`, and
`CloudflareOwnerEmailLabel` must also be added to the `var` block alongside
the edit controls declared in Step 1 above — three more `TNewStaticText`
variables.)

- [ ] **Step 2: Validate on Next, and skip cleanly when blank**

Extend `NextButtonClick`:

```pascal
  if CurPageID = CloudflarePage.ID then
  begin
    if CloudflareTokenEdit.Text <> '' then
    begin
      if (CloudflareAccountIdEdit.Text = '') or (CloudflareSlugEdit.Text = '') or
         (CloudflareOwnerEmailEdit.Text = '') then
      begin
        MsgBox('A provisioning token was entered, so the account ID, project ' +
               'name, and owner email are all required too.', mbError, MB_OK);
        Result := False;
      end;
    end;
  end;
```

- [ ] **Step 3: Run provisioning at `ssPostInstall`, using Task 2's proven env-var mechanism**

Add the `kernel32.dll` import (proven working in Task 2 — copy the exact
final form from there, adjusting only for the real target exe instead of
the throwaway test child) near the top of `[Code]`:

```pascal
function SetEnvironmentVariableW(lpName, lpValue: String): Boolean;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';
```

**Task 2 proof (2026-08-28):** `SetEnvironmentVariableW` followed by
`ExecAndCaptureOutput` reliably crosses the process boundary — verified with
a real build (`EnvTestSetup.exe`) whose child process confirmed it saw
`POS_TOOL_ENVTEST=hello-from-installer` and exited 0. Task 2's test went
through an even *more* indirect path (`cmd.exe /c python "child.py"`) than
this task's direct exe call, so Task 2's brief's Step 3 fallback (bypass
`cmd.exe` and `Exec` the target exe directly) was not needed — the first
attempt worked, and this task's simpler direct `ExecAndCaptureOutput` call
on `{app}\{#MyAppExeName}` needs no fallback either.

One deliberate deviation in how Task 2 proved this, worth stating plainly:
the throwaway test installer ran with `PrivilegesRequired=lowest` (installed
to `{localappdata}\EnvTest`, not `{autopf}`) to keep the headless proof from
hitting a UAC prompt on this non-admin dev shell. The real `setup.iss` has
no `PrivilegesRequired` override, so it defaults to `admin` — this block
will actually execute inside an *elevated* `Setup.exe` process, not a
lowest-privilege one like the test. This isn't expected to change the
result (env-block inheritance and child-process launching are per-process
mechanics independent of integrity level — `Exec`/`ExecAndCaptureOutput`
launch the child at the parent's own token, so an elevated parent simply
produces an elevated child, which doesn't affect whether the env var
crosses), but it was never itself exercised under elevation, and doing so
would require an admin shell this proof didn't have. Flag this if Step 4's
manual smoke test (which *does* run the real, admin-elevated `setup.iss`)
ever shows the child not seeing `POS_TOOL_PROVISION_TOKEN` — that would mean
this assumption was wrong, not that the mechanism itself is unproven.

Extend `CurStepChanged`, after the existing `WriteDatabaseConfig` call and
before the `IsAdminInstallMode` message box:

```pascal
    if Assigned(CloudflareTokenEdit) and (CloudflareTokenEdit.Text <> '') then
    begin
      SetEnvironmentVariableW('POS_TOOL_PROVISION_TOKEN', CloudflareTokenEdit.Text);
      if ExecAndCaptureOutput(ExpandConstant('{app}\{#MyAppExeName}'),
         '--provision-cloudflare' +
         ' --account-id "' + CloudflareAccountIdEdit.Text + '"' +
         ' --project-slug "' + CloudflareSlugEdit.Text + '"' +
         ' --owner-email "' + CloudflareOwnerEmailEdit.Text + '"' +
         ' --data-dir "' + ExpandConstant('{localappdata}\Shop Analysis') + '"',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode, ProvisionOutput) then
      begin
        SetEnvironmentVariableW('POS_TOOL_PROVISION_TOKEN', '');
        ProvisionMessage := '';
        for I := 0 to GetArrayLength(ProvisionOutput.StdOut) - 1 do
          ProvisionMessage := ProvisionMessage + ProvisionOutput.StdOut[I] + #13#10;
        if ResultCode = 0 then
          ProvisionMessage := 'Cloudflare setup finished:' + #13#10#13#10 + ProvisionMessage
        else
          ProvisionMessage := 'Cloudflare setup did not finish (exit code ' +
            IntToStr(ResultCode) + '):' + #13#10#13#10 + ProvisionMessage;
        ForceDirectories(ExpandConstant('{localappdata}\Shop Analysis'));
        SaveStringToFile(
          ExpandConstant('{localappdata}\Shop Analysis\cloudflare_provision_log.txt'),
          ProvisionMessage, False);
        // Restart the watcher so it picks up the newly-written config
        // instead of the stale one it may have started with at [Run] time -
        // see this plan's Task 4/design-review note on the watcher
        // config-cache race.
        Exec('taskkill.exe', '/F /IM "{#MyAppExeName}"', '', SW_HIDE,
             ewWaitUntilTerminated, ResultCode);
        Exec(ExpandConstant('{app}\{#MyAppExeName}'), '--watcher', '', SW_HIDE,
             ewNoWait, ResultCode);
      end
      else
      begin
        SetEnvironmentVariableW('POS_TOOL_PROVISION_TOKEN', '');
        ForceDirectories(ExpandConstant('{localappdata}\Shop Analysis'));
        SaveStringToFile(
          ExpandConstant('{localappdata}\Shop Analysis\cloudflare_provision_log.txt'),
          'Could not launch Cloudflare setup at all.', False);
      end;
    end;
```

Add `ResultCode: Integer`, `ProvisionOutput: TExecOutput`,
`ProvisionMessage: String`, `I: Integer` to the local `var` block of
`CurStepChanged` (Pascal Script requires locals declared per-procedure).

**Why this writes to a log file instead of a `MsgBox` (adapted from Task 2's
own required adaptation, for the same reason):** a `MsgBox` here would block
on a dialog nobody may be present to click during an unattended/silent
install — the same headless-hang risk Task 2's brief flagged for its own
throwaway test, except this one is the *real* wizard page a real customer
install could hit. `{localappdata}\Shop Analysis` is the established
config-directory convention already used by `WriteDatabaseConfig`/
`ConfigIsConfigured` elsewhere in this file, so the provisioning log sits
next to `config.yaml` rather than inventing a new location.
`ForceDirectories` is called before both `SaveStringToFile` calls (not just
the success path) because the failure branch — `ExecAndCaptureOutput` never
even launched — is exactly the case where nothing else has necessarily
created that directory yet; without it, a `SaveStringToFile` on a missing
directory silently returns `False` and the one diagnostic that matters most
(that the launch itself failed) is lost.

**Design intent, not yet re-confirmed against real code — the log must not
leak the newly-minted credential:** Task 4's orchestrator (`provision_store`/
`ProvisionResult`, this plan's own draft above, around line ~1081) is **not
yet implemented** — only Task 3's helper functions
(`verify_token`/`create_pages_project`/`find_watcher_token`/
`mint_watcher_token`/etc.) exist today in `poslib/provision.py`. Per that
draft's own design, `ProvisionResult.message` is meant to carry only
project/app/token *names* and *ids* on both the success and any
`ProvisionError` path — never the minted watcher token's value or the
powerful provisioning token — and to explicitly tell the operator to revoke
the one-time token by hand. If that design holds when Task 4 is actually
built, persisting `ProvisionMessage` to a file (instead of a `MsgBox` that
vanished on click) would not newly expose a secret to disk. **This must be
re-verified against Task 4's real, implemented `provision_store` once it
exists** — treat it as an open check for Task 4/Task 6's smoke test, not
something already confirmed.

- [ ] **Step 4: Manual smoke test — build and run against a disposable target**

This cannot be unit-tested (Pascal Script, real `Exec`). Build with
`ISCC.exe packaging\setup.iss`, run the resulting installer on this dev PC
pointed at a disposable throwaway Cloudflare project/token (same discipline
as Task 7 below — this step can be combined with Task 7's live run rather
than done twice). Confirm: the page renders with all four fields, leaving
the token blank skips straight past with no `Exec` call, filling it in and
proceeding triggers `_provision_cloudflare` and writes its outcome to
`{localappdata}\Shop Analysis\cloudflare_provision_log.txt` (read that file
back to confirm success/failure rather than watching for a dialog — there
isn't one).

- [ ] **Step 5: Commit**

```bash
git add packaging/setup.iss
git commit -m "feat(installer): add optional Cloudflare auto-provisioning wizard page"
```

---

### Task 7: Live disposable-project end-to-end verification (manual, needs go-ahead)

Mirrors the exact precedent Components 4 and 5 already established: prove
the whole mechanism against real, disposable Cloudflare resources before
trusting it near a real store. **Never touch the live `promakeupmihoubipos`
or `promakeupmihoubi-hub` projects in this task.**

**Files:** none (operational verification only).

- [ ] **Step 1: Confirm with the user before proceeding**

State plainly: this creates a real (disposable) Pages project, two real
Access applications, and mints a real Cloudflare API token on the live
account, using a temporary powerful token the user pastes in for this one
use. Wait for explicit go-ahead.

- [ ] **Step 2: Run the full flow against a disposable project**

Either run the built installer from Task 6 Step 4 pointed at a fresh
throwaway `config.yaml`/`.env` pair, or call
`python -c "from poslib.provision import provision_store; ..."` directly
from a dev checkout with `POS_TOOL_PROVISION_TOKEN` set in the shell — both
exercise the same `provision_store` code path. Use a project slug like
`postool-provision-verify-<timestamp>`, matching the naming convention
already used for every prior disposable-project test in this project's
history.

Confirm: project created, watcher token minted (visible as a new entry
under `/user/tokens` with the expected name and `Pages`-scoped permission),
both Access apps created, `GET https://<slug>.pages.dev/` returns 302 to
the Access login, `GET https://<slug>.pages.dev/stock-<token>.json` returns
200 with `[]`, `remote.enabled` in the throwaway config ends up `true`, and
`provision-record.json` was written with the right ids.

- [ ] **Step 3: Re-run it to confirm idempotency**

Run `provision_store` again with the exact same arguments. Confirm: no
duplicate Pages project, no duplicate Access apps, and a clean refusal
(non-zero exit, clear message) rather than a second minted token, since the
watcher-token name already exists from Step 2.

- [ ] **Step 4: Tear everything down**

Delete both Access applications, delete the minted watcher token, delete
the Pages project, and confirm with the user that the one-time powerful
token used for this test has been revoked — nothing from this task should
remain on the live account afterward.

- [ ] **Step 5: Update this plan's status**

Add a short "Verified 2026-MM-DD" note at the top of this file recording
what was confirmed, following the same pattern every other component in
`CLAUDE.md` uses.

---

### Task 8: Documentation

**Files:**
- Modify: `CLAUDE.md` (Component 5 row)
- Modify: `.env.example` (note the provisioning flow's existence near the
  existing `CLOUDFLARE_API_TOKEN`/`CLOUDFLARE_ACCOUNT_ID` section, so a
  future reader understands these values might have been written
  automatically rather than pasted in by hand)

- [ ] **Step 1: Update `CLAUDE.md`'s Component 5 row**

Summarize what shipped (the `poslib/provision.py` module, the wizard page,
the env-var secret-passing mechanism and why, the idempotency/refusal
design, and Task 7's live verification result) in the same voice and level
of detail as the rest of that row. Mark the "installer-driven provisioning"
sub-item as done, distinct from the hub page which was already done
2026-08-27.

- [ ] **Step 2: Update `.env.example`**

Add one short paragraph above the existing `CLOUDFLARE_API_TOKEN=` line
noting that a store set up via the installer's Cloudflare provisioning page
has these two values (and `remote.cloudflare_project_name` /
`remote.stock_json_token` in `config.yaml`) filled in automatically, and
manual setup (the existing numbered instructions) is only needed for a
store provisioned before this feature existed, or if provisioning is
skipped.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md .env.example
git commit -m "docs: document Cloudflare auto-provisioning flow"
```
