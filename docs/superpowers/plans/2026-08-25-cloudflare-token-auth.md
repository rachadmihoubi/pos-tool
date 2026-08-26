# Cloudflare Scoped Token Auth Implementation Plan

> **STATUS: COMPLETE, then SUPERSEDED.** This plan's approach (authenticate
> `wrangler` with a scoped `CLOUDFLARE_API_TOKEN` instead of an OAuth login)
> shipped as commit `24b1755`. It was fully replaced on 2026-08-26 by a
> direct Cloudflare REST API implementation in `poslib/remote.py` (commit
> `5df4d73`) that drops the `wrangler`/Node.js dependency entirely — needed
> because frozen customer installs (Component 1/4, see
> `2026-08-25-packaging-installer.md` and CLAUDE.md's "Customer
> distribution" section) have no Node.js. The scoped-token *credential*
> this plan introduced is still what's used; only the CLI-shelling
> mechanism it authenticated is gone. Kept here as historical record.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop authenticating `wrangler` with the account-owner's broad `wrangler login` OAuth credential; authenticate it instead with a `CLOUDFLARE_API_TOKEN` restricted to the `Pages:Edit` permission group, read the same way every other secret in this codebase is read (`Config.secret()`, sourced from `.env`).

**Architecture:** `wrangler` already reads `CLOUDFLARE_API_TOKEN` from its process environment and prefers it over a stored OAuth login when present. `poslib/remote.py`'s `push_remote()` starts passing an explicit `env=` to `subprocess.run()`: a copy of the current process environment, with `CLOUDFLARE_API_TOKEN` set from `cfg.secret("CLOUDFLARE_API_TOKEN")` when that secret is non-empty. No token configured -> behavior is unchanged (falls back to whatever `wrangler login` session already exists), so this is a safe, backward-compatible rollout: the code ships first, the real token gets created and dropped into `.env` after, verified against a live push before the old OAuth login is ever revoked.

**Tech Stack:** Python stdlib only (`os`, `subprocess`) — no new dependency. Reuses the existing `Config.secret()` / `.env` convention (`poslib/config.py`).

## Global Constraints

- Never place the Cloudflare token in `config.yaml` — secrets only ever live in `.env` (gitignored), read via `Config.secret()`. This is an existing, non-negotiable project rule (`poslib/config.py`'s own docstring; every other credential in this codebase follows it).
- `push_remote(cfg: Config) -> bool` must keep its exact signature and never-raises contract — it's called from `watcher.py`'s `_run_remote_push()` inside a bare `try/except Exception`, but the existing convention (verified in the current implementation) is that `push_remote` itself absorbs every failure and returns `False`, not that the caller does the absorbing.
- No new pip dependency. `requests`-based REST reimplementation was considered and rejected (see `docs/superpowers/specs/2026-08-25-installer-updates-multistore-design.md`, Component 4) — the Cloudflare Pages direct-upload REST flow is undocumented, and two third-party sources describing it disagree on the file-hashing algorithm.
- The scoped Cloudflare API token itself is a manual, one-time step in the Cloudflare dashboard (Task 2 below) — not something to script or automate. Nothing in Task 1 requires the real token to exist; all of Task 1 is testable with a fake one.

---

### Task 1: `push_remote` authenticates via `CLOUDFLARE_API_TOKEN` when configured

**Files:**
- Modify: `poslib/remote.py:50-97` (`push_remote`)
- Modify: `tests/test_remote.py` (`FakeConfig`, new test class)
- Modify: `.env.example` (new `CLOUDFLARE_API_TOKEN` section)
- Modify: `config.yaml:298-303` (remote viewing comment block)
- Modify: `.claude/skills/cloudflare-remote-debug/SKILL.md` (auth method note)

**Interfaces:**
- Consumes: `Config.secret(name: str, default: str = "") -> str` (`poslib/config.py`, already exists, unchanged).
- Produces: `push_remote(cfg: Config) -> bool` — signature and never-raises contract unchanged; internal behavior now sets `env["CLOUDFLARE_API_TOKEN"]` on the `subprocess.run()` call when `cfg.secret("CLOUDFLARE_API_TOKEN")` is non-empty.

- [ ] **Step 1: Write the failing tests**

Add a `secret()` method to `FakeConfig` and a new test class to `tests/test_remote.py`:

```python
class FakeConfig:
    def __init__(self, project="", export_dir_exists=True, cloudflare_api_token=""):
        self._project = project
        self._export_dir_exists = export_dir_exists
        self._cloudflare_api_token = cloudflare_api_token

    def get(self, key, default=None):
        if key == "remote.cloudflare_project_name":
            return self._project
        return default

    def path(self, key, default=""):
        if key == "remote.export_dir":
            class FakeDir:
                def __init__(self, exists):
                    self._exists = exists

                def is_dir(self):
                    return self._exists

                def __str__(self):
                    return "remote-site"

            return FakeDir(self._export_dir_exists)
        return default

    def secret(self, name, default=""):
        if name == "CLOUDFLARE_API_TOKEN":
            return self._cloudflare_api_token
        return default
```

(This replaces the existing `FakeConfig` class, which currently has no `secret()` method — every existing test that constructs `FakeConfig(project=..., export_dir_exists=...)` keeps working unchanged since `cloudflare_api_token` defaults to `""`.)

```python
class TestPushRemoteCredential:

    def test_passes_cloudflare_api_token_to_subprocess_env_when_configured(self, monkeypatch):
        cfg = FakeConfig(project="my-shop", export_dir_exists=True,
                          cloudflare_api_token="fake-scoped-token")
        monkeypatch.setattr(remote, "_wrangler_path", lambda: "C:/fake/wrangler.CMD")

        seen_kwargs = {}

        def fake_run(command, **kwargs):
            seen_kwargs.update(kwargs)
            return subprocess.CompletedProcess(command, returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(remote.subprocess, "run", fake_run)
        assert remote.push_remote(cfg) is True
        assert seen_kwargs["env"]["CLOUDFLARE_API_TOKEN"] == "fake-scoped-token"

    def test_does_not_set_cloudflare_api_token_when_not_configured(self, monkeypatch):
        cfg = FakeConfig(project="my-shop", export_dir_exists=True, cloudflare_api_token="")
        monkeypatch.setattr(remote, "_wrangler_path", lambda: "C:/fake/wrangler.CMD")

        seen_kwargs = {}

        def fake_run(command, **kwargs):
            seen_kwargs.update(kwargs)
            return subprocess.CompletedProcess(command, returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(remote.subprocess, "run", fake_run)
        assert remote.push_remote(cfg) is True
        assert "CLOUDFLARE_API_TOKEN" not in seen_kwargs["env"]

    def test_still_passes_rest_of_the_process_environment(self, monkeypatch):
        cfg = FakeConfig(project="my-shop", export_dir_exists=True,
                          cloudflare_api_token="fake-token")
        monkeypatch.setattr(remote, "_wrangler_path", lambda: "C:/fake/wrangler.CMD")
        monkeypatch.setenv("PATH_MARKER_FOR_TEST", "still-here")

        seen_kwargs = {}

        def fake_run(command, **kwargs):
            seen_kwargs.update(kwargs)
            return subprocess.CompletedProcess(command, returncode=0, stdout="ok", stderr="")

        monkeypatch.setattr(remote.subprocess, "run", fake_run)
        assert remote.push_remote(cfg) is True
        assert seen_kwargs["env"].get("PATH_MARKER_FOR_TEST") == "still-here"
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `python -m pytest tests/test_remote.py -v`
Expected: the 3 new `TestPushRemoteCredential` tests FAIL with `KeyError: 'env'` (current `fake_run` calls never receive an `env` kwarg — `push_remote` doesn't pass one yet). All pre-existing tests in the file still PASS (the `FakeConfig` change is additive/backward-compatible).

- [ ] **Step 3: Implement — pass a scoped-token env to the subprocess call**

In `poslib/remote.py`, add `import os` to the imports (alongside the existing `logging`, `shutil`, `subprocess`, `sys`), and change `push_remote` from:

```python
    command = [wrangler, "pages", "deploy", str(export_dir),
              "--project-name", project, "--commit-dirty=true"]
    try:
        # wrangler prints UTF-8 (including emoji) regardless of the
        # Windows console's own codepage - decoding with that codepage
        # instead of UTF-8 crashes subprocess's internal stdout-reader
        # thread on some of its output. Force UTF-8, replace anything
        # that still doesn't decode rather than fail the whole push over
        # a logging detail.
        result = subprocess.run(
            command, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=_WRANGLER_TIMEOUT_SECONDS, check=False,
            creationflags=_NO_WINDOW)
```

to:

```python
    command = [wrangler, "pages", "deploy", str(export_dir),
              "--project-name", project, "--commit-dirty=true"]

    # wrangler reads CLOUDFLARE_API_TOKEN from the environment and prefers
    # it over a stored `wrangler login` OAuth session when present. Only
    # override it when a token is actually configured - leaving it unset
    # falls back to whatever OAuth login already exists, so this stays
    # backward compatible until the scoped token is created and dropped
    # into .env (see .claude/skills/cloudflare-remote-debug/SKILL.md).
    env = os.environ.copy()
    token = cfg.secret("CLOUDFLARE_API_TOKEN")
    if token:
        env["CLOUDFLARE_API_TOKEN"] = token

    try:
        # wrangler prints UTF-8 (including emoji) regardless of the
        # Windows console's own codepage - decoding with that codepage
        # instead of UTF-8 crashes subprocess's internal stdout-reader
        # thread on some of its output. Force UTF-8, replace anything
        # that still doesn't decode rather than fail the whole push over
        # a logging detail.
        result = subprocess.run(
            command, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=_WRANGLER_TIMEOUT_SECONDS, check=False,
            creationflags=_NO_WINDOW, env=env)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_remote.py -v`
Expected: all tests in the file PASS, including the 3 new ones.

- [ ] **Step 5: Update `.env.example` with the new secret**

Add a new section, following the file's existing numbered-steps convention (see the `TELEGRAM` section for the pattern), after the final `WHATSAPP` section:

```
# -----------------------------------------------------------------------------
#  CLOUDFLARE  (only needed if remote viewing is turned on in config.yaml)
# -----------------------------------------------------------------------------
#  This lets the tool push your dashboard to Cloudflare Pages so you can
#  check it from your phone. Create a token scoped to Pages only - not your
#  full Cloudflare account login - so a stolen copy of this file can only
#  ever touch your Pages dashboards, nothing else on the Cloudflare account.
#
#     1. Go to  https://dash.cloudflare.com/profile/api-tokens
#     2. Press "Create Token"
#     3. Press "Create Custom Token" (not one of the templates)
#     4. Under Permissions, choose:  Account  ->  Cloudflare Pages  ->  Edit
#     5. Under Account Resources, choose your account (there is no option
#        to limit this to a single Pages project - it will be able to
#        edit every Pages project on the account, just nothing else)
#     6. Press "Continue to summary", then "Create Token"
#     7. Copy the token shown (you only get to see it once) and paste it
#        below

CLOUDFLARE_API_TOKEN=
```

- [ ] **Step 6: Update `config.yaml`'s remote-viewing comment block**

In `config.yaml`, change:

```yaml
# -----------------------------------------------------------------------------
#  REMOTE VIEWING - a lean, read-only snapshot pushed to Cloudflare Pages so
#  you can check the shop from your phone. Off by default - turning it on
#  needs a one-time Cloudflare account and `wrangler login` (see SETUP.md).
#  Only summaries leave this computer; the real database never does.
# -----------------------------------------------------------------------------
```

to:

```yaml
# -----------------------------------------------------------------------------
#  REMOTE VIEWING - a lean, read-only snapshot pushed to Cloudflare Pages so
#  you can check the shop from your phone. Off by default - turning it on
#  needs a one-time Cloudflare account and a Pages-scoped API token (see
#  .env.example and .claude/skills/cloudflare-remote-debug/SKILL.md).
#  Only summaries leave this computer; the real database never does.
# -----------------------------------------------------------------------------
```

- [ ] **Step 7: Update the `cloudflare-remote-debug` skill doc**

In `.claude/skills/cloudflare-remote-debug/SKILL.md`, change:

```
- Authenticated via `wrangler login` (OAuth, browser-based — cannot be
  scripted; the account is `rachadm23@gmail.com`).
```

to:

```
- Authenticated via a `CLOUDFLARE_API_TOKEN` in `.env`, scoped to the
  `Pages:Edit` permission only (created at
  https://dash.cloudflare.com/profile/api-tokens — see `.env.example` for
  the exact steps). `poslib/remote.py:push_remote()` sets this on the
  `wrangler` subprocess's environment when the secret is configured; it
  falls back to a `wrangler login` OAuth session otherwise. Cloudflare does
  not support scoping a Pages token to a single project — this token can
  edit every Pages project on the account (`rachadm23@gmail.com`), not
  DNS/Workers/zones/billing.
```

- [ ] **Step 8: Run the full test suite**

Run: `python -m pytest tests -q`
Expected: same pass/fail state as before this change on every file except `test_remote.py` (specifically: the pre-existing, unrelated `TestConsistency::test_verification_table` failure on `dead_stock_value` — already present before this task, tracked separately, not something this task touches — may still appear; nothing in `test_remote.py` should fail).

- [ ] **Step 9: Commit**

```bash
git add poslib/remote.py tests/test_remote.py .env.example config.yaml .claude/skills/cloudflare-remote-debug/SKILL.md
git commit -m "$(cat <<'EOF'
feat(remote): authenticate wrangler via scoped CLOUDFLARE_API_TOKEN

Replaces the broad wrangler login OAuth session with a Pages:Edit-only
API token, read via the existing Config.secret()/.env convention. Falls
back to the existing OAuth login when no token is configured, so this is
safe to ship before the real token exists.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Create the real scoped token and verify a live push (manual, not scriptable)

**Files:** none (operational verification only — no code changes).

**Interfaces:**
- Consumes: `push_remote(cfg: Config) -> bool` and `.env`'s `CLOUDFLARE_API_TOKEN`, both produced by Task 1.
- Produces: nothing consumed by a later task — this is the end of Component 4.

This step needs the Cloudflare account login (`rachadm23@gmail.com`), which only the user has access to — it cannot be done by an agent.

- [ ] **Step 1: Create the token**

Follow the 7 steps written into `.env.example` in Task 1 (dash.cloudflare.com/profile/api-tokens -> Create Custom Token -> Account -> Cloudflare Pages -> Edit). Paste the resulting token into this dev PC's `.env` as `CLOUDFLARE_API_TOKEN=<token>`.

- [ ] **Step 2: Verify wrangler actually uses it (non-destructive — do not run `wrangler logout`)**

The live OAuth session is the working fallback for the remote push that already runs automatically every 90 seconds — do not remove it to test this; if the new token turns out to be wrong, that leaves the push broken until someone re-logs in.

Instead, prove precedence without touching the OAuth session: temporarily set `CLOUDFLARE_API_TOKEN` in `.env` to an obviously invalid value (e.g. `CLOUDFLARE_API_TOKEN=invalid-test-token`) and trigger a push (`python -c "from poslib.config import get_config; from poslib import remote; print(remote.push_remote(get_config()))"`). Expect it to **fail** (`False`, wrangler reports an auth error) — if it still succeeds, wrangler is silently ignoring the bad token and falling back to OAuth, meaning the env var isn't actually taking effect and needs debugging before going further.

Once the deliberately-invalid token demonstrably breaks the push, replace it in `.env` with the real token from Step 1 and re-run the same command — confirm it now returns `True`. This proves the token is both being read and being preferred over OAuth, without ever revoking the working OAuth session.

- [ ] **Step 3: Verify the token's actual scope**

Using the Cloudflare dashboard (Manage Account -> API Tokens), confirm the token's permission is exactly "Cloudflare Pages: Edit" and nothing else — no Workers, DNS, zones, or billing permission was accidentally added. This is the security property this whole component exists for; check it directly rather than trusting the token was created correctly from memory.

- [ ] **Step 4: Update `CLAUDE.md`**

Add a discovery/session note (following the existing numbered-discovery convention already used throughout the file) recording: the credential swap is live and verified on this dev PC, and the corrected fact that a Pages:Edit token is account-wide, not per-project — so future sessions don't have to re-derive this from the spec.
