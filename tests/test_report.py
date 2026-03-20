"""Tests for PortfolioReporter."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from rc_unit_economics.models import (
    OperationCost,
    PortfolioSummary,
    SubscriberEconomics,
    SubscriberStatus,
    SubscriptionRevenue,
)
from rc_unit_economics.report import PortfolioReporter


def make_revenue(
    subscriber_id: str = "user_1",
    ltv_usd: float = 9.99,
    mrr_usd: float = 9.99,
    entitlement_id: str | None = "premium",
) -> SubscriptionRevenue:
    return SubscriptionRevenue(
        subscriber_id=subscriber_id,
        product_id="monthly_pro",
        entitlement_id=entitlement_id,
        status=SubscriberStatus.ACTIVE,
        mrr_usd=mrr_usd,
        ltv_usd=ltv_usd,
        first_seen=datetime(2026, 1, 1),
        last_seen=datetime(2026, 3, 1),
    )


def make_sub(
    subscriber_id: str = "user_1",
    mrr_usd: float = 9.99,
    monthly_cost: float = 1.0,
    entitlement_id: str | None = "premium",
) -> SubscriberEconomics:
    now = datetime(2026, 3, 1)
    ops = []
    if monthly_cost > 0:
        ops = [
            OperationCost(
                subscriber_id=subscriber_id,
                operation="inference",
                amount=1,
                timestamp=now - timedelta(days=30),
                usd_per_credit=monthly_cost / 2,
            ),
            OperationCost(
                subscriber_id=subscriber_id,
                operation="inference",
                amount=1,
                timestamp=now,
                usd_per_credit=monthly_cost / 2,
            ),
        ]
    return SubscriberEconomics(
        subscriber_id=subscriber_id,
        revenue=make_revenue(
            subscriber_id=subscriber_id, mrr_usd=mrr_usd, entitlement_id=entitlement_id
        ),
        operations=ops,
    )


def make_portfolio(subs: list[SubscriberEconomics] | None = None) -> PortfolioSummary:
    if subs is None:
        subs = [make_sub("u1"), make_sub("u2", mrr_usd=5.0, monthly_cost=3.0)]
    return PortfolioSummary(subscribers=subs)


class TestMarkdownReport:
    def test_markdown_contains_portfolio_report_header(self) -> None:
        reporter = PortfolioReporter(make_portfolio())
        md = reporter.as_markdown()
        assert "# Portfolio Report" in md

    def test_markdown_contains_summary_section(self) -> None:
        reporter = PortfolioReporter(make_portfolio())
        md = reporter.as_markdown()
        assert "## Summary" in md

    def test_markdown_contains_forecast_section(self) -> None:
        reporter = PortfolioReporter(make_portfolio())
        md = reporter.as_markdown(months=6)
        assert "6-Month LTV Forecast" in md

    def test_markdown_contains_top_profitable(self) -> None:
        reporter = PortfolioReporter(make_portfolio())
        md = reporter.as_markdown()
        assert "## Top Profitable" in md

    def test_markdown_contains_at_risk(self) -> None:
        reporter = PortfolioReporter(make_portfolio())
        md = reporter.as_markdown()
        assert "## At Risk" in md

    def test_markdown_contains_cohort_breakdown(self) -> None:
        reporter = PortfolioReporter(make_portfolio())
        md = reporter.as_markdown()
        assert "## Cohort Breakdown" in md

    def test_markdown_months_param_reflected(self) -> None:
        reporter = PortfolioReporter(make_portfolio())
        md12 = reporter.as_markdown(months=12)
        md24 = reporter.as_markdown(months=24)
        assert "12-Month LTV Forecast" in md12
        assert "24-Month LTV Forecast" in md24

    def test_markdown_empty_portfolio(self) -> None:
        reporter = PortfolioReporter(PortfolioSummary(subscribers=[]))
        md = reporter.as_markdown()
        assert "# Portfolio Report" in md
        assert "## Summary" in md
        # Should handle empty gracefully
        assert "## Top Profitable" in md


class TestJsonReport:
    def test_json_is_parseable(self) -> None:
        reporter = PortfolioReporter(make_portfolio())
        raw = reporter.as_json()
        data = json.loads(raw)
        assert isinstance(data, dict)

    def test_json_has_summary_key(self) -> None:
        reporter = PortfolioReporter(make_portfolio())
        data = json.loads(reporter.as_json())
        assert "summary" in data

    def test_json_has_cohorts_key(self) -> None:
        reporter = PortfolioReporter(make_portfolio())
        data = json.loads(reporter.as_json())
        assert "cohorts" in data

    def test_json_has_forecasts_key(self) -> None:
        reporter = PortfolioReporter(make_portfolio())
        data = json.loads(reporter.as_json())
        assert "forecasts" in data

    def test_json_summary_fields(self) -> None:
        reporter = PortfolioReporter(make_portfolio())
        data = json.loads(reporter.as_json())
        summary = data["summary"]
        assert "total_subscribers" in summary
        assert "profitable_count" in summary
        assert "mrr_usd" in summary

    def test_json_forecasts_list(self) -> None:
        reporter = PortfolioReporter(make_portfolio())
        data = json.loads(reporter.as_json())
        assert isinstance(data["forecasts"], list)
        assert len(data["forecasts"]) == 3  # 3 scenarios

    def test_json_empty_portfolio(self) -> None:
        reporter = PortfolioReporter(PortfolioSummary(subscribers=[]))
        data = json.loads(reporter.as_json())
        assert data["summary"]["total_subscribers"] == 0
        assert data["forecasts"] is not None

    def test_json_months_param(self) -> None:
        reporter = PortfolioReporter(make_portfolio())
        data6 = json.loads(reporter.as_json(months=6))
        data12 = json.loads(reporter.as_json(months=12))
        assert data6["forecasts"][0]["months"] == 6
        assert data12["forecasts"][0]["months"] == 12
