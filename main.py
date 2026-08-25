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
