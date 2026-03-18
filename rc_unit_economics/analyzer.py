"""Core analysis engine: cross-reference revenue and cost data."""

from __future__ import annotations

from .models import OperationCost, PortfolioSummary, SubscriberEconomics, SubscriptionRevenue


class UnitEconomicsAnalyzer:
    """Cross-reference revenue and cost data to compute per-subscriber economics."""

    def analyze_subscriber(
        self,
        revenue: SubscriptionRevenue,
        costs: list[OperationCost],
    ) -> SubscriberEconomics:
        """Compute unit economics for a single subscriber."""
        subscriber_costs = [c for c in costs if c.subscriber_id == revenue.subscriber_id]
        return SubscriberEconomics(
            subscriber_id=revenue.subscriber_id,
            revenue=revenue,
            operations=subscriber_costs,
        )

    def build_portfolio(
        self,
        revenues: list[SubscriptionRevenue],
        all_costs: list[OperationCost],
    ) -> PortfolioSummary:
        """Build portfolio summary from a list of revenues and costs."""
        economics = [self.analyze_subscriber(rev, all_costs) for rev in revenues]
        # Include subscribers who have costs but no revenue record
        rev_ids = {r.subscriber_id for r in revenues}
        cost_only_ids = {c.subscriber_id for c in all_costs} - rev_ids
        # These are cost-only (no RC data) — create minimal revenue records
        for sid in cost_only_ids:
            from .models import SubscriberStatus
            from .models import SubscriptionRevenue as SR

            minimal_rev = SR(
                subscriber_id=sid,
                product_id=None,
                entitlement_id=None,
                status=SubscriberStatus.UNKNOWN,
                mrr_usd=0.0,
                ltv_usd=0.0,
                first_seen=None,
                last_seen=None,
            )
            sub_costs = [c for c in all_costs if c.subscriber_id == sid]
            economics.append(
                SubscriberEconomics(
                    subscriber_id=sid,
                    revenue=minimal_rev,
                    operations=sub_costs,
                )
            )

        return PortfolioSummary(subscribers=economics)
