"""
metrics.py - every business calculation in the tool.

If a number appears anywhere on a screen, in the Excel report or in the
daily digest, it was worked out in this file. Nothing calculates anything
anywhere else. That way there is one place to check when you want to know
what a figure really means, and one place to change it.

THE RULES THIS FILE APPLIES, IN PLAIN LANGUAGE
----------------------------------------------
1. A line on a ticket with no real product attached is not a sale.
   Nearly all of them say "Paiement de reglement" - a customer paying off
   what they owe. That money is real, but it is a COLLECTION, not revenue.
   Counting it as revenue would overstate sales by about 30 million DZD.

2. The cost total written on the ticket header cannot be trusted. On the
   nine "DV" tickets it is simply zero. So cost of goods is always added up
   from the individual lines: quantity x unit cost.

3. Lines with a negative quantity are returns. They stay in the figures and
   pull revenue down, which is correct, and they are also counted on their
   own so you can see how big they are.

4. Some lines have no cost recorded at all - almost all of them are the
   "Article divers" catch-all where a price is typed in by hand. Their
   profit is UNKNOWN, not 100%. They are left out of every margin
   percentage and reported separately.

5. Customer number 1, "Client divers", is the anonymous walk-in till. The
   money counts as revenue but it is not treated as a customer, because it
   is really hundreds of different people.
"""

from __future__ import annotations

import datetime
import logging
import sqlite3
from functools import cached_property
from typing import Any

import numpy as np
import pandas as pd

from .config import Config, get_config

log = logging.getLogger(__name__)

# Average days in a month. Used to turn "sold per day" into "months of stock".
DAYS_PER_MONTH = 30.44


class Metrics:
    """
    All the business figures, worked out from the local cache.

    Create one of these per request or per report run. The heavy tables are
    loaded once and reused, so asking for twenty different metrics still
    only reads the data once.
    """

    def __init__(self, conn: sqlite3.Connection, cfg: Config | None = None,
                 now: datetime.datetime | None = None):
        self.conn = conn
        self.cfg = cfg or get_config()
        # "Now" is a parameter so tests can pin it to a fixed moment.
        self.now = now or datetime.datetime.now()

    # =====================================================================
    #  Settings, read once
    # =====================================================================

    @property
    def walkin_id(self) -> int:
        return int(self.cfg.get("data_rules.walkin_customer_id", 1))

    @property
    def misc_item_id(self) -> int:
        return int(self.cfg.get("data_rules.misc_item_id", 1))

    def t(self, key: str, default: Any = None) -> Any:
        return self.cfg.get(f"thresholds.{key}", default)

    # =====================================================================
    #  The raw tables
    # =====================================================================

    def _read(self, sql: str, params: tuple = ()) -> pd.DataFrame:
        return pd.read_sql_query(sql, self.conn, params=params)

    def _has_table(self, name: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return row is not None

    def _columns_of(self, table: str) -> set[str]:
        if not self._has_table(table):
            return set()
        return {r[1] for r in self.conn.execute(f'PRAGMA table_info("{table}")')}

    @cached_property
    def lines(self) -> pd.DataFrame:
        """
        Every line of every ticket, with its ticket's date and customer
        attached, plus the derived columns the whole tool relies on.

        This is the single source of truth for sales. Every revenue and
        profit figure in the tool is a filter or a grouping of this table.
        """
        df = self._read("""
            SELECT
                e.ID              AS entry_id,
                e.ReceiptID       AS receipt_id,
                e.ItemID          AS item_id,
                e.ItemName        AS item_name,
                e.Qty             AS qty,
                e.Price           AS price,
                e.Cost            AS unit_cost,
                e.Amount          AS amount,
                e.Discount        AS discount,
                e.TotalItemMargin AS reported_margin,
                r.Time            AS ticket_time,
                r.CustomerID      AS customer_id,
                r.ReceiptNo       AS ticket_no,
                r.ReceiptType     AS ticket_type,
                r.Cash            AS cash,
                r.Cheque          AS cheque,
                r.Transfer        AS transfer,
                r.CreditAccount   AS credit_account,
                r.Total           AS ticket_total
            FROM ReceiptEntry e
            JOIN Receipt r ON r.ID = e.ReceiptID
        """)

        df["ticket_time"] = pd.to_datetime(df["ticket_time"], errors="coerce")
        for col in ("qty", "price", "unit_cost", "amount", "discount",
                    "reported_margin", "ticket_total"):
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df["item_id"] = pd.to_numeric(df["item_id"], errors="coerce").fillna(0).astype(int)
        df["customer_id"] = pd.to_numeric(df["customer_id"], errors="coerce").fillna(0).astype(int)

        # Rule 1: a line with no real product is not a sale.
        df["is_sale"] = df["item_id"] > 0
        # A "collection" is a customer paying down their account balance.
        df["is_collection"] = ~df["is_sale"]

        # Rule 4: is the cost of this line actually known?
        df["cost_known"] = df["unit_cost"].notna() & (df["unit_cost"] != 0)

        # Rule 2: cost of goods always comes from the line, never the header.
        # Where cost is unknown we treat it as zero for the headline profit
        # figure (there is nothing better to do), but `cost_known` lets every
        # margin PERCENTAGE leave those lines out.
        df["line_cost"] = df["qty"].fillna(0) * df["unit_cost"].fillna(0)
        df["gross_profit"] = df["amount"].fillna(0) - df["line_cost"]

        # Rule 3
        df["is_return"] = df["qty"].fillna(0) < 0

        df["date"] = df["ticket_time"].dt.normalize()
        df["month"] = df["ticket_time"].dt.to_period("M").dt.to_timestamp()
        df["weekday"] = df["ticket_time"].dt.weekday        # 0 = Monday
        df["hour"] = df["ticket_time"].dt.hour

        return df

    @cached_property
    def sales(self) -> pd.DataFrame:
        """Just the real product sales - the basis of every revenue figure."""
        return self.lines[self.lines["is_sale"]].copy()

    @cached_property
    def collections(self) -> pd.DataFrame:
        """Account payments that the POS recorded as if they were goods."""
        return self.lines[self.lines["is_collection"]].copy()

    @cached_property
    def tickets(self) -> pd.DataFrame:
        """One row per ticket, with its true line-level cost and profit."""
        df = self._read("""
            SELECT ID AS receipt_id, ReceiptNo AS ticket_no, ReceiptType AS ticket_type,
                   CustomerID AS customer_id, Time AS ticket_time, Total AS total,
                   TotalCost AS header_cost, Margin AS header_margin,
                   Cash AS cash, Cheque AS cheque, Transfer AS transfer,
                   CreditAccount AS credit_account, AccountPayment AS account_payment,
                   Discount AS discount, TotalQty AS total_qty, BatchID AS batch_id
            FROM Receipt
        """)
        df["ticket_time"] = pd.to_datetime(df["ticket_time"], errors="coerce")
        for col in ("total", "header_cost", "header_margin", "cash", "cheque",
                    "transfer", "credit_account", "account_payment", "discount",
                    "total_qty"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["customer_id"] = pd.to_numeric(df["customer_id"], errors="coerce").fillna(0).astype(int)
        df["batch_id"] = pd.to_numeric(df["batch_id"], errors="coerce")

        # Attach the truth from the lines.
        agg = self.sales.groupby("receipt_id").agg(
            revenue=("amount", "sum"),
            line_cost=("line_cost", "sum"),
            gross_profit=("gross_profit", "sum"),
            n_lines=("entry_id", "count"),
        )
        df = df.merge(agg, on="receipt_id", how="left")
        for col in ("revenue", "line_cost", "gross_profit", "n_lines"):
            df[col] = df[col].fillna(0)

        coll = self.collections.groupby("receipt_id")["amount"].sum().rename("collected")
        df = df.merge(coll, on="receipt_id", how="left")
        df["collected"] = df["collected"].fillna(0)

        df["date"] = df["ticket_time"].dt.normalize()
        df["month"] = df["ticket_time"].dt.to_period("M").dt.to_timestamp()
        df["weekday"] = df["ticket_time"].dt.weekday
        df["hour"] = df["ticket_time"].dt.hour
        df["is_walkin"] = df["customer_id"] == self.walkin_id

        # Cash-realized vs on-account, scoped to the Today and Tickets
        # screens only (see docs/superpowers/specs/2026-08-16-tickets-
        # catalog-suppliers-design.md for why this doesn't touch revenue
        # anywhere else). The POS records tender per ticket header, not per
        # line, so a ticket that is partly on account has every one of its
        # lines prorated by the same ratio - the best the data supports.
        # A ticket with no tender recorded at all defaults to fully
        # realized: silence is not evidence a customer owes money.
        df["realized_tender"] = (df["cash"].fillna(0) + df["cheque"].fillna(0) +
                                 df["transfer"].fillna(0))
        df["total_tender"] = df["realized_tender"] + df["credit_account"].fillna(0)
        df["realized_share"] = np.where(df["total_tender"] > 0,
                                        df["realized_tender"] / df["total_tender"], 1.0)
        df["cash_revenue"] = df["revenue"] * df["realized_share"]
        df["on_account_revenue"] = df["revenue"] - df["cash_revenue"]
        return df

    @cached_property
    def items(self) -> pd.DataFrame:
        """The product catalogue, with stock valued at cost."""
        df = self._read("""
            SELECT i.ID AS item_id, i.ItemNo AS item_no, i.ItemName AS item_name,
                   i.ItemFamilyID AS family_id, i.Stock AS stock,
                   i.StockAlert AS stock_alert, i.Cost AS cost, i.Price AS price,
                   i.LastSold AS last_sold, i.LastPurchased AS last_purchased,
                   i.Inactive AS inactive, i.QtyPerParcel AS qty_per_parcel,
                   i.DateCreated AS date_created,
                   f.FamilyName AS family_name
            FROM Item i
            LEFT JOIN ItemFamily f ON f.ID = i.ItemFamilyID
        """)
        for col in ("stock", "stock_alert", "cost", "price", "qty_per_parcel"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["last_sold"] = pd.to_datetime(df["last_sold"], errors="coerce")
        df["last_purchased"] = pd.to_datetime(df["last_purchased"], errors="coerce")
        df["date_created"] = pd.to_datetime(df["date_created"], errors="coerce")
        df["inactive"] = df["inactive"].fillna(0).astype(int).astype(bool)
        df["family_name"] = df["family_name"].fillna("—")

        # Stock value at cost. Rows with negative stock are POS mistakes; they
        # are kept in the total (so it matches the books) and reported on
        # their own in the data-quality panel.
        df["stock_value"] = df["stock"].fillna(0) * df["cost"].fillna(0)
        df["days_since_sold"] = (self.now - df["last_sold"]).dt.days
        df["never_sold"] = df["last_sold"].isna()
        df["markup_pct"] = np.where(
            df["cost"].fillna(0) > 0,
            (df["price"].fillna(0) - df["cost"].fillna(0)) / df["cost"].replace(0, np.nan),
            np.nan)
        return df

    @cached_property
    def customers(self) -> pd.DataFrame:
        """The customer list, with what they owe."""
        df = self._read("""
            SELECT ID AS customer_id, CustomerNo AS customer_no,
                   CustomerName AS customer_name, Phone AS phone, City AS city,
                   Account AS balance, LastVisit AS last_visit,
                   TotalVisits AS total_visits, AllowAccount AS allow_account,
                   Inactive AS inactive
            FROM Customer
        """)
        df["balance"] = pd.to_numeric(df["balance"], errors="coerce").fillna(0.0)
        df["total_visits"] = pd.to_numeric(df["total_visits"], errors="coerce").fillna(0).astype(int)
        df["last_visit"] = pd.to_datetime(df["last_visit"], errors="coerce")
        df["allow_account"] = df["allow_account"].fillna(0).astype(int).astype(bool)
        df["phone"] = df["phone"].fillna("").astype(str)
        df["customer_name"] = df["customer_name"].fillna("").astype(str)
        df["is_walkin"] = df["customer_id"] == self.walkin_id
        return df

    @cached_property
    def suppliers(self) -> pd.DataFrame:
        df = self._read("""
            SELECT ID AS supplier_id, SupplierName AS supplier_name, Phone AS phone,
                   Account AS balance, TotalPurchased AS total_purchased,
                   LastPurchase AS last_purchase
            FROM Supplier
        """)
        df["balance"] = pd.to_numeric(df["balance"], errors="coerce").fillna(0.0)
        df["total_purchased"] = pd.to_numeric(df["total_purchased"], errors="coerce").fillna(0)
        df["last_purchase"] = pd.to_datetime(df["last_purchase"], errors="coerce")
        df["supplier_name"] = df["supplier_name"].fillna("").astype(str)
        return df

    @cached_property
    def purchases(self) -> pd.DataFrame:
        """
        Supplier purchase lines, with the supplier and a date attached where
        the data allows it.

        This POS does not store a purchase header table, so a purchase line
        on its own says nothing about who was bought from or when. The link
        has to be rebuilt from SupplierItem, which records supplier, purchase
        and item together with a date.

        The rebuild is NOT complete - about a fifth of purchase lines cannot
        be traced to a supplier and most purchases have no date. Rather than
        quietly dropping them, everything is kept and `purchase_coverage()`
        reports exactly how much is traceable, so no supplier figure is ever
        presented as more complete than it is.
        """
        if not self._columns_of("PurchaseEntry"):
            return pd.DataFrame()

        df = self._read("""
            SELECT ID AS entry_id, PurchaseID AS purchase_id, ItemID AS item_id,
                   ItemName AS item_name, Qty AS qty, Price AS price, Cost AS cost,
                   NewCost AS new_cost, NewStock AS new_stock, Amount AS amount
            FROM PurchaseEntry
        """)
        for col in ("qty", "price", "cost", "new_cost", "new_stock", "amount"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["item_id"] = pd.to_numeric(df["item_id"], errors="coerce").fillna(0).astype(int)
        df["supplier_id"] = np.nan
        df["purchase_time"] = pd.NaT

        if not self._has_table("SupplierItem"):
            return df

        si = self._read("""
            SELECT SupplierID AS supplier_id, PurchaseID AS purchase_id,
                   ItemID AS item_id, PurchasePrice AS purchase_price,
                   DateLastUpdated AS when_updated
            FROM SupplierItem
        """)
        if si.empty:
            return df
        si["when_updated"] = pd.to_datetime(si["when_updated"], errors="coerce")
        si["supplier_id"] = pd.to_numeric(si["supplier_id"], errors="coerce")

        # First choice: the exact supplier for this purchase AND this item.
        exact = (si.dropna(subset=["supplier_id"])
                 .groupby(["purchase_id", "item_id"])["supplier_id"].first())
        # Second choice: whoever supplied most of the rest of that purchase.
        by_purchase = (si.dropna(subset=["supplier_id"])
                       .groupby("purchase_id")["supplier_id"]
                       .agg(lambda s: s.mode().iloc[0] if not s.mode().empty else np.nan))
        # The date of the purchase is the earliest date recorded against it.
        dates = si.groupby("purchase_id")["when_updated"].min()

        idx = pd.MultiIndex.from_arrays([df["purchase_id"], df["item_id"]])
        df["supplier_id"] = pd.Series(exact.reindex(idx).to_numpy(), index=df.index)
        df["supplier_id"] = df["supplier_id"].fillna(df["purchase_id"].map(by_purchase))
        df["purchase_time"] = df["purchase_id"].map(dates)

        return df

    def purchase_coverage(self) -> dict[str, Any]:
        """
        How much of the purchase history can actually be traced back to a
        supplier and a date. Shown on the Suppliers and Data quality screens
        so the supplier figures are read with the right amount of trust.
        """
        p = self.purchases
        if p.empty:
            return {"lines": 0, "with_supplier": 0, "supplier_share": 0.0,
                    "orders": 0, "orders_dated": 0, "date_share": 0.0,
                    "value": 0.0, "value_with_supplier": 0.0}
        total_val = float(p["amount"].sum())
        matched = p[p["supplier_id"].notna()]
        orders = int(p["purchase_id"].nunique())
        dated = int(p.dropna(subset=["purchase_time"])["purchase_id"].nunique())

        # Sanity check the purchase totals against what we know was sold.
        # Everything ever sold cost this much, and this much is still on the
        # shelf, so purchases should be roughly the sum of the two. In this
        # database the purchase lines add up to about twice that, which
        # cannot be right - so purchase amounts are reported as relative
        # shares between suppliers and never as an amount of money spent.
        cogs = float(self.sales["line_cost"].sum())
        stock = float(self.items["stock_value"].sum())
        expected = cogs + stock

        return {
            "lines": int(len(p)),
            "with_supplier": int(len(matched)),
            "supplier_share": len(matched) / len(p) if len(p) else 0.0,
            "orders": orders,
            "orders_dated": dated,
            "date_share": dated / orders if orders else 0.0,
            "value": total_val,
            "value_with_supplier": float(matched["amount"].sum()),
            "value_share": (float(matched["amount"].sum()) / total_val) if total_val else 0.0,
            "expected_value": expected,
            "value_ratio": (total_val / expected) if expected else None,
            "value_reconciles": bool(expected and 0.75 <= total_val / expected <= 1.35),
        }

    # =====================================================================
    #  Reference points
    # =====================================================================

    @cached_property
    def data_range(self) -> dict[str, Any]:
        """The first and last sale in the data, and how fresh it is."""
        t = self.tickets["ticket_time"].dropna()
        if t.empty:
            return {"first": None, "last": None, "days": 0, "age_hours": None}
        first, last = t.min(), t.max()
        return {
            "first": first,
            "last": last,
            "days": (last - first).days,
            "age_hours": round((self.now - last).total_seconds() / 3600.0, 1),
        }

    def _window(self, df: pd.DataFrame, days: int,
                col: str = "ticket_time") -> pd.DataFrame:
        cutoff = self.now - datetime.timedelta(days=days)
        return df[df[col] >= cutoff]

    def _window_range(self, df: pd.DataFrame, start: datetime.date | None,
                       end: datetime.date | None,
                       col: str = "ticket_time") -> pd.DataFrame:
        """
        Narrow a dataframe to a whole-day date range. Either end can be left
        out to mean "no limit that side". Used by the date-range picker,
        which works in whole days - unlike `_window()`, which cuts off at
        the exact moment `days` ago.
        """
        if start is not None:
            df = df[df[col] >= pd.Timestamp(start)]
        if end is not None:
            # `end` is inclusive, so the cutoff is the start of the next day.
            df = df[df[col] < pd.Timestamp(end) + pd.DateOffset(days=1)]
        return df

    # =====================================================================
    #  HEADLINE TOTALS
    # =====================================================================

    def headline(self, days: int | None = None) -> dict[str, Any]:
        """
        The top-level numbers, either for all time or for a recent window.

        `margin_pct` deliberately leaves out lines whose cost is unknown, so
        it is a percentage of the revenue we can actually measure. Both the
        measurable revenue and the unmeasurable amount are returned so the
        figure can always be shown honestly.
        """
        s = self.sales if days is None else self._window(self.sales, days)

        revenue = float(s["amount"].sum())
        gross_profit = float(s["gross_profit"].sum())

        known = s[s["cost_known"]]
        rev_known = float(known["amount"].sum())
        gp_known = float(known["gross_profit"].sum())

        tk = self.tickets if days is None else self._window(self.tickets, days)
        n_tickets = int(tk["receipt_id"].nunique())

        coll = self.collections if days is None else self._window(self.collections, days)

        real = s[s["customer_id"] != self.walkin_id]

        return {
            "revenue": revenue,
            "gross_profit": gross_profit,
            "margin_pct": (gp_known / rev_known) if rev_known else None,
            "revenue_measurable": rev_known,
            "revenue_unmeasurable": revenue - rev_known,
            "tickets": n_tickets,
            "avg_basket": (revenue / n_tickets) if n_tickets else 0.0,
            "units": float(s["qty"].sum()),
            "collections": float(coll["amount"].sum()),
            "active_customers": int(real["customer_id"].nunique()),
            "returns_value": float(s.loc[s["is_return"], "amount"].sum()),
            "returns_lines": int(s["is_return"].sum()),
        }

    # =====================================================================
    #  PAGE 1 - TODAY / LIVE
    # =====================================================================

    def today(self, target_date: datetime.date | None = None) -> dict[str, Any]:
        """
        Everything the Today screen shows, for any single day - defaults to
        the actual current day when `target_date` is not given, which is
        every existing caller's behaviour unchanged.

        "Sales" means cash-realized only: Cash + Cheque + Transfer tender,
        never the portion of a sale put on the customer's account. The
        on-account portion is reported alongside it, always separately,
        never folded in - see `tickets`' `cash_revenue`/`on_account_revenue`
        columns and docs/superpowers/specs/2026-08-16-tickets-catalog-
        suppliers-design.md for why this definition is scoped to this
        screen and the Tickets screen only.

        Comparisons are chosen to be fair rather than flattering:
          - the day before target_date, for a same-shape comparison
          - the same weekday the week before, because a Sunday is not a
            Tuesday
          - the average of the last 8 of the same weekday, so one freak day
            does not set the bar
        When target_date is the live current day, every comparison is cut
        off at the same time of day target_date has reached so far, so an
        in-progress day is compared fairly against finished ones. When
        target_date is a day that has already finished, nothing is clipped
        - the whole day is compared against whole days.
        """
        target = target_date or self.now.date()
        is_current_day = target == self.now.date()
        day_before = target - datetime.timedelta(days=1)
        same_day_last_week = target - datetime.timedelta(days=7)
        cutoff_time = self.now.time() if is_current_day else None

        def day_slice(d: datetime.date, until_time: datetime.time | None = None
                      ) -> pd.DataFrame:
            s = self.sales[self.sales["ticket_time"].dt.date == d]
            if until_time is not None:
                s = s[s["ticket_time"].dt.time <= until_time]
            return s

        def ticket_slice(d: datetime.date, until_time: datetime.time | None = None
                         ) -> pd.DataFrame:
            tk = self.tickets[self.tickets["ticket_time"].dt.date == d]
            if until_time is not None:
                tk = tk[tk["ticket_time"].dt.time <= until_time]
            return tk

        def day_stats(d: datetime.date, clip: bool = False) -> dict[str, Any]:
            cut = cutoff_time if clip else None
            s = day_slice(d, cut)
            tk = ticket_slice(d, cut)
            known = s[s["cost_known"]]
            rev = float(s["amount"].sum())
            n = int(tk["receipt_id"].nunique())
            return {
                "date": d,
                "revenue": rev,
                "cash_revenue": float(tk["cash_revenue"].sum()),
                "on_account_revenue": float(tk["on_account_revenue"].sum()),
                "gross_profit": float(s["gross_profit"].sum()),
                "margin_pct": (float(known["gross_profit"].sum()) /
                               float(known["amount"].sum())) if float(known["amount"].sum()) else None,
                "tickets": n,
                "avg_basket": rev / n if n else 0.0,
                "units": float(s["qty"].sum()),
            }

        now_stats = day_stats(target, clip=True)
        yest = day_stats(day_before, clip=True)
        last_week = day_stats(same_day_last_week, clip=True)

        # Average of the last 8 occurrences of this weekday, cash-realized,
        # up to the same time of day, ignoring days the shop took nothing.
        weekday = target.weekday()
        same_weekday_totals: list[float] = []
        d = target - datetime.timedelta(days=7)
        while len(same_weekday_totals) < 8 and d >= (self.data_range["first"].date()
                                                     if self.data_range["first"] is not None else target):
            tk = ticket_slice(d, cutoff_time)
            v = float(tk["cash_revenue"].sum())
            if v > 0:
                same_weekday_totals.append(v)
            d -= datetime.timedelta(days=7)
        weekday_avg = float(np.mean(same_weekday_totals)) if same_weekday_totals else 0.0

        # How the money came in on target_date - the whole day, never
        # clipped, since there is nothing "in progress" about a payments
        # breakdown for a day already being fully shown elsewhere on the
        # page.
        tk_target = self.tickets[self.tickets["ticket_time"].dt.date == target]
        payments = {
            "cash": float(tk_target["cash"].sum()),
            "cheque": float(tk_target["cheque"].sum()),
            "transfer": float(tk_target["transfer"].sum()),
            "credit": float(tk_target["credit_account"].sum()),
        }

        s_target = day_slice(target)
        top_items = (s_target.groupby(["item_id", "item_name"], as_index=False)
                     .agg(qty=("qty", "sum"), revenue=("amount", "sum"),
                          gross_profit=("gross_profit", "sum"))
                     .sort_values("revenue", ascending=False)
                     .head(10))

        by_hour = (s_target.groupby("hour", as_index=False)
                   .agg(revenue=("amount", "sum"))
                   .sort_values("hour"))

        top_customers = (s_target[s_target["customer_id"] != self.walkin_id]
                         .groupby("customer_id", as_index=False)
                         .agg(revenue=("amount", "sum")))
        if not top_customers.empty:
            top_customers = (top_customers
                             .merge(self.customers[["customer_id", "customer_name"]],
                                    on="customer_id", how="left")
                             .sort_values("revenue", ascending=False).head(5))

        def delta(a: float, b: float) -> float | None:
            return ((a - b) / b) if b else None

        return {
            "today": now_stats,
            "yesterday": yest,
            "last_week_same_day": last_week,
            "weekday_average": weekday_avg,
            "weekday_sample": len(same_weekday_totals),
            "vs_yesterday": delta(now_stats["cash_revenue"], yest["cash_revenue"]),
            "vs_last_week": delta(now_stats["cash_revenue"], last_week["cash_revenue"]),
            "vs_weekday_avg": delta(now_stats["cash_revenue"], weekday_avg),
            "payments": payments,
            "collections_today": float(
                self.collections[self.collections["ticket_time"].dt.date == target]["amount"].sum()),
            "top_items": top_items,
            "by_hour": by_hour,
            "top_customers": top_customers,
            "data_age_hours": self.data_range["age_hours"],
            "last_sale_time": self.data_range["last"],
            "is_current_day": is_current_day,
        }

    def period_stats(self, start: datetime.date, end: datetime.date) -> dict[str, Any]:
        """
        The same shape of figures as `today()`, for an arbitrary whole-day
        range - the Today screen's view when a range preset (this week,
        last 7 days, a custom multi-day range) is chosen instead of a
        single day. `end` is inclusive, matching `_window_range`.
        """
        s = self._window_range(self.sales, start, end)
        tk = self._window_range(self.tickets, start, end)
        coll = self._window_range(self.collections, start, end)

        revenue = float(s["amount"].sum())
        known = s[s["cost_known"]]
        rev_known = float(known["amount"].sum())
        gp_known = float(known["gross_profit"].sum())
        n_tickets = int(tk["receipt_id"].nunique())

        top_items = (s.groupby(["item_id", "item_name"], as_index=False)
                     .agg(qty=("qty", "sum"), revenue=("amount", "sum"),
                          gross_profit=("gross_profit", "sum"))
                     .sort_values("revenue", ascending=False).head(10))

        top_customers = (s[s["customer_id"] != self.walkin_id]
                         .groupby("customer_id", as_index=False)
                         .agg(revenue=("amount", "sum")))
        if not top_customers.empty:
            top_customers = (top_customers
                             .merge(self.customers[["customer_id", "customer_name"]],
                                    on="customer_id", how="left")
                             .sort_values("revenue", ascending=False).head(5))

        return {
            "start": start,
            "end": end,
            "revenue": revenue,
            "cash_revenue": float(tk["cash_revenue"].sum()),
            "on_account_revenue": float(tk["on_account_revenue"].sum()),
            "gross_profit": float(s["gross_profit"].sum()),
            "margin_pct": (gp_known / rev_known) if rev_known else None,
            "tickets": n_tickets,
            "avg_basket": (revenue / n_tickets) if n_tickets else 0.0,
            "units": float(s["qty"].sum()),
            "collections": float(coll["amount"].sum()),
            "top_items": top_items,
            "top_customers": top_customers,
        }

    # =====================================================================
    #  PAGE 1B - TICKETS
    # =====================================================================

    def ticket_list(self, start: datetime.date, end: datetime.date) -> pd.DataFrame:
        """
        Every ticket in a date range, for the Tickets screen: who, when,
        how much, and how much of that was cash-realized vs put on the
        customer's account. Built from `tickets`, filtered to the range -
        not a new aggregation.
        """
        tk = self._window_range(self.tickets, start, end)
        if tk.empty:
            return tk
        tk = tk.merge(self.customers[["customer_id", "customer_name"]],
                      on="customer_id", how="left")
        tk["customer_name"] = tk["customer_name"].fillna("—")
        return tk[["receipt_id", "ticket_no", "ticket_time", "customer_id",
                   "customer_name", "revenue", "cash_revenue", "on_account_revenue",
                   "collected", "total", "n_lines"]].sort_values(
            "ticket_time", ascending=False).reset_index(drop=True)

    def ticket_detail(self, receipt_id: int) -> dict[str, Any] | None:
        """
        Everything on one ticket, for the drill-down page: its header
        (customer, time, totals, cash-realized vs on-account) and every
        line on it, sales and collections both - a collection line stays
        labelled as a collection, never folded into "sale". Returns None
        if the ticket does not exist.
        """
        tk = self.tickets[self.tickets["receipt_id"] == receipt_id]
        if tk.empty:
            return None
        header = tk.iloc[0].to_dict()

        cust = self.customers[self.customers["customer_id"] == header["customer_id"]]
        header["customer_name"] = (cust.iloc[0]["customer_name"]
                                   if not cust.empty else "—")

        lines = (self.lines[self.lines["receipt_id"] == receipt_id]
                 .sort_values("entry_id"))

        return {
            "header": header,
            "lines": lines[["entry_id", "item_name", "qty", "price", "discount",
                            "amount", "is_sale", "is_return"]],
        }

    # =====================================================================
    #  PAGE 2 - TREND
    # =====================================================================

    def monthly(self, start: datetime.date | None = None,
                end: datetime.date | None = None) -> pd.DataFrame:
        """
        One row per month: revenue, profit, margin, tickets, basket size and
        how many different customers bought.

        `start`/`end` narrow the data to a whole-day range before grouping -
        used by the date-range picker on the Trend and Cash pages. Left as
        None (the default), this is all-time, exactly as before.
        """
        s = self.sales
        tk_df = self.tickets
        coll_df = self.collections
        if start is not None or end is not None:
            s = self._window_range(s, start, end)
            tk_df = self._window_range(tk_df, start, end)
            coll_df = self._window_range(coll_df, start, end)

        if s.empty:
            return pd.DataFrame()

        known = s[s["cost_known"]]

        rev = s.groupby("month")["amount"].sum().rename("revenue")
        gp = s.groupby("month")["gross_profit"].sum().rename("gross_profit")
        rev_k = known.groupby("month")["amount"].sum().rename("revenue_measurable")
        gp_k = known.groupby("month")["gross_profit"].sum().rename("gross_profit_measurable")
        units = s.groupby("month")["qty"].sum().rename("units")

        tk = tk_df.groupby("month")["receipt_id"].nunique().rename("tickets")
        cust = (s[s["customer_id"] != self.walkin_id]
                .groupby("month")["customer_id"].nunique().rename("active_customers"))
        coll = coll_df.groupby("month")["amount"].sum().rename("collections")

        df = pd.concat([rev, gp, rev_k, gp_k, units, tk, cust, coll], axis=1).fillna(0.0)
        df = df.sort_index().reset_index()

        df["margin_pct"] = np.where(df["revenue_measurable"] > 0,
                                    df["gross_profit_measurable"] / df["revenue_measurable"],
                                    np.nan)
        df["avg_basket"] = np.where(df["tickets"] > 0, df["revenue"] / df["tickets"], 0.0)
        df["month_label"] = df["month"].dt.strftime("%Y-%m")

        # Change on the month before, and on the same month a year earlier.
        df["revenue_mom"] = df["revenue"].pct_change()
        df["gross_profit_mom"] = df["gross_profit"].pct_change()
        df["revenue_yoy"] = df["revenue"].pct_change(12)
        df["gross_profit_yoy"] = df["gross_profit"].pct_change(12)
        df["margin_change"] = df["margin_pct"].diff()

        return df

    def complete_months(self) -> pd.DataFrame:
        """
        Monthly figures with the current, unfinished month removed.

        Comparing a half-finished month against full ones is the single
        easiest way to scare yourself for no reason, so anything that looks
        at trends uses this.
        """
        df = self.monthly()
        if df.empty:
            return df
        this_month = pd.Timestamp(self.now.year, self.now.month, 1)
        return df[df["month"] < this_month].copy()

    def weekday_hour_heatmap(self) -> pd.DataFrame:
        """When in the week the money actually comes in."""
        s = self.sales.dropna(subset=["ticket_time"])
        if s.empty:
            return pd.DataFrame()
        g = (s.groupby(["weekday", "hour"], as_index=False)
             .agg(revenue=("amount", "sum"), tickets=("receipt_id", "nunique")))
        return g

    def seasonality(self) -> pd.DataFrame:
        """
        Average revenue by calendar month across all the years of data, so
        the months that consistently underperform stand out.
        """
        df = self.complete_months()
        if df.empty:
            return pd.DataFrame()
        df = df.copy()
        df["month_no"] = df["month"].dt.month
        g = (df.groupby("month_no", as_index=False)
             .agg(avg_revenue=("revenue", "mean"),
                  avg_gross_profit=("gross_profit", "mean"),
                  years=("revenue", "count")))
        overall = g["avg_revenue"].mean()
        g["vs_average"] = (g["avg_revenue"] - overall) / overall if overall else np.nan
        return g.sort_values("month_no")

    # =====================================================================
    #  PAGE 3 - CUSTOMERS
    # =====================================================================

    def _days_since_last_payment(self) -> pd.Series:
        """
        Days since each customer last paid down their account balance,
        indexed by customer_id. Built from `collections` - the `ItemID<=0`
        definition used everywhere else in this file for an account
        payment - never the `"glement"` substring match, which exists only
        for the reporting breakdown inside `data_quality()`.
        """
        coll = self.collections
        if coll.empty:
            return pd.Series(dtype=float)
        last_payment = coll.groupby("customer_id")["ticket_time"].max()
        return (self.now - last_payment).dt.days

    def _credit_risk_tier_series(self, balance: pd.Series, customer_id: pd.Series,
                                  days_since_sale: pd.Series) -> pd.Series:
        """
        low/medium/high credit-risk tier for customers who currently owe
        money, combining how big the balance is, how long since they last
        paid anything off, and how long since they last bought at all.

        Customers with no positive balance get no tier (None) - this is
        about deciding whether to extend more credit to someone who
        already owes money, not a general customer-health score (that's
        what the RFM segment in `_add_rfm` is for).
        """
        floor = float(self.t("customers.credit_risk_balance_floor", 100000))
        medium_days = float(self.t("customers.credit_risk_medium_days", 30))
        high_days = float(self.t("customers.credit_risk_high_days", 90))

        days_since_payment = customer_id.map(self._days_since_last_payment())

        def tier(bal: float, d_pay: float, d_sale: float) -> str | None:
            if bal is None or pd.isna(bal) or bal <= 0:
                return None
            candidates = [d for d in (d_pay, d_sale) if d is not None and pd.notna(d)]
            worst = max(candidates) if candidates else None
            if worst is not None and worst >= high_days:
                return "high"
            if bal >= floor or (worst is not None and worst >= medium_days):
                return "medium"
            return "low"

        return pd.Series(
            [tier(b, dp, ds) for b, dp, ds in
             zip(balance, days_since_payment, days_since_sale)],
            index=balance.index)

    def customer_summary(self) -> pd.DataFrame:
        """
        One row per real customer: what they have bought, what they earn you,
        when they last came, and which group they fall into.

        The walk-in till is left out - it is not one customer.
        """
        s = self.sales[self.sales["customer_id"] != self.walkin_id]

        if s.empty:
            return pd.DataFrame()

        known = s[s["cost_known"]]

        g = s.groupby("customer_id").agg(
            revenue=("amount", "sum"),
            gross_profit=("gross_profit", "sum"),
            units=("qty", "sum"),
            lines=("entry_id", "count"),
            first_purchase=("ticket_time", "min"),
            last_purchase=("ticket_time", "max"),
            visits=("receipt_id", "nunique"),
        )
        gk = known.groupby("customer_id").agg(
            revenue_measurable=("amount", "sum"),
            gross_profit_measurable=("gross_profit", "sum"))
        g = g.join(gk).reset_index()

        # Trailing twelve months, which is what "worth chasing" really means.
        cut = self.now - datetime.timedelta(days=365)
        t12 = s[s["ticket_time"] >= cut].groupby("customer_id").agg(
            revenue_12m=("amount", "sum"),
            gross_profit_12m=("gross_profit", "sum"),
            visits_12m=("receipt_id", "nunique"))
        g = g.merge(t12, on="customer_id", how="left")
        for c in ("revenue_12m", "gross_profit_12m", "visits_12m"):
            g[c] = g[c].fillna(0.0)

        g = g.merge(
            self.customers[["customer_id", "customer_name", "customer_no",
                            "phone", "city", "balance", "allow_account"]],
            on="customer_id", how="left")
        g["customer_name"] = g["customer_name"].fillna("(unknown)")
        g["balance"] = g["balance"].fillna(0.0)

        g["margin_pct"] = np.where(g["revenue_measurable"].fillna(0) > 0,
                                   g["gross_profit_measurable"] / g["revenue_measurable"],
                                   np.nan)
        g["recency_days"] = (self.now - g["last_purchase"]).dt.days
        g["avg_basket"] = np.where(g["visits"] > 0, g["revenue"] / g["visits"], 0.0)
        g["tenure_days"] = (self.now - g["first_purchase"]).dt.days
        g["revenue_share"] = g["revenue"] / g["revenue"].sum() if g["revenue"].sum() else 0.0

        g = self._add_rfm(g)
        g["credit_risk"] = self._credit_risk_tier_series(
            g["balance"], g["customer_id"], g["recency_days"])
        return g.sort_values("revenue", ascending=False).reset_index(drop=True)

    def _add_rfm(self, g: pd.DataFrame) -> pd.DataFrame:
        """
        Score each customer 1-5 on how recently they bought (R), how often
        (F) and how much they spend (M), then give them a plain name.

        The scores are relative to your own customer base, not to some
        outside benchmark - a 5 for frequency means "among your most frequent",
        which is the only comparison that means anything.
        """
        def score(series: pd.Series, reverse: bool = False) -> pd.Series:
            # Fall back to a flat middle score when there is not enough spread
            # to split into five groups.
            try:
                q = pd.qcut(series.rank(method="first"), 5, labels=[1, 2, 3, 4, 5])
                out = q.astype(int)
            except ValueError:
                out = pd.Series(3, index=series.index)
            return (6 - out) if reverse else out

        g = g.copy()
        g["r_score"] = score(g["recency_days"].fillna(9999), reverse=True)
        g["f_score"] = score(g["visits"].fillna(0))
        g["m_score"] = score(g["revenue"].fillna(0))
        g["rfm"] = g["r_score"] * 100 + g["f_score"] * 10 + g["m_score"]

        lapsed_days = float(self.t("customers.lapsed_days", 90))
        min_visits = int(self.t("customers.lapsed_min_visits", 2))

        def segment(row: pd.Series) -> str:
            rec = row["recency_days"] if pd.notna(row["recency_days"]) else 9999
            visits = row["visits"]
            r, f, m = row["r_score"], row["f_score"], row["m_score"]

            # One purchase, ever, and it was a while ago.
            if visits < min_visits:
                return "one_time" if rec > lapsed_days else "new"
            # Gone quiet for longer than your lapsed threshold.
            if rec > lapsed_days:
                return "lapsed"
            # Best of the best: recent, frequent, big.
            if r >= 4 and f >= 4 and m >= 4:
                return "champion"
            # Slipping: they used to be regular and have gone quiet-ish.
            if r <= 2 and f >= 3:
                return "at_risk"
            if f >= 3 and m >= 3:
                return "loyal"
            return "active"

        g["segment"] = g.apply(segment, axis=1)
        return g

    def segment_summary(self) -> pd.DataFrame:
        """How many customers are in each group, and what they are worth."""
        cs = self.customer_summary()
        if cs.empty:
            return pd.DataFrame()
        g = (cs.groupby("segment", as_index=False)
             .agg(customers=("customer_id", "count"),
                  revenue=("revenue", "sum"),
                  revenue_12m=("revenue_12m", "sum"),
                  gross_profit=("gross_profit", "sum"),
                  balance=("balance", "sum")))
        total = g["revenue"].sum()
        g["revenue_share"] = g["revenue"] / total if total else 0.0
        order = ["champion", "loyal", "active", "new", "at_risk", "lapsed", "one_time"]
        g["__o"] = g["segment"].apply(lambda s: order.index(s) if s in order else 99)
        return g.sort_values("__o").drop(columns="__o").reset_index(drop=True)

    def call_list(self) -> pd.DataFrame:
        """
        The customers worth telephoning: they used to buy regularly, they
        have gone quiet, and they were worth real money. Sorted by what they
        used to spend, because that is who to ring first.
        """
        cs = self.customer_summary()
        if cs.empty:
            return pd.DataFrame()

        lapsed_days = float(self.t("customers.lapsed_days", 90))
        min_visits = int(self.t("customers.lapsed_min_visits", 2))

        out = cs[(cs["recency_days"] > lapsed_days) &
                 (cs["visits"] >= min_visits)].copy()

        # What they used to be worth per month while they were still coming.
        active_months = (out["last_purchase"] - out["first_purchase"]).dt.days / DAYS_PER_MONTH
        out["monthly_when_active"] = np.where(active_months > 0,
                                              out["revenue"] / active_months,
                                              out["revenue"])
        # A rough figure for what a year of silence costs.
        out["annual_revenue_at_risk"] = out["monthly_when_active"] * 12

        cols = ["customer_id", "customer_no", "customer_name", "phone", "city",
                "revenue", "gross_profit", "margin_pct", "visits", "avg_basket",
                "last_purchase", "recency_days", "balance",
                "monthly_when_active", "annual_revenue_at_risk", "segment"]
        return out[cols].sort_values("revenue", ascending=False).reset_index(drop=True)

    def customer_pareto(self) -> pd.DataFrame:
        """
        Customers ranked by revenue with a running total, so you can see how
        few of them make up most of the business.
        """
        cs = self.customer_summary()
        if cs.empty:
            return pd.DataFrame()
        df = cs[["customer_id", "customer_name", "revenue"]].copy()
        df = df.sort_values("revenue", ascending=False).reset_index(drop=True)
        total = df["revenue"].sum()
        df["rank"] = np.arange(1, len(df) + 1)
        df["cumulative"] = df["revenue"].cumsum()
        df["cumulative_share"] = df["cumulative"] / total if total else 0.0
        df["customer_share"] = df["rank"] / len(df)
        return df

    def churn_by_month(self) -> pd.DataFrame:
        """
        For each month: how many customers bought, how many were new, and
        how many of the previous month's customers did not come back.
        """
        s = self.sales[self.sales["customer_id"] != self.walkin_id]
        if s.empty:
            return pd.DataFrame()

        by_month = s.groupby("month")["customer_id"].apply(set)
        months = sorted(by_month.index)
        first_seen = s.groupby("customer_id")["month"].min()

        rows = []
        for i, m in enumerate(months):
            current = by_month[m]
            new = {c for c in current if first_seen.get(c) == m}
            if i == 0:
                prev, retained, lost = set(), set(), set()
            else:
                prev = by_month[months[i - 1]]
                retained = current & prev
                lost = prev - current
            rows.append({
                "month": m,
                "month_label": m.strftime("%Y-%m"),
                "active": len(current),
                "new": len(new),
                "retained": len(retained),
                "lost": len(lost),
                "churn_rate": (len(lost) / len(prev)) if prev else np.nan,
                "retention_rate": (len(retained) / len(prev)) if prev else np.nan,
            })
        return pd.DataFrame(rows)

    def customer_profile(self, customer_id: int) -> dict[str, Any] | None:
        """
        Everything about one customer, for the drill-down page: their row
        from `customer_summary()` (zero-filled if they have never bought
        anything measurable), their row from `receivables()` if they owe
        money, and their purchase and payment history.

        Built entirely from what already exists elsewhere in this file -
        filtered to one ID, not a new aggregation. Returns None if the ID
        does not exist or is the anonymous walk-in till (customer_id=1),
        which is not a real customer and has no profile of its own.
        """
        if customer_id == self.walkin_id:
            return None
        cust = self.customers[self.customers["customer_id"] == customer_id]
        if cust.empty:
            return None

        summary = self.customer_summary()
        row = summary[summary["customer_id"] == customer_id] if not summary.empty else summary
        if not row.empty:
            summary_row = row.iloc[0].to_dict()
        else:
            # A real customer with no measurable purchase history yet.
            # They can still owe money (e.g. an opening balance), so credit
            # risk is still worked out, just with no purchase-recency signal.
            base = cust.iloc[0].to_dict()
            balance = pd.Series([base.get("balance", 0.0)])
            cid = pd.Series([customer_id])
            risk = self._credit_risk_tier_series(balance, cid, pd.Series([None])).iloc[0]
            summary_row = {
                **base, "revenue": 0.0, "gross_profit": 0.0, "units": 0.0,
                "lines": 0, "visits": 0, "revenue_12m": 0.0,
                "gross_profit_12m": 0.0, "margin_pct": None,
                "recency_days": None, "avg_basket": 0.0, "segment": None,
                "credit_risk": risk,
            }

        receivable_row = None
        rec = self.receivables()
        if not rec.empty:
            r = rec[rec["customer_id"] == customer_id]
            if not r.empty:
                receivable_row = r.iloc[0].to_dict()

        s = (self.sales[self.sales["customer_id"] == customer_id]
             .sort_values("ticket_time", ascending=False))
        coll = (self.collections[self.collections["customer_id"] == customer_id]
                .sort_values("ticket_time", ascending=False))

        return {
            "summary": summary_row,
            "receivable": receivable_row,
            "purchases": s[["ticket_time", "receipt_id", "item_name", "qty",
                            "amount", "gross_profit"]],
            "payments": coll[["ticket_time", "receipt_id", "amount"]],
        }

    # =====================================================================
    #  PAGE 4 - MONEY OWED
    # =====================================================================

    def receivables(self) -> pd.DataFrame:
        """
        Every customer who owes money, largest first, with how long it has
        been since they last bought anything.

        Only positive balances count as money owed. A couple of customers
        sit slightly in credit - they have paid more than they have taken -
        and those are shown separately rather than being netted off, because
        you cannot spend somebody else's credit.
        """
        c = self.customers[~self.customers["is_walkin"]].copy()
        owed = c[c["balance"] > 0].copy()
        if owed.empty:
            return pd.DataFrame()

        s = self.sales[self.sales["customer_id"] != self.walkin_id]
        last_buy = s.groupby("customer_id")["ticket_time"].max().rename("last_purchase")
        rev = s.groupby("customer_id")["amount"].sum().rename("revenue")
        cut = self.now - datetime.timedelta(days=365)
        rev12 = s[s["ticket_time"] >= cut].groupby("customer_id")["amount"].sum().rename("revenue_12m")

        owed = owed.merge(last_buy, on="customer_id", how="left")
        owed = owed.merge(rev, on="customer_id", how="left")
        owed = owed.merge(rev12, on="customer_id", how="left")
        owed[["revenue", "revenue_12m"]] = owed[["revenue", "revenue_12m"]].fillna(0.0)

        owed["days_since_purchase"] = (self.now - owed["last_purchase"]).dt.days
        total = owed["balance"].sum()
        owed["share_of_receivables"] = owed["balance"] / total if total else 0.0

        # How many months of their own trade the balance represents. A big
        # balance from a big customer is normal; a big balance from a small
        # one is not.
        monthly = owed["revenue_12m"] / 12.0
        owed["months_of_their_trade"] = np.where(monthly > 0, owed["balance"] / monthly, np.nan)

        risk_days = float(self.t("customers.collection_risk_days", 60))
        owed["at_risk"] = (owed["days_since_purchase"].fillna(9999) > risk_days)
        owed["credit_risk"] = self._credit_risk_tier_series(
            owed["balance"], owed["customer_id"], owed["days_since_purchase"])

        cols = ["customer_id", "customer_no", "customer_name", "phone", "city",
                "balance", "share_of_receivables", "last_purchase",
                "days_since_purchase", "revenue", "revenue_12m",
                "months_of_their_trade", "at_risk", "credit_risk"]
        return owed[cols].sort_values("balance", ascending=False).reset_index(drop=True)

    def receivables_summary(self) -> dict[str, Any]:
        r = self.receivables()
        c = self.customers[~self.customers["is_walkin"]]
        credits = c[c["balance"] < 0]

        if r.empty:
            return {"total": 0.0, "customers": 0, "top_share": 0.0, "top_name": "",
                    "at_risk_total": 0.0, "at_risk_customers": 0,
                    "credit_balances": float(credits["balance"].sum()),
                    "credit_customers": int(len(credits))}

        risky = r[r["at_risk"]]
        return {
            "total": float(r["balance"].sum()),
            "customers": int(len(r)),
            "top_share": float(r.iloc[0]["share_of_receivables"]),
            "top_name": str(r.iloc[0]["customer_name"]),
            "top_balance": float(r.iloc[0]["balance"]),
            "at_risk_total": float(risky["balance"].sum()),
            "at_risk_customers": int(len(risky)),
            "credit_balances": float(credits["balance"].sum()),
            "credit_customers": int(len(credits)),
        }

    # =====================================================================
    #  PAGE 5 - INVENTORY
    # =====================================================================

    @cached_property
    def item_movement(self) -> pd.DataFrame:
        """
        How fast each product sells, measured over the recent window set in
        config.yaml, and how many months of stock that leaves.
        """
        window = int(self.t("inventory.run_rate_days", 90))
        cut = self.now - datetime.timedelta(days=window)
        recent = self.sales[self.sales["ticket_time"] >= cut]

        g = recent.groupby("item_id").agg(
            qty_window=("qty", "sum"),
            revenue_window=("amount", "sum"),
            gross_profit_window=("gross_profit", "sum"),
            tickets_window=("receipt_id", "nunique"),
        )

        all_time = self.sales.groupby("item_id").agg(
            qty_all=("qty", "sum"),
            revenue_all=("amount", "sum"),
            gross_profit_all=("gross_profit", "sum"),
            last_sale=("ticket_time", "max"),
        )
        known = self.sales[self.sales["cost_known"]].groupby("item_id").agg(
            revenue_measurable=("amount", "sum"),
            gross_profit_measurable=("gross_profit", "sum"))

        df = self.items.merge(g, on="item_id", how="left") \
                       .merge(all_time, on="item_id", how="left") \
                       .merge(known, on="item_id", how="left")

        for c in ("qty_window", "revenue_window", "gross_profit_window",
                  "tickets_window", "qty_all", "revenue_all", "gross_profit_all"):
            df[c] = df[c].fillna(0.0)

        # Units per month, from the recent window only.
        df["monthly_rate"] = df["qty_window"] / (window / DAYS_PER_MONTH)
        df["weekly_rate"] = df["qty_window"] / (window / 7.0)

        df["cover_months"] = np.where(df["monthly_rate"] > 0,
                                      df["stock"].fillna(0) / df["monthly_rate"],
                                      np.inf)
        # A product with stock but no recent sales has no meaningful cover.
        df.loc[(df["monthly_rate"] <= 0), "cover_months"] = np.inf

        df["margin_pct"] = np.where(df["revenue_measurable"].fillna(0) > 0,
                                    df["gross_profit_measurable"] / df["revenue_measurable"],
                                    np.nan)

        # WHICH "LAST SOLD" DATE TO BELIEVE.
        #
        # The product record has a LastSold field, but the POS does not always
        # keep it up to date: in this database 60 products show a LastSold
        # that is older than sales actually recorded on the tickets, one of
        # them by more than a year (a product with 432 units in stock that the
        # field claims has not sold since August 2025 but which sold last
        # week). Trusting that field would wrongly condemn roughly 584,000 DZD
        # of perfectly live stock as dead.
        #
        # So the date of the most recent ticket the product appears on wins,
        # and the product's own field is only a fallback for products with no
        # ticket history at all.
        df["last_sale_effective"] = df["last_sale"].fillna(df["last_sold"])
        df["last_sold_field_stale_days"] = (df["last_sale"] - df["last_sold"]).dt.days
        df["days_since_sale"] = (self.now - df["last_sale_effective"]).dt.days
        return df

    def catalog(self) -> pd.DataFrame:
        """
        The full product catalogue for the Stock catalog screen: reference,
        name, family/brand, quantity, cost, price. A thin, sorted view over
        `items` - no new business logic, just what a search screen needs.
        """
        cols = ["item_id", "item_no", "item_name", "family_name", "stock",
                "cost", "price", "inactive"]
        return self.items[cols].sort_values("item_name").reset_index(drop=True)

    def inventory_summary(self) -> dict[str, Any]:
        """The stock picture in one set of numbers."""
        it = self.item_movement
        dead_days = float(self.t("inventory.dead_stock_days", 120))
        slow_days = float(self.t("inventory.slow_stock_days", 60))

        in_stock = it[it["stock"].fillna(0) > 0]
        total_value = float(it["stock_value"].sum())

        days = in_stock["days_since_sale"]
        dead = in_stock[days.isna() | (days > dead_days)]
        slow = in_stock[(days > slow_days) & (days <= dead_days)]
        healthy = in_stock[(days <= slow_days)]

        neg = it[it["stock"].fillna(0) < 0]

        return {
            "total_value": total_value,
            "total_items": int(len(it)),
            "items_in_stock": int(len(in_stock)),
            "dead_value": float(dead["stock_value"].sum()),
            "dead_items": int(len(dead)),
            "dead_share": (float(dead["stock_value"].sum()) / total_value) if total_value else 0.0,
            "slow_value": float(slow["stock_value"].sum()),
            "slow_items": int(len(slow)),
            "healthy_value": float(healthy["stock_value"].sum()),
            "healthy_items": int(len(healthy)),
            "negative_items": int(len(neg)),
            "negative_value": float(neg["stock_value"].sum()),
            "negative_units": float(neg["stock"].sum()),
            "zero_cost_items": int(((it["cost"].fillna(0) <= 0) &
                                    (it["stock"].fillna(0) != 0)).sum()),
            "dead_stock_days": dead_days,
        }

    def dead_stock(self) -> pd.DataFrame:
        """
        Products sitting in stock that nobody has bought for a long time.
        This is cash on a shelf. Never-sold products count as dead.
        """
        dead_days = float(self.t("inventory.dead_stock_days", 120))
        it = self.item_movement
        d = it[(it["stock"].fillna(0) > 0) &
               (it["days_since_sale"].isna() | (it["days_since_sale"] > dead_days))].copy()
        cols = ["item_id", "item_no", "item_name", "family_name", "stock", "cost",
                "price", "stock_value", "last_sale_effective", "days_since_sale",
                "revenue_all", "qty_all"]
        return d[cols].sort_values("stock_value", ascending=False).reset_index(drop=True)

    def stockout_risk(self) -> pd.DataFrame:
        """
        Products that are selling now and are about to run out, ranked by
        how much revenue a week of being out of stock would cost.
        """
        cover = float(self.t("inventory.stockout_cover_months", 0.75))
        it = self.item_movement
        r = it[(it["qty_window"] > 0) & (it["cover_months"] < cover)].copy()
        if r.empty:
            return pd.DataFrame()

        r["days_of_cover"] = r["cover_months"] * DAYS_PER_MONTH
        r["weekly_revenue"] = r["weekly_rate"] * r["price"].fillna(0)
        r["weekly_profit"] = r["weekly_rate"] * (r["price"].fillna(0) - r["cost"].fillna(0))
        r["suggested_order_qty"] = np.ceil(
            np.maximum(r["monthly_rate"] * 2 - r["stock"].fillna(0), 0))

        cols = ["item_id", "item_no", "item_name", "family_name", "stock",
                "monthly_rate", "weekly_rate", "cover_months", "days_of_cover",
                "price", "cost", "weekly_revenue", "weekly_profit",
                "suggested_order_qty", "last_purchased"]
        return r[cols].sort_values("weekly_revenue", ascending=False).reset_index(drop=True)

    def overstock(self) -> pd.DataFrame:
        """Products with far more stock than the rate of sale justifies."""
        cover = float(self.t("inventory.overstock_cover_months", 12.0))
        it = self.item_movement
        r = it[(it["qty_window"] > 0) &
               (it["cover_months"] > cover) &
               (np.isfinite(it["cover_months"])) &
               (it["stock"].fillna(0) > 0)].copy()
        if r.empty:
            return pd.DataFrame()
        # Stock beyond a sensible level, and what that excess costs.
        target_months = cover / 2.0
        r["excess_units"] = np.maximum(r["stock"] - r["monthly_rate"] * target_months, 0)
        r["excess_value"] = r["excess_units"] * r["cost"].fillna(0)
        cols = ["item_id", "item_no", "item_name", "family_name", "stock",
                "monthly_rate", "cover_months", "cost", "stock_value",
                "excess_units", "excess_value"]
        return r[cols].sort_values("excess_value", ascending=False).reset_index(drop=True)

    def negative_stock(self) -> pd.DataFrame:
        """
        Products recorded as having less than nothing in stock. This is
        always a mistake in the POS - goods sold without being booked in -
        and it means the stock figure for that product cannot be trusted.
        """
        it = self.item_movement
        n = it[it["stock"].fillna(0) < 0].copy()
        cols = ["item_id", "item_no", "item_name", "family_name", "stock",
                "cost", "stock_value", "last_sale_effective", "last_purchased"]
        return n[cols].sort_values("stock").reset_index(drop=True)

    def abc_classification(self) -> pd.DataFrame:
        """
        Sort products by how much revenue they bring in and split them into
        three groups: A is the top 80% of revenue, B the next 15%, C the
        last 5%. The C group is usually most of the catalogue.
        """
        it = self.item_movement.copy()
        it = it.sort_values("revenue_all", ascending=False).reset_index(drop=True)
        total = it["revenue_all"].sum()
        if not total:
            return pd.DataFrame()
        it["cumulative_share"] = it["revenue_all"].cumsum() / total
        it["revenue_share"] = it["revenue_all"] / total
        it["abc"] = np.where(it["cumulative_share"] <= 0.80, "A",
                             np.where(it["cumulative_share"] <= 0.95, "B", "C"))
        cols = ["item_id", "item_no", "item_name", "family_name", "abc",
                "revenue_all", "revenue_share", "cumulative_share",
                "gross_profit_all", "margin_pct", "stock", "stock_value",
                "cover_months", "days_since_sale"]
        return it[cols]

    def abc_summary(self) -> pd.DataFrame:
        abc = self.abc_classification()
        if abc.empty:
            return pd.DataFrame()
        g = (abc.groupby("abc", as_index=False)
             .agg(items=("item_id", "count"),
                  revenue=("revenue_all", "sum"),
                  gross_profit=("gross_profit_all", "sum"),
                  stock_value=("stock_value", "sum")))
        g["revenue_share"] = g["revenue"] / g["revenue"].sum()
        g["items_share"] = g["items"] / g["items"].sum()
        return g.sort_values("abc")

    def stock_by_family(self) -> pd.DataFrame:
        it = self.item_movement
        g = (it.groupby("family_name", as_index=False)
             .agg(items=("item_id", "count"),
                  stock_value=("stock_value", "sum"),
                  revenue=("revenue_all", "sum"),
                  gross_profit=("gross_profit_all", "sum")))
        return g.sort_values("stock_value", ascending=False).reset_index(drop=True)

    def shrinkage_events(self) -> pd.DataFrame:
        """
        Stock that went missing, one row per physical stocktake ("StockTake")
        event - net cost variance, and units over/under, for the whole
        count.

        This is deliberately event-level, not per-product. R.Lynx's
        `StockTake` table only stores aggregate over/under totals for the
        whole count; there is no per-line detail table (`StockTakeEntry`)
        in this database to say which specific products drove the
        variance - confirmed absent even at the schema-definition level,
        not just "no rows yet". Don't try to join this to individual
        products; the data to do so honestly does not exist here.

        This is a different thing from dead stock (`dead_stock()`): dead
        stock is unsold but still physically present; shrinkage is stock
        that a physical count found to be missing (or, when the sign is
        positive, present but not in the books - "count over").
        """
        if not self._has_table("StockTake"):
            return pd.DataFrame()

        st = self._read("""
            SELECT ID AS stocktake_id, StockTakeNo AS stocktake_no,
                   Status AS status, DateOpened AS date_opened,
                   DateClosed AS date_closed, DateCalculated AS date_calculated,
                   TotalCountOver AS units_over, TotalCountUnder AS units_under,
                   TotalCountCounted AS units_counted,
                   TotalCostOver AS cost_over, TotalCostUnder AS cost_under,
                   TotalCostNet AS cost_net
            FROM StockTake
        """)
        if st.empty:
            return st

        for col in ("units_over", "units_under", "units_counted",
                    "cost_over", "cost_under", "cost_net"):
            st[col] = pd.to_numeric(st[col], errors="coerce")
        for col in ("date_opened", "date_closed", "date_calculated"):
            st[col] = pd.to_datetime(st[col], errors="coerce")

        # Sort by whichever date the event actually has - most stocktakes
        # will have a close date, but fall back gracefully if not.
        st["event_date"] = st["date_closed"].fillna(st["date_calculated"]).fillna(st["date_opened"])
        return st.sort_values("event_date", ascending=False).reset_index(drop=True)

    # =====================================================================
    #  PAGE 6 - PRODUCTS AND MARGIN
    # =====================================================================

    def product_margin(self) -> pd.DataFrame:
        """Every product that has ever sold, with what it actually earns."""
        it = self.item_movement
        p = it[it["revenue_all"] != 0].copy()
        if p.empty:
            return pd.DataFrame()

        total_rev = p["revenue_all"].sum()
        p["revenue_share"] = p["revenue_all"] / total_rev if total_rev else 0.0
        p["cost_measurable"] = p["revenue_measurable"].notna()
        p["current_margin_pct"] = np.where(
            p["price"].fillna(0) > 0,
            (p["price"].fillna(0) - p["cost"].fillna(0)) / p["price"].replace(0, np.nan),
            np.nan)

        cols = ["item_id", "item_no", "item_name", "family_name",
                "revenue_all", "revenue_share", "gross_profit_all", "margin_pct",
                "current_margin_pct", "qty_all", "price", "cost", "stock",
                "stock_value", "days_since_sale", "cost_measurable"]
        return p[cols].sort_values("revenue_all", ascending=False).reset_index(drop=True)

    def family_margin(self) -> pd.DataFrame:
        """The same picture one level up, by brand / category."""
        s = self.sales.merge(self.items[["item_id", "family_name"]],
                             on="item_id", how="left")
        s["family_name"] = s["family_name"].fillna("—")
        if s.empty:
            return pd.DataFrame()

        known = s[s["cost_known"]]
        g = s.groupby("family_name", as_index=False).agg(
            revenue=("amount", "sum"),
            gross_profit=("gross_profit", "sum"),
            units=("qty", "sum"),
            lines=("entry_id", "count"))
        gk = known.groupby("family_name", as_index=False).agg(
            revenue_measurable=("amount", "sum"),
            gross_profit_measurable=("gross_profit", "sum"))
        g = g.merge(gk, on="family_name", how="left")
        g["margin_pct"] = np.where(g["revenue_measurable"].fillna(0) > 0,
                                   g["gross_profit_measurable"] / g["revenue_measurable"],
                                   np.nan)
        g["revenue_share"] = g["revenue"] / g["revenue"].sum()

        stock = self.items.groupby("family_name", as_index=False)["stock_value"].sum()
        g = g.merge(stock, on="family_name", how="left")
        g["stock_value"] = g["stock_value"].fillna(0.0)
        return g.sort_values("revenue", ascending=False).reset_index(drop=True)

    def family_margin_outliers(self, threshold_pp: float | None = None) -> pd.DataFrame:
        """
        Products that look fine in isolation but are outliers next to their
        own brand/category: their margin trails their family's average by
        more than `threshold_pp` percentage points.

        This catches mispriced items a plain "below X% margin" rule would
        miss - a family that mostly runs at 40% margin with one product at
        30% is a real outlier even though 30% would look healthy on its own.
        """
        pp = (threshold_pp if threshold_pp is not None
              else float(self.t("margin.family_benchmark_pp", 15)))

        p = self.product_margin()
        fam = self.family_margin()
        if p.empty or fam.empty:
            return pd.DataFrame()

        merged = p.merge(
            fam[["family_name", "margin_pct"]].rename(
                columns={"margin_pct": "family_margin_pct"}),
            on="family_name", how="left")
        merged = merged[merged["margin_pct"].notna() & merged["family_margin_pct"].notna()]
        merged["gap_pp"] = (merged["family_margin_pct"] - merged["margin_pct"]) * 100

        out = merged[merged["gap_pp"] >= pp].copy()
        if out.empty:
            return out
        cols = ["item_id", "item_no", "item_name", "family_name",
                "margin_pct", "family_margin_pct", "gap_pp",
                "revenue_all", "price", "cost"]
        return out[cols].sort_values("gap_pp", ascending=False).reset_index(drop=True)

    def new_arrivals(self, days: int | None = None) -> pd.DataFrame:
        """
        Products added to the catalogue in a recent window - newest first.
        For a "new arrivals" social-media post, not a sales metric.
        """
        window = days if days is not None else int(self.cfg.get("catalog.new_arrivals_days", 7))
        it = self.items[self.items["date_created"].notna()]
        cutoff = self.now - datetime.timedelta(days=window)
        recent = it[it["date_created"] >= cutoff]
        cols = ["item_id", "item_no", "item_name", "family_name", "price",
                "date_created"]
        return recent[cols].sort_values("date_created", ascending=False).reset_index(drop=True)

    def high_revenue_low_margin(self, top_n: int = 40) -> pd.DataFrame:
        """
        Products that sell a lot and earn little. These are where a small
        price change is worth the most money.
        """
        p = self.product_margin()
        if p.empty:
            return pd.DataFrame()
        healthy = float(self.t("margin.healthy_gross_margin", 0.10))
        r = p[(p["margin_pct"].notna()) & (p["margin_pct"] < healthy)].copy()
        if r.empty:
            return pd.DataFrame()
        # What two extra points of margin would have been worth.
        r["uplift_2pts"] = r["revenue_all"] * 0.02
        r["gap_to_healthy"] = (healthy - r["margin_pct"]) * r["revenue_all"]
        return (r.sort_values("revenue_all", ascending=False)
                .head(top_n).reset_index(drop=True))

    def selling_below_cost(self) -> pd.DataFrame:
        """
        Products sold for less than they cost. Every one of these loses money
        on every unit sold.
        """
        s = self.sales[self.sales["cost_known"]].copy()
        s = s[s["qty"] > 0]
        below = s[s["gross_profit"] < 0]
        if below.empty:
            return pd.DataFrame()

        g = (below.groupby(["item_id", "item_name"], as_index=False)
             .agg(loss=("gross_profit", "sum"),
                  units=("qty", "sum"),
                  lines=("entry_id", "count"),
                  revenue=("amount", "sum"),
                  last_time=("ticket_time", "max")))
        g["loss_per_unit"] = np.where(g["units"] > 0, g["loss"] / g["units"], 0.0)

        cur = self.items[["item_id", "price", "cost", "stock", "family_name"]]
        g = g.merge(cur, on="item_id", how="left")
        g["still_below_cost"] = g["price"].fillna(0) < g["cost"].fillna(0)
        return g.sort_values("loss").reset_index(drop=True)

    def price_cost_drift(self) -> pd.DataFrame:
        """
        Products whose cost has gone up while the selling price has stayed
        where it was. This is how margin disappears without anyone noticing.

        Built from the price-change history the POS keeps, comparing the
        earliest recorded cost and price with what they are today.
        """
        if not self._has_table("PricingUpdateEntry"):
            return pd.DataFrame()

        pu = self._read("""
            SELECT ItemID AS item_id, Cost AS cost, Price AS price,
                   PricingUpdateID AS update_id, ID AS row_id
            FROM PricingUpdateEntry
            WHERE ItemID > 0
        """)
        if pu.empty:
            return pd.DataFrame()
        pu["cost"] = pd.to_numeric(pu["cost"], errors="coerce")
        pu["price"] = pd.to_numeric(pu["price"], errors="coerce")

        # Try to attach a date from the price-change header.
        if self._has_table("PricingUpdate"):
            head_cols = self._columns_of("PricingUpdate")
            # ADate is the date the price change was applied in this POS.
            date_col = next((c for c in ("ADate", "DateValidated", "DateOpened",
                                         "Time", "DateCreated", "Date",
                                         "DateLastUpdated") if c in head_cols), None)
            if date_col:
                head = self._read(
                    f'SELECT ID AS update_id, "{date_col}" AS when_changed FROM PricingUpdate')
                head["when_changed"] = pd.to_datetime(head["when_changed"], errors="coerce")
                pu = pu.merge(head, on="update_id", how="left")

        if "when_changed" not in pu.columns:
            pu["when_changed"] = pd.NaT
        # Where there is no date, fall back to the order the rows were written.
        pu = pu.sort_values(["item_id", "when_changed", "row_id"])

        first = pu.groupby("item_id").first().rename(
            columns={"cost": "first_cost", "price": "first_price",
                     "when_changed": "first_changed"})
        last = pu.groupby("item_id").last().rename(
            columns={"cost": "last_cost", "price": "last_price",
                     "when_changed": "last_changed"})
        changes = pu.groupby("item_id").size().rename("price_changes")

        df = first[["first_cost", "first_price", "first_changed"]].join(
            last[["last_cost", "last_price", "last_changed"]]).join(changes).reset_index()

        cur = self.item_movement[["item_id", "item_no", "item_name", "family_name",
                                  "cost", "price", "revenue_all", "qty_all",
                                  "margin_pct", "stock", "days_since_sale"]]
        df = df.merge(cur, on="item_id", how="inner")

        df["cost_change_pct"] = np.where(df["first_cost"].fillna(0) > 0,
                                         (df["cost"] - df["first_cost"]) / df["first_cost"],
                                         np.nan)
        df["price_change_pct"] = np.where(df["first_price"].fillna(0) > 0,
                                          (df["price"] - df["first_price"]) / df["first_price"],
                                          np.nan)
        df["margin_then"] = np.where(df["first_price"].fillna(0) > 0,
                                     (df["first_price"] - df["first_cost"]) / df["first_price"],
                                     np.nan)
        df["margin_now"] = np.where(df["price"].fillna(0) > 0,
                                    (df["price"] - df["cost"]) / df["price"],
                                    np.nan)
        df["margin_drift"] = df["margin_now"] - df["margin_then"]

        # Yearly cost of the drift at the current rate of sale.
        window = int(self.t("inventory.run_rate_days", 90))
        rate = self.item_movement.set_index("item_id")["qty_window"] / (window / 365.0)
        df["annual_units"] = df["item_id"].map(rate).fillna(0.0)
        df["annual_margin_lost"] = (-df["margin_drift"].fillna(0)) * \
                                   df["price"].fillna(0) * df["annual_units"]
        return df.sort_values("annual_margin_lost", ascending=False).reset_index(drop=True)

    def silent_margin_erosion(self) -> pd.DataFrame:
        """
        The specific case worth acting on: cost up meaningfully, price not
        moved at all, and the product still sells.
        """
        d = self.price_cost_drift()
        if d.empty:
            return pd.DataFrame()
        creep = float(self.t("pricing.cost_creep_warn", 0.05))
        r = d[(d["cost_change_pct"] > creep) &
              (d["price_change_pct"].fillna(0).abs() < 0.01) &
              (d["annual_units"] > 0)].copy()
        return r.sort_values("annual_margin_lost", ascending=False).reset_index(drop=True)

    def product_profile(self, item_id: int) -> dict[str, Any] | None:
        """
        Everything about one product, for the drill-down page: its row from
        `item_movement` (stock, movement, margin), how its family is doing
        for comparison, and its sales history. Filtered to one ID, not a
        new aggregation. Returns None if the item does not exist.
        """
        it = self.item_movement
        row = it[it["item_id"] == item_id]
        if row.empty:
            return None
        summary_row = row.iloc[0].to_dict()

        family_row = None
        fam_name = summary_row.get("family_name")
        if fam_name:
            fam = self.family_margin()
            f = fam[fam["family_name"] == fam_name]
            if not f.empty:
                family_row = f.iloc[0].to_dict()

        s = (self.sales[self.sales["item_id"] == item_id]
             .sort_values("ticket_time", ascending=False))

        return {
            "summary": summary_row,
            "family": family_row,
            "sales_history": s[["ticket_time", "receipt_id", "qty", "price",
                                "amount", "gross_profit"]],
        }

    # =====================================================================
    #  PAGE 7 - SUPPLIERS
    # =====================================================================

    def supplier_summary(self) -> pd.DataFrame:
        """
        What each supplier is worth to the business.

        Two different measures are given, because they answer two different
        questions and neither is complete on its own:

          purchase_value - what you have actually bought from them, from the
                           purchase lines that could be traced to a supplier
          item_revenue   - what the products they supply have earned you

        Use `purchase_coverage()` alongside this. About a fifth of purchase
        lines cannot be traced to any supplier, so purchase_value understates
        every supplier by an unknown amount and must not be read as a total.
        """
        sup = self.suppliers.copy()
        if sup.empty:
            return pd.DataFrame()

        pur = self.purchases
        if not pur.empty and pur["supplier_id"].notna().any():
            g = (pur.dropna(subset=["supplier_id"])
                 .groupby("supplier_id", as_index=False)
                 .agg(purchase_value=("amount", "sum"),
                      purchase_lines=("entry_id", "count"),
                      orders=("purchase_id", "nunique"),
                      items_bought=("item_id", "nunique")))
            g["supplier_id"] = g["supplier_id"].astype(int)
            sup = sup.merge(g, on="supplier_id", how="left")
        else:
            for c in ("purchase_value", "purchase_lines", "orders", "items_bought"):
                sup[c] = np.nan

        # What the products this supplier provides have earned. This is the
        # best measure of how much each supplier actually matters, and it
        # does not depend on the incomplete purchase link.
        if self._has_table("SupplierItem"):
            si = self._read("SELECT SupplierID AS supplier_id, ItemID AS item_id "
                            "FROM SupplierItem").drop_duplicates()
            rev = self.item_movement[["item_id", "revenue_all", "gross_profit_all",
                                      "stock_value", "revenue_window"]]
            joined = si.merge(rev, on="item_id", how="left")
            g2 = (joined.groupby("supplier_id", as_index=False)
                  .agg(item_revenue=("revenue_all", "sum"),
                       item_gross_profit=("gross_profit_all", "sum"),
                       item_stock_value=("stock_value", "sum"),
                       item_revenue_recent=("revenue_window", "sum"),
                       items_supplied=("item_id", "nunique")))
            sup = sup.merge(g2, on="supplier_id", how="left")

        for c in ("item_revenue", "item_gross_profit", "item_stock_value",
                  "item_revenue_recent", "purchase_value"):
            if c in sup.columns:
                sup[c] = sup[c].fillna(0.0)

        sup["days_since_purchase"] = (self.now - sup["last_purchase"]).dt.days
        sup["margin_pct"] = np.where(sup["item_revenue"] > 0,
                                     sup["item_gross_profit"] / sup["item_revenue"], np.nan)

        for col, share in (("purchase_value", "purchase_share"),
                           ("item_revenue", "revenue_share")):
            if col in sup.columns:
                total = sup[col].sum()
                sup[share] = sup[col] / total if total else 0.0

        # The headline ordering uses whichever measure is actually populated.
        sort_col = "purchase_value" if sup.get("purchase_value") is not None and \
            float(sup["purchase_value"].sum()) > 0 else "item_revenue"
        return sup.sort_values(sort_col, ascending=False).reset_index(drop=True)

    def supplier_transactions(self) -> pd.DataFrame:
        """
        Purchases grouped into transactions, the same way tickets group
        ticket lines - one row per `purchase_id`, for the Suppliers
        drill-down list. Supplier and date are blank exactly as often as
        `purchase_coverage()` already reports they are missing - nothing
        here is guessed.
        """
        p = self.purchases
        if p.empty:
            return p
        g = (p.groupby("purchase_id", as_index=False)
             .agg(supplier_id=("supplier_id", "first"),
                  purchase_time=("purchase_time", "first"),
                  lines=("entry_id", "count"),
                  total=("amount", "sum"),
                  items=("item_id", "nunique")))
        g = g.merge(self.suppliers[["supplier_id", "supplier_name"]],
                    on="supplier_id", how="left")
        g["supplier_name"] = g["supplier_name"].fillna("")
        return g.sort_values("purchase_time", ascending=False,
                             na_position="last").reset_index(drop=True)

    def purchase_detail(self, purchase_id: Any) -> dict[str, Any] | None:
        """One purchase's lines, for the Suppliers drill-down detail page."""
        p = self.purchases[self.purchases["purchase_id"] == purchase_id]
        if p.empty:
            return None
        header = {
            "purchase_id": purchase_id,
            "supplier_id": p.iloc[0]["supplier_id"],
            "purchase_time": p.iloc[0]["purchase_time"],
            "total": float(p["amount"].sum()),
            "lines": int(len(p)),
        }
        sup = self.suppliers[self.suppliers["supplier_id"] == header["supplier_id"]]
        header["supplier_name"] = sup.iloc[0]["supplier_name"] if not sup.empty else ""

        return {
            "header": header,
            "lines": p[["entry_id", "item_id", "item_name", "qty", "price",
                       "cost", "new_cost", "new_stock", "amount"]].sort_values("entry_id"),
        }

    def supplier_cost_trend(self) -> pd.DataFrame:
        """
        How much you spent buying stock each month, and the average cost per
        unit bought. Covers only the purchases that carry a date.
        """
        pur = self.purchases
        if pur.empty or pur["purchase_time"].isna().all():
            return pd.DataFrame()
        p = pur.dropna(subset=["purchase_time"]).copy()
        p["month"] = p["purchase_time"].dt.to_period("M").dt.to_timestamp()
        g = (p.groupby("month", as_index=False)
             .agg(purchase_value=("amount", "sum"),
                  units=("qty", "sum"),
                  lines=("entry_id", "count"),
                  orders=("purchase_id", "nunique")))
        g["avg_unit_cost"] = np.where(g["units"] > 0, g["purchase_value"] / g["units"], np.nan)
        g["month_label"] = g["month"].dt.strftime("%Y-%m")
        return g

    def supplier_cost_by_supplier(self, top_n: int = 8) -> pd.DataFrame:
        """Average unit cost per month for the suppliers you buy most from."""
        pur = self.purchases
        if pur.empty or pur["purchase_time"].isna().all():
            return pd.DataFrame()
        p = pur.dropna(subset=["purchase_time", "supplier_id"]).copy()
        if p.empty:
            return pd.DataFrame()
        p["month"] = p["purchase_time"].dt.to_period("M").dt.to_timestamp()
        biggest = (p.groupby("supplier_id")["amount"].sum()
                   .sort_values(ascending=False).head(top_n).index)
        p = p[p["supplier_id"].isin(biggest)]
        g = (p.groupby(["supplier_id", "month"], as_index=False)
             .agg(purchase_value=("amount", "sum"), units=("qty", "sum")))
        g["avg_unit_cost"] = np.where(g["units"] > 0, g["purchase_value"] / g["units"], np.nan)
        g["supplier_id"] = g["supplier_id"].astype(int)
        g = g.merge(self.suppliers[["supplier_id", "supplier_name"]],
                    on="supplier_id", how="left")
        g["month_label"] = g["month"].dt.strftime("%Y-%m")
        return g

    def purchase_gaps(self) -> pd.DataFrame:
        """
        How long it usually is between orders from each supplier.

        This is a stand-in for lead time, not a measurement of it: the POS
        records no delivery dates, so the best we can say is how often you
        reorder. Only suppliers with at least two dated orders appear.
        """
        pur = self.purchases
        if pur.empty or "supplier_id" not in pur.columns or pur["supplier_id"].isna().all() \
                or pur["purchase_time"].isna().all():
            return pd.DataFrame()

        p = pur.dropna(subset=["supplier_id", "purchase_time"]).copy()
        dates = (p.groupby(["supplier_id", "purchase_id"], as_index=False)["purchase_time"]
                 .min().sort_values(["supplier_id", "purchase_time"]))
        dates["gap_days"] = dates.groupby("supplier_id")["purchase_time"].diff().dt.days

        g = (dates.groupby("supplier_id", as_index=False)
             .agg(orders=("purchase_id", "count"),
                  avg_gap_days=("gap_days", "mean"),
                  median_gap_days=("gap_days", "median"),
                  last_order=("purchase_time", "max")))
        g = g[g["orders"] >= 2]
        if g.empty:
            return pd.DataFrame()
        g["supplier_id"] = g["supplier_id"].astype(int)
        g = g.merge(self.suppliers[["supplier_id", "supplier_name", "phone", "balance"]],
                    on="supplier_id", how="left")
        g["days_since_last"] = (self.now - g["last_order"]).dt.days
        # "Overdue" here means: you normally reorder more often than this.
        g["overdue"] = g["days_since_last"] > (g["median_gap_days"] * 1.5)
        return g.sort_values("orders", ascending=False).reset_index(drop=True)

    # =====================================================================
    #  PAGE 8 - CASH / P&L
    # =====================================================================

    def income_statement(self, days: int | None = None,
                          start: datetime.date | None = None,
                          end: datetime.date | None = None) -> dict[str, Any]:
        """
        Revenue, cost of goods sold and gross profit, month by month - the
        shape of a simple income statement.

        `days` uses the same precise cutoff as `headline()`, so
        `income_statement(days=365)["revenue"]` matches
        `headline(days=365)["revenue"]` exactly. `start`/`end` are whole-day
        boundaries instead, for the date-range picker. Give at most one of
        the two; with neither, this is all-time.
        """
        if days is not None:
            s = self._window(self.sales, days)
        elif start is not None or end is not None:
            s = self._window_range(self.sales, start, end)
        else:
            s = self.sales

        if s.empty:
            return {"by_month": pd.DataFrame(), "revenue": 0.0, "cogs": 0.0,
                    "gross_profit": 0.0, "margin_pct": None}

        known = s[s["cost_known"]]
        rev = s.groupby("month")["amount"].sum().rename("revenue")
        gp = s.groupby("month")["gross_profit"].sum().rename("gross_profit")
        rev_k = known.groupby("month")["amount"].sum().rename("revenue_measurable")
        gp_k = known.groupby("month")["gross_profit"].sum().rename("gross_profit_measurable")

        by_month = pd.concat([rev, gp, rev_k, gp_k], axis=1).fillna(0.0)
        by_month = by_month.sort_index().reset_index()
        by_month["cogs"] = by_month["revenue"] - by_month["gross_profit"]
        by_month["margin_pct"] = np.where(
            by_month["revenue_measurable"] > 0,
            by_month["gross_profit_measurable"] / by_month["revenue_measurable"],
            np.nan)
        by_month["month_label"] = by_month["month"].dt.strftime("%Y-%m")

        revenue = float(by_month["revenue"].sum())
        gross_profit = float(by_month["gross_profit"].sum())
        rev_known_total = float(by_month["revenue_measurable"].sum())
        gp_known_total = float(by_month["gross_profit_measurable"].sum())

        return {
            "by_month": by_month,
            "revenue": revenue,
            "cogs": revenue - gross_profit,
            "gross_profit": gross_profit,
            "margin_pct": (gp_known_total / rev_known_total) if rev_known_total else None,
        }

    def cash_position(self, days: int | None = None,
                       start: datetime.date | None = None,
                       end: datetime.date | None = None) -> dict[str, Any]:
        """
        How the money that came in was actually paid - cash, cheque,
        transfer or put on the customer's account - both as one total split
        for the window and trended month by month.
        """
        if days is not None:
            tk = self._window(self.tickets, days)
        elif start is not None or end is not None:
            tk = self._window_range(self.tickets, start, end)
        else:
            tk = self.tickets

        totals = {
            "cash": float(tk["cash"].sum()),
            "cheque": float(tk["cheque"].sum()),
            "transfer": float(tk["transfer"].sum()),
            "credit": float(tk["credit_account"].sum()),
        }

        if tk.empty:
            by_month = pd.DataFrame()
        else:
            by_month = (tk.groupby("month", as_index=False)
                        .agg(cash=("cash", "sum"), cheque=("cheque", "sum"),
                             transfer=("transfer", "sum"),
                             credit=("credit_account", "sum"))
                        .sort_values("month"))
            by_month["month_label"] = by_month["month"].dt.strftime("%Y-%m")
            by_month["total"] = (by_month["cash"] + by_month["cheque"]
                                  + by_month["transfer"] + by_month["credit"])

        return {"totals": totals, "total": sum(totals.values()), "by_month": by_month}

    def till_reconciliation(self) -> pd.DataFrame:
        """
        For each cash-register session ("batch"), the expected position
        (opening float + actual sales recorded against that session, per
        tender type) against whatever closing figure R.Lynx itself
        recorded.

        The "expected" side is recomputed from the real tickets
        (`Receipt.BatchID`), never trusted from `Batch`'s own `*Shift`
        columns - verified against the live data that those sit frozen at
        their opening value (0) on a session that has stayed open a long
        time, against ~39 million DZD of real cash sales that happened
        under it. Same reason the rest of this file never trusts a
        POS-computed header total over the underlying rows.

        Two honesty notes, deliberately not smoothed over:
        - `Batch`'s `*Close` columns are populated by the POS when a
          session is closed, not entered by a cashier counting a drawer by
          hand - there is no independently-counted figure anywhere in this
          database. This can only ever show a computed-vs-computed
          comparison, never a real physical-count reconciliation.
        - "Cash paid out" (bank drops, change float top-ups) is not
          detected here - R.Lynx's own `StoreSafeIn`/`StoreSafeOut` tables
          exist for this but are empty in this database. If the shop
          starts using that feature, extend this to include them.

        The "recorded close" side is only shown once a session actually
        has one - a session with `ClosingTime` still empty is rendered as
        "still open", never as a zero or an error.
        """
        if not self._has_table("Batch"):
            return pd.DataFrame()

        b = self._read("""
            SELECT ID AS batch_id, BatchNo AS batch_no, RegisterID AS register_id,
                   OpeningTime AS opening_time, ClosingTime AS closing_time,
                   CashOpen AS cash_open, ChequeOpen AS cheque_open,
                   TransferOpen AS transfer_open, CreditAccountOpen AS credit_open,
                   CashClose AS cash_close, ChequeClose AS cheque_close,
                   TransferClose AS transfer_close, CreditAccountClose AS credit_close
            FROM Batch
        """)
        if b.empty:
            return pd.DataFrame()

        for col in ("cash_open", "cheque_open", "transfer_open", "credit_open",
                    "cash_close", "cheque_close", "transfer_close", "credit_close"):
            b[col] = pd.to_numeric(b[col], errors="coerce")
        b["opening_time"] = pd.to_datetime(b["opening_time"], errors="coerce")
        b["closing_time"] = pd.to_datetime(b["closing_time"], errors="coerce")
        b["is_open"] = b["closing_time"].isna()

        if self._has_table("Register"):
            reg = self._read("SELECT ID AS register_id, RegisterName AS register_name FROM Register")
            b = b.merge(reg, on="register_id", how="left")
        else:
            b["register_name"] = None
        b["register_name"] = b["register_name"].fillna(b["register_id"].astype(str))

        tk = self.tickets
        sold = (tk.groupby("batch_id").agg(
            cash_sold=("cash", "sum"), cheque_sold=("cheque", "sum"),
            transfer_sold=("transfer", "sum"), credit_sold=("credit_account", "sum"))
            if not tk.empty else pd.DataFrame())
        b = b.merge(sold, left_on="batch_id", right_index=True, how="left")
        for col in ("cash_sold", "cheque_sold", "transfer_sold", "credit_sold"):
            b[col] = b[col].fillna(0.0)

        close_cols = ["cash_close", "cheque_close", "transfer_close", "credit_close"]
        b["has_recorded_close"] = b[close_cols].notna().any(axis=1)

        for tender in ("cash", "cheque", "transfer", "credit"):
            b[f"{tender}_expected"] = b[f"{tender}_open"].fillna(0) + b[f"{tender}_sold"]

        expected_cols = ["cash_expected", "cheque_expected", "transfer_expected", "credit_expected"]
        b["expected_total"] = b[expected_cols].sum(axis=1)
        b["recorded_total"] = np.where(b["has_recorded_close"],
                                       b[close_cols].fillna(0).sum(axis=1), np.nan)
        b["total_variance"] = np.where(b["has_recorded_close"],
                                       b["recorded_total"] - b["expected_total"], np.nan)

        cols = ["batch_id", "batch_no", "register_id", "register_name",
                "opening_time", "closing_time", "is_open", "has_recorded_close"] + \
               expected_cols + ["expected_total"] + close_cols + \
               ["recorded_total", "total_variance"]
        return b[cols].sort_values("opening_time", ascending=False).reset_index(drop=True)

    def working_capital(self) -> dict[str, Any]:
        """
        How hard the money is working.

        Stock plus what customers owe is money you have already spent but
        cannot use. Comparing it with a year of gross profit answers: how
        many years of earnings are tied up in the business right now?
        """
        stock_value = float(self.items["stock_value"].sum())
        receivables = float(self.customers[self.customers["balance"] > 0]["balance"].sum())
        t12 = self.headline(days=365)
        gp = t12["gross_profit"]

        tied = stock_value + receivables
        ratio = (tied / gp) if gp else None

        return {
            "stock_value": stock_value,
            "receivables": receivables,
            "tied_up": tied,
            "gross_profit_12m": gp,
            "revenue_12m": t12["revenue"],
            "ratio": ratio,
            "threshold": float(self.t("working_capital.ratio_warn", 3.0)),
            "stock_turns": (t12["revenue"] - gp) / stock_value if stock_value else None,
        }

    # =====================================================================
    #  PAGE 9 - DATA QUALITY
    # =====================================================================

    def data_quality(self) -> dict[str, Any]:
        """
        Every known defect in the source data, counted and priced, so you
        know exactly how much to trust each figure on every other screen.
        """
        lines = self.lines
        sales = self.sales
        coll = self.collections

        # 1 - account payments recorded as if they were goods
        coll_names = coll["item_name"].fillna("").value_counts()
        reglement = coll[coll["item_name"].fillna("").str.contains(
            "glement", case=False, na=False)]
        other_pseudo = coll[~coll.index.isin(reglement.index)]

        # 4 - lines with no cost
        nocost = sales[~sales["cost_known"]]

        # 3 - returns
        returns = sales[sales["is_return"]]

        # 2 - the ticket header cost against the truth from the lines
        tk = self.tickets
        header_gap = tk[(tk["header_cost"].fillna(0) - tk["line_cost"]).abs() > 1]
        zero_header = header_gap[header_gap["header_cost"].fillna(0) == 0]

        # 5 - negative stock
        neg = self.items[self.items["stock"].fillna(0) < 0]

        # Products with stock but no cost recorded - their stock value shows
        # as zero, which understates the total.
        no_cost_stock = self.items[(self.items["stock"].fillna(0) > 0) &
                                   (self.items["cost"].fillna(0) <= 0)]

        # Reported margin against our own calculation
        margin_gap = float(sales["reported_margin"].sum() - sales["gross_profit"].sum())

        # Customers in credit
        credits = self.customers[(self.customers["balance"] < 0) &
                                 (~self.customers["is_walkin"])]

        total_revenue = float(sales["amount"].sum())

        return {
            "checked_at": self.now.strftime("%Y-%m-%d %H:%M"),
            "total_lines": int(len(lines)),
            "total_revenue": total_revenue,

            "collections": {
                "lines": int(len(coll)),
                "value": float(coll["amount"].sum()),
                "reglement_lines": int(len(reglement)),
                "reglement_value": float(reglement["amount"].sum()),
                "other_lines": int(len(other_pseudo)),
                "other_value": float(other_pseudo["amount"].sum()),
                "other_names": other_pseudo["item_name"].fillna("").tolist()[:10],
                "share_if_counted": (float(coll["amount"].sum()) /
                                     (total_revenue + float(coll["amount"].sum()))
                                     if total_revenue else 0.0),
            },

            "header_cost": {
                "tickets_wrong": int(len(header_gap)),
                "tickets_total": int(len(tk)),
                "tickets_zero": int(len(zero_header)),
                "understated_by": float((tk["line_cost"] - tk["header_cost"].fillna(0)).sum()),
                "affected_ticket_nos": header_gap.sort_values(
                    "line_cost", ascending=False)["ticket_no"].head(12).tolist(),
            },

            "returns": {
                "lines": int(len(returns)),
                "value": float(returns["amount"].sum()),
                "units": float(returns["qty"].sum()),
                "share_of_revenue": (abs(float(returns["amount"].sum())) / total_revenue
                                     if total_revenue else 0.0),
            },

            "missing_cost": {
                "lines": int(len(nocost)),
                "value": float(nocost["amount"].sum()),
                "share_of_revenue": (float(nocost["amount"].sum()) / total_revenue
                                     if total_revenue else 0.0),
                "items": int(nocost["item_id"].nunique()),
                "profit_overstated_by_at_most": float(nocost["amount"].sum()),
            },

            "negative_stock": {
                "items": int(len(neg)),
                "units": float(neg["stock"].sum()),
                "value": float(neg["stock_value"].sum()),
                "examples": neg.sort_values("stock")[
                    ["item_no", "item_name", "stock"]].head(8).to_dict("records"),
            },

            "stock_without_cost": {
                "items": int(len(no_cost_stock)),
                "units": float(no_cost_stock["stock"].sum()),
            },

            "reported_vs_calculated_margin": {
                "difference": margin_gap,
                "share": (abs(margin_gap) / total_revenue) if total_revenue else 0.0,
            },

            "credit_balances": {
                "customers": int(len(credits)),
                "value": float(credits["balance"].sum()),
                "names": credits[["customer_name", "balance"]].to_dict("records"),
            },

            # The product's own "last sold" field against what the tickets say.
            "stale_last_sold": self._stale_last_sold(),

            # How much of the purchase history can be traced to a supplier.
            "purchases": self.purchase_coverage(),

            "walkin": {
                "tickets": int((self.tickets["customer_id"] == self.walkin_id).sum()),
                "revenue": float(sales[sales["customer_id"] == self.walkin_id]["amount"].sum()),
                "share": (float(sales[sales["customer_id"] == self.walkin_id]["amount"].sum())
                          / total_revenue if total_revenue else 0.0),
            },

            "coverage": {
                "first_sale": self.data_range["first"],
                "last_sale": self.data_range["last"],
                "days": self.data_range["days"],
                "age_hours": self.data_range["age_hours"],
            },

            "tender_reconciliation": self._tender_reconciliation(),
            "on_account_reconciliation": self._on_account_reconciliation(),
        }

    def _tender_reconciliation(self) -> dict[str, Any]:
        """
        How well Cash+Cheque+Transfer+CreditAccount adds up to the ticket's
        own Total. The cash-realized split (see `tickets`) uses the tender
        total as its own denominator regardless, so this never affects the
        split - it only says how much to trust it.
        """
        tk = self.tickets
        gap = (tk["total_tender"] - tk["total"].fillna(0)).abs()
        mismatched = tk[gap > 1]
        return {
            "tickets_checked": int(len(tk)),
            "tickets_mismatched": int(len(mismatched)),
            "share_mismatched": (len(mismatched) / len(tk)) if len(tk) else 0.0,
            "max_gap": float(gap.max()) if not tk.empty else 0.0,
        }

    def _on_account_reconciliation(self) -> dict[str, Any]:
        """
        Cross-checks the new on-account figure against the existing
        Receivables total, which is computed an entirely different way
        (from Customer.balance, not from ticket tenders). They are not
        expected to match exactly - opening balances and any adjustment
        made directly in the POS are invisible to ticket-level tender data
        - but a wildly different order of magnitude would mean one of the
        two is wrong.
        """
        on_account_all = float(self.tickets["on_account_revenue"].sum())
        collections_all = float(self.collections["amount"].sum())
        expected = on_account_all - collections_all
        actual = float(self.receivables_summary()["total"])
        gap = actual - expected
        return {
            "on_account_all_time": on_account_all,
            "collections_all_time": collections_all,
            "expected_receivables": expected,
            "actual_receivables": actual,
            "gap": gap,
            "explains_receivables": bool(expected and 0.5 <= actual / expected <= 2.0),
        }

    def _stale_last_sold(self) -> dict[str, Any]:
        """
        How far the product records have drifted from what the tickets show.

        The POS does not always update a product's "last sold" date. Where it
        has not, the product looks dormant when it is actually selling. This
        counts how often that happens and how much stock value it would
        wrongly condemn as dead if the field were trusted.
        """
        it = self.item_movement
        stale = it[it["last_sold_field_stale_days"].fillna(0) > 1]
        dead_days = float(self.t("inventory.dead_stock_days", 120))

        # Products the field would call dead but the tickets say are alive.
        cutoff = self.now - datetime.timedelta(days=dead_days)
        wrongly_dead = it[(it["stock"].fillna(0) > 0) &
                          (it["last_sold"].notna()) & (it["last_sold"] < cutoff) &
                          (it["last_sale"].notna()) & (it["last_sale"] >= cutoff)]

        return {
            "items": int(len(stale)),
            "worst_days": (int(stale["last_sold_field_stale_days"].max())
                           if not stale.empty else 0),
            "wrongly_dead_items": int(len(wrongly_dead)),
            "wrongly_dead_value": float(wrongly_dead["stock_value"].sum()),
            "examples": wrongly_dead.sort_values("stock_value", ascending=False)[
                ["item_no", "item_name", "stock", "last_sold", "last_sale"]
            ].head(6).to_dict("records"),
        }

    # =====================================================================
    #  Verification - proves the parser and the rules still agree
    # =====================================================================

    def verification(self) -> list[dict[str, Any]]:
        """
        Recompute the figures the extraction was originally checked against.

        Shown on the data-quality screen so you can see at a glance that the
        tool is still reading your database correctly. The all-time totals
        should only ever go up, by exactly the value of the new sales.
        """
        h = self.headline()
        h12 = self.headline(days=365)
        inv = self.inventory_summary()
        rec = self.receivables_summary()
        dq = self.data_quality()

        counts = {}
        for name in ("Receipt", "ReceiptEntry", "Item", "Customer"):
            try:
                counts[name] = self.conn.execute(
                    f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            except sqlite3.Error:
                counts[name] = None

        return [
            {"key": "receipt_rows", "value": counts.get("Receipt"), "expected": 7858,
             "kind": "count", "grows": True},
            {"key": "receipt_entry_rows", "value": counts.get("ReceiptEntry"),
             "expected": 39736, "kind": "count", "grows": True},
            {"key": "item_rows", "value": counts.get("Item"), "expected": 1570,
             "kind": "count", "grows": True},
            {"key": "customer_rows", "value": counts.get("Customer"), "expected": 662,
             "kind": "count", "grows": True},
            {"key": "revenue_all_time", "value": h["revenue"], "expected": 266299322,
             "kind": "money", "grows": True},
            {"key": "gross_profit_all_time", "value": h["gross_profit"],
             "expected": 26686300, "kind": "money", "grows": True},
            {"key": "revenue_12m", "value": h12["revenue"], "expected": 167589951,
             "kind": "money", "grows": False},
            {"key": "gross_profit_12m", "value": h12["gross_profit"],
             "expected": 14635724, "kind": "money", "grows": False},
            {"key": "stock_value", "value": inv["total_value"], "expected": 59168540,
             "kind": "money", "grows": False},
            {"key": "dead_stock_value", "value": inv["dead_value"], "expected": 12495043,
             "kind": "money", "grows": False},
            {"key": "receivables", "value": rec["total"], "expected": 18035898,
             "kind": "money", "grows": False},
            {"key": "reglement_value", "value": dq["collections"]["value"],
             "expected": 30420753, "kind": "money", "grows": True},
        ]
