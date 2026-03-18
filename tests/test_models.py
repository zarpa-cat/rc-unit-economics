"""Tests for unit economics models."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

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
    status: SubscriberStatus = SubscriberStatus.ACTIVE,
) -> SubscriptionRevenue:
    return SubscriptionRevenue(
        subscriber_id=subscriber_id,
        product_id="monthly_pro",
        entitlement_id="premium",
        status=status,
        mrr_usd=mrr_usd,
        ltv_usd=ltv_usd,
        first_seen=datetime(2026, 3, 1),
        last_seen=datetime(2026, 3, 18),
    )


def make_op(
    subscriber_id: str = "user_1",
    operation: str = "generate_report",
    amount: int = 10,
    ts_offset_days: int = 0,
    usd_per_credit: float = 0.001,
) -> OperationCost:
    return OperationCost(
        subscriber_id=subscriber_id,
        operation=operation,
        amount=amount,
        timestamp=datetime(2026, 3, 1) + timedelta(days=ts_offset_days),
        usd_per_credit=usd_per_credit,
    )


class TestOperationCost:
    def test_usd_cost(self) -> None:
        op = make_op(amount=10, usd_per_credit=0.001)
        assert op.usd_cost == pytest.approx(0.01)

    def test_zero_cost_credit(self) -> None:
        op = make_op(amount=0, usd_per_credit=0.001)
        assert op.usd_cost == 0.0

    def test_custom_rate(self) -> None:
        op = make_op(amount=100, usd_per_credit=0.005)
        assert op.usd_cost == pytest.approx(0.5)


class TestSubscriberEconomics:
    def test_profitable_subscriber(self) -> None:
        rev = make_revenue(ltv_usd=9.99)
        ops = [make_op(amount=10, usd_per_credit=0.001) for _ in range(5)]  # $0.05 total
        econ = SubscriberEconomics(subscriber_id="user_1", revenue=rev, operations=ops)

        assert econ.total_cost_usd == pytest.approx(0.05)
        assert econ.gross_profit_usd == pytest.approx(9.94)
        assert econ.is_profitable is True

    def test_unprofitable_subscriber(self) -> None:
        rev = make_revenue(ltv_usd=1.00)
        ops = [make_op(amount=1000, usd_per_credit=0.001)]  # $1.00 cost
        econ = SubscriberEconomics(subscriber_id="user_1", revenue=rev, operations=ops)

        assert econ.total_cost_usd == pytest.approx(1.00)
        assert econ.gross_profit_usd == pytest.approx(0.0)
        assert econ.is_profitable is False

    def test_zero_cost_subscriber(self) -> None:
        rev = make_revenue(ltv_usd=9.99)
        econ = SubscriberEconomics(subscriber_id="user_1", revenue=rev, operations=[])

        assert econ.total_cost_usd == 0.0
        assert econ.gross_profit_usd == pytest.approx(9.99)
        assert econ.is_profitable is True

    def test_gross_margin_calculation(self) -> None:
        rev = make_revenue(ltv_usd=10.00)
        ops = [make_op(amount=1000, usd_per_credit=0.001)]  # $1.00 cost
        econ = SubscriberEconomics(subscriber_id="user_1", revenue=rev, operations=ops)

        assert econ.gross_margin_pct == pytest.approx(90.0)

    def test_zero_revenue_margin(self) -> None:
        rev = make_revenue(ltv_usd=0.0)
        econ = SubscriberEconomics(subscriber_id="user_1", revenue=rev, operations=[])
        assert econ.gross_margin_pct == 0.0

    def test_operation_count(self) -> None:
        rev = make_revenue()
        ops = [make_op() for _ in range(7)]
        econ = SubscriberEconomics(subscriber_id="user_1", revenue=rev, operations=ops)
        assert econ.operation_count == 7

    def test_cost_per_operation(self) -> None:
        rev = make_revenue()
        ops = [make_op(amount=10, usd_per_credit=0.001) for _ in range(4)]  # $0.01 each
        econ = SubscriberEconomics(subscriber_id="user_1", revenue=rev, operations=ops)
        assert econ.cost_per_operation == pytest.approx(0.01)

    def test_no_ops_cost_per_op(self) -> None:
        rev = make_revenue()
        econ = SubscriberEconomics(subscriber_id="user_1", revenue=rev, operations=[])
        assert econ.cost_per_operation == 0.0

    def test_monthly_cost_multiday_span(self) -> None:
        """Monthly cost should extrapolate from span."""
        rev = make_revenue()
        ops = [
            make_op(amount=10, usd_per_credit=0.001, ts_offset_days=0),  # day 0
            make_op(amount=10, usd_per_credit=0.001, ts_offset_days=10),  # day 10
        ]  # $0.02 over 10 days → $0.06/month
        econ = SubscriberEconomics(subscriber_id="user_1", revenue=rev, operations=ops)
        assert econ.monthly_cost_usd == pytest.approx(0.06, rel=0.01)

    def test_single_op_monthly_cost(self) -> None:
        """Single op returns its total as monthly cost (no span to extrapolate)."""
        rev = make_revenue()
        ops = [make_op(amount=10, usd_per_credit=0.001)]
        econ = SubscriberEconomics(subscriber_id="user_1", revenue=rev, operations=ops)
        assert econ.monthly_cost_usd == pytest.approx(0.01)


class TestPortfolioSummary:
    def _make_portfolio(self) -> PortfolioSummary:
        # user_1: profitable ($9.99 rev, $0.05 cost)
        rev1 = make_revenue("user_1", ltv_usd=9.99, mrr_usd=9.99)
        ops1 = [make_op("user_1", amount=10) for _ in range(5)]  # $0.05
        econ1 = SubscriberEconomics("user_1", rev1, ops1)

        # user_2: unprofitable ($1.00 rev, $2.00 cost)
        rev2 = make_revenue("user_2", ltv_usd=1.00, mrr_usd=0.0)
        ops2 = [make_op("user_2", amount=2000)]  # $2.00
        econ2 = SubscriberEconomics("user_2", rev2, ops2)

        # user_3: zero cost (trial)
        rev3 = make_revenue("user_3", ltv_usd=0.0, mrr_usd=0.0, status=SubscriberStatus.TRIAL)
        econ3 = SubscriberEconomics("user_3", rev3, [])

        return PortfolioSummary(subscribers=[econ1, econ2, econ3])

    def test_counts(self) -> None:
        p = self._make_portfolio()
        assert p.total_subscribers == 3
        assert p.profitable_count == 1  # user_1
        assert p.unprofitable_count == 1  # user_2 (cost > 0, not profitable)
        assert p.zero_cost_count == 1  # user_3 only (user_1 has $0.05 cost)

    def test_totals(self) -> None:
        p = self._make_portfolio()
        assert p.total_revenue_usd == pytest.approx(10.99)
        assert p.total_cost_usd == pytest.approx(2.05)
        assert p.total_gross_profit_usd == pytest.approx(8.94)

    def test_portfolio_margin(self) -> None:
        p = self._make_portfolio()
        expected = (8.94 / 10.99) * 100
        assert p.portfolio_margin_pct == pytest.approx(expected, rel=0.01)

    def test_mrr(self) -> None:
        p = self._make_portfolio()
        assert p.mrr_usd == pytest.approx(9.99)

    def test_top_unprofitable(self) -> None:
        p = self._make_portfolio()
        unprofitable = p.top_unprofitable(5)
        assert len(unprofitable) == 1
        assert unprofitable[0].subscriber_id == "user_2"

    def test_top_profitable(self) -> None:
        p = self._make_portfolio()
        profitable = p.top_profitable(5)
        assert len(profitable) == 1
        assert profitable[0].subscriber_id == "user_1"

    def test_zero_cost_count_excludes_with_cost(self) -> None:
        """zero_cost_count should only count subs with truly zero cost."""
        p = self._make_portfolio()
        # user_1 has $0.05 cost, user_2 has $2.00 cost, user_3 has $0.00 cost
        assert p.zero_cost_count == 1  # only user_3
