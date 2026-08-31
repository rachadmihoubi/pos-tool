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
    (str(PROJECT_ROOT / "VERSION"), "."),
    # Bundled so poslib/provision.py's register_store_with_hub can push the
    # hub's static assets during --provision-cloudflare without needing this
    # git repo on the till PC. Excludes the live stores-<token>.json entirely
    # (register_store_with_hub always writes a freshly-fetched-and-merged
    # copy of that one file itself - see its own docstring for why) so a
    # stale bundled copy of *that* file specifically can never be pushed by
    # mistake. poslib/provision.py's HUB_VERSION must be bumped by hand
    # whenever index.html/app.js/style.css change here - see that module's
    # "Cross-store hub registration" section.
    (str(PROJECT_ROOT / "hub-site" / "index.html"), "hub-site"),
    (str(PROJECT_ROOT / "hub-site" / "app.js"), "hub-site"),
    (str(PROJECT_ROOT / "hub-site" / "style.css"), "hub-site"),
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
#
# filter=... excludes each package's own test suite (numpy.tests.*,
# pandas.tests.*, and nested test dirs like pandas.core.tests.* /
# numpy.linalg.tests.*): a first version of this without the filter passed
# review-clean at build time but a follow-up review caught it bloating the
# bundle with ~2,250 pandas.tests/numpy.tests entries and a stray top-level
# pytest, which PyInstaller's own bundled hook-numpy.py deliberately
# excludes for the same reason (it excludes scipy/pytest/nose/setuptools/
# numpy.distutils). Only the non-test submodules are actually needed to fix
# the ImportError above.
_not_test = lambda name: ".tests" not in name
hiddenimports = (
    collect_submodules("numpy", filter=_not_test)
    + collect_submodules("pandas", filter=_not_test)
)

# The .tests filter above only strips each package's own unit-test tree
# (the plural "*.tests.*" packages). It does not touch numpy.testing /
# pandas.testing / pandas._testing - the singular "testing" assertion-
# helper modules, which are real public submodules collect_submodules()
# still picks up. Those modules have a handful of *function-local* (not
# module-level) `import pytest` statements deep inside rarely-used helpers
# (e.g. pandas._testing._io.round_trip_localpath,
# pandas._testing.__init__.external_error_raised) - PyInstaller's static
# scan follows those and pulls all of pytest into the bundle as a
# hiddenimport, even though our app never calls those specific helpers
# (checked: only tests/*.py does, nothing under poslib/, app.py, main.py,
# watcher.py or export_static.py). Confirmed via PYZ-00.toc: one top-level
# 'pytest' entry remained with only the .tests filter applied.
#
# The fix is to exclude `pytest` itself, NOT numpy.testing/pandas.testing/
# pandas._testing wholesale - a first attempt at the latter broke the
# build differently: pandas/__init__.py itself does `from pandas import
# testing` at its own top level (confirmed in warn-pos-tool.txt: "excluded
# module named pandas.testing - imported by pandas (top-level)"), so
# excluding pandas.testing crashed the frozen exe at launch with
# "cannot import name 'testing' from partially initialized module
# 'pandas' (most likely due to a circular import)" - a real regression,
# caught by re-running Step 5 after the first excludes attempt, not
# assumed safe. `nose` is included too, matching PyInstaller's own bundled
# hook-numpy.py (which excludes scipy/pytest/nose/setuptools/
# numpy.distutils for the same "not needed by the shipped app" reason);
# `pytest` is the only one of that list this project's dependency tree
# actually pulled in, so it is the only one that mattered here.
excludes = ["pytest", "nose"]

a = Analysis(
    [str(PROJECT_ROOT / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
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
