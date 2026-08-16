"""
export_static.py - renders the real local dashboard pages into a static
site for Cloudflare Pages.

Every page - Today, Tickets, Trend, Customers, Receivables, Inventory,
Stock catalog, Products, Suppliers, Cash, Diagnostics, Data Quality - is
rendered via Flask's own test client, hitting the exact same routes and
templates a real browser would, in each of the three languages, under a
SCRIPT_NAME prefix (/en, /fr, /ar). Every url_for()-generated link (nav
tabs, static assets) therefore already points at the right per-language
path, and Cloudflare Pages resolves e.g. "/en/today" to "/en/today.html"
automatically (its built-in clean-URL behaviour) - no link rewriting
needed.

`app.py`'s `inject_globals()` sets `is_static_export=True` only when the
request carries `?__static__=1` (added below) - every real local request
never sets it, so this export can share 100% of the routes/templates with
zero behavioural change to the live local server.

Customer and product drill-down pages (/customers/<id>, /products/<id>)
are NOT exported - there are 600+/1500+ of them and no natural recency
cutoff (a customer or product doesn't "age out"). Clicking one remotely
hits the custom 404 page below.

Ticket and purchase drill-downs (/tickets/<id>,
/suppliers/purchases/<id>) ARE exported, but only for the recent window
`DRILLDOWN_WINDOW_DAYS` covers - a ticket from years ago is not something
anyone checks from their phone, and without a cutoff the count only grows,
forever, with the live shop. Purchases are exported in full regardless of
date - there are only ~500-600 of them, an order of magnitude fewer than
tickets, so windowing them buys nothing.

Also exported: bounded date-preset variants of the Today screen
(`TODAY_PRESET_FILES` - yesterday/this-week/last-7-days), because
Cloudflare Pages serves a static file regardless of its query string, so
`/today?start=...` can't work remotely the way it does locally - each
preset needs its own pre-rendered file instead. `daily.json` is a compact
per-day revenue rollup (see `Metrics.daily_rollup()`) that lets the Today
page's remote custom-range picker sum an arbitrary range client-side,
since a truly arbitrary range can't be pre-rendered at all.
"""

from __future__ import annotations

import datetime
import json
import logging
import shutil
from pathlib import Path

from poslib.config import (PROJECT_ROOT, REMOTE_TICKET_WINDOW_DAYS, Config,
                           get_config, setup_logging)
from poslib.etl import ETL
from poslib.i18n import LANGUAGES
from poslib.metrics import Metrics
from poslib.present import status_payload

log = logging.getLogger(__name__)

# Matches app.py's routes exactly (minus the leading slash).
PAGES = ["today", "tickets", "trend", "customers", "receivables", "inventory",
         "catalog", "products", "suppliers", "cash", "diagnostics", "data-quality"]

# Pages whose route has its own subdirectory - exported the same way as
# PAGES, just written under a matching folder instead of the language root.
NESTED_PAGES = ["suppliers/purchases"]

# Bounded date-range variants of the Today screen (see module docstring
# and templates/today.html's preset picker, which links to exactly these
# filenames when is_static_export is set). "today" itself is not repeated
# here - it's just "today.html" from PAGES above.
TODAY_PRESET_FILES = ["today-yesterday", "today-week", "today-last7"]

# How many days back to export ticket drill-downs remotely (see module
# docstring), and to show on the exported Tickets list page. Purchases are
# exported in full, unwindowed - see above. Shared with app.py (see
# poslib/config.py) so the Tickets template can tell a visitor the same
# number.
DRILLDOWN_WINDOW_DAYS = REMOTE_TICKET_WINDOW_DAYS

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


def _today_preset_ranges(today: datetime.date) -> dict[str, tuple[datetime.date, datetime.date]]:
    """Date ranges for TODAY_PRESET_FILES, keyed by filename stem."""
    return {
        "today-yesterday": (today - datetime.timedelta(days=1),) * 2,
        "today-week": (today - datetime.timedelta(days=today.weekday()), today),
        "today-last7": (today - datetime.timedelta(days=6), today),
    }


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
    from flask import render_template

    from app import app, row_dict, rows
    app.config["TESTING"] = True
    client = app.test_client()

    default_lang = cfg.default_language

    etl = ETL(cfg)
    conn = etl.connect()
    try:
        # One Metrics instance, reused for every ticket/purchase drill-down
        # below. Each of PAGES/NESTED_PAGES/presets still goes through a
        # real client.get() (open_metrics() builds its own Metrics per
        # request, matching exactly what a live visitor gets - fine at
        # ~15 pages x 3 languages). The drill-down loops do NOT use
        # client.get(): measured, that took ~19 minutes for ~1,700
        # tickets+purchases x 3 languages, because each request rebuilt
        # Metrics.lines/.tickets/.sales/.customers/.suppliers/.purchases
        # from scratch. Rendering directly against this one shared,
        # already-cached Metrics instance instead keeps the whole export
        # fast enough for a push every ~90 seconds.
        m = Metrics(conn, cfg)
        today = m.now.date()
        window_start = today - datetime.timedelta(days=DRILLDOWN_WINDOW_DAYS)
        ticket_ids = [int(x) for x in m.ticket_list(window_start, today)["receipt_id"]]
        purchases = m.supplier_transactions()
        purchase_ids = ([int(x) for x in purchases["purchase_id"]]
                        if not purchases.empty else [])
        daily = m.daily_rollup()
        cache_info = etl.cache_info()

        daily_records = []
        for _, row in daily.iterrows():
            daily_records.append({
                "date": row["date"].strftime("%Y-%m-%d"),
                "revenue": round(float(row["revenue"]), 2),
                "cash_revenue": round(float(row["cash_revenue"]), 2),
                "on_account_revenue": round(float(row["on_account_revenue"]), 2),
                "gross_profit": round(float(row["gross_profit"]), 2),
                "tickets": int(row["tickets"]),
            })
        (out_dir / "daily.json").write_text(
            json.dumps(daily_records, ensure_ascii=False), encoding="utf-8")

        presets = _today_preset_ranges(today)

        for lang in LANGUAGES:
            lang_dir = out_dir / lang
            lang_dir.mkdir(parents=True, exist_ok=True)

            # Every page links to /<lang>/static/style.css (via url_for() +
            # the SCRIPT_NAME prefix below) - Flask's local dev server
            # serves that dynamically from static/, but a static export
            # needs the actual file to physically exist at that path, per
            # language, since there is no server left to serve it from
            # once deployed.
            static_dir = lang_dir / "static"
            static_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(PROJECT_ROOT / "static" / "style.css", static_dir / "style.css")

            def render(path: str, out_file: Path) -> None:
                sep = "&" if "?" in path else "?"
                response = client.get(
                    f"{path}{sep}lang={lang}&__static__=1",
                    environ_overrides={"SCRIPT_NAME": f"/{lang}"})
                if response.status_code != 200:
                    raise RuntimeError(
                        f"Exporting {path} in {lang} returned {response.status_code}")
                out_file.parent.mkdir(parents=True, exist_ok=True)
                out_file.write_text(response.get_data(as_text=True), encoding="utf-8")

            for slug in PAGES:
                if slug == "tickets":
                    # The bare route defaults to today only - not useful as
                    # a standing remote snapshot. Export the same window as
                    # the ticket drill-downs below instead, so the list and
                    # the links on it agree with each other.
                    render(f"/tickets?start={window_start.isoformat()}&end={today.isoformat()}",
                           lang_dir / "tickets.html")
                else:
                    render(f"/{slug}", lang_dir / f"{slug}.html")

            for slug in NESTED_PAGES:
                render(f"/{slug}", lang_dir / f"{slug}.html")

            for filename, (start, end) in presets.items():
                render(f"/today?start={start.isoformat()}&end={end.isoformat()}",
                       lang_dir / f"{filename}.html")

            tickets_dir = lang_dir / "tickets"
            tickets_dir.mkdir(parents=True, exist_ok=True)
            for receipt_id in ticket_ids:
                detail = m.ticket_detail(receipt_id)
                if detail is None:
                    raise RuntimeError(f"ticket {receipt_id} vanished mid-export")
                with app.test_request_context(
                        f"/tickets/{receipt_id}?lang={lang}&__static__=1",
                        environ_overrides={"SCRIPT_NAME": f"/{lang}"}):
                    html = render_template(
                        "ticket_detail.html",
                        header=row_dict(detail["header"]),
                        lines=rows(detail["lines"]),
                        cache=cache_info,
                    )
                (tickets_dir / f"{receipt_id}.html").write_text(html, encoding="utf-8")

            purchases_dir = lang_dir / "suppliers" / "purchases"
            purchases_dir.mkdir(parents=True, exist_ok=True)
            for purchase_id in purchase_ids:
                detail = m.purchase_detail(purchase_id)
                if detail is None:
                    raise RuntimeError(f"purchase {purchase_id} vanished mid-export")
                with app.test_request_context(
                        f"/suppliers/purchases/{purchase_id}?lang={lang}&__static__=1",
                        environ_overrides={"SCRIPT_NAME": f"/{lang}"}):
                    html = render_template(
                        "purchase_detail.html",
                        header=row_dict(detail["header"]),
                        lines=rows(detail["lines"]),
                        cache=cache_info,
                    )
                (purchases_dir / f"{purchase_id}.html").write_text(html, encoding="utf-8")
    finally:
        conn.close()

    # Root + per-language redirects to the Today page, and a friendly 404
    # for anything not exported (customer/product drill-downs, and any
    # ticket/purchase older than the recent window above).
    redirects = [f"/  /{default_lang}/today  302"]
    for lang in LANGUAGES:
        redirects.append(f"/{lang}  /{lang}/today  302")
    (out_dir / "_redirects").write_text("\n".join(redirects) + "\n", encoding="utf-8")
    (out_dir / "404.html").write_text(_NOT_FOUND_HTML, encoding="utf-8")

    status = status_payload(cache_info, generated_at=datetime.datetime.now())
    (out_dir / "status.json").write_text(
        json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")

    total_per_lang = len(PAGES) + len(NESTED_PAGES) + len(presets) + len(ticket_ids) + len(purchase_ids)
    log.info("Static export written to %s (%d pages x %d languages, "
             "%d tickets + %d purchases within the last %d days)",
             out_dir, total_per_lang, len(LANGUAGES),
             len(ticket_ids), len(purchase_ids), DRILLDOWN_WINDOW_DAYS)
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
