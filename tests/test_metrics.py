"""
Integration tests: do the business figures still come out right?

These are the tests that matter most. They check the whole chain - read the
Access file, load it into the cache, apply the business rules - against
figures that were verified by hand.

The rule for the all-time totals: they may GROW, by the value of new sales,
but they must never fall or jump. A fall means rows are being missed. A jump
means something is being counted twice.
"""

from __future__ import annotations

import datetime

import pandas as pd
import pytest

from poslib.metrics import Metrics


def approximately(actual: float, expected: float, tolerance: float) -> bool:
    return abs(actual - expected) <= abs(expected) * tolerance


class TestHeadlineTotals:

    def test_revenue_excludes_account_payments(self, metrics: Metrics, expected_totals):
        """
        The single most important rule. Around 30 million DZD of "sales" are
        really customers paying off what they owe. Counting them as revenue
        would overstate the business by more than a tenth.
        """
        revenue = metrics.headline()["revenue"]
        expected = expected_totals["revenue_all_time"]
        assert revenue >= expected, (
            f"revenue is {revenue:,.0f} but was {expected:,.0f} when checked. "
            "It can only grow.")
        assert revenue < expected * 1.5, (
            f"revenue is {revenue:,.0f}, far above the {expected:,.0f} checked "
            "by hand. Account payments may be leaking into revenue.")

    def test_account_payments_counted_separately(self, metrics: Metrics, expected_totals):
        collections = metrics.data_quality()["collections"]["value"]
        expected = expected_totals["reglement_value"]
        assert collections >= expected * 0.999
        assert collections < expected * 1.5

    def test_no_sale_line_has_a_missing_product(self, metrics: Metrics):
        assert (metrics.sales["item_id"] > 0).all()

    def test_gross_profit(self, metrics: Metrics, expected_totals):
        profit = metrics.headline()["gross_profit"]
        expected = expected_totals["gross_profit_all_time"]
        assert profit >= expected * 0.999
        assert profit < expected * 1.5

    def test_profit_is_computed_from_lines_not_the_ticket_header(self, metrics: Metrics):
        """
        Nine tickets record a cost of zero. Trusting the header would
        overstate profit by more than a million.
        """
        tickets = metrics.tickets
        header_total = tickets["header_cost"].fillna(0).sum()
        line_total = tickets["line_cost"].sum()
        assert line_total > header_total, \
            "the ticket header cost should be the lower, unreliable one"

        h = metrics.headline()
        from_lines = h["revenue"] - h["gross_profit"]
        assert abs(from_lines - line_total) < 1000, \
            "cost of goods does not match the sum of the lines"

    def test_margin_ignores_lines_with_no_cost(self, metrics: Metrics):
        """
        A line with no cost has an unknown margin, not a 100% one. It must be
        left out of the percentage but still counted in revenue.
        """
        h = metrics.headline()
        assert h["revenue_measurable"] < h["revenue"]
        assert h["revenue_unmeasurable"] > 0

        measurable_margin = h["margin_pct"]
        naive_margin = h["gross_profit"] / h["revenue"]
        assert measurable_margin < naive_margin, \
            "the honest margin should be lower than the naive one"
        assert 0.0 < measurable_margin < 0.5

    def test_returns_reduce_revenue(self, metrics: Metrics):
        returns = metrics.sales[metrics.sales["is_return"]]
        assert len(returns) > 50, "returns should be present in the data"
        assert returns["amount"].sum() < 0, "returns should pull revenue down"

    def test_trailing_12_months(self, metrics: Metrics, expected_totals):
        """A rolling window drifts, so this is checked loosely."""
        h12 = metrics.headline(days=365)
        assert approximately(h12["revenue"], expected_totals["revenue_12m"], 0.15)
        assert approximately(h12["gross_profit"], expected_totals["gross_profit_12m"], 0.20)
        assert h12["revenue"] < metrics.headline()["revenue"]


class TestStock:

    def test_stock_value_includes_negative_rows(self, metrics: Metrics, expected_totals):
        """
        Negative stock is a real POS error, and excluding those rows
        overstates the stock value by half a million.
        """
        value = metrics.inventory_summary()["total_value"]
        assert approximately(value, expected_totals["stock_value"], 0.15)

    def test_negative_stock_is_flagged_not_hidden(self, metrics: Metrics):
        inv = metrics.inventory_summary()
        assert inv["negative_items"] > 0
        assert inv["negative_value"] < 0
        assert len(metrics.negative_stock()) == inv["negative_items"]

    def test_dead_stock_uses_the_ticket_history(self, metrics: Metrics):
        """
        The product's own "last sold" field is stale on dozens of products.
        Using it would condemn live stock as dead.
        """
        stale = metrics.data_quality()["stale_last_sold"]
        assert stale["items"] > 0, "expected some stale last-sold dates"
        assert stale["wrongly_dead_value"] > 0, \
            "expected the stale field to wrongly condemn some live stock"

        dead = metrics.dead_stock()
        assert not dead.empty
        assert (dead["stock"] > 0).all(), "dead stock must actually be in stock"

    def test_dead_stock_share_is_sane(self, metrics: Metrics):
        inv = metrics.inventory_summary()
        assert 0 <= inv["dead_share"] <= 1
        total = inv["healthy_value"] + inv["slow_value"] + inv["dead_value"]
        in_stock_value = float(
            metrics.items[metrics.items["stock"].fillna(0) > 0]["stock_value"].sum())
        assert abs(total - in_stock_value) < 1.0, \
            "the healthy / slow / dead split does not add up to the stock in hand"

    def test_stockout_items_are_actually_selling(self, metrics: Metrics):
        risk = metrics.stockout_risk()
        if risk.empty:
            pytest.skip("nothing is close to running out")
        assert (risk["monthly_rate"] > 0).all()
        assert (risk["cover_months"] < 1).all()


class TestCustomers:

    def test_walkin_excluded_from_customer_analysis(self, metrics: Metrics):
        """
        Customer 1 is the anonymous till. Its money is revenue, but it is not
        a customer - treating it as one would make it the biggest by far.
        """
        summary = metrics.customer_summary()
        assert metrics.walkin_id not in set(summary["customer_id"])

        walkin_revenue = metrics.data_quality()["walkin"]["revenue"]
        assert walkin_revenue > 0, "the walk-in till should still earn revenue"

    def test_receivables_count_positive_balances_only(self, metrics: Metrics,
                                                      expected_totals):
        """
        Two customers are in credit. Netting them off would understate what
        is actually owed, and you cannot spend somebody else's credit.
        """
        summary = metrics.receivables_summary()
        assert approximately(summary["total"], expected_totals["receivables"], 0.15)
        assert (metrics.receivables()["balance"] > 0).all()
        assert summary["credit_customers"] > 0
        assert summary["credit_balances"] < 0

    def test_receivable_shares_add_up(self, metrics: Metrics):
        r = metrics.receivables()
        assert abs(r["share_of_receivables"].sum() - 1.0) < 0.001

    def test_call_list_only_has_lapsed_regulars(self, metrics: Metrics):
        calls = metrics.call_list()
        if calls.empty:
            pytest.skip("no lapsed customers")
        lapsed_days = float(metrics.t("customers.lapsed_days", 90))
        min_visits = int(metrics.t("customers.lapsed_min_visits", 2))
        assert (calls["recency_days"] > lapsed_days).all()
        assert (calls["visits"] >= min_visits).all()
        # Sorted by what they were worth, biggest first.
        assert calls["revenue"].is_monotonic_decreasing

    def test_every_customer_gets_one_segment(self, metrics: Metrics):
        summary = metrics.customer_summary()
        assert summary["segment"].notna().all()
        counts = metrics.segment_summary()
        assert counts["customers"].sum() == len(summary)

    def test_pareto_is_cumulative(self, metrics: Metrics):
        p = metrics.customer_pareto()
        assert p["cumulative_share"].is_monotonic_increasing
        assert abs(p["cumulative_share"].iloc[-1] - 1.0) < 0.001


class TestConsistency:

    def test_monthly_adds_up_to_the_total(self, metrics: Metrics):
        monthly = metrics.monthly()
        assert abs(monthly["revenue"].sum() - metrics.headline()["revenue"]) < 1.0
        assert abs(monthly["gross_profit"].sum() -
                   metrics.headline()["gross_profit"]) < 1.0

    def test_family_margin_adds_up(self, metrics: Metrics):
        fam = metrics.family_margin()
        assert abs(fam["revenue"].sum() - metrics.headline()["revenue"]) < 1.0

    def test_product_revenue_adds_up(self, metrics: Metrics):
        products = metrics.product_margin()
        # Only products that ever sold appear, so this is a close match, not
        # an exact one - lines against deleted products are the difference.
        assert approximately(products["revenue_all"].sum(),
                             metrics.headline()["revenue"], 0.02)

    def test_no_future_dated_sales(self, metrics: Metrics):
        last = metrics.data_range["last"]
        assert last <= datetime.datetime.now() + datetime.timedelta(days=1)

    def test_verification_table(self, metrics: Metrics):
        """
        The figures the extraction was originally signed off against. All-time
        totals may only grow; point-in-time ones may drift.
        """
        for check in metrics.verification():
            value, expected = check["value"], check["expected"]
            assert value is not None, f"{check['key']} could not be computed"
            if check["grows"]:
                assert value >= expected * 0.9999, (
                    f"{check['key']} is {value:,.0f}, BELOW the {expected:,.0f} "
                    "checked by hand. Rows or money are being lost.")
                assert value <= expected * 1.5, (
                    f"{check['key']} is {value:,.0f}, far above {expected:,.0f}. "
                    "Something is being double counted.")
            else:
                assert approximately(value, expected, 0.20), (
                    f"{check['key']} is {value:,.0f} against {expected:,.0f}.")


class TestDataQuality:

    def test_every_quirk_is_quantified(self, metrics: Metrics):
        dq = metrics.data_quality()
        for section in ("collections", "header_cost", "returns", "missing_cost",
                        "negative_stock", "stale_last_sold", "purchases",
                        "credit_balances", "walkin"):
            assert section in dq, f"the data quality panel is missing {section}"

    def test_reported_margin_agrees_except_where_cost_is_missing(self, metrics: Metrics):
        """
        Our own profit figure and the one the POS recorded should agree
        everywhere the cost is known. They differ only on the lines with no
        cost, which is a good sign the reading is right.
        """
        gap = abs(metrics.data_quality()["reported_vs_calculated_margin"]["difference"])
        revenue = metrics.headline()["revenue"]
        assert gap / revenue < 0.001, \
            "reported and calculated profit disagree by more than a rounding error"

    def test_purchase_totals_are_marked_as_not_reconciling(self, metrics: Metrics):
        """
        The purchase lines add up to about twice the cost of everything ever
        sold. That cannot be right, and the tool must say so rather than
        presenting it as spend.
        """
        coverage = metrics.purchase_coverage()
        assert coverage["value_ratio"] is not None
        assert coverage["value_reconciles"] is False, \
            "if purchases now reconcile, the warning on the Suppliers page " \
            "should be removed"
