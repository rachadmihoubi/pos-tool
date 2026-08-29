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
import secrets as _secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from . import remote as _remote
from .config import Config

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


_PAGES_EDIT_GROUP_NAMES = {"Pages Write", "Pages Edit"}


def get_pages_edit_permission_group_id(session: requests.Session) -> str:
    """
    Matches by exact name, not substring - a live 2026-08-29 run against the
    real account found a substring match on "pages" + ("edit" or "write")
    false-positives on several unrelated groups that also happen to contain
    those words: "Access: Custom Pages Write", "Account Custom Pages Write",
    "Custom Pages Write" (all Access custom-error-page permissions, zone- or
    account-scoped, nothing to do with the Cloudflare Pages product). Only
    "Pages Write" (confirmed live) or "Pages Edit" (the name Cloudflare's own
    docs use elsewhere) are accepted.
    """
    resp = session.get(f"{_API_BASE}/user/tokens/permission_groups",
                       timeout=_REQUEST_TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()
    matches = [
        g for g in data.get("result", [])
        if g.get("name", "") in _PAGES_EDIT_GROUP_NAMES
    ]
    if not matches:
        raise ProvisionError(
            "Could not find a Cloudflare permission group named 'Pages Write' "
            "or 'Pages Edit' - Cloudflare may have renamed it. This needs a "
            "human to check the Cloudflare dashboard's current naming."
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


# ---------------------------------------------------------------------------
# Access application creation.
#
# The payload shape below is not a guess - it reproduces the exact,
# live-verified shape recorded in
# docs/superpowers/specs/2026-08-29-store-access-app-shapes.md (Task 1's
# read-only investigation against the real Cloudflare account). That
# investigation also found a live gap on the hub's own broad Access app: its
# self_hosted_domains/destinations omit the `*.<domain>` wildcard, which
# leaves every Cloudflare Pages preview-deployment subdomain completely
# ungated (verified with a bare curl - 200 OK with no Access redirect at
# all). Both fields must carry the bare hostname AND the wildcard together,
# or the app is under-scoped - see the post-create verification and the
# idempotency check in create_broad_access_app below, both of which exist
# specifically to make sure that gap can never be reproduced by this
# installer.
# ---------------------------------------------------------------------------


def _find_access_app(session: requests.Session, account_id: str, domain: str) -> dict | None:
    resp = session.get(
        f"{_API_BASE}/accounts/{account_id}/access/apps",
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json()
    for app in data.get("result", []):
        if app.get("domain") == domain:
            return app
    return None


def access_app_exists(session: requests.Session, account_id: str, domain: str) -> bool:
    return _find_access_app(session, account_id, domain) is not None


def _covers_wildcard(app: dict, wildcard: str) -> bool:
    """
    True only if `wildcard` (e.g. `*.storeb.pages.dev`) appears in BOTH
    self_hosted_domains AND destinations[] - per
    docs/superpowers/specs/2026-08-29-store-access-app-shapes.md, Cloudflare
    treats these as two fields that must be kept in sync together (a PUT
    updating only one desyncs them - error 12130 - and this is exactly the
    shape of the confirmed live hub gap: self_hosted_domains alone looking
    fine while destinations[] silently lacks the wildcard, or vice versa).
    Checking only one field is not enough to trust an app as correctly
    scoped.
    """
    self_hosted = app.get("self_hosted_domains") or []
    destinations = app.get("destinations") or []
    dest_uris = {d.get("uri") for d in destinations if isinstance(d, dict)}
    return wildcard in self_hosted and wildcard in dest_uris


def create_broad_access_app(session: requests.Session, account_id: str, domain: str,
                             owner_email: str) -> str:
    """
    Owner-only-gated Access application covering the whole
    `<project>.pages.dev` domain, including its `*.<project>.pages.dev`
    preview-deployment subdomains. Idempotent: if an app already exists for
    this domain it is reused - UNLESS it exists but is missing the
    wildcard in either self_hosted_domains or destinations[], in which case
    this raises rather than silently accepting an under-scoped app (a
    previous partial run, or a hand-created app, could otherwise leave
    preview subdomains ungated exactly like the confirmed live hub gap -
    which manifested as exactly this kind of two-field desync).
    """
    wildcard = f"*.{domain}"
    existing = _find_access_app(session, account_id, domain)
    if existing is not None:
        if not _covers_wildcard(existing, wildcard):
            raise ProvisionError(
                f"An Access application for '{domain}' already exists but does "
                f"not cover '{wildcard}' in both self_hosted_domains and "
                "destinations[] - it is under-scoped (same class of "
                "gap as the hub's own broad app - see "
                "docs/superpowers/specs/2026-08-29-store-access-app-shapes.md). "
                "This needs a human to fix it in the Cloudflare Zero Trust "
                "dashboard (add the wildcard to both self_hosted_domains and "
                "destinations) before this can be re-run."
            )
        return existing["id"]

    resp = session.post(
        f"{_API_BASE}/accounts/{account_id}/access/apps",
        json={
            "type": "self_hosted",
            "name": f"Store - {domain}",
            "domain": domain,
            "self_hosted_domains": [domain, wildcard],
            "destinations": [
                {"type": "public", "uri": domain},
                {"type": "public", "uri": wildcard},
            ],
            "session_duration": "24h",
            "policies": [{
                "decision": "allow",
                "include": [{"email": {"email": owner_email}}],
                "name": "owner only",
                "reusable": True,
            }],
        },
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise ProvisionError(
            f"Could not create the owner-only Access application for '{domain}': "
            f"{data.get('errors')}"
        )
    result = data["result"]
    if not _covers_wildcard(result, wildcard):
        raise ProvisionError(
            f"Cloudflare accepted the Access application for '{domain}' but its "
            f"response does not confirm '{wildcard}' is covered in both "
            "self_hosted_domains and destinations[] - refusing to "
            "continue rather than risk shipping an ungated preview-subdomain "
            "gap. Check the app in the Zero Trust dashboard."
        )
    return result["id"]


def create_bypass_access_app(session: requests.Session, account_id: str,
                              path_domain: str) -> str:
    """
    Narrow, deliberately unauthenticated Access application scoped to exactly
    one file (`<project>.pages.dev/stock-<token>.json`) - never a wildcard.
    This is what makes the cross-store hub's stock search reachable without
    a login while the rest of the store's site stays owner-only. Idempotent:
    reused if an app already exists for this exact path.
    """
    existing = _find_access_app(session, account_id, path_domain)
    if existing is not None:
        return existing["id"]

    resp = session.post(
        f"{_API_BASE}/accounts/{account_id}/access/apps",
        json={
            "type": "self_hosted",
            "name": f"Store - {path_domain} (public bypass)",
            "domain": path_domain,
            "self_hosted_domains": [path_domain],
            "destinations": [{"type": "public", "uri": path_domain}],
            "session_duration": "24h",
            "policies": [{
                "decision": "bypass",
                "include": [{"everyone": {}}],
                "name": "public bypass",
                "reusable": False,
            }],
        },
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise ProvisionError(
            f"Could not create the public bypass Access application for "
            f"'{path_domain}': {data.get('errors')}"
        )
    return data["result"]["id"]


def write_placeholder_site(export_dir: Path, stock_json_filename: str) -> None:
    """
    A minimal placeholder page + empty stock JSON, written and pushed
    BEFORE either Access application exists - so the Pages project has
    something live (not a 404) the moment it's created, and so the first
    real push (once Access is wired up) isn't the very first content the
    project ever serves. The placeholder is always this literal content -
    never the real exporter - precisely because pushing happens before the
    bypass Access app protects/exposes the stock file as designed.
    """
    export_dir.mkdir(parents=True, exist_ok=True)
    (export_dir / "index.html").write_text(
        "<!doctype html>\n<html><head><meta charset=\"utf-8\">"
        "<title>Setting up</title></head><body>"
        "<p>This store is being set up. Check back in a few minutes.</p>"
        "</body></html>\n",
        encoding="utf-8",
    )
    (export_dir / stock_json_filename).write_text("[]", encoding="utf-8")


def _flip_remote_enabled(config_path: Path, value: bool) -> None:
    """
    Line-based patch of just the `remote:` block's `enabled:` key. Separate
    from patch_config_remote_section because `enabled` is a bare boolean,
    never a quoted string - and because this is deliberately the very last
    write of a successful provisioning run, done only once everything else
    (project, tokens, Access apps, first push) has already succeeded.
    """
    lines = config_path.read_text(encoding="utf-8").splitlines()
    in_remote = False
    flipped = False
    for i, line in enumerate(lines):
        if re.match(r"^remote:\s*$", line):
            in_remote = True
            continue
        if in_remote and re.match(r"^\S", line):  # dedent = new top-level section
            in_remote = False
        if in_remote:
            m = re.match(r"^(\s+)enabled:", line)
            if m:
                lines[i] = f"{m.group(1)}enabled: {'true' if value else 'false'}"
                flipped = True
    if not flipped:
        raise ProvisionError(
            "config.yaml's remote: section has no 'enabled' key to flip - the "
            "template may have changed. This needs a human to check."
        )
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@dataclass
class ProvisionResult:
    ok: bool
    message: str


def provision_store(cfg: Config, *, powerful_token: str, account_id: str,
                     project_slug: str, owner_email: str) -> ProvisionResult:
    """
    One-time, idempotent setup of a new store: Cloudflare Pages project,
    watcher token (Pages:Edit only), a placeholder first push, both Access
    applications (owner-only broad + unauthenticated stock.json bypass),
    reachability verification, then flips remote.enabled on. Every step
    before token minting checks-before-creating, so a failed run can simply
    be re-run - token minting is the deliberate exception (see
    find_watcher_token's use below and this module's own docstring).

    `cfg` is the store's existing Config (config.yaml/.env not yet
    patched). A fresh Config is constructed from the same paths after
    patching, so the values pushed to Cloudflare are read back from disk,
    not assumed from the pre-patch instance.
    """
    if not _valid_project_slug(project_slug):
        return ProvisionResult(
            False,
            f"'{project_slug}' is not a valid Cloudflare Pages project name - "
            "use lowercase letters, digits and hyphens only, starting and "
            "ending with a letter or digit.",
        )

    try:
        session = requests.Session()
        session.headers["Authorization"] = f"Bearer {powerful_token}"

        verify_token(session)
        create_pages_project(session, account_id, project_slug)

        watcher_token_name = f"pos-tool watcher - {project_slug}"
        if find_watcher_token(session, watcher_token_name) is not None:
            raise ProvisionError(
                f"A Cloudflare API token named '{watcher_token_name}' already "
                "exists - refusing to mint a second one, since a token's value "
                "can't be recovered after creation and this store may already "
                "be provisioned. Check .env and the Cloudflare dashboard; "
                "revoke the old token by hand first if this really is a "
                "fresh setup."
            )
        group_id = get_pages_edit_permission_group_id(session)
        watcher_token_id, watcher_token_value = mint_watcher_token(
            session, account_id, watcher_token_name, group_id
        )

        # Reuse an already-generated stock token if a previous partial run
        # got this far - a fresh token here would orphan the bypass Access
        # app's filename from the config that gets patched below.
        stock_json_token = str(cfg.get("remote.stock_json_token", "") or "").strip()
        if not stock_json_token:
            stock_json_token = _secrets.token_hex(16)
        stock_json_filename = f"stock-{stock_json_token}.json"

        patch_config_remote_section(cfg.config_path, {
            "cloudflare_project_name": project_slug,
            "stock_json_token": stock_json_token,
        })
        patch_env_secrets(cfg.env_path, {
            "CLOUDFLARE_API_TOKEN": watcher_token_value,
            "CLOUDFLARE_ACCOUNT_ID": account_id,
        })

        # Re-read from disk rather than trust the pre-patch instance - see
        # docstring above.
        fresh_cfg = Config(cfg.config_path, cfg.env_path)

        export_dir = cfg.path("remote.export_dir", "remote-site")
        write_placeholder_site(export_dir, stock_json_filename)

        pushed = _remote.push_remote(fresh_cfg, project=project_slug, export_dir=export_dir)
        if not pushed:
            raise ProvisionError(
                "The first push to Cloudflare Pages failed - check the logs "
                "for details."
            )

        domain = f"{project_slug}.pages.dev"
        broad_app_id = create_broad_access_app(session, account_id, domain, owner_email)
        bypass_app_id = create_bypass_access_app(
            session, account_id, f"{domain}/{stock_json_filename}"
        )

        broad_reachable = verify_reachable(f"https://{domain}/", expect_status=302)
        bypass_reachable = verify_reachable(
            f"https://{domain}/{stock_json_filename}", expect_status=200
        )
        if not broad_reachable or not bypass_reachable:
            raise ProvisionError(
                "Could not confirm the store is reachable after provisioning "
                "- Access changes can take a minute or two to propagate. "
                "Check the Cloudflare dashboard and try reloading the page "
                "before assuming this failed."
            )

        _flip_remote_enabled(cfg.config_path, True)

        record_path = cfg.config_path.parent / f"provision-record-{project_slug}.json"
        record = {
            "project": project_slug,
            "broad_access_app_id": broad_app_id,
            "bypass_access_app_id": bypass_app_id,
            "watcher_token_id": watcher_token_id,
        }
        try:
            write_provision_record(record_path, record)
        except OSError as exc:
            # remote.enabled is already flipped True and the store is
            # correctly gated at this point - this is a broken local-file
            # write, not a provisioning failure, so ok stays True. See
            # this fix round's Finding #2: provision_store must always
            # return a ProvisionResult, never raise, so callers like
            # main.py's CLI dispatch can rely on the return value alone.
            return ProvisionResult(
                True,
                f"Provisioned '{project_slug}' successfully, but could not "
                f"write the local provision record to {record_path}: {exc}. "
                "The store is live and gated; note the following ids by hand "
                f"for future reference: broad_access_app_id={broad_app_id}, "
                f"bypass_access_app_id={bypass_app_id}, "
                f"watcher_token_id={watcher_token_id}.",
            )

        return ProvisionResult(
            True,
            f"Store '{project_slug}' is set up and live at https://{domain}/. "
            "Remember to add it to the cross-store hub page by hand - this "
            "installer only provisions the individual store.",
        )
    except ProvisionError as exc:
        return ProvisionResult(False, str(exc))
    except requests.RequestException as exc:
        return ProvisionResult(False, f"Network error while talking to Cloudflare: {exc}")
