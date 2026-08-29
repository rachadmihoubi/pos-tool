"""
main.py - the single entry point PyInstaller builds into ShopAnalysis.exe.

Both app.py (the dashboard) and watcher.py (the background refresh/digest/
backup loop) stay runnable on their own for the dev workflow (start.bat,
start-quiet.bat call them directly, unchanged). This file exists only so a
packaged build has one exe with four modes, dispatched by a flag, instead
of separate exes:

    ShopAnalysis.exe                 the dashboard (same as app.py)
    ShopAnalysis.exe --watcher ...   the watcher (same as watcher.py),
                                      any flags after --watcher are app.py's
                                      own (--once, --digest-now, --backup-now)
    ShopAnalysis.exe --apply-update  one-shot: run the silent auto-update
      [--data-dir PATH]              check/download/install. This is what
                                      the elevated "Shop Analysis - Updater"
                                      scheduled task runs (packaging/setup.iss)
                                      - see
                                      docs/superpowers/specs/2026-08-27-update-elevation-fix.md
                                      for why this is a separate, always-
                                      elevated task rather than something the
                                      (deliberately de-elevated) watcher does
                                      itself.
    ShopAnalysis.exe                 one-shot: set up Cloudflare Pages
      --provision-cloudflare         project, Access apps, and persistent
      --account-id ID                token for a new store. Runs from
      --project-slug SLUG            packaging/setup.iss's optional
      --owner-email EMAIL            provisioning wizard page only.
      [--data-dir PATH]
"""

from __future__ import annotations

import os
import sys

import app
import watcher
from poslib.provision import provision_store


def _apply_update(argv: list[str]) -> int:
    """
    Runs as SYSTEM via the elevated "Shop Analysis - Updater" task, not as
    the shop's own Windows user - so SYSTEM's own %LOCALAPPDATA% is not
    where config.yaml actually lives. --data-dir (baked into the scheduled
    task's command line at install time, from the installing user's own
    %LOCALAPPDATA%) is set as an override every path in poslib/paths.py
    resolves through, so config.yaml/cache.db/the log file all still point
    at the real shop data. Never raises - a bad config here just means no
    update happens, same as check_and_apply_update()'s own contract.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="ShopAnalysis.exe --apply-update")
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args(argv)

    if args.data_dir:
        os.environ["SHOP_ANALYSIS_DATA_DIR"] = args.data_dir

    from poslib.config import ConfigError, get_config, setup_logging
    from poslib.updater import check_and_apply_update

    try:
        cfg = get_config()
    except ConfigError as exc:
        print(f"\nThere is a problem with config.yaml:\n\n{exc}\n")
        return 1

    setup_logging(cfg)
    check_and_apply_update(cfg)
    return 0


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


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    if argv and argv[0] == "--watcher":
        sys.argv = ["watcher.py", *argv[1:]]
        return watcher.main()

    if argv and argv[0] == "--apply-update":
        return _apply_update(argv[1:])

    if argv and argv[0] == "--provision-cloudflare":
        return _provision_cloudflare(argv[1:])

    sys.argv = ["app.py", *argv]
    return app.main()


if __name__ == "__main__":
    raise SystemExit(main())
