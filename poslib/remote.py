"""
remote.py - pushes the static export to Cloudflare Pages.

One job: upload poslib/present.py's static export to Cloudflare Pages and
report whether it worked. Never raises - a failed push (no internet,
Cloudflare down, missing credentials) must never crash the watcher; the
next cycle just tries again. The real database and the live cache never
leave this computer - only the lean export does.

Talks to Cloudflare's "Direct Upload" REST API directly (requests, already
a bundled dependency) instead of shelling out to `wrangler` - a frozen
customer install has no Node.js on the machine, so the previous
wrangler-CLI approach silently could not push at all there. This is not a
documented single-call API: it is reverse-engineered from Cloudflare's own
API reference (four separate endpoints, two different auth schemes) plus
wrangler's own source for the one thing Cloudflare's docs never explain -
the asset content-hash algorithm. Get that hash wrong and every stage
still reports success while every asset 404s forever, with no error to
find - see _cf_hash's docstring.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
from pathlib import Path

import requests
from blake3 import blake3

from .config import Config

log = logging.getLogger(__name__)

_API_BASE = "https://api.cloudflare.com/client/v4"
_REQUEST_TIMEOUT_SECONDS = 30
_UPLOAD_TIMEOUT_SECONDS = 120
# Cloudflare's own limits (wrangler's ceiling is 1000/bucket; batching
# tighter than that leaves headroom without needing to tune it further).
_MAX_FILES_PER_UPLOAD_BATCH = 500
_MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024

# Wrangler's own ignore list (packages/wrangler/src/pages/validate.ts),
# minus "_headers" and "_redirects" - Cloudflare Pages DOES require both in
# the uploaded asset set (neither is served as a page itself, but Cloudflare
# parses them from the deployment - "_headers" for response headers, see
# export_static.py's stock.json CORS header; "_redirects" for the root ->
# /{lang}/today rule export_static.py writes). Confirmed the hard way: with
# "_redirects" excluded here, every store's "/" 404'd from day one (only
# deep links like /en/today ever worked) - Cloudflare Pages Functions
# source, which really is out of scope for this Functions-less static
# deploy; export_static.py does not currently emit either, but skip them
# defensively rather than assume that never changes.
_IGNORED_FILE_NAMES = {"_worker.js", "_routes.json"}
_IGNORED_DIR_NAMES = {"functions", "node_modules", ".git", ".wrangler"}


def _cf_hash(data: bytes, rel_path: str) -> str:
    """
    Cloudflare Pages' asset content-hash key.

    Undocumented by Cloudflare; confirmed against wrangler's own source
    (packages/deploy-helpers/src/deploy/helpers/hash.ts):

        blake3(base64(file_bytes) + extension_without_dot).hex()[:32]

    Every detail matters: BLAKE3 specifically (not SHA-256/MD5), hashing
    the base64 *text* of the file rather than its raw bytes, the file
    extension with its leading dot stripped, and only the first 32 hex
    characters (128 of the 256 digest bits). The API accepts and stores an
    upload under whatever key it's given with no validation against the
    content, so a wrong hash here produces a deployment that reports full
    success and then serves 404 on every asset, permanently, with nothing
    in any response to point at why.
    """
    ext = Path(rel_path).suffix.lstrip(".")
    b64 = base64.b64encode(data)
    return blake3(b64 + ext.encode("ascii")).hexdigest()[:32]


def _iter_export_files(export_dir: Path):
    for candidate in sorted(export_dir.rglob("*")):
        if not candidate.is_file():
            continue
        rel_parts = candidate.relative_to(export_dir).parts
        if candidate.name in _IGNORED_FILE_NAMES:
            continue
        if any(part in _IGNORED_DIR_NAMES for part in rel_parts[:-1]):
            continue
        yield candidate


def _guess_content_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _get_upload_token(session: requests.Session, account_id: str, project: str) -> str | None:
    """
    Step 1 of 4. Uses the normal API-token auth already on the session and
    the full /accounts/{id}/pages/projects/{project}/... path.
    """
    url = f"{_API_BASE}/accounts/{account_id}/pages/projects/{project}/upload-token"
    resp = session.get(url, timeout=_REQUEST_TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        log.warning("Cloudflare rejected the upload-token request: %s", data.get("errors"))
        return None
    return data["result"]["jwt"]


def _upload_assets(session: requests.Session, jwt: str,
                    files: list[tuple[str, bytes, str]]) -> bool:
    """
    Step 2 of 4. Uses the short-lived JWT from step 1, not the API token -
    and, confirmed from Cloudflare's own docs, no /accounts/{id}/ prefix
    here: the JWT already carries account scope, and pasting one in
    produces a 404 that reads like the endpoint doesn't exist.
    """
    headers = {"Authorization": f"Bearer {jwt}"}
    for start in range(0, len(files), _MAX_FILES_PER_UPLOAD_BATCH):
        batch = files[start:start + _MAX_FILES_PER_UPLOAD_BATCH]
        payload = [
            {
                "key": key,
                "value": base64.b64encode(data).decode("ascii"),
                "metadata": {"contentType": ctype},
                "base64": True,
            }
            for key, data, ctype in batch
        ]
        resp = session.post(f"{_API_BASE}/pages/assets/upload", headers=headers,
                            json=payload, timeout=_UPLOAD_TIMEOUT_SECONDS)
        resp.raise_for_status()
        result = resp.json()
        if not result.get("success"):
            log.warning("Cloudflare rejected an asset upload batch: %s", result.get("errors"))
            return False
    return True


def _upsert_hashes(session: requests.Session, jwt: str, hashes: list[str]) -> bool:
    """Step 3 of 4. Same JWT auth and no-account-prefix URL shape as step 2."""
    headers = {"Authorization": f"Bearer {jwt}"}
    resp = session.post(f"{_API_BASE}/pages/assets/upsert-hashes", headers=headers,
                        json={"hashes": hashes}, timeout=_REQUEST_TIMEOUT_SECONDS)
    resp.raise_for_status()
    result = resp.json()
    if not result.get("success"):
        log.warning("Cloudflare rejected upsert-hashes: %s", result.get("errors"))
        return False
    return True


def _create_deployment(session: requests.Session, account_id: str, project: str,
                        manifest: dict[str, str]) -> str | None:
    """Step 4 of 4. Back to the normal API-token auth and full account path."""
    url = f"{_API_BASE}/accounts/{account_id}/pages/projects/{project}/deployments"
    # multipart/form-data with a JSON-string manifest field, per Cloudflare's
    # Create Deployment contract - not a JSON body.
    files = {"manifest": (None, json.dumps(manifest))}
    resp = session.post(url, files=files, timeout=_REQUEST_TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        log.warning("Cloudflare rejected the deployment: %s", data.get("errors"))
        return None
    return data["result"].get("url")


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

    api_token = cfg.secret("CLOUDFLARE_API_TOKEN")
    if not api_token:
        log.warning("CLOUDFLARE_API_TOKEN is not set - cannot push. See SETUP.md.")
        return False

    account_id = cfg.secret("CLOUDFLARE_ACCOUNT_ID")
    if not account_id:
        log.warning("CLOUDFLARE_ACCOUNT_ID is not set - cannot push. See SETUP.md.")
        return False

    try:
        files: list[tuple[str, bytes, str]] = []
        manifest: dict[str, str] = {}
        for path in _iter_export_files(export_dir):
            data = path.read_bytes()
            if len(data) > _MAX_FILE_SIZE_BYTES:
                log.warning("Skipping %s - exceeds Cloudflare Pages' 25 MB per-file limit.",
                           path)
                continue
            rel = "/" + path.relative_to(export_dir).as_posix()
            key = _cf_hash(data, rel)
            files.append((key, data, _guess_content_type(path)))
            manifest[rel] = key

        if not files:
            log.warning("Nothing to push - %s has no files to upload.", export_dir)
            return False

        session = requests.Session()
        session.headers["Authorization"] = f"Bearer {api_token}"

        jwt = _get_upload_token(session, account_id, project)
        if not jwt:
            return False

        if not _upload_assets(session, jwt, files):
            return False

        if not _upsert_hashes(session, jwt, [key for key, _data, _ctype in files]):
            return False

        url = _create_deployment(session, account_id, project, manifest)
        if url is None:
            return False

        log.info("Pushed to Cloudflare Pages (%s): %s", project, url)
        return True

    except (requests.RequestException, OSError, KeyError, ValueError) as exc:
        log.warning("Could not push to Cloudflare Pages: %s", exc)
        return False
