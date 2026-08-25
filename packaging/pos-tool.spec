# packaging/pos-tool.spec
# -*- mode: python ; coding: utf-8 -*-
#
# Builds one onedir bundle containing ShopAnalysis.exe. main.py dispatches
# between dashboard mode (default) and watcher mode (--watcher) at runtime -
# see main.py's own docstring. console=False on purpose: no terminal window
# is ever shown, per the spec's "zero technical involvement" goal.
#
# Explicit datas allowlist only - never glob the project root. In
# particular: never bundle .env, cache.db, data/owner.db, logs/, digests/,
# backups/, remote-site/, or static/photo-cache/* - all of those either hold
# secrets or this dev machine's own shop data, and none of them belong on a
# customer's PC.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

PROJECT_ROOT = Path(SPECPATH).resolve().parent

datas = [
    (str(PROJECT_ROOT / "templates"), "templates"),
    (str(PROJECT_ROOT / "static" / "style.css"), "static"),
    (str(PROJECT_ROOT / "locales" / "en.json"), "locales"),
    (str(PROJECT_ROOT / "locales" / "fr.json"), "locales"),
    (str(PROJECT_ROOT / "locales" / "ar.json"), "locales"),
    (str(PROJECT_ROOT / "config.template.yaml"), "."),
    (str(PROJECT_ROOT / ".env.example"), "."),
]

# numpy/pandas C-extension gotcha (named in advance in this task's own
# brief): PyInstaller's default import scan does not reliably discover every
# numpy._core.* / pandas._libs.* C-extension submodule via static analysis
# alone. Without this, the frozen exe builds and launches cleanly (exit code
# 0, no error at build time) but crashes at runtime the moment app.py's
# `import numpy` / `import pandas` actually executes, with
# "No module named 'numpy._core._exceptions'" in a hidden windowed-app error
# dialog (console=False means this failure is otherwise invisible - it does
# not print anywhere, it only shows as a MessageBox). Confirmed empirically
# on this dev PC (2026-08-26): the first Step 5 run hit exactly this. Explicit
# collect_submodules() for both packages fixes it.
hiddenimports = collect_submodules("numpy") + collect_submodules("pandas")

a = Analysis(
    [str(PROJECT_ROOT / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ShopAnalysis",
    console=False,
    disable_windowed_traceback=False,
    # PyInstaller >=6.0 defaults onedir builds to a "_internal" subfolder
    # for everything except the exe itself. poslib/paths.py's app_root()
    # (Task 1) resolves read-only bundled files - templates/, static/,
    # locales/, config.template.yaml - relative to Path(sys.executable)'s
    # own folder, i.e. the pre-6.0 flat layout. contents_directory="."
    # restores that flat layout so app_root() finds them; confirmed
    # empirically on this dev PC (2026-08-26) - without this, everything
    # bundled via `datas` lands in dist/ShopAnalysis/_internal/ instead of
    # next to ShopAnalysis.exe and the frozen build can't find its own
    # templates or bootstrap config.yaml. (Note: this must be set on EXE,
    # not COLLECT - COLLECT only inherits it from the EXE instance passed
    # to it; setting it on COLLECT's own kwargs is silently ignored.)
    contents_directory=".",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ShopAnalysis",
)
