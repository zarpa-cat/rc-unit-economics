"""Tests for LTV forecasting."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from rc_unit_economics.forecast import ChurnScenario, LTVForecaster
from rc_unit_economics.models import (
    OperationCost,
    PortfolioSummary,
    SubscriberEconomics,
    SubscriberStatus,
    SubscriptionRevenue,
)


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
) -> SubscriberEconomics:
    """Create a subscriber with a fixed monthly_cost_usd by placing ops spanning 30 days."""
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
        revenue=make_revenue(subscriber_id=subscriber_id, mrr_usd=mrr_usd),
        operations=ops,
    )


def make_portfolio(subs: list[SubscriberEconomics] | None = None) -> PortfolioSummary:
    if subs is None:
        subs = [make_sub()]
    return PortfolioSummary(subscribers=subs)


class TestChurnScenario:
    def test_default_scenarios_count(self) -> None:
        assert len(LTVForecaster.DEFAULT_SCENARIOS) == 3

    def test_default_scenario_labels(self) -> None:
        labels = [s.label for s in LTVForecaster.DEFAULT_SCENARIOS]
        assert "optimistic" in labels
        assert "realistic" in labels
        assert "pessimistic" in labels

    def test_optimistic_lowest_churn(self) -> None:
        scenarios = LTVForecaster.DEFAULT_SCENARIOS
        opt = next(s for s in scenarios if s.label == "optimistic")
        pess = next(s for s in scenarios if s.label == "pessimistic")
        assert opt.monthly_churn_rate < pess.monthly_churn_rate


class TestForecastSubscriber:
    def test_zero_churn_full_months(self) -> None:
        """With 0% churn, projected LTV = mrr * months."""
        forecaster = LTVForecaster()
        sub = make_sub(mrr_usd=10.0, monthly_cost=0.0)
        scenario = ChurnScenario("zero", 0.0)
        result = forecaster.forecast_subscriber(sub, scenario, months=12)
        assert abs(result.projected_ltv_usd - 120.0) < 1e-9
        assert result.projected_cost_usd == 0.0
        assert result.projected_profit_usd == pytest.approx(120.0)

    def test_full_churn_zero_value(self) -> None:
        """With 100% churn, no future value."""
        forecaster = LTVForecaster()
        sub = make_sub(mrr_usd=10.0, monthly_cost=1.0)
        scenario = ChurnScenario("obliterated", 1.0)
        result = forecaster.forecast_subscriber(sub, scenario, months=12)
        assert result.projected_ltv_usd == 0.0
        assert result.projected_cost_usd == 0.0
        assert result.projected_profit_usd == 0.0

    def test_partial_churn_less_than_zero_churn(self) -> None:
        """With some churn, projected LTV < zero-churn LTV."""
        forecaster = LTVForecaster()
        sub = make_sub(mrr_usd=10.0, monthly_cost=0.0)
        zero_churn = ChurnScenario("zero", 0.0)
        some_churn = ChurnScenario("some", 0.05)
        r0 = forecaster.forecast_subscriber(sub, zero_churn, months=12)
        r5 = forecaster.forecast_subscriber(sub, some_churn, months=12)
        assert r5.projected_ltv_usd < r0.projected_ltv_usd

    def test_break_even_month_profitable_sub(self) -> None:
        """A profitable sub (mrr > monthly_cost) should break even early."""
        forecaster = LTVForecaster()
        sub = make_sub(mrr_usd=10.0, monthly_cost=1.0)
        scenario = ChurnScenario("zero", 0.0)
        result = forecaster.forecast_subscriber(sub, scenario, months=12)
        # Net MRR is positive so break-even is month 1
        assert result.break_even_month == 1

    def test_break_even_month_none_for_unprofitable(self) -> None:
        """A sub where cost > mrr never breaks even."""
        forecaster = LTVForecaster()
        sub = make_sub(mrr_usd=1.0, monthly_cost=5.0)
        scenario = ChurnScenario("zero", 0.0)
        result = forecaster.forecast_subscriber(sub, scenario, months=12)
        assert result.break_even_month is None

    def test_net_mrr_field(self) -> None:
        forecaster = LTVForecaster()
        sub = make_sub(mrr_usd=9.0, monthly_cost=3.0)
        scenario = ChurnScenario("zero", 0.0)
        result = forecaster.forecast_subscriber(sub, scenario, months=12)
        assert result.net_mrr_usd == pytest.approx(6.0, abs=1e-3)

    def test_subscriber_id_preserved(self) -> None:
        forecaster = LTVForecaster()
        sub = make_sub(subscriber_id="alice_42", mrr_usd=5.0, monthly_cost=0.5)
        scenario = ChurnScenario("realistic", 0.05)
        result = forecaster.forecast_subscriber(sub, scenario, months=6)
        assert result.subscriber_id == "alice_42"
        assert result.months == 6
        assert result.scenario is scenario

    def test_geometric_series_correctness(self) -> None:
        """Verify geometric series formula matches manual sum."""
        forecaster = LTVForecaster()
        mrr = 10.0
        churn = 0.05
        months = 6
        sub = make_sub(mrr_usd=mrr, monthly_cost=0.0)
        scenario = ChurnScenario("test", churn)
        result = forecaster.forecast_subscriber(sub, scenario, months=months)
        # Manual: sum_{t=1}^{6} 10 * 0.95^t
        manual = sum(mrr * ((1 - churn) ** t) for t in range(1, months + 1))
        assert result.projected_ltv_usd == pytest.approx(manual, rel=1e-6)


class TestForecastPortfolio:
    def test_portfolio_sums_subscriber_forecasts(self) -> None:
        forecaster = LTVForecaster()
        subs = [make_sub("u1", 10.0, 1.0), make_sub("u2", 20.0, 2.0)]
        portfolio = make_portfolio(subs)
        scenario = ChurnScenario("zero", 0.0)
        pf = forecaster.forecast_portfolio(portfolio, scenario, months=12)
        expected_rev = sum(sf.projected_ltv_usd for sf in pf.subscriber_forecasts)
        assert pf.projected_revenue_usd == pytest.approx(expected_rev)

    def test_expected_survivors_zero_churn(self) -> None:
        """With 0% churn, all subscribers survive."""
        forecaster = LTVForecaster()
        subs = [make_sub("u1"), make_sub("u2"), make_sub("u3")]
        portfolio = make_portfolio(subs)
        scenario = ChurnScenario("zero", 0.0)
        pf = forecaster.forecast_portfolio(portfolio, scenario, months=12)
        assert pf.expected_survivors == pytest.approx(3.0)

    def test_expected_survivors_full_churn(self) -> None:
        """With 100% monthly churn, no survivors after 1+ months."""
        forecaster = LTVForecaster()
        subs = [make_sub("u1"), make_sub("u2")]
        portfolio = make_portfolio(subs)
        scenario = ChurnScenario("full", 1.0)
        pf = forecaster.forecast_portfolio(portfolio, scenario, months=12)
        assert pf.expected_survivors == pytest.approx(0.0)

    def test_all_scenarios_returns_three(self) -> None:
        forecaster = LTVForecaster()
        portfolio = make_portfolio()
        results = forecaster.all_scenarios(portfolio, months=12)
        assert len(results) == 3

    def test_optimistic_better_than_pessimistic(self) -> None:
        """Optimistic scenario projects higher revenue than pessimistic."""
        forecaster = LTVForecaster()
        subs = [make_sub("u1", 10.0, 0.0)]
        portfolio = make_portfolio(subs)
        results = forecaster.all_scenarios(portfolio, months=12)
        opt_pf = next(pf for pf in results if pf.scenario.label == "optimistic")
        pess_pf = next(pf for pf in results if pf.scenario.label == "pessimistic")
        assert opt_pf.projected_revenue_usd > pess_pf.projected_revenue_usd

    def test_empty_portfolio_forecast(self) -> None:
        forecaster = LTVForecaster()
        portfolio = make_portfolio([])
        scenario = ChurnScenario("realistic", 0.05)
        pf = forecaster.forecast_portfolio(portfolio, scenario, months=12)
        assert pf.projected_revenue_usd == 0.0
        assert pf.projected_profit_usd == 0.0
        assert pf.expected_survivors == 0.0

    def test_months_param_respected(self) -> None:
        """Forecast for 6 months should project less than 12 months."""
        forecaster = LTVForecaster()
        portfolio = make_portfolio()
        scenario = ChurnScenario("zero", 0.0)
        pf6 = forecaster.forecast_portfolio(portfolio, scenario, months=6)
        pf12 = forecaster.forecast_portfolio(portfolio, scenario, months=12)
        assert pf6.projected_revenue_usd < pf12.projected_revenue_usd
        assert pf6.months == 6
        assert pf12.months == 12
