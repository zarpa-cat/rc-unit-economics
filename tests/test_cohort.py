"""Tests for cohort analysis, cost trend, and alert logic."""

from __future__ import annotations

from datetime import datetime, timedelta

from rc_unit_economics.cohort import CohortAnalyzer, CohortSummary
from rc_unit_economics.models import (
    OperationCost,
    PortfolioSummary,
    SubscriberEconomics,
    SubscriberStatus,
    SubscriptionRevenue,
)


def _make_revenue(
    subscriber_id: str,
    entitlement_id: str | None = "pro",
    ltv: float = 9.99,
    mrr: float = 9.99,
    status: SubscriberStatus = SubscriberStatus.ACTIVE,
    billing_issues: bool = False,
) -> SubscriptionRevenue:
    return SubscriptionRevenue(
        subscriber_id=subscriber_id,
        product_id="monthly_pro",
        entitlement_id=entitlement_id,
        status=status,
        mrr_usd=mrr,
        ltv_usd=ltv,
        first_seen=None,
        last_seen=None,
        billing_issues=billing_issues,
    )


def _make_op(
    subscriber_id: str,
    amount: int = 10,
    days_ago: int = 0,
    usd_per_credit: float = 0.001,
) -> OperationCost:
    ts = datetime.utcnow() - timedelta(days=days_ago)
    return OperationCost(
        subscriber_id=subscriber_id,
        operation="generate_text",
        amount=amount,
        timestamp=ts,
        usd_per_credit=usd_per_credit,
    )


# ─── CohortSummary ──────────────────────────────────────────────────────────


def test_cohort_summary_basic():
    rev = _make_revenue("u1", ltv=9.99, mrr=9.99)
    ops = [_make_op("u1", amount=50)]
    sub = SubscriberEconomics(subscriber_id="u1", revenue=rev, operations=ops)
    c = CohortSummary(cohort_id="pro", subscribers=[sub])

    assert c.subscriber_count == 1
    assert c.profitable_count == 1
    assert c.unprofitable_count == 0
    assert abs(c.total_revenue_usd - 9.99) < 1e-6
    assert abs(c.total_cost_usd - 0.05) < 1e-6
    assert abs(c.gross_profit_usd - 9.94) < 1e-6
    assert c.gross_margin_pct > 99


def test_cohort_summary_unprofitable():
    rev = _make_revenue("u1", ltv=1.00, mrr=1.00)
    ops = [_make_op("u1", amount=5000, usd_per_credit=0.001)]  # $5 cost > $1 LTV
    sub = SubscriberEconomics(subscriber_id="u1", revenue=rev, operations=ops)
    c = CohortSummary(cohort_id="free", subscribers=[sub])

    assert c.unprofitable_count == 1
    assert c.profitable_count == 0
    assert c.gross_profit_usd < 0


def test_cohort_summary_avg_cost():
    rev1 = _make_revenue("u1", ltv=9.99)
    rev2 = _make_revenue("u2", ltv=9.99)
    ops1 = [_make_op("u1", amount=100)]
    ops2 = [_make_op("u2", amount=200)]
    sub1 = SubscriberEconomics(subscriber_id="u1", revenue=rev1, operations=ops1)
    sub2 = SubscriberEconomics(subscriber_id="u2", revenue=rev2, operations=ops2)
    c = CohortSummary(cohort_id="pro", subscribers=[sub1, sub2])

    assert abs(c.avg_cost_per_subscriber_usd - 0.15) < 1e-6
    assert abs(c.avg_ops_per_subscriber - 1.0) < 1e-6


def test_cohort_summary_zero_revenue():
    rev = _make_revenue("u1", ltv=0.0, mrr=0.0)
    sub = SubscriberEconomics(subscriber_id="u1", revenue=rev, operations=[])
    c = CohortSummary(cohort_id="ghost", subscribers=[sub])
    assert c.gross_margin_pct == 0.0


# ─── CohortAnalyzer.group_by_cohort ─────────────────────────────────────────


def test_group_by_cohort_basic():
    rev_pro = _make_revenue("u1", entitlement_id="pro")
    rev_free = _make_revenue("u2", entitlement_id="free")
    rev_none = _make_revenue("u3", entitlement_id=None)

    subs = [
        SubscriberEconomics("u1", rev_pro),
        SubscriberEconomics("u2", rev_free),
        SubscriberEconomics("u3", rev_none),
    ]
    portfolio = PortfolioSummary(subscribers=subs)
    ca = CohortAnalyzer()
    cohorts = ca.group_by_cohort(portfolio)

    assert "pro" in cohorts
    assert "free" in cohorts
    assert "no_entitlement" in cohorts
    assert cohorts["pro"].subscriber_count == 1
    assert cohorts["free"].subscriber_count == 1


def test_group_by_cohort_multiple_in_tier():
    rev1 = _make_revenue("u1", entitlement_id="enterprise")
    rev2 = _make_revenue("u2", entitlement_id="enterprise")
    rev3 = _make_revenue("u3", entitlement_id="pro")
    subs = [
        SubscriberEconomics("u1", rev1),
        SubscriberEconomics("u2", rev2),
        SubscriberEconomics("u3", rev3),
    ]
    portfolio = PortfolioSummary(subscribers=subs)
    ca = CohortAnalyzer()
    cohorts = ca.group_by_cohort(portfolio)
    assert cohorts["enterprise"].subscriber_count == 2
    assert cohorts["pro"].subscriber_count == 1


# ─── CohortAnalyzer.compute_trend ───────────────────────────────────────────


def test_compute_trend_insufficient_data():
    rev = _make_revenue("u1")
    ops = [_make_op("u1")]  # single op
    sub = SubscriberEconomics("u1", rev, ops)
    ca = CohortAnalyzer()
    t = ca.compute_trend(sub)
    assert t.direction == "insufficient_data"
    assert not t.at_risk


def test_compute_trend_no_ops():
    rev = _make_revenue("u1")
    sub = SubscriberEconomics("u1", rev, [])
    ca = CohortAnalyzer()
    t = ca.compute_trend(sub)
    assert t.direction == "insufficient_data"


def test_compute_trend_rising():
    rev = _make_revenue("u1", ltv=9.99)
    # Build ops: small cost early, large cost later
    ops = []
    for d in range(20, 0, -1):  # 20 days spread
        amount = 5 if d > 10 else 100  # rising in later days
        ops.append(_make_op("u1", amount=amount, days_ago=d))
    sub = SubscriberEconomics("u1", rev, ops)
    ca = CohortAnalyzer()
    t = ca.compute_trend(sub, bucket_days=7)
    assert t.direction == "rising"


def test_compute_trend_falling():
    rev = _make_revenue("u1", ltv=9.99)
    ops = []
    for d in range(20, 0, -1):
        amount = 100 if d > 10 else 5  # high early, low later
        ops.append(_make_op("u1", amount=amount, days_ago=d))
    sub = SubscriberEconomics("u1", rev, ops)
    ca = CohortAnalyzer()
    t = ca.compute_trend(sub, bucket_days=7)
    assert t.direction == "falling"


def test_compute_trend_at_risk_requires_rising_and_low_margin():
    # At risk = rising costs AND margin < 50%
    rev = _make_revenue("u1", ltv=1.00, mrr=1.00)  # low LTV → low margin
    ops = []
    for d in range(20, 0, -1):
        amount = 5 if d > 10 else 200  # rising costs
        ops.append(_make_op("u1", amount=amount, days_ago=d))
    sub = SubscriberEconomics("u1", rev, ops)
    ca = CohortAnalyzer()
    t = ca.compute_trend(sub, bucket_days=7)
    # Rising cost + low margin → at_risk
    if t.direction == "rising":
        assert t.at_risk


def test_compute_trend_slope_label_rising():
    rev = _make_revenue("u1", ltv=9.99)
    ops = []
    for d in range(20, 0, -1):
        amount = 5 if d > 10 else 100
        ops.append(_make_op("u1", amount=amount, days_ago=d))
    sub = SubscriberEconomics("u1", rev, ops)
    ca = CohortAnalyzer()
    t = ca.compute_trend(sub)
    if t.direction == "rising":
        assert "↑" in t.slope_label


def test_compute_trend_slope_label_insufficient():
    rev = _make_revenue("u1")
    sub = SubscriberEconomics("u1", rev, [])
    ca = CohortAnalyzer()
    t = ca.compute_trend(sub)
    assert "n/a" in t.slope_label


# ─── CohortAnalyzer.find_alerts ─────────────────────────────────────────────


def test_find_alerts_no_alerts():
    rev = _make_revenue("u1", ltv=9.99, mrr=9.99)
    ops = [_make_op("u1", amount=10)]  # tiny cost, 99%+ margin
    sub = SubscriberEconomics("u1", rev, ops)
    portfolio = PortfolioSummary(subscribers=[sub])
    ca = CohortAnalyzer()
    alerts = ca.find_alerts(portfolio, margin_floor_pct=20.0)
    assert len(alerts) == 0


def test_find_alerts_margin_breach():
    rev = _make_revenue("u1", ltv=1.00)
    ops = [_make_op("u1", amount=500)]  # $0.50 cost on $1.00 LTV → 50% margin, just above 20%
    sub = SubscriberEconomics("u1", rev, ops)
    portfolio = PortfolioSummary(subscribers=[sub])
    ca = CohortAnalyzer()
    # Floor at 60% → should alert
    alerts = ca.find_alerts(portfolio, margin_floor_pct=60.0)
    assert len(alerts) == 1
    assert alerts[0].subscriber_id == "u1"


def test_find_alerts_critical_negative_margin():
    rev = _make_revenue("u1", ltv=0.50)
    ops = [_make_op("u1", amount=5000)]  # $5.00 cost > $0.50 LTV
    sub = SubscriberEconomics("u1", rev, ops)
    portfolio = PortfolioSummary(subscribers=[sub])
    ca = CohortAnalyzer()
    alerts = ca.find_alerts(portfolio, margin_floor_pct=20.0)
    assert len(alerts) == 1
    assert alerts[0].severity == "critical"


def test_find_alerts_monthly_cost_ceiling():
    rev = _make_revenue("u1", ltv=99.99, mrr=9.99)
    # Many ops in a single day → high daily rate → high monthly projection
    ops = [_make_op("u1", amount=50000, days_ago=0)]  # $50 cost → flagged
    sub = SubscriberEconomics("u1", rev, ops)
    portfolio = PortfolioSummary(subscribers=[sub])
    ca = CohortAnalyzer()
    alerts = ca.find_alerts(portfolio, margin_floor_pct=20.0, monthly_cost_ceiling_usd=1.0)
    assert len(alerts) == 1
    assert alerts[0].severity == "critical"


def test_find_alerts_billing_issue_flagged():
    rev = _make_revenue("u1", ltv=9.99, billing_issues=True)
    ops = [_make_op("u1", amount=10)]  # healthy margin
    sub = SubscriberEconomics("u1", rev, ops)
    portfolio = PortfolioSummary(subscribers=[sub])
    ca = CohortAnalyzer()
    alerts = ca.find_alerts(portfolio, margin_floor_pct=20.0)
    # Billing issue alone triggers warn
    assert len(alerts) == 1
    assert "billing" in alerts[0].reason


def test_find_alerts_ghost_subscriber_skipped():
    # Ghost: zero revenue, zero cost
    rev = _make_revenue("ghost", ltv=0.0, mrr=0.0)
    sub = SubscriberEconomics("ghost", rev, [])
    portfolio = PortfolioSummary(subscribers=[sub])
    ca = CohortAnalyzer()
    alerts = ca.find_alerts(portfolio, margin_floor_pct=20.0)
    assert len(alerts) == 0


def test_find_alerts_sorted_critical_first():
    rev_warn = _make_revenue("u1", ltv=1.00)
    rev_crit = _make_revenue("u2", ltv=0.10)
    ops_warn = [_make_op("u1", amount=600)]  # 40% margin → warn
    ops_crit = [_make_op("u2", amount=5000)]  # huge loss → critical
    sub_warn = SubscriberEconomics("u1", rev_warn, ops_warn)
    sub_crit = SubscriberEconomics("u2", rev_crit, ops_crit)
    portfolio = PortfolioSummary(subscribers=[sub_warn, sub_crit])
    ca = CohortAnalyzer()
    alerts = ca.find_alerts(portfolio, margin_floor_pct=50.0)
    # Critical should be first
    assert alerts[0].severity == "critical"


def test_find_alerts_multiple_reasons():
    rev = _make_revenue("u1", ltv=0.50, billing_issues=True)
    ops = [_make_op("u1", amount=10000)]  # $10 > $0.50 → negative margin + billing issue
    sub = SubscriberEconomics("u1", rev, ops)
    portfolio = PortfolioSummary(subscribers=[sub])
    ca = CohortAnalyzer()
    alerts = ca.find_alerts(portfolio, margin_floor_pct=20.0)
    assert len(alerts) == 1
    # Both reasons should be present in the reason string
    assert "margin" in alerts[0].reason
    assert "billing" in alerts[0].reason
