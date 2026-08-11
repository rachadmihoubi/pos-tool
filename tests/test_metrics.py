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

    def test_shrinkage_events_does_not_raise(self, metrics: Metrics):
        events = metrics.shrinkage_events()
        assert not events.empty, "expected at least the one live stocktake"
        assert "cost_net" in events.columns

    def test_shrinkage_events_handles_missing_table(self, metrics: Metrics, monkeypatch):
        """
        Must degrade to an empty frame, never raise, if a database somehow
        has no StockTake table at all.
        """
        original = Metrics._has_table
        monkeypatch.setattr(
            Metrics, "_has_table",
            lambda self, name: False if name == "StockTake" else original(self, name))
        assert metrics.shrinkage_events().empty


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

    def test_every_debtor_gets_exactly_one_credit_risk_tier(self, metrics: Metrics):
        r = metrics.receivables()
        assert (r["balance"] > 0).all(), "receivables() should only ever list debtors"
        assert r["credit_risk"].isin(["low", "medium", "high"]).all()

    def test_non_debtors_get_no_credit_risk_tier(self, metrics: Metrics):
        cs = metrics.customer_summary()
        non_debtors = cs[cs["balance"] <= 0]
        if non_debtors.empty:
            pytest.skip("every customer currently owes something")
        assert non_debtors["credit_risk"].isna().all()

    def test_credit_risk_survives_a_customer_who_never_paid(self, metrics: Metrics):
        """
        A debtor with zero rows in `collections` (never made an account
        payment) must still get a tier - the days-since-last-payment
        lookup must fall back to "never", not raise or produce NaN-poisoned
        comparisons.
        """
        r = metrics.receivables()
        never_paid_ids = set(r["customer_id"]) - set(metrics.collections["customer_id"])
        if not never_paid_ids:
            pytest.skip("every current debtor has made at least one payment")
        some_id = next(iter(never_paid_ids))
        row = r[r["customer_id"] == some_id].iloc[0]
        assert row["credit_risk"] in ("low", "medium", "high")

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


class TestCash:

    def test_income_statement_matches_headline(self, metrics: Metrics):
        """
        `income_statement(days=...)` uses the same precise cutoff as
        `headline(days=...)`, so its revenue total must match exactly - a
        cheap regression net against the two ever drifting apart.
        """
        inc = metrics.income_statement(days=365)
        headline = metrics.headline(days=365)
        assert abs(inc["revenue"] - headline["revenue"]) < 0.01
        assert abs(inc["gross_profit"] - headline["gross_profit"]) < 0.01

    def test_income_statement_all_time(self, metrics: Metrics):
        inc = metrics.income_statement()
        headline = metrics.headline()
        assert abs(inc["revenue"] - headline["revenue"]) < 0.01
        assert abs(inc["cogs"] - (inc["revenue"] - inc["gross_profit"])) < 0.01

    def test_cash_position_totals_match_tickets(self, metrics: Metrics):
        cash = metrics.cash_position()
        tk = metrics.tickets
        assert abs(cash["totals"]["cash"] - float(tk["cash"].sum())) < 0.01
        assert abs(cash["total"] - (float(tk["cash"].sum()) + float(tk["cheque"].sum())
                                     + float(tk["transfer"].sum())
                                     + float(tk["credit_account"].sum()))) < 0.01

    def test_till_reconciliation_does_not_raise(self, metrics: Metrics):
        till = metrics.till_reconciliation()
        assert not till.empty, "expected at least the one live batch"

    def test_till_reconciliation_expected_always_populated(self, metrics: Metrics):
        """
        The "expected" side must always be computable, even for a session
        that has never closed - it's built from real tickets, not from the
        POS's own (possibly-stale) running total.
        """
        till = metrics.till_reconciliation()
        assert till["expected_total"].notna().all()

    def test_till_reconciliation_open_batch_has_no_recorded_close(self, metrics: Metrics):
        """
        The one live batch has never been closed - its recorded-close
        fields must be None, not a fabricated zero, and it must be
        flagged as still open.
        """
        till = metrics.till_reconciliation()
        open_batches = till[till["is_open"]]
        assert not open_batches.empty
        assert (~open_batches["has_recorded_close"]).all()
        assert open_batches["recorded_total"].isna().all()

    def test_till_reconciliation_expected_matches_real_sales(self, metrics: Metrics):
        """
        Cross-check: the expected total (opening float + real ticket sums)
        should be in the same ballpark as revenue + collections all-time -
        proof this reads real tickets, not the frozen Batch.*Shift columns
        (which sit at 0 on the live database despite real sales).
        """
        till = metrics.till_reconciliation()
        headline = metrics.headline()
        expected_total = float(till["expected_total"].sum())
        real_scale = headline["revenue"] + headline["collections"]
        assert expected_total > real_scale * 0.5, (
            "till expected_total looks too small next to real revenue+collections - "
            "may be reading a stale POS field instead of the actual tickets")

    def test_cash_position_handles_no_data(self, metrics: Metrics):
        # A window with nothing in it must degrade cleanly, not raise.
        far_future = datetime.date(2099, 1, 1)
        cash = metrics.cash_position(start=far_future, end=far_future)
        assert cash["total"] == 0.0
        assert cash["by_month"].empty


class TestCatalog:

    def test_new_arrivals_upper_bound(self, metrics: Metrics):
        """
        A huge window should return (at most) every item that has a
        DateCreated at all - the sanity ceiling that proves the filter
        isn't accidentally inclusive of items with no date.
        """
        wide = metrics.new_arrivals(days=99999)
        with_date = metrics.items["date_created"].notna().sum()
        assert len(wide) <= with_date
        assert len(wide) > 0, "expected at least one item with a creation date"

    def test_new_arrivals_zero_days_is_empty_or_tiny(self, metrics: Metrics):
        # days=0 means "created since this exact moment" - never raises,
        # never returns anything created before right now.
        today = metrics.new_arrivals(days=0)
        assert today.empty or (today["date_created"] >= metrics.now.replace(
            hour=0, minute=0, second=0, microsecond=0)).all()

    def test_new_arrivals_sorted_newest_first(self, metrics: Metrics):
        recent = metrics.new_arrivals(days=730)
        if len(recent) < 2:
            pytest.skip("not enough recent items to check ordering")
        dates = recent["date_created"].tolist()
        assert dates == sorted(dates, reverse=True)


class TestFamilyMarginOutliers:

    def test_every_outlier_trails_its_family_by_the_threshold(self, metrics: Metrics):
        threshold = float(metrics.t("margin.family_benchmark_pp", 15))
        out = metrics.family_margin_outliers()
        if out.empty:
            pytest.skip("no outliers in this database at the default threshold")
        assert (out["gap_pp"] >= threshold - 0.001).all()
        # The family really does out-earn the product on every flagged row.
        assert (out["family_margin_pct"] > out["margin_pct"]).all()

    def test_a_tighter_threshold_never_finds_more_than_a_looser_one(self, metrics: Metrics):
        loose = metrics.family_margin_outliers(threshold_pp=5)
        tight = metrics.family_margin_outliers(threshold_pp=30)
        assert len(tight) <= len(loose)

    def test_zero_threshold_does_not_raise(self, metrics: Metrics):
        # Every product with any gap at all would qualify - just checking
        # this edge case degrades cleanly, not that it's a sensible setting.
        metrics.family_margin_outliers(threshold_pp=0.0001)


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
