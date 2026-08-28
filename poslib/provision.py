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
