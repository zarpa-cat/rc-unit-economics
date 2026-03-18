"""Tests for UnitEconomicsAnalyzer."""

from __future__ import annotations

from datetime import datetime

import pytest

from rc_unit_economics.analyzer import UnitEconomicsAnalyzer
from rc_unit_economics.models import (
    OperationCost,
    SubscriberStatus,
    SubscriptionRevenue,
)


def make_revenue(
    subscriber_id: str,
    ltv: float = 9.99,
    mrr: float = 9.99,
) -> SubscriptionRevenue:
    return SubscriptionRevenue(
        subscriber_id=subscriber_id,
        product_id="monthly_pro",
        entitlement_id="premium",
        status=SubscriberStatus.ACTIVE,
        mrr_usd=mrr,
        ltv_usd=ltv,
        first_seen=datetime(2026, 3, 1),
        last_seen=datetime(2026, 3, 18),
    )


def make_op(subscriber_id: str, amount: int = 10) -> OperationCost:
    return OperationCost(
        subscriber_id=subscriber_id,
        operation="test_op",
        amount=amount,
        timestamp=datetime(2026, 3, 10),
        usd_per_credit=0.001,
    )


class TestUnitEconomicsAnalyzer:
    def setup_method(self) -> None:
        self.analyzer = UnitEconomicsAnalyzer()

    def test_analyze_subscriber_assigns_costs(self) -> None:
        rev = make_revenue("user_1")
        costs = [make_op("user_1"), make_op("user_1"), make_op("user_2")]
        econ = self.analyzer.analyze_subscriber(rev, costs)
        assert econ.operation_count == 2  # only user_1's ops
        assert econ.subscriber_id == "user_1"

    def test_analyze_subscriber_no_costs(self) -> None:
        rev = make_revenue("user_1")
        econ = self.analyzer.analyze_subscriber(rev, [])
        assert econ.total_cost_usd == 0.0
        assert econ.is_profitable is True

    def test_build_portfolio_basic(self) -> None:
        revenues = [make_revenue("user_1"), make_revenue("user_2")]
        costs = [make_op("user_1"), make_op("user_2"), make_op("user_2")]
        portfolio = self.analyzer.build_portfolio(revenues, costs)
        assert portfolio.total_subscribers == 2

    def test_build_portfolio_cost_only_subscriber(self) -> None:
        """Subscribers with costs but no RC revenue record appear in portfolio."""
        revenues = [make_revenue("user_1")]
        costs = [make_op("user_1"), make_op("ghost_user")]  # ghost_user has no revenue record
        portfolio = self.analyzer.build_portfolio(revenues, costs)
        assert portfolio.total_subscribers == 2  # user_1 + ghost_user

    def test_cost_only_subscriber_has_unknown_status(self) -> None:
        revenues = []
        costs = [make_op("ghost_user")]
        portfolio = self.analyzer.build_portfolio(revenues, costs)
        ghost = next(s for s in portfolio.subscribers if s.subscriber_id == "ghost_user")
        assert ghost.revenue.status == SubscriberStatus.UNKNOWN
        assert ghost.revenue.ltv_usd == 0.0

    def test_cost_only_subscriber_is_unprofitable(self) -> None:
        """A subscriber with costs but zero revenue is unprofitable."""
        revenues = []
        costs = [make_op("ghost_user", amount=100)]
        portfolio = self.analyzer.build_portfolio(revenues, costs)
        ghost = portfolio.subscribers[0]
        assert ghost.is_profitable is False
        assert ghost.total_cost_usd == pytest.approx(0.10)

    def test_build_portfolio_aggregate_revenue(self) -> None:
        revenues = [make_revenue("user_1", ltv=10.0), make_revenue("user_2", ltv=5.0)]
        portfolio = self.analyzer.build_portfolio(revenues, [])
        assert portfolio.total_revenue_usd == pytest.approx(15.0)

    def test_build_portfolio_aggregate_cost(self) -> None:
        revenues = [make_revenue("user_1"), make_revenue("user_2")]
        costs = [make_op("user_1", amount=100), make_op("user_2", amount=50)]  # $0.10 + $0.05
        portfolio = self.analyzer.build_portfolio(revenues, costs)
        assert portfolio.total_cost_usd == pytest.approx(0.15)

    def test_build_empty_portfolio(self) -> None:
        portfolio = self.analyzer.build_portfolio([], [])
        assert portfolio.total_subscribers == 0
        assert portfolio.total_revenue_usd == 0.0
        assert portfolio.total_cost_usd == 0.0

    def test_portfolio_margin_healthy(self) -> None:
        """High LTV, low cost → healthy margin."""
        revenues = [make_revenue("user_1", ltv=100.0)]
        costs = [make_op("user_1", amount=100)]  # $0.10 cost on $100 rev = 99.9% margin
        portfolio = self.analyzer.build_portfolio(revenues, costs)
        assert portfolio.portfolio_margin_pct > 99.0

    def test_portfolio_margin_underwater(self) -> None:
        """Cost exceeds revenue → negative margin."""
        revenues = [make_revenue("user_1", ltv=1.0)]
        costs = [make_op("user_1", amount=2000)]  # $2.00 cost on $1.00 rev
        portfolio = self.analyzer.build_portfolio(revenues, costs)
        assert portfolio.portfolio_margin_pct < 0
