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


def test_api_item_photo_route_does_not_raise_name_error():
    """
    Regression test: /api/item-photo/<item_id> must not raise NameError for
    get_item_photo. This caught a deleted import in an earlier version.
    """
    from app import app
    app.config["TESTING"] = True
    client = app.test_client()
    # Hit the route with a nonexistent item_id - should 404 or similar, but
    # not raise NameError (which would be a 500 with name 'get_item_photo' is not defined)
    response = client.get("/api/item-photo/999999999")
    # We don't care if it's a 404, 200, or any other status - we just care that
    # it doesn't raise NameError (which would result in a 500 and mention get_item_photo)
    assert response.status_code != 500, f"Route raised an error: {response.get_data(as_text=True)}"
