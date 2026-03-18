"""Domain models for unit economics analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class SubscriberStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    TRIAL = "trial"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass
class SubscriptionRevenue:
    """Revenue data for a single subscriber from RevenueCat."""

    subscriber_id: str
    product_id: str | None
    entitlement_id: str | None
    status: SubscriberStatus
    mrr_usd: float  # monthly recurring revenue in USD
    ltv_usd: float  # total revenue earned from this subscriber
    first_seen: datetime | None
    last_seen: datetime | None
    renewal_count: int = 0
    is_trial: bool = False
    trial_start: datetime | None = None
    billing_issues: bool = False


@dataclass
class OperationCost:
    """Cost data for a single operation from the billing meter audit log."""

    subscriber_id: str
    operation: str
    amount: int  # credits debited
    timestamp: datetime
    usd_per_credit: float = 0.0  # configured conversion rate

    @property
    def usd_cost(self) -> float:
        return self.amount * self.usd_per_credit


@dataclass
class SubscriberEconomics:
    """Unit economics for a single subscriber: revenue vs cost."""

    subscriber_id: str
    revenue: SubscriptionRevenue
    operations: list[OperationCost] = field(default_factory=list)

    @property
    def total_cost_usd(self) -> float:
        return sum(op.usd_cost for op in self.operations)

    @property
    def gross_profit_usd(self) -> float:
        return self.revenue.ltv_usd - self.total_cost_usd

    @property
    def gross_margin_pct(self) -> float:
        if self.revenue.ltv_usd == 0:
            return 0.0
        return (self.gross_profit_usd / self.revenue.ltv_usd) * 100

    @property
    def is_profitable(self) -> bool:
        return self.gross_profit_usd > 0

    @property
    def monthly_cost_usd(self) -> float:
        """Estimated monthly cost based on operation history."""
        if not self.operations:
            return 0.0
        if len(self.operations) == 1:
            return self.total_cost_usd
        # Sort by timestamp and compute cost rate
        sorted_ops = sorted(self.operations, key=lambda o: o.timestamp)
        span_days = (sorted_ops[-1].timestamp - sorted_ops[0].timestamp).days
        if span_days == 0:
            return self.total_cost_usd * 30  # assume daily rate if all in one day
        daily_rate = self.total_cost_usd / span_days
        return daily_rate * 30

    @property
    def cost_per_operation(self) -> float:
        if not self.operations:
            return 0.0
        return self.total_cost_usd / len(self.operations)

    @property
    def operation_count(self) -> int:
        return len(self.operations)


@dataclass
class PortfolioSummary:
    """Aggregate economics across all subscribers."""

    subscribers: list[SubscriberEconomics]

    @property
    def total_subscribers(self) -> int:
        return len(self.subscribers)

    @property
    def profitable_count(self) -> int:
        return sum(1 for s in self.subscribers if s.is_profitable)

    @property
    def unprofitable_count(self) -> int:
        return sum(1 for s in self.subscribers if not s.is_profitable and s.total_cost_usd > 0)

    @property
    def zero_cost_count(self) -> int:
        return sum(1 for s in self.subscribers if s.total_cost_usd == 0)

    @property
    def total_revenue_usd(self) -> float:
        return sum(s.revenue.ltv_usd for s in self.subscribers)

    @property
    def total_cost_usd(self) -> float:
        return sum(s.total_cost_usd for s in self.subscribers)

    @property
    def total_gross_profit_usd(self) -> float:
        return self.total_revenue_usd - self.total_cost_usd

    @property
    def portfolio_margin_pct(self) -> float:
        if self.total_revenue_usd == 0:
            return 0.0
        return (self.total_gross_profit_usd / self.total_revenue_usd) * 100

    @property
    def mrr_usd(self) -> float:
        return sum(s.revenue.mrr_usd for s in self.subscribers)

    @property
    def monthly_cost_run_rate_usd(self) -> float:
        return sum(s.monthly_cost_usd for s in self.subscribers)

    @property
    def break_even_subscriber_count(self) -> int | None:
        """Subscribers needed to cover fixed costs (if any)."""
        # For pure variable costs, break-even is always 0 fixed
        return None

    def top_unprofitable(self, n: int = 5) -> list[SubscriberEconomics]:
        return sorted(
            [s for s in self.subscribers if not s.is_profitable and s.total_cost_usd > 0],
            key=lambda s: s.gross_profit_usd,
        )[:n]

    def top_profitable(self, n: int = 5) -> list[SubscriberEconomics]:
        return sorted(
            [s for s in self.subscribers if s.is_profitable],
            key=lambda s: s.gross_profit_usd,
            reverse=True,
        )[:n]
