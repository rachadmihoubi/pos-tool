"""
remote.py - pushes the static export to Cloudflare Pages.

One job: run `wrangler pages deploy`, report whether it worked. Never
raises - a failed push (no internet, Cloudflare down, not yet authenticated)
must never crash the watcher; the next cycle just tries again. The real
database and the live cache never leave this computer - only the lean
export in poslib/present.py's payload does.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys

from .config import Config

log = logging.getLogger(__name__)

_WRANGLER_TIMEOUT_SECONDS = 120

# watcher.py runs headless via pythonw.exe (no console of its own). Without
# this flag, spawning a console app (wrangler.CMD, via cmd.exe) from a
# windowless parent makes Windows allocate a brand-new visible console
# window for the child - it flashes on screen for however long wrangler
# takes, every push_interval_seconds, forever. Only meaningful on Windows.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _wrangler_path() -> str | None:
    """
    The full resolved path to wrangler, not just the bare name.

    On Windows, npm installs wrangler as a `wrangler.CMD` shim, which
    subprocess.run() cannot execute directly from a bare "wrangler" in the
    argument list (CreateProcess needs the real executable, not something
    only cmd.exe knows how to run) - verified empirically: `["wrangler",
    ...]` raises WinError 2 even though shutil.which() finds it just fine,
    while the fully resolved path works.
    """
    return shutil.which("wrangler")


def wrangler_available() -> bool:
    return _wrangler_path() is not None


def push_remote(cfg: Config) -> bool:
    """
    Deploy the export directory to Cloudflare Pages. Returns True on
    success, False on any problem at all - never raises.
    """
    project = str(cfg.get("remote.cloudflare_project_name", "")).strip()
    if not project:
        log.warning("remote.cloudflare_project_name is not set - skipping push. "
                    "See SETUP.md to create a Cloudflare Pages project.")
        return False

    export_dir = cfg.path("remote.export_dir", "remote-site")
    if not export_dir.is_dir():
        log.warning("Nothing to push yet - %s does not exist. Run the export first.",
                    export_dir)
        return False

    wrangler = _wrangler_path()
    if not wrangler:
        log.warning("wrangler is not installed or not on PATH - cannot push. "
                    "See SETUP.md.")
        return False

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
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("Could not run wrangler: %s", exc)
        return False

    if result.returncode == 0:
        log.info("Pushed to Cloudflare Pages (%s).", project)
        return True

    log.warning("wrangler exited %s: %s", result.returncode,
               (result.stderr or result.stdout or "").strip()[:500])
    return False
