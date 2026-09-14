"""
Regression test for a real bug found live on store #1, 2026-09-14: the
watcher's remote export crashed on every attempt with FileNotFoundError,
because export_static.py copies static/remote-detail.js into every export
(added by the product/customer JSON replatform), but packaging/pos-tool.spec's
explicit datas allowlist was never updated to bundle it - so every packaged
install has had a static/ folder missing that file since the replatform
shipped in v1.0.15. This slipped past the whole test suite because pytest
always runs in dev mode, where PROJECT_ROOT points at the real source tree
(poslib.paths.app_root()) - the missing-from-the-bundle failure mode only
exists in a real frozen build, which no existing test exercised.

This test can't run PyInstaller for real (slow, needs a full build), but it
can statically catch the exact class of gap that caused this: any file
export_static.py copies from PROJECT_ROOT/"static"/<name> must also appear
in packaging/pos-tool.spec's datas allowlist for "static".
"""
from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPORT_STATIC_PY = PROJECT_ROOT / "export_static.py"
POS_TOOL_SPEC = PROJECT_ROOT / "packaging" / "pos-tool.spec"

_COPY2_PATTERN = re.compile(
    r'shutil\.copy2\(PROJECT_ROOT\s*/\s*"static"\s*/\s*"([^"]+)"')
_DATAS_STATIC_PATTERN = re.compile(
    r'PROJECT_ROOT\s*/\s*"static"\s*/\s*"([^"]+)"\s*\)\s*,\s*"static"')


def _static_files_export_static_copies() -> set[str]:
    source = EXPORT_STATIC_PY.read_text(encoding="utf-8")
    return set(_COPY2_PATTERN.findall(source))


def _static_files_bundled_by_spec() -> set[str]:
    source = POS_TOOL_SPEC.read_text(encoding="utf-8")
    return set(_DATAS_STATIC_PATTERN.findall(source))


def test_every_static_file_export_static_copies_is_bundled_in_the_installer():
    copied = _static_files_export_static_copies()
    assert copied, "expected to find at least one static/ shutil.copy2 call " \
        "in export_static.py - the regex above may be stale"
    bundled = _static_files_bundled_by_spec()
    missing = copied - bundled
    assert not missing, (
        f"export_static.py copies {sorted(missing)} from static/ into every "
        "export, but packaging/pos-tool.spec's datas allowlist does not "
        "bundle them - a real packaged install's remote export will crash "
        "with FileNotFoundError the moment it tries to copy a missing file "
        "(see this test's module docstring for the real incident this "
        "guards against)."
    )
