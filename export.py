"""
export.py - builds the Excel report.

One command produces a workbook with every report in it, formatted, with
frozen headings, filters and sensible column widths, in whichever of the
three languages you ask for.

    python export.py            uses the language in config.yaml
    python export.py --lang ar  Arabic, and the sheets read right to left
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from poslib.config import Config, get_config, setup_logging
from poslib.diagnostics import Diagnostics
from poslib.etl import ETL
from poslib.i18n import Translator, get_translator, normalise
from poslib.metrics import Metrics

log = logging.getLogger(__name__)

# Excel refuses sheet names longer than this or containing these characters.
MAX_SHEET_NAME = 31
BAD_SHEET_CHARS = set(r"[]:*?/\\")


def _is_blank(value: Any) -> bool:
    """
    Should this cell be left empty?

    Excel has no way to store "not a number" or "infinity", and both turn up
    honestly here: a product with stock that has never sold has an infinite
    number of months of cover, and a product with no cost has an unknowable
    margin. Both belong in the sheet as an empty cell, not as a zero, which
    would be a different and wrong claim.
    """
    if value is None:
        return True
    if isinstance(value, (str, bytes)):
        return False
    try:
        if value != value:                      # not a number, or NaT
            return True
    except (TypeError, ValueError):
        return False
    if isinstance(value, (float, np.floating)) and value in (
            float("inf"), float("-inf")):
        return True
    return False


def _safe_sheet_name(name: str, used: set[str]) -> str:
    cleaned = "".join(c for c in name if c not in BAD_SHEET_CHARS)[:MAX_SHEET_NAME]
    cleaned = cleaned.strip() or "Sheet"
    base, n = cleaned, 2
    while cleaned.lower() in used:
        suffix = f" {n}"
        cleaned = base[:MAX_SHEET_NAME - len(suffix)] + suffix
        n += 1
    used.add(cleaned.lower())
    return cleaned


class Workbook:
    """A thin wrapper over XlsxWriter that keeps the formatting consistent."""

    def __init__(self, path: Path, t: Translator, cfg: Config):
        self.t = t
        self.cfg = cfg
        self.path = path
        self.writer = pd.ExcelWriter(path, engine="xlsxwriter",
                                     datetime_format="yyyy-mm-dd hh:mm",
                                     date_format="yyyy-mm-dd")
        self.book = self.writer.book
        self._used_names: set[str] = set()

        base = {"font_name": "Calibri", "font_size": 11}
        self.f_title = self.book.add_format({**base, "bold": True, "font_size": 16})
        self.f_sub = self.book.add_format({**base, "font_color": "#666666", "font_size": 10})
        self.f_head = self.book.add_format({
            **base, "bold": True, "font_color": "#ffffff", "bg_color": "#1f5fa8",
            "border": 1, "border_color": "#15467c", "text_wrap": True,
            "valign": "vcenter", "align": "right" if t.is_rtl else "left"})
        self.f_text = self.book.add_format({**base, "align": "right" if t.is_rtl else "left"})
        self.f_money = self.book.add_format({**base, "num_format": "#,##0"})
        self.f_money2 = self.book.add_format({**base, "num_format": "#,##0.00"})
        self.f_pct = self.book.add_format({**base, "num_format": "0.0%"})
        self.f_int = self.book.add_format({**base, "num_format": "#,##0"})
        self.f_date = self.book.add_format({**base, "num_format": "yyyy-mm-dd"})
        self.f_bold = self.book.add_format({**base, "bold": True})
        self.f_bad = self.book.add_format({**base, "font_color": "#b3261e", "bold": True})
        self.f_good = self.book.add_format({**base, "font_color": "#14795a", "bold": True})
        self.f_wrap = self.book.add_format({**base, "text_wrap": True, "valign": "top",
                                            "align": "right" if t.is_rtl else "left"})

    # -- writing sheets ----------------------------------------------------

    def sheet(self, title_key: str) -> Any:
        name = _safe_sheet_name(self.t.get(title_key), self._used_names)
        ws = self.book.add_worksheet(name)
        if self.t.is_rtl:
            ws.right_to_left()
        return ws

    def _format_for(self, kind: str):
        return {
            "money": self.f_money,
            "money2": self.f_money2,
            "pct": self.f_pct,
            "int": self.f_int,
            "date": self.f_date,
            "text": self.f_text,
        }.get(kind, self.f_text)

    def table(self, ws: Any, df: pd.DataFrame, columns: list[tuple[str, str, str]],
              start_row: int = 0, title: str = "", note: str = "") -> int:
        """
        Write a dataframe as a formatted table.

        `columns` is a list of (dataframe column, heading text, kind), where
        kind decides the number format.
        """
        row = start_row
        if title:
            ws.write(row, 0, title, self.f_title)
            row += 1
        if note:
            ws.write(row, 0, note, self.f_sub)
            row += 1
        if title or note:
            row += 1

        present = [(c, h, k) for c, h, k in columns if df is not None and c in df.columns]
        if df is None or df.empty or not present:
            ws.write(row, 0, self.t.get("app.no_data"), self.f_sub)
            return row + 2

        header_row = row
        for i, (_, heading, _) in enumerate(present):
            ws.write(header_row, i, heading, self.f_head)

        for r, (_, record) in enumerate(df.iterrows(), start=header_row + 1):
            for c, (col, _, kind) in enumerate(present):
                value = record[col]
                fmt = self._format_for(kind)

                if _is_blank(value):
                    ws.write_blank(r, c, None, fmt)
                    continue

                if isinstance(value, (pd.Timestamp, datetime.datetime, datetime.date)):
                    ws.write_datetime(r, c, pd.Timestamp(value).to_pydatetime(), self.f_date)
                elif isinstance(value, (np.integer, int)) and not isinstance(value, bool):
                    ws.write_number(r, c, int(value), fmt)
                elif isinstance(value, (np.floating, float)):
                    ws.write_number(r, c, float(value), fmt)
                elif isinstance(value, (bool, np.bool_)):
                    ws.write_string(r, c, self.t.get("common.yes" if value else "common.no"), fmt)
                else:
                    ws.write_string(r, c, str(value), fmt)

        last_row = header_row + len(df)

        # Column widths from the content, within sensible limits.
        for i, (col, heading, kind) in enumerate(present):
            if kind in ("money", "money2"):
                width = 15
            elif kind in ("pct", "int"):
                width = 12
            elif kind == "date":
                width = 12
            else:
                longest = df[col].astype(str).str.len().max() if len(df) else 10
                width = int(min(46, max(len(heading) + 2, (longest or 10) + 2)))
            ws.set_column(i, i, width)

        ws.freeze_panes(header_row + 1, 0)
        ws.autofilter(header_row, 0, last_row, len(present) - 1)
        return last_row + 3


# ---------------------------------------------------------------------------
# Building the report
# ---------------------------------------------------------------------------

def build_workbook(language: str | None = None,
                   output_dir: str | Path | None = None,
                   cfg: Config | None = None) -> Path:
    """Build the whole report and return where it was written."""
    cfg = cfg or get_config()
    lang = normalise(language or cfg.default_language)
    t = get_translator(lang)

    etl = ETL(cfg)
    conn = etl.connect()
    try:
        m = Metrics(conn, cfg)
        diag = Diagnostics(m, cfg)
        cache = etl.cache_info()

        out_dir = Path(output_dir) if output_dir else (cfg.digest_dir / "reports")
        out_dir.mkdir(parents=True, exist_ok=True)

        stamp = datetime.datetime.now().strftime("%Y-%m-%d")
        filename = t.get("excel.filename", date=stamp)
        # Arabic filenames are fine on Windows, but keep them free of
        # anything a mail client might mangle.
        filename = "".join(c for c in filename if c not in '\\/:*?"<>|')
        path = out_dir / f"{filename}.xlsx"

        wb = Workbook(path, t, cfg)
        _sheet_summary(wb, m, diag, cache)
        _sheet_diagnostics(wb, diag, t)
        _sheet_monthly(wb, m)
        _sheet_customers(wb, m)
        _sheet_calllist(wb, m)
        _sheet_receivables(wb, m)
        _sheet_inventory(wb, m)
        _sheet_deadstock(wb, m)
        _sheet_stockout(wb, m)
        _sheet_products(wb, m)
        _sheet_families(wb, m)
        _sheet_suppliers(wb, m)
        _sheet_dataquality(wb, m)

        wb.writer.close()
        log.info("Report written to %s", path)
        return path
    finally:
        conn.close()


def _sheet_summary(wb: Workbook, m: Metrics, diag: Diagnostics,
                   cache: dict[str, Any]) -> None:
    t = wb.t
    ws = wb.sheet("excel.sheet_summary")
    ws.set_column(0, 0, 44)
    ws.set_column(1, 1, 22)

    h = m.headline()
    h12 = m.headline(days=365)
    inv = m.inventory_summary()
    rec = m.receivables_summary()
    wc = m.working_capital()

    ws.write(0, 0, t.get("excel.summary_title"), wb.f_title)
    ws.write(1, 0, t.get("excel.generated", when=t.datetime(datetime.datetime.now())), wb.f_sub)
    ws.write(2, 0, t.get("excel.source", when=t.datetime(cache.get("source_modified"))), wb.f_sub)
    ws.write(3, 0, t.get("excel.warning_note"), wb.f_sub)

    rows: list[tuple[str, Any, str]] = [
        (t.get("common.all_time"), None, "head"),
        (t.get("common.revenue"), h["revenue"], "money"),
        (t.get("common.gross_profit"), h["gross_profit"], "money"),
        (t.get("common.margin_pct"), h["margin_pct"], "pct"),
        (t.get("common.tickets"), h["tickets"], "int"),
        (t.get("today.avg_basket"), h["avg_basket"], "money"),
        (t.get("today.collections"), h["collections"], "money"),
        ("", None, "blank"),
        (t.get("common.last_12_months"), None, "head"),
        (t.get("common.revenue"), h12["revenue"], "money"),
        (t.get("common.gross_profit"), h12["gross_profit"], "money"),
        (t.get("common.margin_pct"), h12["margin_pct"], "pct"),
        (t.get("common.tickets"), h12["tickets"], "int"),
        (t.get("customers.active_12m"), h12["active_customers"], "int"),
        ("", None, "blank"),
        (t.get("inventory.title"), None, "head"),
        (t.get("inventory.total_value"), inv["total_value"], "money"),
        (t.get("inventory.dead"), inv["dead_value"], "money"),
        (t.get("inventory.dead") + " %", inv["dead_share"], "pct"),
        (t.get("inventory.negative_title"), inv["negative_items"], "int"),
        ("", None, "blank"),
        (t.get("receivables.title"), None, "head"),
        (t.get("receivables.total_owed"), rec["total"], "money"),
        (t.get("receivables.customers_owing"), rec["customers"], "int"),
        (t.get("receivables.biggest_holder"), rec["top_share"], "pct"),
        (t.get("receivables.at_risk"), rec["at_risk_total"], "money"),
        ("", None, "blank"),
        (t.get("findings.working_capital_tied.title"), None, "head"),
        (t.get("inventory.total_value") + " + " + t.get("receivables.total_owed"),
         wc["tied_up"], "money"),
        (t.get("common.gross_profit") + " (12m)", wc["gross_profit_12m"], "money"),
        ("Ratio", wc["ratio"], "money2"),
    ]

    r = 5
    for label, value, kind in rows:
        if kind == "blank":
            r += 1
            continue
        if kind == "head":
            ws.write(r, 0, label, wb.f_bold)
            r += 1
            continue
        ws.write(r, 0, label, wb.f_text)
        if value is None:
            ws.write_blank(r, 1, None, wb.f_text)
        else:
            ws.write_number(r, 1, float(value), wb._format_for(kind))
        r += 1

    r += 1
    failing = diag.ranked("failing")
    ws.write(r, 0, t.get("diagnostics.failing_title"), wb.f_bold)
    ws.write_number(r, 1, sum(abs(f.money) for f in failing), wb.f_money)


def _sheet_diagnostics(wb: Workbook, diag: Diagnostics, t: Translator) -> None:
    ws = wb.sheet("excel.sheet_diagnostics")
    ws.set_column(0, 0, 13)
    ws.set_column(1, 1, 46)
    ws.set_column(2, 2, 16)
    ws.set_column(3, 3, 74)
    ws.set_column(4, 4, 62)
    ws.set_column(5, 5, 44)

    rendered = diag.render(t)
    r = 0
    for section_key, items in (("diagnostics.failing_title", rendered["failing"]),
                               ("diagnostics.working_title", rendered["working"])):
        ws.write(r, 0, t.get(section_key), wb.f_title)
        r += 2
        for head, col in ((t.get("diagnostics.severity_high"), 0),
                          (t.get("common.name"), 1),
                          (t.get("diagnostics.money_at_stake"), 2),
                          (t.get("diagnostics.the_number"), 3),
                          (t.get("diagnostics.what_to_do"), 4),
                          (t.get("diagnostics.if_you_fix_it"), 5)):
            ws.write(r, col, head, wb.f_head)
        r += 1
        for f in items:
            ws.write(r, 0, f["severity_label"], wb.f_text)
            ws.write(r, 1, f["title"], wb.f_wrap)
            ws.write_number(r, 2, float(f["money"]), wb.f_money)
            ws.write(r, 3, f["statement"], wb.f_wrap)
            ws.write(r, 4, f["action"], wb.f_wrap)
            ws.write(r, 5, f["fix"] or "", wb.f_wrap)
            ws.set_row(r, 46)
            r += 1
        r += 2


def _sheet_monthly(wb: Workbook, m: Metrics) -> None:
    t = wb.t
    ws = wb.sheet("excel.sheet_monthly")
    df = m.monthly().iloc[::-1].copy()
    wb.table(ws, df, [
        ("month_label", t.get("trend.col_month"), "text"),
        ("revenue", t.get("trend.col_revenue"), "money"),
        ("revenue_mom", t.get("trend.col_mom"), "pct"),
        ("revenue_yoy", t.get("trend.col_yoy"), "pct"),
        ("gross_profit", t.get("trend.col_profit"), "money"),
        ("margin_pct", t.get("trend.col_margin"), "pct"),
        ("tickets", t.get("trend.col_tickets"), "int"),
        ("avg_basket", t.get("trend.col_basket"), "money"),
        ("active_customers", t.get("trend.col_customers"), "int"),
        ("units", t.get("common.units"), "int"),
        ("collections", t.get("today.collections"), "money"),
    ], title=t.get("trend.table_title"), note=t.get("trend.incomplete_month"))


def _sheet_customers(wb: Workbook, m: Metrics) -> None:
    t = wb.t
    ws = wb.sheet("excel.sheet_customers")
    df = m.customer_summary().copy()
    if not df.empty:
        df["segment_label"] = df["segment"].map(lambda s: t.get(f"segments.{s}"))
    wb.table(ws, df, [
        ("customer_no", t.get("common.rank"), "text"),
        ("customer_name", t.get("customers.col_customer"), "text"),
        ("phone", t.get("common.phone"), "text"),
        ("city", t.get("common.city"), "text"),
        ("segment_label", t.get("customers.col_segment"), "text"),
        ("revenue", t.get("customers.col_revenue"), "money"),
        ("revenue_12m", t.get("customers.col_revenue_12m"), "money"),
        ("gross_profit", t.get("customers.col_profit"), "money"),
        ("margin_pct", t.get("customers.col_margin"), "pct"),
        ("visits", t.get("customers.col_visits"), "int"),
        ("avg_basket", t.get("customers.col_basket"), "money"),
        ("last_purchase", t.get("customers.col_last"), "date"),
        ("recency_days", t.get("customers.col_recency"), "int"),
        ("balance", t.get("customers.col_balance"), "money"),
    ], title=t.get("customers.list_title"))


def _sheet_calllist(wb: Workbook, m: Metrics) -> None:
    t = wb.t
    ws = wb.sheet("excel.sheet_calllist")
    wb.table(ws, m.call_list(), [
        ("customer_name", t.get("customers.col_customer"), "text"),
        ("phone", t.get("common.phone"), "text"),
        ("city", t.get("common.city"), "text"),
        ("revenue", t.get("customers.col_revenue"), "money"),
        ("visits", t.get("customers.col_visits"), "int"),
        ("last_purchase", t.get("customers.col_last"), "date"),
        ("recency_days", t.get("customers.col_recency"), "int"),
        ("monthly_when_active", t.get("customers.col_was_worth"), "money"),
        ("annual_revenue_at_risk", t.get("customers.col_year_at_risk"), "money"),
        ("balance", t.get("customers.col_balance"), "money"),
    ], title=t.get("customers.call_list_title"), note=t.get("customers.call_list_note"))


def _sheet_receivables(wb: Workbook, m: Metrics) -> None:
    t = wb.t
    ws = wb.sheet("excel.sheet_receivables")
    wb.table(ws, m.receivables(), [
        ("customer_name", t.get("receivables.col_customer"), "text"),
        ("phone", t.get("common.phone"), "text"),
        ("city", t.get("common.city"), "text"),
        ("balance", t.get("receivables.col_balance"), "money"),
        ("share_of_receivables", t.get("receivables.col_share"), "pct"),
        ("last_purchase", t.get("receivables.col_last"), "date"),
        ("days_since_purchase", t.get("receivables.col_days"), "int"),
        ("revenue_12m", t.get("receivables.col_revenue_12m"), "money"),
        ("months_of_their_trade", t.get("receivables.col_months_trade"), "money2"),
        ("at_risk", t.get("receivables.risk_flag"), "text"),
    ], title=t.get("receivables.title"), note=t.get("receivables.months_trade_note"))


def _sheet_inventory(wb: Workbook, m: Metrics) -> None:
    t = wb.t
    ws = wb.sheet("excel.sheet_inventory")
    df = m.abc_classification().copy()
    wb.table(ws, df, [
        ("item_no", t.get("common.rank"), "text"),
        ("item_name", t.get("common.product"), "text"),
        ("family_name", t.get("common.family"), "text"),
        ("abc", t.get("inventory.col_abc"), "text"),
        ("revenue_all", t.get("common.revenue"), "money"),
        ("revenue_share", t.get("common.share"), "pct"),
        ("gross_profit_all", t.get("common.gross_profit"), "money"),
        ("margin_pct", t.get("common.margin_pct"), "pct"),
        ("stock", t.get("common.stock"), "int"),
        ("stock_value", t.get("common.value"), "money"),
        ("cover_months", t.get("inventory.col_cover"), "money2"),
        ("days_since_sale", t.get("common.days"), "int"),
    ], title=t.get("inventory.abc_title"), note=t.get("inventory.abc_note"))


def _sheet_deadstock(wb: Workbook, m: Metrics) -> None:
    t = wb.t
    ws = wb.sheet("excel.sheet_deadstock")
    days = int(m.t("inventory.dead_stock_days", 120))
    wb.table(ws, m.dead_stock(), [
        ("item_no", t.get("common.rank"), "text"),
        ("item_name", t.get("common.product"), "text"),
        ("family_name", t.get("common.family"), "text"),
        ("stock", t.get("common.stock"), "int"),
        ("cost", t.get("common.cost"), "money"),
        ("stock_value", t.get("common.value"), "money"),
        ("last_sale_effective", t.get("common.last_sale"), "date"),
        ("days_since_sale", t.get("common.days"), "int"),
        ("revenue_all", t.get("common.revenue"), "money"),
    ], title=t.get("inventory.dead_title"), note=t.get("inventory.dead_note", days=days))


def _sheet_stockout(wb: Workbook, m: Metrics) -> None:
    t = wb.t
    ws = wb.sheet("excel.sheet_stockout")
    months = float(m.t("inventory.stockout_cover_months", 0.75))
    wb.table(ws, m.stockout_risk(), [
        ("item_no", t.get("common.rank"), "text"),
        ("item_name", t.get("common.product"), "text"),
        ("family_name", t.get("common.family"), "text"),
        ("stock", t.get("common.stock"), "int"),
        ("monthly_rate", t.get("inventory.col_rate"), "money2"),
        ("cover_months", t.get("inventory.col_cover"), "money2"),
        ("weekly_revenue", t.get("inventory.col_weekly_revenue"), "money"),
        ("suggested_order_qty", t.get("inventory.col_suggested"), "int"),
        ("price", t.get("common.price"), "money"),
        ("cost", t.get("common.cost"), "money"),
        ("last_purchased", t.get("common.last_purchase"), "date"),
    ], title=t.get("inventory.stockout_title"),
        note=t.get("inventory.stockout_note", months=t.number(months, 2)))


def _sheet_products(wb: Workbook, m: Metrics) -> None:
    t = wb.t
    ws = wb.sheet("excel.sheet_products")
    wb.table(ws, m.product_margin(), [
        ("item_no", t.get("common.rank"), "text"),
        ("item_name", t.get("common.product"), "text"),
        ("family_name", t.get("common.family"), "text"),
        ("revenue_all", t.get("products.col_revenue"), "money"),
        ("revenue_share", t.get("common.share"), "pct"),
        ("gross_profit_all", t.get("products.col_profit"), "money"),
        ("margin_pct", t.get("products.col_margin"), "pct"),
        ("current_margin_pct", t.get("products.col_current_margin"), "pct"),
        ("qty_all", t.get("products.col_units"), "int"),
        ("price", t.get("common.price"), "money"),
        ("cost", t.get("common.cost"), "money"),
        ("stock", t.get("products.col_stock"), "int"),
        ("stock_value", t.get("common.value"), "money"),
        ("days_since_sale", t.get("common.days"), "int"),
    ], title=t.get("products.product_title"))


def _sheet_families(wb: Workbook, m: Metrics) -> None:
    t = wb.t
    ws = wb.sheet("excel.sheet_families")
    r = wb.table(ws, m.family_margin(), [
        ("family_name", t.get("common.family"), "text"),
        ("revenue", t.get("products.col_revenue"), "money"),
        ("revenue_share", t.get("common.share"), "pct"),
        ("gross_profit", t.get("products.col_profit"), "money"),
        ("margin_pct", t.get("products.col_margin"), "pct"),
        ("units", t.get("products.col_units"), "int"),
        ("stock_value", t.get("inventory.total_value"), "money"),
    ], title=t.get("products.family_title"))

    wb.table(ws, m.silent_margin_erosion(), [
        ("item_name", t.get("common.product"), "text"),
        ("first_cost", t.get("products.col_cost_then"), "money"),
        ("cost", t.get("products.col_cost_now"), "money"),
        ("first_price", t.get("products.col_price_then"), "money"),
        ("price", t.get("products.col_price_now"), "money"),
        ("margin_then", t.get("products.col_margin_then"), "pct"),
        ("margin_now", t.get("products.col_margin_now"), "pct"),
        ("annual_margin_lost", t.get("products.col_annual_loss"), "money"),
    ], start_row=r, title=t.get("products.drift_title"), note=t.get("products.drift_note"))


def _sheet_suppliers(wb: Workbook, m: Metrics) -> None:
    t = wb.t
    ws = wb.sheet("excel.sheet_suppliers")
    cov = m.purchase_coverage()
    r = wb.table(ws, m.supplier_summary(), [
        ("supplier_name", t.get("suppliers.col_supplier"), "text"),
        ("phone", t.get("common.phone"), "text"),
        ("item_revenue", t.get("suppliers.col_item_revenue"), "money"),
        ("revenue_share", t.get("suppliers.col_revenue_share"), "pct"),
        ("margin_pct", t.get("suppliers.col_margin"), "pct"),
        ("item_stock_value", t.get("suppliers.col_stock_value"), "money"),
        ("purchase_value", t.get("suppliers.col_purchase_value"), "money"),
        ("balance", t.get("suppliers.col_balance"), "money"),
        ("last_purchase", t.get("suppliers.col_last"), "date"),
        ("days_since_purchase", t.get("suppliers.col_days_since"), "int"),
    ], title=t.get("suppliers.title"),
        note=t.get("suppliers.coverage_warning",
                   share=t.percent(cov["supplier_share"], 0),
                   date_share=t.percent(cov["date_share"], 0)))

    wb.table(ws, m.purchase_gaps(), [
        ("supplier_name", t.get("suppliers.col_supplier"), "text"),
        ("orders", t.get("suppliers.col_orders"), "int"),
        ("median_gap_days", t.get("suppliers.col_gap"), "money2"),
        ("days_since_last", t.get("suppliers.col_days_since"), "int"),
        ("overdue", t.get("suppliers.col_overdue"), "text"),
    ], start_row=r, title=t.get("suppliers.gaps_title"), note=t.get("suppliers.gaps_note"))


def _sheet_dataquality(wb: Workbook, m: Metrics) -> None:
    t = wb.t
    ws = wb.sheet("excel.sheet_dataquality")
    ws.set_column(0, 0, 52)
    ws.set_column(1, 1, 20)
    ws.set_column(2, 2, 96)

    dq = m.data_quality()
    ws.write(0, 0, t.get("dataquality.title"), wb.f_title)
    ws.write(1, 0, t.get("dataquality.intro"), wb.f_sub)

    items = [
        (t.get("dataquality.collections_title"), dq["collections"]["value"],
         t.get("dataquality.collections_body",
               lines=t.number(dq["collections"]["lines"]),
               value=t.money(dq["collections"]["value"]),
               share=t.percent(dq["collections"]["share_if_counted"]))),
        (t.get("dataquality.header_cost_title"), dq["header_cost"]["understated_by"],
         t.get("dataquality.header_cost_body",
               wrong=t.number(dq["header_cost"]["tickets_wrong"]),
               total=t.number(dq["header_cost"]["tickets_total"]),
               zero=t.number(dq["header_cost"]["tickets_zero"]),
               value=t.money(dq["header_cost"]["understated_by"]))),
        (t.get("dataquality.missing_cost_title"), dq["missing_cost"]["value"],
         t.get("dataquality.missing_cost_body",
               lines=t.number(dq["missing_cost"]["lines"]),
               value=t.money(dq["missing_cost"]["value"]))),
        (t.get("dataquality.returns_title"), dq["returns"]["value"],
         t.get("dataquality.returns_body",
               lines=t.number(dq["returns"]["lines"]),
               value=t.money(dq["returns"]["value"]),
               share=t.percent(dq["returns"]["share_of_revenue"]))),
        (t.get("dataquality.negative_stock_title"), dq["negative_stock"]["value"],
         t.get("dataquality.negative_stock_body",
               items=t.number(dq["negative_stock"]["items"]),
               units=t.number(abs(dq["negative_stock"]["units"])))),
        (t.get("dataquality.stale_last_sold_title"), dq["stale_last_sold"]["wrongly_dead_value"],
         t.get("dataquality.stale_last_sold_body",
               items=t.number(dq["stale_last_sold"]["items"]),
               worst=t.number(dq["stale_last_sold"]["worst_days"]))),
        (t.get("dataquality.purchases_title"), None,
         t.get("dataquality.purchases_body",
               share=t.percent(dq["purchases"]["supplier_share"], 0),
               date_share=t.percent(dq["purchases"]["date_share"], 0))),
        (t.get("dataquality.walkin_title"), dq["walkin"]["revenue"],
         t.get("dataquality.walkin_body",
               tickets=t.number(dq["walkin"]["tickets"]),
               value=t.money(dq["walkin"]["revenue"]),
               share=t.percent(dq["walkin"]["share"]))),
    ]

    r = 3
    for head, col in ((t.get("common.name"), 0),
                      (t.get("dataquality.impact"), 1),
                      (t.get("dataquality.handled"), 2)):
        ws.write(r, col, head, wb.f_head)
    r += 1
    for title, value, body in items:
        ws.write(r, 0, title, wb.f_wrap)
        if value is None:
            ws.write_blank(r, 1, None, wb.f_money)
        else:
            ws.write_number(r, 1, float(value), wb.f_money)
        ws.write(r, 2, body, wb.f_wrap)
        ws.set_row(r, 44)
        r += 1

    # The proof that the database is still being read correctly.
    r += 2
    ws.write(r, 0, t.get("dataquality.verification_title"), wb.f_title)
    r += 2
    for head, col in ((t.get("dataquality.verification_check"), 0),
                      (t.get("dataquality.verification_computed"), 1),
                      (t.get("dataquality.verification_expected"), 2)):
        ws.write(r, col, head, wb.f_head)
    r += 1
    for c in m.verification():
        ws.write(r, 0, c["key"].replace("_", " "), wb.f_text)
        ws.write_number(r, 1, float(c["value"] or 0), wb.f_money)
        ws.write_number(r, 2, float(c["expected"]), wb.f_money)
        r += 1


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build the Excel report.")
    parser.add_argument("--lang", choices=["en", "fr", "ar"], default=None)
    parser.add_argument("--out", default=None, help="Folder to write it to.")
    args = parser.parse_args()

    cfg = get_config()
    setup_logging(cfg)

    etl = ETL(cfg)
    etl.refresh()

    path = build_workbook(language=args.lang, output_dir=args.out, cfg=cfg)
    print(f"\nReport written to:\n  {path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
