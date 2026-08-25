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
import secrets
import sqlite3
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from flask import (Flask, Response, abort, jsonify, make_response, redirect,
                   render_template, request, send_file, url_for)

from poslib import charts, ownerdata
from poslib.config import (REMOTE_TICKET_WINDOW_DAYS, ConfigError, get_config,
                           setup_logging)
from poslib.diagnostics import Diagnostics
from poslib.etl import ETL, ETLError
from poslib.i18n import available_languages, get_translator, normalise
from poslib.metrics import Metrics
from poslib.paths import app_root
from poslib.photos import get_item_photo

log = logging.getLogger(__name__)

app = Flask(__name__,
            template_folder=str(app_root() / "templates"),
            static_folder=str(app_root() / "static"))

# The chosen language is remembered in a cookie for this long.
LANGUAGE_COOKIE = "pos_lang"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365

# Rebuilding the cache from two browser tabs at once would be wasteful, so
# only one refresh runs at a time.
_refresh_lock = threading.Lock()

# The button can only force a rebuild this often. Without a cooldown, a
# stuck browser tab (or someone leaning on the button) could force a full
# copy-and-parse of the database over and over with no benefit.
_MIN_SECONDS_BETWEEN_MANUAL_REFRESHES = 10
_last_manual_refresh = 0.0

# /api/status is polled every few seconds by the Today page. It only reads
# cache metadata, so one ETL is reused instead of building a new one - and
# doing all the config/path lookups in its constructor - on every poll.
_status_etl: ETL | None = None


# ---------------------------------------------------------------------------
# Optional password
# ---------------------------------------------------------------------------
# Off by default, because the normal setup is this server bound to
# 127.0.0.1 - reachable only from this one computer, where Windows'
# own login already gates who gets to it. Setting 'interface.password' in
# config.yaml only matters if the dashboard is ever bound to something more
# than localhost (see the loud warning about that in main(), below).

@app.before_request
def _require_password() -> Response | None:
    if request.path.startswith("/static/"):
        return None
    password = str(get_config().get("interface.password", "") or "")
    if not password:
        return None
    auth = request.authorization
    if not auth or not secrets.compare_digest(auth.password or "", password):
        return Response(
            "A password is set in config.yaml for this dashboard.", 401,
            {"WWW-Authenticate": 'Basic realm="Shop Analysis"'})
    return None


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


def date_range_from_request() -> tuple[datetime.date | None, datetime.date | None]:
    """
    The date-range picker's `?start=`/`?end=` query params, parsed
    defensively - anything missing, malformed, or an unreasonable year is
    treated as "not given" rather than ever raising, so a bad URL just
    falls back to the page's usual default window instead of a 500.
    """
    def parse(name: str) -> datetime.date | None:
        raw = request.args.get(name)
        if not raw:
            return None
        try:
            d = datetime.date.fromisoformat(raw)
        except ValueError:
            return None
        # Keeps pandas' Timestamp range (roughly 1677-2262) comfortably.
        if not (2000 <= d.year <= 2200):
            return None
        return d
    return parse("start"), parse("end")


@app.context_processor
def inject_globals() -> dict[str, Any]:
    """Things every page needs: the translator, the menu, the language list."""
    cfg = get_config()
    lang = current_language()
    t = get_translator(lang)

    pages = [
        ("today", url_for("page_today")),
        ("tickets", url_for("page_tickets")),
        ("trend", url_for("page_trend")),
        ("customers", url_for("page_customers")),
        ("receivables", url_for("page_receivables")),
        ("inventory", url_for("page_inventory")),
        ("catalog", url_for("page_catalog")),
        ("products", url_for("page_products")),
        ("suppliers", url_for("page_suppliers")),
        ("cash", url_for("page_cash")),
        ("diagnostics", url_for("page_diagnostics")),
        ("dataquality", url_for("page_dataquality")),
    ]

    return {
        "t": t,
        "lang": lang,
        "timedelta": datetime.timedelta,
        "dir": t.direction,
        "is_rtl": t.is_rtl,
        "languages": available_languages(),
        "pages": pages,
        "current_page": request.endpoint or "",
        "cfg": cfg,
        "refresh_seconds": int(cfg.get("interface.auto_refresh_seconds", 30)),
        "now": datetime.datetime.now(),
        # Set only by export_static.py's rendering pass (a "?__static__=1"
        # marker on the request) - every other request (the real local
        # server) never sets this, so is_static_export is False by default
        # and every page behaves exactly as it always has.
        "is_static_export": request.args.get("__static__") == "1",
        "page_slug": request.path.strip("/") or "today",
        "remote_window_days": REMOTE_TICKET_WINDOW_DAYS,
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


def row_dict(d: dict[str, Any] | None) -> dict[str, Any] | None:
    """Clean a single dict of numpy/pandas values, the same way rows() does -
    used for the one-row summaries the drill-down pages build."""
    if d is None:
        return None
    return rows(pd.DataFrame([d]))[0]


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
    target = {"today": "page_today", "tickets": "page_tickets", "trend": "page_trend",
              "customers": "page_customers", "receivables": "page_receivables",
              "inventory": "page_inventory", "products": "page_products",
              "suppliers": "page_suppliers", "cash": "page_cash",
              "diagnostics": "page_diagnostics",
              "dataquality": "page_dataquality"}.get(default, "page_today")
    return redirect(url_for(target))


@app.route("/today")
def page_today() -> str:
    m, etl, conn = open_metrics()
    try:
        t = get_translator(current_language())
        start, end = date_range_from_request()

        if start is None and end is None:
            single_day = m.now.date()
        elif start is not None and (end is None or end == start):
            single_day = start
        elif start is None and end is not None:
            single_day = end
        else:
            # Both given and different (the only way to reach this branch,
            # since the branch above already caught start == end) - a real
            # multi-day range.
            single_day = None

        if single_day is not None:
            d = m.today(target_date=single_day)
            period = None
        else:
            d = None
            period = m.period_stats(start, end)

        cache = etl.cache_info()

        if d is not None:
            by_hour = d["by_hour"]
            all_hours = m.sales["hour"].dropna()
            lo = int(all_hours.quantile(0.01)) if not all_hours.empty else 8
            hi = int(all_hours.quantile(0.99)) if not all_hours.empty else 20
            hours = list(range(max(0, lo), min(23, hi) + 1))
            hour_map = dict(zip(by_hour["hour"].tolist(),
                                by_hour["revenue"].tolist())) if not by_hour.empty else {}
            hour_chart = charts.combo_chart(
                labels=[f"{h:02d}" for h in hours],
                bars=[float(hour_map.get(h, 0.0)) for h in hours],
                rtl=t.is_rtl, max_labels=24)
            weekday_name = t.weekday_name(single_day.weekday())
        else:
            hour_chart = None
            weekday_name = None

        return render_template(
            "today.html",
            d=d,
            period=period,
            single_day=single_day,
            weekday_name=weekday_name,
            top_items=rows(d["top_items"]) if d is not None else rows(period["top_items"]),
            top_customers=rows(d["top_customers"]) if d is not None else rows(period["top_customers"]),
            hour_chart=hour_chart,
            cache=cache,
        )
    finally:
        conn.close()


@app.route("/tickets")
def page_tickets() -> str:
    m, etl, conn = open_metrics()
    try:
        start, end = date_range_from_request()
        if start is None:
            start = m.now.date()
        if end is None:
            end = m.now.date()
        tl = m.ticket_list(start, end)
        return render_template(
            "tickets.html",
            tickets=rows(tl),
            start=start,
            end=end,
            cache=etl.cache_info(),
        )
    finally:
        conn.close()


@app.route("/tickets/<int:receipt_id>")
def page_ticket(receipt_id: int) -> str:
    m, etl, conn = open_metrics()
    try:
        detail = m.ticket_detail(receipt_id)
        if detail is None:
            abort(404)
        return render_template(
            "ticket_detail.html",
            header=row_dict(detail["header"]),
            lines=rows(detail["lines"]),
            cache=etl.cache_info(),
        )
    finally:
        conn.close()


@app.route("/trend")
def page_trend() -> str:
    m, etl, conn = open_metrics()
    try:
        t = get_translator(current_language())
        start, end = date_range_from_request()
        monthly = m.monthly(start=start, end=end)
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


@app.route("/customers/<int:customer_id>")
def page_customer(customer_id: int) -> str:
    m, etl, conn = open_metrics()
    try:
        profile = m.customer_profile(customer_id)
        if profile is None:
            abort(404)
        return render_template(
            "customer_detail.html",
            summary=row_dict(profile["summary"]),
            receivable=row_dict(profile["receivable"]),
            purchases=rows(profile["purchases"], limit=200),
            payments=rows(profile["payments"], limit=100),
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
        shrinkage = m.shrinkage_events()
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
            shrinkage=rows(shrinkage),
            cache=etl.cache_info(),
        )
    finally:
        conn.close()


@app.route("/catalog")
def page_catalog() -> str:
    m, etl, conn = open_metrics()
    try:
        cat = m.catalog()
        return render_template(
            "catalog.html",
            products=rows(cat),
            product_count=len(cat),
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
        outliers = m.family_margin_outliers()
        dq = m.data_quality()
        t = get_translator(current_language())
        fam_top = fam.head(14)

        arrivals_days = int(get_config().get("catalog.new_arrivals_days", 7))
        arrivals = m.new_arrivals(days=arrivals_days)
        arrivals_text = "\n".join(
            f"{r['item_name']} — {t.money(r['price'])}"
            f"{' · ' + r['family_name'] if r['family_name'] and r['family_name'] != '—' else ''}"
            for r in arrivals.to_dict("records")
        )

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
            outliers=rows(outliers),
            benchmark_pp=float(m.t("margin.family_benchmark_pp", 15)),
            fam_chart=charts.combo_chart(
                labels=[str(x) for x in fam_top["family_name"]] if not fam_top.empty else [],
                bars=series_to_list(fam_top["revenue"]) if not fam_top.empty else [],
                line=series_to_list(fam_top["margin_pct"] * 100) if not fam_top.empty else None,
                line_is_percent=True, rtl=t.is_rtl, max_labels=14),
            healthy=float(m.t("margin.healthy_gross_margin", 0.10)),
            missing_cost=dq["missing_cost"],
            arrivals=rows(arrivals),
            arrivals_days=arrivals_days,
            arrivals_text=arrivals_text,
            cache=etl.cache_info(),
        )
    finally:
        conn.close()


@app.route("/products/<int:item_id>")
def page_product(item_id: int) -> str:
    m, etl, conn = open_metrics()
    try:
        profile = m.product_profile(item_id)
        if profile is None:
            abort(404)
        competitor_prices = ownerdata.competitor_prices_for_item(get_config(), item_id)
        return render_template(
            "product_detail.html",
            summary=row_dict(profile["summary"]),
            family=row_dict(profile["family"]),
            sales_history=rows(profile["sales_history"], limit=200),
            competitor_prices=rows(competitor_prices),
            form_error=request.args.get("form_error"),
            cache=etl.cache_info(),
        )
    finally:
        conn.close()


@app.route("/products/<int:item_id>/competitor-price", methods=["POST"])
def product_competitor_price_add(item_id: int) -> Response:
    """
    Log what a competitor was charging - the owner's own manual entry, not
    anything read from the POS. Validated here before it ever reaches
    poslib/ownerdata.py; on any problem, redirect back with a locale key
    naming what was wrong rather than a raw error page or a 500.
    """
    lang = current_language()
    competitor_name = request.form.get("competitor_name", "").strip()
    price_raw = request.form.get("price", "")
    date_raw = request.form.get("observed_date", "")
    note = request.form.get("note", "")

    error_key: str | None = None
    price: float | None = None
    observed_date: datetime.date | None = None

    if not competitor_name:
        error_key = "missing_name"
    if error_key is None:
        try:
            price = float(price_raw)
            if price <= 0:
                error_key = "invalid_price"
        except (TypeError, ValueError):
            error_key = "invalid_price"
    if error_key is None:
        try:
            observed_date = datetime.date.fromisoformat(date_raw)
        except (TypeError, ValueError):
            error_key = "invalid_date"

    if error_key is None:
        try:
            ownerdata.add_competitor_price(
                get_config(), item_id, competitor_name, price, observed_date, note)
        except ownerdata.OwnerDataError:
            error_key = "invalid_price"

    target = url_for("page_product", item_id=item_id, lang=lang)
    if error_key:
        target += f"&form_error={error_key}"
    return redirect(target)


@app.route("/products/<int:item_id>/competitor-price/<int:price_id>/delete", methods=["POST"])
def product_competitor_price_delete(item_id: int, price_id: int) -> Response:
    ownerdata.delete_competitor_price(get_config(), price_id)
    return redirect(url_for("page_product", item_id=item_id, lang=current_language()))


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


@app.route("/suppliers/purchases")
def page_supplier_transactions() -> str:
    m, etl, conn = open_metrics()
    try:
        st = m.supplier_transactions()
        return render_template(
            "supplier_transactions.html",
            transactions=rows(st),
            cache=etl.cache_info(),
        )
    finally:
        conn.close()


@app.route("/suppliers/purchases/<purchase_id>")
def page_purchase(purchase_id: str) -> str:
    m, etl, conn = open_metrics()
    try:
        try:
            pid: Any = int(purchase_id)
        except ValueError:
            pid = purchase_id
        detail = m.purchase_detail(pid)
        if detail is None:
            abort(404)
        return render_template(
            "purchase_detail.html",
            header=row_dict(detail["header"]),
            lines=rows(detail["lines"]),
            cache=etl.cache_info(),
        )
    finally:
        conn.close()


@app.route("/cash")
def page_cash() -> str:
    m, etl, conn = open_metrics()
    try:
        t = get_translator(current_language())
        start, end = date_range_from_request()
        inc = m.income_statement(start=start, end=end)
        cash = m.cash_position(start=start, end=end)
        wc = m.working_capital()

        by_month = inc["by_month"]
        labels = [t.month_label(x) for x in by_month["month"]] if not by_month.empty else []
        empty = by_month.empty

        tender_labels = [
            ("cash", t.get("cash.tender_cash"), "var(--c1)"),
            ("cheque", t.get("cash.tender_cheque"), "var(--c2)"),
            ("transfer", t.get("cash.tender_transfer"), "var(--c3)"),
            ("credit", t.get("cash.tender_credit"), "var(--c4)"),
        ]
        tender_split = charts.stacked_bar(
            [{"label": label, "value": cash["totals"][key], "colour": colour}
             for key, label, colour in tender_labels if cash["totals"][key]],
            rtl=t.is_rtl)

        till = m.till_reconciliation()

        return render_template(
            "cash.html",
            inc=inc,
            by_month=rows(by_month.iloc[::-1]) if not empty else [],
            money_chart=charts.combo_chart(
                labels=labels,
                bars=series_to_list(by_month["revenue"]) if not empty else [],
                bars2=series_to_list(by_month["gross_profit"]) if not empty else [],
                line=series_to_list(by_month["margin_pct"] * 100) if not empty else None,
                line_is_percent=True, rtl=t.is_rtl),
            cash=cash,
            cash_by_month=rows(cash["by_month"].iloc[::-1]) if not cash["by_month"].empty else [],
            tender_split=tender_split,
            wc=wc,
            till=rows(till),
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
    global _status_etl
    if _status_etl is None:
        _status_etl = ETL(get_config())
    info = _status_etl.cache_info()
    return jsonify({
        "parsed_at": info.get("parsed_at", ""),
        "source_modified": info.get("source_modified", ""),
        "rows": sum(info.get("row_counts", {}).values()),
    })


@app.route("/api/refresh", methods=["POST"])
def api_refresh() -> Response:
    """Read the POS database again, now, because the button was pressed."""
    global _last_manual_refresh

    since = time.time() - _last_manual_refresh
    if since < _MIN_SECONDS_BETWEEN_MANUAL_REFRESHES:
        return jsonify({
            "ok": True,
            "busy": True,
            "cooldown_seconds": round(_MIN_SECONDS_BETWEEN_MANUAL_REFRESHES - since, 1),
        })

    if not _refresh_lock.acquire(blocking=False):
        return jsonify({"ok": True, "busy": True})
    try:
        _last_manual_refresh = time.time()
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


_PHOTO_MIMETYPES = {"jpg": "image/jpeg", "png": "image/png",
                    "bmp": "image/bmp", "gif": "image/gif"}


@app.route("/api/item-photo/<int:item_id>")
def api_item_photo(item_id: int) -> Response:
    """
    An item's photo, read on demand straight from the source database (the
    cached copy never keeps this column - see poslib/photos.py). 404 when
    there is none; the product page's <img onerror> hides it quietly.
    """
    found = get_item_photo(get_config(), item_id)
    if found is None:
        abort(404)
    data, ext = found
    response = make_response(data)
    response.headers["Content-Type"] = _PHOTO_MIMETYPES.get(ext, "application/octet-stream")
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


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

    # Fails loudly, not silently: the default (127.0.0.1) is only reachable
    # from this computer, so there is nothing to protect. Anything else
    # means the dashboard - and the shop's sales figures - are reachable
    # from other devices, with no password unless one is set.
    if host not in ("127.0.0.1", "localhost", "::1") and not cfg.get("interface.password"):
        log.warning(
            "The dashboard is bound to %s, not just this computer, but "
            "'interface.password' is not set in config.yaml. Anyone who can "
            "reach %s:%s on the network can open it. Set interface.password "
            "to protect it.", host, host, port)

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
