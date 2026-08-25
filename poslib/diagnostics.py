"""
diagnostics.py - the rules that turn numbers into things to do.

This is the part of the tool that earns its keep. Everywhere else works out
what is true; this file decides what matters and what to do about it.

HOW A RULE WORKS
----------------
Every rule is one function that looks at the metrics and either returns a
finding or returns nothing. A finding always carries five things:

    severity      how bad, judged against the size of THIS business
    statement     what is true, in plain language, with the real number in it
    money         how much money is involved
    action        one specific thing to do - never "consider optimising"
    workings      the arithmetic, so the number can be checked

Rules are registered by adding @rule above a function. To add a new one,
write a function here and add its text to the three locale files. Nothing
else in the tool needs to change.

WHY SEVERITY IS NOT A FIXED NUMBER
----------------------------------
"12 million DZD" means something different to a corner shop and to a
national distributor. So severity is judged against this business's own
gross profit for the last twelve months. A problem worth a quarter of a
year's gross profit is critical here no matter what the raw figure is.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from .config import Config, get_config
from .i18n import Translator
from .metrics import Metrics

log = logging.getLogger(__name__)

# Severity, worst first. Used for sorting inside an equal-money group.
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "good": 4}

# How big a problem has to be, as a share of the last twelve months' gross
# profit, before it counts as each level.
SEVERITY_BANDS = (
    ("critical", 0.25),
    ("high", 0.10),
    ("medium", 0.03),
)


@dataclass
class Finding:
    """One thing worth telling the owner about."""

    id: str
    kind: str = "failing"          # "failing" or "working"
    severity: str = "medium"
    money: float = 0.0             # what is at stake, used for ranking
    params: dict[str, tuple[str, Any]] = field(default_factory=dict)
    page: str = ""                 # which screen has the detail
    order_hint: int = 0

    def render(self, t: Translator) -> dict[str, Any]:
        """Turn the finding into finished text in one language."""
        values = {name: _format(t, kind, value)
                  for name, (kind, value) in self.params.items()}

        base = f"findings.{self.id}"
        fix = t.get(f"{base}.fix", **values) if t.has(f"{base}.fix") else ""
        workings = t.get(f"{base}.workings", **values) if t.has(f"{base}.workings") else ""

        return {
            "id": self.id,
            "kind": self.kind,
            "severity": self.severity,
            "severity_label": t.get(f"diagnostics.severity_{self.severity}"),
            "money": self.money,
            "money_label": t.money(self.money) if self.money else "",
            "title": t.get(f"{base}.title", **values),
            "statement": t.get(f"{base}.statement", **values),
            "action": t.get(f"{base}.action", **values),
            "fix": fix.strip(),
            "workings": workings.strip(),
            "page": self.page,
        }


def _format_month_list(t: Translator, months: list[int]) -> str:
    """Turn [1, 2] into "January and February" in the reader's language."""
    names = [t.month_name(m) for m in months]
    if len(names) == 1:
        return names[0]
    return t.get("common.list_join").join(names[:-1]) + \
        t.get("common.list_last") + names[-1]


def _format(t: Translator, kind: str, value: Any) -> str:
    """Turn one raw value into text in the reader's language."""
    if kind == "month_list":
        return _format_month_list(t, value)
    if kind == "money":
        return t.money(value)
    if kind == "money_short":
        return t.money_short(value)
    if kind == "pct":
        return t.percent(value)
    if kind == "pct0":
        return t.percent(value, 0)
    if kind == "signed_pct":
        return t.signed_percent(value)
    if kind == "number":
        return t.number(value)
    if kind == "number1":
        return t.number(value, 1)
    if kind == "date":
        return t.date(value)
    if kind == "month":
        return t.month_label(value)
    if kind == "days_ago":
        return t.days_ago(value)
    return str(value)


# ---------------------------------------------------------------------------
# The rule registry
# ---------------------------------------------------------------------------

RuleFn = Callable[["Context"], list[Finding]]
_RULES: list[tuple[str, RuleFn]] = []


def rule(name: str) -> Callable[[RuleFn], RuleFn]:
    """Register a rule. Add @rule("something") above a function."""
    def wrap(fn: RuleFn) -> RuleFn:
        _RULES.append((name, fn))
        return fn
    return wrap


@dataclass
class Context:
    """Everything the rules are allowed to look at, worked out once."""
    m: Metrics
    cfg: Config

    def __post_init__(self) -> None:
        self.h12 = self.m.headline(days=365)
        self.gp12 = float(self.h12["gross_profit"]) or 1.0
        self.rev12 = float(self.h12["revenue"])

    def t(self, key: str, default: Any = None) -> Any:
        return self.cfg.get(f"thresholds.{key}", default)

    def severity_for(self, money: float) -> str:
        """
        Judge how serious an amount is against a year of gross profit.
        """
        share = abs(money) / self.gp12 if self.gp12 else 0.0
        for name, floor in SEVERITY_BANDS:
            if share >= floor:
                return name
        return "low"


# ===========================================================================
#  MARGIN
# ===========================================================================

@rule("margin_level")
def _margin_level(c: Context) -> list[Finding]:
    """Is the overall margin good enough for this trade?"""
    healthy = float(c.t("margin.healthy_gross_margin", 0.10))
    h = c.h12
    margin = h["margin_pct"]
    if margin is None:
        return []

    rev = float(h["revenue_measurable"])

    if margin < healthy:
        gain = rev * (healthy - margin)
        return [Finding(
            id="margin_below_healthy",
            kind="failing",
            severity=c.severity_for(gain),
            money=gain,
            page="products",
            params={"revenue": ("money", h["revenue"]),
                    "profit": ("money", h["gross_profit"]),
                    "margin": ("pct", margin),
                    "healthy": ("pct", healthy),
                    "gain": ("money", gain)},
        )]

    return [Finding(
        id="margin_healthy",
        kind="working",
        severity="good",
        money=float(h["gross_profit"]),
        page="products",
        params={"margin": ("pct", margin), "healthy": ("pct", healthy),
                "revenue": ("money", h["revenue"]),
                "gain": ("money", h["gross_profit"])},
    )]


@rule("margin_trend")
def _margin_trend(c: Context) -> list[Finding]:
    """Has margin been sliding for several months running?"""
    need = int(c.t("margin.declining_months", 3))
    min_drop = float(c.t("margin.declining_min_drop", 0.005))

    df = c.m.complete_months()
    if df.empty or len(df) < need + 1:
        return []

    recent = df.tail(need + 1)
    margins = recent["margin_pct"].to_numpy(dtype=float)
    if np.isnan(margins).any():
        return []

    # Every step must be down, and the whole fall must be worth noticing.
    steps = np.diff(margins)
    if not (steps < 0).all():
        return []
    drop = margins[0] - margins[-1]
    if drop < min_drop:
        return []

    rev = float(c.h12["revenue_measurable"])
    gain = rev * drop
    per_point = rev * 0.01

    return [Finding(
        id="margin_declining",
        kind="failing",
        severity=c.severity_for(gain),
        money=gain,
        page="trend",
        params={"months": ("number", need),
                "from_margin": ("pct", margins[0]),
                "to_margin": ("pct", margins[-1]),
                "from_month": ("month", recent.iloc[0]["month"]),
                "to_month": ("month", recent.iloc[-1]["month"]),
                "drop": ("pct", drop),
                "per_point": ("money", per_point),
                "revenue": ("money", rev),
                "gain": ("money", gain)},
    )]


@rule("revenue_vs_profit")
def _revenue_vs_profit(c: Context) -> list[Finding]:
    """Selling more but not earning more means discounting or cost creep."""
    df = c.m.complete_months()
    if len(df) < 6:
        return []

    recent = df.tail(3)
    before = df.tail(6).head(3)

    rev_now, rev_before = float(recent["revenue"].sum()), float(before["revenue"].sum())
    gp_now, gp_before = float(recent["gross_profit"].sum()), float(before["gross_profit"].sum())
    if rev_before <= 0 or gp_before <= 0:
        return []

    rev_change = (rev_now - rev_before) / rev_before
    gp_change = (gp_now - gp_before) / gp_before

    rev_floor = float(c.t("margin.revenue_growth_floor", 0.05))
    profit_ceiling = float(c.t("margin.profit_growth_ceiling", 0.0))

    if rev_change >= rev_floor and gp_change <= profit_ceiling:
        old_margin = gp_before / rev_before
        gain = rev_now * old_margin - gp_now
        return [Finding(
            id="revenue_up_profit_flat",
            kind="failing",
            severity=c.severity_for(gain),
            money=max(gain, 0.0),
            page="trend",
            params={"rev_change": ("signed_pct", rev_change),
                    "profit_change": ("signed_pct", gp_change),
                    "revenue": ("money", rev_now),
                    "old_margin": ("pct", old_margin),
                    "profit": ("money", gp_now),
                    "gain": ("money", max(gain, 0.0))},
        )]

    if rev_change > 0.02 and gp_change > 0.02:
        return [Finding(
            id="revenue_growing",
            kind="working",
            severity="good",
            money=gp_now * 4,
            page="trend",
            params={"rev_change": ("signed_pct", rev_change),
                    "profit_change": ("signed_pct", gp_change),
                    "profit": ("money", gp_now),
                    "gain": ("money", gp_now * 4)},
        )]

    return []


@rule("negative_period")
def _negative_period(c: Context) -> list[Finding]:
    """A recent month where the goods sold cost more than they sold for."""
    df = c.m.complete_months()
    if df.empty:
        return []

    recent = df.tail(3)
    bad = recent[recent["gross_profit"] < 0]
    if bad.empty:
        return []

    worst = bad.sort_values("gross_profit").iloc[0]
    loss = abs(float(worst["gross_profit"]))

    return [Finding(
        id="negative_gross_profit_month",
        kind="failing",
        severity=c.severity_for(loss),
        money=loss,
        page="trend",
        params={"month": ("month", worst["month"]),
                "revenue": ("money", worst["revenue"]),
                "loss": ("money", loss),
                "count": ("number", len(bad))},
    )]


@rule("silent_margin_erosion")
def _silent_erosion(c: Context) -> list[Finding]:
    """Cost went up, price did not follow, product still sells."""
    d = c.m.silent_margin_erosion()
    if d.empty:
        return []

    loss = float(d["annual_margin_lost"].sum())
    if loss <= 0:
        return []
    top = d.iloc[0]

    return [Finding(
        id="silent_margin_erosion",
        kind="failing",
        severity=c.severity_for(loss),
        money=loss,
        page="products",
        params={"count": ("number", len(d)),
                "top_name": ("text", top["item_name"]),
                "top_cost_then": ("money", top["first_cost"]),
                "top_cost_now": ("money", top["cost"]),
                "top_price": ("money", top["price"]),
                "gain": ("money", loss)},
    )]


@rule("below_cost")
def _below_cost(c: Context) -> list[Finding]:
    """Products sold for less than they cost."""
    b = c.m.selling_below_cost()
    if b.empty:
        return []

    loss = float(b["loss"].sum())
    if loss >= 0:
        return []
    top = b.iloc[0]
    still = int(b["still_below_cost"].sum())

    return [Finding(
        id="selling_below_cost",
        kind="failing",
        severity=c.severity_for(loss),
        money=abs(loss),
        page="products",
        params={"count": ("number", len(b)),
                "loss": ("money", loss),
                "loss_abs": ("money", abs(loss)),
                "top_name": ("text", top["item_name"]),
                "top_loss_unit": ("money", abs(float(top["loss_per_unit"]))),
                "top_units": ("number", top["units"]),
                "still_count": ("number", still)},
    )]


@rule("family_margin_benchmark")
def _family_margin_benchmark(c: Context) -> list[Finding]:
    """
    Products that look fine priced in isolation, but are outliers next to
    every other product in their own brand/category.
    """
    out = c.m.family_margin_outliers()
    if out.empty:
        return []

    gain = float((out["gap_pp"] / 100 * out["revenue_all"]).sum())
    if gain <= 0:
        return []
    top = out.iloc[0]

    return [Finding(
        id="family_margin_below_average",
        kind="failing",
        severity=c.severity_for(gain),
        money=gain,
        page="products",
        params={"count": ("number", len(out)),
                "top_name": ("text", top["item_name"]),
                "top_family": ("text", top["family_name"]),
                "top_margin": ("pct", top["margin_pct"]),
                "top_family_margin": ("pct", top["family_margin_pct"]),
                "gap": ("pct", top["gap_pp"] / 100),
                "gain": ("money", gain)},
    )]


# ===========================================================================
#  MONEY OWED
# ===========================================================================

@rule("receivable_concentration")
def _receivable_concentration(c: Context) -> list[Finding]:
    """One customer holding too much of what you are owed."""
    s = c.m.receivables_summary()
    if not s["customers"]:
        return []

    warn = float(c.t("customers.receivable_concentration_warn", 0.25))
    r = c.m.receivables()

    if s["top_share"] >= warn:
        top = r.iloc[0]
        gain = float(top["balance"]) * 0.5
        return [Finding(
            id="receivable_concentration",
            kind="failing",
            severity=c.severity_for(float(top["balance"])),
            money=float(top["balance"]),
            page="receivables",
            params={"name": ("text", top["customer_name"]),
                    "amount": ("money", top["balance"]),
                    "share": ("pct", top["share_of_receivables"]),
                    "total": ("money", s["total"]),
                    "last_purchase": ("days_ago", top["days_since_purchase"]),
                    "gain": ("money", gain)},
        )]

    return [Finding(
        id="receivables_controlled",
        kind="working",
        severity="good",
        money=float(s["total"]),
        page="receivables",
        params={"total": ("money", s["total"]),
                "count": ("number", s["customers"]),
                "share": ("pct", s["top_share"])},
    )]


@rule("collection_risk")
def _collection_risk(c: Context) -> list[Finding]:
    """Customers who owe money and have stopped buying."""
    r = c.m.receivables()
    if r.empty:
        return []

    days = float(c.t("customers.collection_risk_days", 60))
    risky = r[r["at_risk"]].sort_values("balance", ascending=False)
    if risky.empty:
        return []

    total = float(risky["balance"].sum())
    worst = risky.sort_values("days_since_purchase", ascending=False).iloc[0]
    gain = total * 0.6

    return [Finding(
        id="collection_risk",
        kind="failing",
        severity=c.severity_for(total),
        money=total,
        page="receivables",
        params={"count": ("number", len(risky)),
                "amount": ("money", total),
                "days": ("number", days),
                "worst_name": ("text", worst["customer_name"]),
                "worst_days": ("number", worst["days_since_purchase"]),
                "worst_amount": ("money", worst["balance"]),
                "gain": ("money", gain)},
    )]


@rule("collections_habit")
def _collections_habit(c: Context) -> list[Finding]:
    """Are account payments actually coming in?"""
    h12 = c.h12
    collected = float(h12["collections"])
    owed = float(c.m.receivables_summary()["total"])
    if collected <= 0 or owed <= 0:
        return []
    # Collecting less than a third of the outstanding book in a year is not
    # a habit worth praising.
    if collected < owed * 0.33:
        return []

    return [Finding(
        id="collections_healthy",
        kind="working",
        severity="good",
        money=collected,
        page="receivables",
        params={"collected": ("money", collected), "owed": ("money", owed)},
    )]


# ===========================================================================
#  CUSTOMERS
# ===========================================================================

@rule("customer_low_margin")
def _customer_low_margin(c: Context) -> list[Finding]:
    """Big customers being served at almost no profit."""
    cs = c.m.customer_summary()
    if cs.empty:
        return []

    floor = float(c.t("customers.high_revenue_floor", 500000))
    low = float(c.t("customers.low_margin_warn", 0.04))

    bad = cs[(cs["revenue_12m"] >= floor) &
             (cs["margin_pct"].notna()) &
             (cs["margin_pct"] < low)].copy()
    if bad.empty:
        return []

    revenue = float(bad["revenue_12m"].sum())
    gain = revenue * 0.03
    worst = bad.sort_values("margin_pct").iloc[0]
    avg_margin = (float(bad["gross_profit_measurable"].sum()) /
                  float(bad["revenue_measurable"].sum())
                  if float(bad["revenue_measurable"].sum()) else 0.0)

    return [Finding(
        id="customer_low_margin",
        kind="failing",
        severity=c.severity_for(gain),
        money=gain,
        page="customers",
        params={"count": ("number", len(bad)),
                "revenue": ("money", revenue),
                "margin": ("pct", avg_margin),
                "worst_name": ("text", worst["customer_name"]),
                "worst_revenue": ("money", worst["revenue_12m"]),
                "worst_margin": ("pct", worst["margin_pct"]),
                "gain": ("money", gain)},
    )]


@rule("lapsed_customers")
def _lapsed_customers(c: Context) -> list[Finding]:
    """Regulars who have stopped coming."""
    cl = c.m.call_list()
    if cl.empty:
        return []

    days = float(c.t("customers.lapsed_days", 90))
    revenue = float(cl["revenue"].sum())
    monthly = float(cl["monthly_when_active"].sum())
    gain = monthly * 12 * 0.25

    return [Finding(
        id="lapsed_revenue_at_risk",
        kind="failing",
        severity=c.severity_for(gain),
        money=gain,
        page="customers",
        params={"count": ("number", len(cl)),
                "revenue": ("money", revenue),
                "days": ("number", days),
                "monthly": ("money", monthly),
                "gain": ("money", gain)},
    )]


@rule("top_customer_quiet")
def _top_customer_quiet(c: Context) -> list[Finding]:
    """
    A big customer who has gone quiet by their own standards.

    "Quiet" is judged against how often that particular customer normally
    buys, not against a fixed number of days. A customer who comes weekly
    and has not been seen for a month is a worry; one who comes twice a
    year is not.
    """
    cs = c.m.customer_summary()
    if cs.empty:
        return []

    top = cs.nlargest(20, "revenue_12m")
    findings: list[Finding] = []

    for _, row in top.iterrows():
        visits = float(row["visits"])
        tenure = float(row["tenure_days"] or 0)
        recency = float(row["recency_days"] or 0)
        if visits < 4 or tenure <= 0:
            continue

        usual_gap = tenure / visits
        # Three times their normal gap, and at least a month.
        if recency < max(usual_gap * 3, 30):
            continue

        monthly = float(row["revenue_12m"]) / 12.0
        gain = float(row["revenue_12m"])
        findings.append(Finding(
            id="top_customer_quiet",
            kind="failing",
            severity=c.severity_for(gain * 0.2),
            money=gain,
            page="customers",
            params={"name": ("text", row["customer_name"]),
                    "revenue": ("money", row["revenue"]),
                    "days": ("number", recency),
                    "last_date": ("date", row["last_purchase"]),
                    "usual": ("number", round(usual_gap)),
                    "phone": ("text", row["phone"] or "—"),
                    "monthly": ("money", monthly),
                    "gain": ("money", gain)},
        ))

    # Only the three most valuable, so this one rule cannot flood the page.
    findings.sort(key=lambda f: -f.money)
    return findings[:3]


@rule("revenue_concentration")
def _revenue_concentration(c: Context) -> list[Finding]:
    """How much of the business rests on ten customers."""
    p = c.m.customer_pareto()
    if p.empty or len(p) < 10:
        return []

    warn = float(c.t("customers.top10_revenue_concentration_warn", 0.50))
    top10 = float(p.head(10)["revenue"].sum())
    total = float(p["revenue"].sum())
    share = top10 / total if total else 0.0

    cs = c.m.customer_summary()
    biggest = float(cs["revenue_12m"].max()) if not cs.empty else 0.0

    if share >= warn:
        return [Finding(
            id="revenue_concentration",
            kind="failing",
            severity=c.severity_for(biggest),
            money=biggest,
            page="customers",
            params={"share": ("pct", share), "revenue": ("money", top10),
                    "total": ("money", total), "count": ("number", len(p)),
                    "gain": ("money", biggest)},
        )]

    return [Finding(
        id="revenue_spread",
        kind="working",
        severity="good",
        money=total,
        page="customers",
        params={"share": ("pct", share), "count": ("number", len(p))},
    )]


@rule("churn")
def _churn(c: Context) -> list[Finding]:
    """Are more customers arriving than leaving?"""
    ch = c.m.churn_by_month()
    if len(ch) < 4:
        return []

    # Ignore the current, unfinished month.
    this_month = pd.Timestamp(c.m.now.year, c.m.now.month, 1)
    ch = ch[ch["month"] < this_month]
    if len(ch) < 3:
        return []

    recent = ch.tail(3)
    lost = int(recent["lost"].sum())
    gained = int(recent["new"].sum())

    cs = c.m.customer_summary()
    avg_value = float(cs["revenue_12m"].mean()) if not cs.empty else 0.0
    lost_value = lost * avg_value

    if lost > gained:
        gain = lost_value * 0.33
        return [Finding(
            id="customer_churn",
            kind="failing",
            severity=c.severity_for(gain),
            money=gain,
            page="customers",
            params={"lost": ("number", lost), "gained": ("number", gained),
                    "revenue": ("money", lost_value), "gain": ("money", gain)},
        )]

    return [Finding(
        id="customer_growth",
        kind="working",
        severity="good",
        money=gained * avg_value,
        page="customers",
        params={"lost": ("number", lost), "gained": ("number", gained)},
    )]


@rule("champions")
def _champions(c: Context) -> list[Finding]:
    """The customers who are carrying the business."""
    seg = c.m.segment_summary()
    if seg.empty:
        return []
    champs = seg[seg["segment"] == "champion"]
    if champs.empty:
        return []

    row = champs.iloc[0]
    if int(row["customers"]) == 0:
        return []

    return [Finding(
        id="champions_strong",
        kind="working",
        severity="good",
        money=float(row["revenue_12m"]),
        page="customers",
        params={"count": ("number", row["customers"]),
                "revenue": ("money", row["revenue_12m"]),
                "share": ("pct", row["revenue_share"])},
    )]


# ===========================================================================
#  STOCK
# ===========================================================================

@rule("dead_stock")
def _dead_stock(c: Context) -> list[Finding]:
    """Money sitting on shelves."""
    inv = c.m.inventory_summary()
    if not inv["total_value"]:
        return []

    warn = float(c.t("inventory.dead_stock_share_warn", 0.15))
    share = float(inv["dead_share"])
    value = float(inv["dead_value"])
    days = int(inv["dead_stock_days"])

    if share >= warn:
        discount = 0.30       # what clearing it realistically costs
        gain = value * (1 - discount)
        return [Finding(
            id="dead_stock_share",
            kind="failing",
            severity=c.severity_for(gain),
            money=gain,
            page="inventory",
            params={"share": ("pct", share), "value": ("money", value),
                    "items": ("number", inv["dead_items"]),
                    "days": ("number", days),
                    "discount": ("pct0", discount),
                    "gain": ("money", gain)},
        )]

    return [Finding(
        id="dead_stock_low",
        kind="working",
        severity="good",
        money=float(inv["healthy_value"]),
        page="inventory",
        params={"share": ("pct", share), "value": ("money", value),
                "days": ("number", days)},
    )]


@rule("stockout")
def _stockout(c: Context) -> list[Finding]:
    """Fast sellers about to run out."""
    s = c.m.stockout_risk()
    cover = float(c.t("inventory.stockout_cover_months", 0.75))

    if s.empty:
        return [Finding(
            id="stock_turning", kind="working", severity="good", money=0.0,
            page="inventory", params={"months": ("number1", cover)},
        )]

    weekly = float(s["weekly_revenue"].sum())
    weekly_profit = float(s["weekly_profit"].sum())
    top = s.iloc[0]
    margin = weekly_profit / weekly if weekly else 0.0

    # A month out of stock is the exposure worth ranking on.
    exposure = weekly_profit * 4

    return [Finding(
        id="stockout_imminent",
        kind="failing",
        severity=c.severity_for(exposure),
        money=exposure,
        page="inventory",
        params={"count": ("number", len(s)),
                "weekly": ("money", weekly),
                "weekly_profit": ("money", weekly_profit),
                "margin": ("pct", margin),
                "top_name": ("text", top["item_name"]),
                "top_stock": ("number", top["stock"]),
                "top_rate": ("number", round(float(top["monthly_rate"]))),
                "top_weekly": ("money", top["weekly_revenue"])},
    )]


@rule("overstock")
def _overstock(c: Context) -> list[Finding]:
    """Far more stock than the rate of sale justifies."""
    o = c.m.overstock()
    if o.empty:
        return []

    value = float(o["excess_value"].sum())
    if value <= 0:
        return []

    return [Finding(
        id="overstock_capital",
        kind="failing",
        severity=c.severity_for(value),
        money=value,
        page="inventory",
        params={"items": ("number", len(o)), "value": ("money", value)},
    )]


@rule("negative_stock")
def _negative_stock(c: Context) -> list[Finding]:
    """Stock records that cannot be true."""
    inv = c.m.inventory_summary()
    if not inv["negative_items"]:
        return []

    return [Finding(
        id="negative_stock",
        kind="failing",
        severity="low",       # it is a data problem, not lost money
        money=abs(float(inv["negative_value"])),
        page="inventory",
        order_hint=1,
        params={"items": ("number", inv["negative_items"]),
                "units": ("number", abs(float(inv["negative_units"]))),
                "value": ("money", abs(float(inv["negative_value"])))},
    )]


# ===========================================================================
#  CASH
# ===========================================================================

@rule("working_capital")
def _working_capital(c: Context) -> list[Finding]:
    """How hard the money is working."""
    w = c.m.working_capital()
    if w["ratio"] is None:
        return []

    ratio = float(w["ratio"])
    threshold = float(w["threshold"])

    if ratio > threshold:
        gain = float(w["tied_up"]) * 0.25
        return [Finding(
            id="working_capital_tied",
            kind="failing",
            severity=c.severity_for(gain),
            money=gain,
            page="inventory",
            params={"stock": ("money", w["stock_value"]),
                    "receivables": ("money", w["receivables"]),
                    "tied": ("money", w["tied_up"]),
                    "profit": ("money", w["gross_profit_12m"]),
                    "ratio": ("number1", ratio),
                    "gain": ("money", gain)},
        )]

    return [Finding(
        id="working_capital_healthy",
        kind="working",
        severity="good",
        money=float(w["gross_profit_12m"]),
        page="inventory",
        params={"tied": ("money", w["tied_up"]),
                "profit": ("money", w["gross_profit_12m"]),
                "ratio": ("number1", ratio)},
    )]


# ===========================================================================
#  SEASON
# ===========================================================================

@rule("seasonality")
def _seasonality(c: Context) -> list[Finding]:
    """Months that consistently underperform, so buying can be planned."""
    s = c.m.seasonality()
    if s.empty:
        return []

    # Only months seen in more than one year can be called a pattern.
    s = s[s["years"] >= 2]
    weak = s[s["vs_average"] <= -0.20]
    if weak.empty:
        return []

    month_numbers = [int(x) for x in weak["month_no"].tolist()]
    gap = float(weak["vs_average"].mean())

    # The cash saved by not buying for a normal month and then selling a
    # third less than normal.
    #
    # This is worked out from cost of goods sold, NOT from the recorded
    # purchase totals. The purchase lines in this POS add up to roughly
    # twice the cost of everything ever sold, which cannot be right, so
    # they are not trusted for any money figure. Cost of goods sold comes
    # from the sale lines and does reconcile.
    monthly_cogs = (c.rev12 - c.gp12) / 12.0
    gain = abs(monthly_cogs * gap) * len(weak)

    return [Finding(
        id="seasonality_weak",
        kind="failing",
        severity=c.severity_for(gain),
        money=gain,
        page="trend",
        params={"months": ("month_list", month_numbers),
                "gap": ("pct", abs(gap)),
                "gain": ("money", gain)},
    )]


# ===========================================================================
#  Running the rules
# ===========================================================================

class Diagnostics:
    """Runs every rule and hands back the findings, ranked by money."""

    def __init__(self, metrics: Metrics, cfg: Config | None = None):
        self.m = metrics
        self.cfg = cfg or get_config()
        self._findings: list[Finding] | None = None

    def findings(self) -> list[Finding]:
        if self._findings is not None:
            return self._findings

        ctx = Context(self.m, self.cfg)
        out: list[Finding] = []

        for name, fn in _RULES:
            try:
                produced = fn(ctx) or []
            except Exception as exc:                     # noqa: BLE001
                # A broken rule must never take the whole page down.
                log.exception("Diagnostic rule %r failed: %s", name, exc)
                continue
            out.extend(produced)

        self._findings = out
        return out

    def ranked(self, kind: str | None = None) -> list[Finding]:
        """
        Findings ordered by money at stake, biggest first.

        Ranking by money rather than by severity label is deliberate: a
        "medium" worth 12 million matters more than a "high" worth 40,000.
        """
        items = self.findings()
        if kind:
            items = [f for f in items if f.kind == kind]
        return sorted(items, key=lambda f: (f.order_hint, -abs(f.money),
                                            SEVERITY_ORDER.get(f.severity, 9)))

    def render(self, t: Translator) -> dict[str, Any]:
        """Everything the diagnostics screen and the digest need."""
        failing = [f.render(t) for f in self.ranked("failing")]
        working = [f.render(t) for f in self.ranked("working")]

        total = sum(f["money"] for f in failing)
        return {
            "failing": failing,
            "working": working,
            "total_at_stake": total,
            "total_at_stake_label": t.money(total),
            "count": len(failing),
        }

    def urgent(self, limit: int = 5) -> list[Finding]:
        """The few worth waking somebody up for - used by the daily digest."""
        return [f for f in self.ranked("failing")
                if f.severity in ("critical", "high")][:limit]


