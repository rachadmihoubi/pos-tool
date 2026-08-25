"""
The one thing this plan changes about app.py: template_folder/static_folder
must resolve to an absolute path from poslib.paths.app_root(), not Flask's
own default (relative to app.py's __file__), so both still work in a
PyInstaller onedir build.
"""

from __future__ import annotations

from poslib import paths


def test_template_and_static_folders_are_under_app_root():
    import app as app_module
    root = paths.app_root()
    assert app_module.app.template_folder == str(root / "templates")
    assert app_module.app.static_folder == str(root / "static")
