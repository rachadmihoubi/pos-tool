"""
tools/deploy_hub.py - one-time / occasional manual push of the multi-store
hub's static site to its own Cloudflare Pages project.

Not run by any watcher or scheduled task - the hub has no per-store data of
its own (see docs/superpowers/specs/2026-08-27-component5-hub-design.md),
so it is redeployed by hand, on rachad's own dev PC, only when hub-site/'s
design files (index.html/app.js/style.css) change. Reuses poslib/remote.py's
already-proven Cloudflare Pages upload flow - never reimplements it.

Adding a new store to the hub's store list no longer needs this script -
poslib/provision.py's register_store_with_hub does that automatically
during --provision-cloudflare, reading and rewriting the live
stores-<token>.json itself (see that module's "Cross-store hub
registration" section for why, and INSTALL_GUIDE.md's Step 6 for the
manual fallback if it fails or is skipped).

IMPORTANT: any push from this script overwrites index.html/app.js/style.css
on the live hub. If you change any of those three files, bump HUB_VERSION
in poslib/provision.py AND the matching "hub_version" value already sitting
in hub-site/stores-<token>.json BEFORE running this script - otherwise a
provisioning run using an older, not-yet-rebuilt installer will see the
live hub_version as newer than its own and correctly refuse to touch the
hub at all (see register_store_with_hub's docstring), silently blocking
automatic hub registration until a new installer is built.

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
