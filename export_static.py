"""
export_static.py - renders the real local dashboard pages into a static
site for Cloudflare Pages.

Every page - Today, Trend, Customers, Receivables, Inventory, Products,
Suppliers, Cash, Diagnostics, Data Quality - is rendered via Flask's own
test client, hitting the exact same routes and templates a real browser
would, in each of the three languages, under a SCRIPT_NAME prefix
(/en, /fr, /ar). Every url_for()-generated link (nav tabs, static assets)
therefore already points at the right per-language path, and Cloudflare
Pages resolves e.g. "/en/today" to "/en/today.html" automatically (its
built-in clean-URL behaviour) - no link rewriting needed.

`app.py`'s `inject_globals()` sets `is_static_export=True` only when the
request carries `?__static__=1` (added below) - every real local request
never sets it, so this export can share 100% of the routes/templates with
zero behavioural change to the live local server.

What is deliberately NOT exported: the individual customer/product
drill-down pages (/customers/<id>, /products/<id>) - there are 600+/1500+
of them; regenerating that many pages every ~90 seconds would defeat the
point of a lean, frequent push. Those stay local-only for now, consistent
with the original spec's "deep drill-down can stay local-only, or sync
less often". Clicking one remotely hits the custom 404 page below.
"""

from __future__ import annotations

import datetime
import json
import logging
import shutil
from pathlib import Path

from poslib.config import PROJECT_ROOT, Config, get_config, setup_logging
from poslib.etl import ETL
from poslib.i18n import LANGUAGES
from poslib.present import status_payload

log = logging.getLogger(__name__)

# Matches app.py's routes exactly (minus the leading slash).
PAGES = ["today", "trend", "customers", "receivables", "inventory",
         "products", "suppliers", "cash", "diagnostics", "data-quality"]

_NOT_FOUND_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Not available remotely</title></head>
<body style="font-family: sans-serif; max-width: 640px; margin: 80px auto; text-align: center;">
<h1>Not available remotely</h1>
<p>This specific page (usually a single customer or product's detail view)
is not part of the remote snapshot - only the main dashboard pages are.
Open the tool on the store PC to see it.</p>
<p><a href="/">Back to the dashboard</a></p>
</body></html>
"""


def export(cfg: Config | None = None) -> Path:
    """
    Build and write the static export. Returns the export directory.
    Raises on failure - the caller (watcher.py) is responsible for
    catching and logging, same as every other periodic task there.
    """
    cfg = cfg or get_config()
    out_dir = cfg.path("remote.export_dir", "remote-site")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Imported here, not at module level, so importing this module (e.g.
    # from tests that only want PAGES or the 404 text) never pays the cost
    # of constructing the Flask app.
    from app import app
    app.config["TESTING"] = True
    client = app.test_client()

    default_lang = cfg.default_language

    for lang in LANGUAGES:
        lang_dir = out_dir / lang
        lang_dir.mkdir(parents=True, exist_ok=True)

        # Every page links to /<lang>/static/style.css (via url_for() +
        # the SCRIPT_NAME prefix below) - Flask's local dev server serves
        # that dynamically from static/, but a static export needs the
        # actual file to physically exist at that path, per language,
        # since there is no server left to serve it from once deployed.
        static_dir = lang_dir / "static"
        static_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / "static" / "style.css", static_dir / "style.css")

        for slug in PAGES:
            response = client.get(
                f"/{slug}?lang={lang}&__static__=1",
                environ_overrides={"SCRIPT_NAME": f"/{lang}"})
            if response.status_code != 200:
                raise RuntimeError(
                    f"Exporting /{slug} in {lang} returned {response.status_code}")
            (lang_dir / f"{slug}.html").write_text(
                response.get_data(as_text=True), encoding="utf-8")

    # Root + per-language redirects to the Today page, and a friendly 404
    # for anything not exported (mainly the customer/product drill-downs).
    redirects = [f"/  /{default_lang}/today  302"]
    for lang in LANGUAGES:
        redirects.append(f"/{lang}  /{lang}/today  302")
    (out_dir / "_redirects").write_text("\n".join(redirects) + "\n", encoding="utf-8")
    (out_dir / "404.html").write_text(_NOT_FOUND_HTML, encoding="utf-8")

    etl = ETL(cfg)
    conn = etl.connect()
    try:
        cache_info = etl.cache_info()
    finally:
        conn.close()
    status = status_payload(cache_info, generated_at=datetime.datetime.now())
    (out_dir / "status.json").write_text(
        json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")

    log.info("Static export written to %s (%d pages x %d languages)",
             out_dir, len(PAGES), len(LANGUAGES))
    return out_dir


def main() -> int:
    from poslib.config import ConfigError
    try:
        cfg = get_config()
    except ConfigError as exc:
        print(f"\nThere is a problem with config.yaml:\n\n{exc}\n")
        return 1

    setup_logging(cfg)
    export(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
