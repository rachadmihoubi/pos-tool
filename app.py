"""
app.py - the dashboard you look at.

A small web server that runs on this computer only. Nothing is sent
anywhere. Open http://127.0.0.1:8777 in any browser.

Start it with start.bat, which opens the browser for you.

WHY A WEB PAGE AND NOT A WINDOWS PROGRAM
----------------------------------------
Because it works on the shop computer, on a laptop, and on a phone on the
same wifi, with no installing. And because it prints properly, which
matters for the call list.
"""

from __future__ import annotations

import datetime
import logging
import sqlite3
import threading
import webbrowser
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from flask import (Flask, Response, abort, jsonify, make_response, redirect,
                   render_template, request, send_file, url_for)

from poslib import charts
from poslib.config import ConfigError, get_config, setup_logging
from poslib.diagnostics import Diagnostics
from poslib.etl import ETL, ETLError
from poslib.i18n import available_languages, get_translator, normalise
from poslib.metrics import Metrics

log = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates", static_folder="static")

# The chosen language is remembered in a cookie for this long.
LANGUAGE_COOKIE = "pos_lang"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365

# Rebuilding the cache from two browser tabs at once would be wasteful, so
# only one refresh runs at a time.
_refresh_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Per-request setup
# ---------------------------------------------------------------------------

def current_language() -> str:
    """
    Which language to use, in order of preference:
      1. ?lang= in the address (what the switcher sends)
      2. the cookie from last time
      3. the default in config.yaml
    """
    if request.args.get("lang"):
        return normalise(request.args["lang"])
    if request.cookies.get(LANGUAGE_COOKIE):
        return normalise(request.cookies[LANGUAGE_COOKIE])
    return get_config().default_language


def open_metrics() -> tuple[Metrics, ETL, sqlite3.Connection]:
    """Open the cache and get a Metrics ready to answer questions."""
    cfg = get_config()
    etl = ETL(cfg)
    conn = etl.connect()
    return Metrics(conn, cfg), etl, conn


@app.context_processor
def inject_globals() -> dict[str, Any]:
    """Things every page needs: the translator, the menu, the language list."""
    cfg = get_config()
    lang = current_language()
    t = get_translator(lang)

    pages = [
        ("today", url_for("page_today")),
        ("trend", url_for("page_trend")),
        ("customers", url_for("page_customers")),
        ("receivables", url_for("page_receivables")),
        ("inventory", url_for("page_inventory")),
        ("products", url_for("page_products")),
        ("suppliers", url_for("page_suppliers")),
        ("diagnostics", url_for("page_diagnostics")),
        ("dataquality", url_for("page_dataquality")),
    ]

    return {
        "t": t,
        "lang": lang,
        "dir": t.direction,
        "is_rtl": t.is_rtl,
        "languages": available_languages(),
        "pages": pages,
        "current_page": request.endpoint or "",
        "cfg": cfg,
        "refresh_seconds": int(cfg.get("interface.auto_refresh_seconds", 30)),
        "now": datetime.datetime.now(),
    }


@app.after_request
def remember_language(response: Response) -> Response:
    """When the language is switched, remember it for next time."""
    chosen = request.args.get("lang")
    if chosen:
        response.set_cookie(LANGUAGE_COOKIE, normalise(chosen),
                            max_age=COOKIE_MAX_AGE, samesite="Lax")
    return response


# ---------------------------------------------------------------------------
# Turning dataframes into something a template can loop over
# ---------------------------------------------------------------------------

def rows(df: pd.DataFrame, limit: int | None = None) -> list[dict[str, Any]]:
    """
    Turn a dataframe into a plain list of dictionaries the template can use,
    with the values Python cannot hand to a template cleaned up.
    """
    if df is None or (hasattr(df, "empty") and df.empty):
        return []
    if limit:
        df = df.head(limit)

    out: list[dict[str, Any]] = []
    for record in df.to_dict("records"):
        clean: dict[str, Any] = {}
        for k, v in record.items():
            # Catches None, NaN and pandas' NaT in one go - a missing value
            # is the only kind that is not equal to itself.
            if v is None or (v != v):
                clean[k] = None
            elif isinstance(v, (np.integer,)):
                clean[k] = int(v)
            elif isinstance(v, (np.floating,)):
                clean[k] = None if np.isnan(v) else float(v)
            elif isinstance(v, np.bool_):
                clean[k] = bool(v)
            elif isinstance(v, pd.Timestamp):
                clean[k] = None if pd.isna(v) else v.to_pydatetime()
            else:
                clean[k] = v
        out.append(clean)
    return out


def series_to_list(s: Any) -> list[Any]:
    """A pandas column as a plain list of numbers for a chart."""
    if s is None:
        return []
    out = []
    for v in list(s):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            out.append(None)
        elif isinstance(v, (np.integer,)):
            out.append(int(v))
        elif isinstance(v, (np.floating,)):
            out.append(None if np.isnan(v) else round(float(v), 4))
        else:
            out.append(v)
    return out


# ---------------------------------------------------------------------------
# Error handling - never show a stack trace to a shopkeeper
# ---------------------------------------------------------------------------

@app.errorhandler(ETLError)
@app.errorhandler(ConfigError)
def handle_known_error(exc: Exception) -> tuple[str, int]:
    log.error("%s", exc)
    return render_template("error.html", message=str(exc)), 500


@app.errorhandler(500)
def handle_unknown_error(exc: Exception) -> tuple[str, int]:
    log.exception("Unexpected error")
    return render_template("error.html", message=str(exc)), 500


@app.errorhandler(404)
def handle_missing(exc: Exception) -> tuple[str, int]:
    return render_template("error.html", message="Page not found"), 404


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.route("/")
def home() -> Response:
    default = str(get_config().get("interface.default_page", "today"))
    target = {"today": "page_today", "trend": "page_trend",
              "customers": "page_customers", "receivables": "page_receivables",
              "inventory": "page_inventory", "products": "page_products",
              "suppliers": "page_suppliers", "diagnostics": "page_diagnostics",
              "dataquality": "page_dataquality"}.get(default, "page_today")
    return redirect(url_for(target))


@app.route("/today")
def page_today() -> str:
    m, etl, conn = open_metrics()
    try:
        d = m.today()
        t = get_translator(current_language())

        # Trading hours, taken from when sales have actually happened rather
        # than assumed, so the chart fits this shop.
        by_hour = d["by_hour"]
        all_hours = m.sales["hour"].dropna()
        lo = int(all_hours.quantile(0.01)) if not all_hours.empty else 8
        hi = int(all_hours.quantile(0.99)) if not all_hours.empty else 20
        hours = list(range(max(0, lo), min(23, hi) + 1))
        hour_map = dict(zip(by_hour["hour"].tolist(),
                            by_hour["revenue"].tolist())) if not by_hour.empty else {}

        return render_template(
            "today.html",
            d=d,
            weekday_name=t.weekday_name(datetime.datetime.now().weekday()),
            top_items=rows(d["top_items"]),
            top_customers=rows(d["top_customers"]),
            hour_chart=charts.combo_chart(
                labels=[f"{h:02d}" for h in hours],
                bars=[float(hour_map.get(h, 0.0)) for h in hours],
                rtl=t.is_rtl, max_labels=24),
            cache=etl.cache_info(),
        )
    finally:
        conn.close()


@app.route("/trend")
def page_trend() -> str:
    m, etl, conn = open_metrics()
    try:
        t = get_translator(current_language())
        monthly = m.monthly()
        complete = m.complete_months()

        labels = [t.month_label(x) for x in monthly["month"]] if not monthly.empty else []

        heat = m.weekday_hour_heatmap()
        heat_hours = sorted(heat["hour"].unique().tolist()) if not heat.empty else []
        heat_grid = []
        if not heat.empty:
            pivot = heat.pivot_table(index="weekday", columns="hour",
                                     values="revenue", aggfunc="sum").fillna(0.0)
            for wd in range(7):
                row = [float(pivot.loc[wd, h]) if (wd in pivot.index and h in pivot.columns)
                       else 0.0 for h in heat_hours]
                heat_grid.append({"weekday": wd, "name": t.weekday_name(wd), "values": row})

        season = m.seasonality()

        empty = monthly.empty
        return render_template(
            "trend.html",
            monthly=rows(monthly.iloc[::-1]),
            complete_count=len(complete),
            money_chart=charts.combo_chart(
                labels=labels,
                bars=series_to_list(monthly["revenue"]) if not empty else [],
                bars2=series_to_list(monthly["gross_profit"]) if not empty else [],
                line=series_to_list(monthly["margin_pct"] * 100) if not empty else None,
                line_is_percent=True, rtl=t.is_rtl),
            volume_chart=charts.combo_chart(
                labels=labels,
                bars=series_to_list(monthly["tickets"]) if not empty else [],
                line=series_to_list(monthly["avg_basket"]) if not empty else None,
                rtl=t.is_rtl),
            customer_chart=charts.combo_chart(
                labels=labels,
                bars=series_to_list(monthly["active_customers"]) if not empty else [],
                rtl=t.is_rtl),
            heat_chart=charts.heatmap(heat_grid, [f"{h:02d}" for h in heat_hours],
                                      rtl=t.is_rtl),
            season=rows(season),
            season_chart=charts.diverging_bars(
                labels=[t.month_name(int(x)) for x in season["month_no"]] if not season.empty else [],
                values=series_to_list(season["vs_average"] * 100) if not season.empty else [],
                rtl=t.is_rtl),
            cache=etl.cache_info(),
        )
    finally:
        conn.close()


@app.route("/customers")
def page_customers() -> str:
    m, etl, conn = open_metrics()
    try:
        t = get_translator(current_language())
        cs = m.customer_summary()
        seg = m.segment_summary()
        calls = m.call_list()
        pareto = m.customer_pareto()
        churn = m.churn_by_month()

        # The Pareto curve, thinned to at most 120 points so the chart stays
        # quick to draw without changing its shape.
        pareto_x, pareto_y = [], []
        if not pareto.empty:
            step = max(1, len(pareto) // 120)
            sample = pareto.iloc[::step]
            pareto_x = series_to_list(sample["customer_share"] * 100)
            pareto_y = series_to_list(sample["cumulative_share"] * 100)

        # How many customers make up 80% of revenue - the headline of the page.
        n80 = int((pareto["cumulative_share"] <= 0.80).sum()) + 1 if not pareto.empty else 0

        dq = m.data_quality()

        return render_template(
            "customers.html",
            customers=rows(cs, limit=400),
            customer_count=len(cs),
            segments=rows(seg),
            calls=rows(calls),
            call_count=len(calls),
            call_revenue=float(calls["revenue"].sum()) if not calls.empty else 0.0,
            pareto_chart=charts.line_chart(
                pareto_x, pareto_y, rtl=t.is_rtl,
                x_label=t.get("customers.pareto_x"), reference=80.0),
            n80=n80,
            n80_share=(float(pareto.head(n80)["revenue"].sum()) /
                       float(pareto["revenue"].sum())) if not pareto.empty and n80 else 0.0,
            churn=rows(churn.iloc[::-1], limit=18),
            churn_chart=charts.combo_chart(
                labels=[t.month_label(x) for x in churn["month"]] if not churn.empty else [],
                bars=series_to_list(churn["new"]) if not churn.empty else [],
                bars2=series_to_list(churn["lost"]) if not churn.empty else [],
                line=series_to_list(churn["active"]) if not churn.empty else None,
                rtl=t.is_rtl),
            walkin=dq["walkin"],
            cache=etl.cache_info(),
        )
    finally:
        conn.close()


@app.route("/receivables")
def page_receivables() -> str:
    m, etl, conn = open_metrics()
    try:
        r = m.receivables()
        s = m.receivables_summary()
        warn = float(m.t("customers.receivable_concentration_warn", 0.25))

        t = get_translator(current_language())
        top = r.head(12) if not r.empty else r
        return render_template(
            "receivables.html",
            receivables=rows(r),
            summary=s,
            concentrated=s.get("top_share", 0) >= warn,
            chart=charts.hbar_chart(
                [str(x) for x in top["customer_name"]] if not top.empty else [],
                series_to_list(top["balance"]) if not top.empty else [],
                rtl=t.is_rtl),
            risk_days=int(m.t("customers.collection_risk_days", 60)),
            cache=etl.cache_info(),
        )
    finally:
        conn.close()


@app.route("/inventory")
def page_inventory() -> str:
    m, etl, conn = open_metrics()
    try:
        inv = m.inventory_summary()
        dead = m.dead_stock()
        risk = m.stockout_risk()
        over = m.overstock()
        neg = m.negative_stock()
        abc = m.abc_summary()
        fam = m.stock_by_family().head(14)
        t = get_translator(current_language())

        split = charts.stacked_bar([
            {"label": t.get("inventory.healthy"), "value": inv["healthy_value"],
             "colour": "var(--c-healthy)"},
            {"label": t.get("inventory.slow"), "value": inv["slow_value"],
             "colour": "var(--c-slow)"},
            {"label": t.get("inventory.dead"), "value": inv["dead_value"],
             "colour": "var(--c-dead)"},
        ], rtl=t.is_rtl)

        return render_template(
            "inventory.html",
            inv=inv,
            split=split,
            dead=rows(dead, limit=200),
            dead_count=len(dead),
            risk=rows(risk, limit=200),
            risk_count=len(risk),
            risk_weekly=float(risk["weekly_revenue"].sum()) if not risk.empty else 0.0,
            over=rows(over, limit=200),
            over_count=len(over),
            over_value=float(over["excess_value"].sum()) if not over.empty else 0.0,
            neg=rows(neg),
            abc=rows(abc),
            families=rows(fam),
            fam_chart=charts.hbar_chart(
                [str(x) for x in fam["family_name"]] if not fam.empty else [],
                series_to_list(fam["stock_value"]) if not fam.empty else [],
                rtl=t.is_rtl),
            slow_days=int(m.t("inventory.slow_stock_days", 60)),
            cover_months=float(m.t("inventory.stockout_cover_months", 0.75)),
            over_months=float(m.t("inventory.overstock_cover_months", 12.0)),
            cache=etl.cache_info(),
        )
    finally:
        conn.close()


@app.route("/products")
def page_products() -> str:
    m, etl, conn = open_metrics()
    try:
        fam = m.family_margin()
        prod = m.product_margin()
        low = m.high_revenue_low_margin()
        below = m.selling_below_cost()
        drift = m.silent_margin_erosion()
        dq = m.data_quality()
        t = get_translator(current_language())
        fam_top = fam.head(14)

        return render_template(
            "products.html",
            families=rows(fam),
            products=rows(prod, limit=400),
            product_count=len(prod),
            low=rows(low),
            below=rows(below, limit=150),
            below_count=len(below),
            below_loss=float(below["loss"].sum()) if not below.empty else 0.0,
            drift=rows(drift),
            drift_loss=float(drift["annual_margin_lost"].sum()) if not drift.empty else 0.0,
            fam_chart=charts.combo_chart(
                labels=[str(x) for x in fam_top["family_name"]] if not fam_top.empty else [],
                bars=series_to_list(fam_top["revenue"]) if not fam_top.empty else [],
                line=series_to_list(fam_top["margin_pct"] * 100) if not fam_top.empty else None,
                line_is_percent=True, rtl=t.is_rtl, max_labels=14),
            healthy=float(m.t("margin.healthy_gross_margin", 0.10)),
            missing_cost=dq["missing_cost"],
            cache=etl.cache_info(),
        )
    finally:
        conn.close()


@app.route("/suppliers")
def page_suppliers() -> str:
    m, etl, conn = open_metrics()
    try:
        sup = m.supplier_summary()
        gaps = m.purchase_gaps()
        trend = m.supplier_cost_trend()
        coverage = m.purchase_coverage()

        t = get_translator(current_language())
        has_rev = not sup.empty and "item_revenue" in sup.columns
        top = sup.nlargest(12, "item_revenue") if has_rev else sup.head(0)
        top5_share = float(sup.nlargest(5, "revenue_share")["revenue_share"].sum()) \
            if not sup.empty and "revenue_share" in sup.columns else 0.0

        return render_template(
            "suppliers.html",
            suppliers=rows(sup),
            gaps=rows(gaps),
            trend=rows(trend.iloc[::-1]),
            coverage=coverage,
            top5_share=top5_share,
            chart=charts.hbar_chart(
                [str(x) for x in top["supplier_name"]] if has_rev else [],
                series_to_list(top["item_revenue"]) if has_rev else [],
                rtl=t.is_rtl),
            trend_chart=charts.combo_chart(
                labels=[x for x in trend["month_label"]] if not trend.empty else [],
                bars=series_to_list(trend["purchase_value"]) if not trend.empty else [],
                line=series_to_list(trend["avg_unit_cost"]) if not trend.empty else None,
                rtl=t.is_rtl),
            cache=etl.cache_info(),
        )
    finally:
        conn.close()


@app.route("/diagnostics")
def page_diagnostics() -> str:
    m, etl, conn = open_metrics()
    try:
        t = get_translator(current_language())
        diag = Diagnostics(m, get_config())
        return render_template("diagnostics.html",
                               diag=diag.render(t), cache=etl.cache_info())
    finally:
        conn.close()


@app.route("/data-quality")
def page_dataquality() -> str:
    m, etl, conn = open_metrics()
    try:
        t = get_translator(current_language())
        dq = m.data_quality()
        checks = m.verification()

        # Say plainly whether each verification figure is where it should be.
        for c in checks:
            val, exp = c["value"], c["expected"]
            if val is None or exp in (None, 0):
                c["status"] = "verification_drift"
                c["delta"] = None
                continue
            delta = val - exp
            c["delta"] = delta
            rel = abs(delta) / abs(exp)
            if c["grows"]:
                # Should be equal or a little higher, never lower.
                if delta < -0.001 * abs(exp):
                    c["status"] = "verification_bad"
                elif rel < 0.0001:
                    c["status"] = "verification_ok"
                elif rel < 0.05:
                    c["status"] = "verification_grown"
                else:
                    c["status"] = "verification_bad"
            else:
                c["status"] = "verification_drift" if rel < 0.15 else "verification_bad"

        return render_template("dataquality.html", dq=dq, checks=checks,
                               cache=etl.cache_info())
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

@app.route("/api/status")
def api_status() -> Response:
    """
    Asked for by the Today page every few seconds. Tells the page whether
    the data has changed, so the page can reload itself without the whole
    screen flashing.
    """
    cfg = get_config()
    etl = ETL(cfg)
    info = etl.cache_info()
    return jsonify({
        "parsed_at": info.get("parsed_at", ""),
        "source_modified": info.get("source_modified", ""),
        "rows": sum(info.get("row_counts", {}).values()),
    })


@app.route("/api/refresh", methods=["POST"])
def api_refresh() -> Response:
    """Read the POS database again, now, because the button was pressed."""
    if not _refresh_lock.acquire(blocking=False):
        return jsonify({"ok": True, "busy": True})
    try:
        etl = ETL(get_config())
        result = etl.refresh(force=True)
        return jsonify({
            "ok": True,
            "rebuilt": result.rebuilt,
            "seconds": round(result.duration_seconds, 1),
            "rows": result.total_rows,
            "warnings": result.warnings,
        })
    except ETLError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        _refresh_lock.release()


@app.route("/export")
def do_export() -> Response:
    """Build the Excel workbook and send it to the browser."""
    from export import build_workbook

    lang = current_language()
    try:
        path = build_workbook(language=lang)
    except Exception as exc:                            # noqa: BLE001
        log.exception("Excel export failed")
        return render_template("error.html", message=str(exc)), 500

    return send_file(path, as_attachment=True, download_name=Path(path).name)


@app.route("/healthz")
def healthz() -> Response:
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Starting up
# ---------------------------------------------------------------------------

def ensure_cache_ready() -> None:
    """Make sure there is something to show before the browser opens."""
    cfg = get_config()
    etl = ETL(cfg)
    stale, reason = etl.is_stale()
    if stale:
        log.info("Building the cache before starting: %s", reason)
        etl.refresh()


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run the shop dashboard.")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    try:
        cfg = get_config()
    except ConfigError as exc:
        print(f"\nThere is a problem with config.yaml:\n\n{exc}\n")
        return 1

    setup_logging(cfg)

    host = args.host or str(cfg.get("interface.host", "127.0.0.1"))
    port = args.port or int(cfg.get("interface.port", 8777))

    try:
        ensure_cache_ready()
    except ETLError as exc:
        # Still start up - the error page explains what is wrong, which is
        # more useful than a console window that closes itself.
        log.error("Could not read the database at startup: %s", exc)

    url = f"http://{host}:{port}/"
    print(f"\n  Shop analysis is running at {url}")
    print("  Leave this window open. Close it to stop.\n")

    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    app.run(host=host, port=port, debug=args.debug, use_reloader=False,
            threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
