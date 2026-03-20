"""Cohort analysis: group subscribers by entitlement/plan and compute aggregate economics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .models import PortfolioSummary, SubscriberEconomics


@dataclass
class CostBucket:
    """Aggregated cost for a time window (e.g. a day or week)."""

    period_start: datetime
    period_label: str  # e.g. "2026-03-15"
    total_cost_usd: float
    operation_count: int


@dataclass
class CostTrend:
    """Cost trajectory for a single subscriber."""

    subscriber_id: str
    buckets: list[CostBucket]
    direction: str  # "rising", "falling", "flat", "insufficient_data"
    cost_delta_usd: float  # difference between last and first bucket
    at_risk: bool  # cost trending toward margin compression

    @property
    def slope_label(self) -> str:
        if self.direction == "rising":
            return f"↑ +${self.cost_delta_usd:.4f}"
        if self.direction == "falling":
            return f"↓ -${abs(self.cost_delta_usd):.4f}"
        if self.direction == "flat":
            return "→ stable"
        return "— n/a"


@dataclass
class CohortSummary:
    """Aggregate economics for a group of subscribers sharing an entitlement/plan."""

    cohort_id: str  # entitlement_id or "none" / "unknown"
    subscribers: list[SubscriberEconomics] = field(default_factory=list)

    @property
    def subscriber_count(self) -> int:
        return len(self.subscribers)

    @property
    def profitable_count(self) -> int:
        return sum(1 for s in self.subscribers if s.is_profitable)

    @property
    def unprofitable_count(self) -> int:
        return sum(1 for s in self.subscribers if not s.is_profitable and s.total_cost_usd > 0)

    @property
    def total_revenue_usd(self) -> float:
        return sum(s.revenue.ltv_usd for s in self.subscribers)

    @property
    def total_cost_usd(self) -> float:
        return sum(s.total_cost_usd for s in self.subscribers)

    @property
    def gross_profit_usd(self) -> float:
        return self.total_revenue_usd - self.total_cost_usd

    @property
    def gross_margin_pct(self) -> float:
        if self.total_revenue_usd == 0:
            return 0.0
        return (self.gross_profit_usd / self.total_revenue_usd) * 100

    @property
    def mrr_usd(self) -> float:
        return sum(s.revenue.mrr_usd for s in self.subscribers)

    @property
    def avg_cost_per_subscriber_usd(self) -> float:
        if not self.subscribers:
            return 0.0
        return self.total_cost_usd / len(self.subscribers)

    @property
    def avg_ops_per_subscriber(self) -> float:
        if not self.subscribers:
            return 0.0
        return sum(s.operation_count for s in self.subscribers) / len(self.subscribers)


@dataclass
class AlertedSubscriber:
    """A subscriber that has triggered an alert condition."""

    subscriber_id: str
    reason: str
    severity: str  # "warn" | "critical"
    gross_margin_pct: float
    monthly_cost_usd: float
    gross_profit_usd: float


class CohortAnalyzer:
    """Extend portfolio analysis with cohort grouping and cost trend detection."""

    def group_by_cohort(self, portfolio: PortfolioSummary) -> dict[str, CohortSummary]:
        """Group subscribers by their RevenueCat entitlement_id."""
        cohorts: dict[str, CohortSummary] = {}
        for sub in portfolio.subscribers:
            key = sub.revenue.entitlement_id or "no_entitlement"
            if key not in cohorts:
                cohorts[key] = CohortSummary(cohort_id=key)
            cohorts[key].subscribers.append(sub)
        return cohorts

    def compute_trend(
        self,
        subscriber: SubscriberEconomics,
        bucket_days: int = 7,
    ) -> CostTrend:
        """Compute cost trend for a subscriber by bucketing operations into time windows."""
        ops = sorted(subscriber.operations, key=lambda o: o.timestamp)

        if len(ops) < 2:
            return CostTrend(
                subscriber_id=subscriber.subscriber_id,
                buckets=[],
                direction="insufficient_data",
                cost_delta_usd=0.0,
                at_risk=False,
            )

        # Determine time range
        t_start = ops[0].timestamp
        t_end = ops[-1].timestamp
        span_days = max((t_end - t_start).days, 1)
        bucket_size_days = (
            max(bucket_days, span_days // 4) if span_days >= bucket_days else span_days
        )

        # Build buckets
        from datetime import timedelta

        buckets: list[CostBucket] = []
        bucket_start = t_start.replace(hour=0, minute=0, second=0, microsecond=0)
        bucket_delta = timedelta(days=bucket_size_days)

        while bucket_start <= t_end:
            bucket_end = bucket_start + bucket_delta
            bucket_ops = [o for o in ops if bucket_start <= o.timestamp < bucket_end]
            if bucket_ops:
                buckets.append(
                    CostBucket(
                        period_start=bucket_start,
                        period_label=bucket_start.strftime("%Y-%m-%d"),
                        total_cost_usd=sum(o.usd_cost for o in bucket_ops),
                        operation_count=len(bucket_ops),
                    )
                )
            bucket_start = bucket_end

        if len(buckets) < 2:
            direction = "flat"
            delta = 0.0
        else:
            first_cost = buckets[0].total_cost_usd
            last_cost = buckets[-1].total_cost_usd
            delta = last_cost - first_cost
            threshold = first_cost * 0.1  # 10% change threshold
            if delta > threshold:
                direction = "rising"
            elif delta < -threshold:
                direction = "falling"
            else:
                direction = "flat"

        # At risk: rising costs AND margin already below 50%
        at_risk = direction == "rising" and subscriber.gross_margin_pct < 50.0

        return CostTrend(
            subscriber_id=subscriber.subscriber_id,
            buckets=buckets,
            direction=direction,
            cost_delta_usd=delta,
            at_risk=at_risk,
        )

    def find_alerts(
        self,
        portfolio: PortfolioSummary,
        margin_floor_pct: float = 20.0,
        monthly_cost_ceiling_usd: float | None = None,
    ) -> list[AlertedSubscriber]:
        """Find subscribers that breach alert thresholds."""
        alerts: list[AlertedSubscriber] = []

        for sub in portfolio.subscribers:
            if sub.total_cost_usd == 0 and sub.revenue.ltv_usd == 0:
                continue  # ghost subscriber, skip

            reasons: list[str] = []
            severity = "warn"

            if sub.revenue.ltv_usd > 0 and sub.gross_margin_pct < margin_floor_pct:
                reasons.append(
                    f"margin {sub.gross_margin_pct:.1f}% < floor {margin_floor_pct:.1f}%"
                )
                if sub.gross_margin_pct < 0:
                    severity = "critical"

            if (
                monthly_cost_ceiling_usd is not None
                and sub.monthly_cost_usd > monthly_cost_ceiling_usd
            ):
                reasons.append(
                    f"monthly cost ${sub.monthly_cost_usd:.4f} > ceiling ${monthly_cost_ceiling_usd:.4f}"
                )
                severity = "critical"

            # Flag billing issues as warn regardless of margin
            if sub.revenue.billing_issues:
                reasons.append("RC billing issue detected")

            if reasons:
                alerts.append(
                    AlertedSubscriber(
                        subscriber_id=sub.subscriber_id,
                        reason="; ".join(reasons),
                        severity=severity,
                        gross_margin_pct=sub.gross_margin_pct,
                        monthly_cost_usd=sub.monthly_cost_usd,
                        gross_profit_usd=sub.gross_profit_usd,
                    )
                )

        # Sort: critical first, then by worst margin
        return sorted(alerts, key=lambda a: (a.severity != "critical", a.gross_margin_pct))
