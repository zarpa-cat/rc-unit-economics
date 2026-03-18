"""RevenueCat REST API client for revenue data."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from .models import SubscriberStatus, SubscriptionRevenue

RC_BASE = "https://api.revenuecat.com/v1"


def _parse_dt(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _parse_status(subscriber: dict[str, Any]) -> SubscriberStatus:
    entitlements = subscriber.get("entitlements", {})
    if not entitlements:
        return SubscriberStatus.UNKNOWN
    # Check if any entitlement is active
    for ent_data in entitlements.values():
        expires = _parse_dt(ent_data.get("expires_date"))
        if expires is None:  # lifetime
            return SubscriberStatus.ACTIVE
        if expires > datetime.now(tz=expires.tzinfo):
            return SubscriberStatus.ACTIVE
    return SubscriberStatus.EXPIRED


def _compute_mrr(subscriber: dict[str, Any]) -> float:
    """Estimate MRR from active subscriptions."""
    subscriptions = subscriber.get("subscriptions", {})
    mrr = 0.0
    for sub in subscriptions.values():
        if sub.get("unsubscribe_detected_at") or sub.get("billing_issues_detected_at"):
            continue
        price = sub.get("price", 0) or 0
        period_type = sub.get("period_type", "normal")
        if period_type == "trial":
            continue
        # Normalize to monthly
        duration = sub.get("duration_in_months", 1) or 1
        mrr += price / duration
    return mrr


def _compute_ltv(subscriber: dict[str, Any]) -> float:
    """Approximate LTV from non-refunded purchases."""
    total = 0.0
    for sub in subscriber.get("subscriptions", {}).values():
        price = sub.get("price", 0) or 0
        sub.get("billing_issues_detected_at") or 0
        if not sub.get("refunded_at"):
            total += price
    return total


class RCClient:
    """Read revenue data from RevenueCat API."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> RCClient:
        self._client = httpx.AsyncClient(
            base_url=RC_BASE,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "X-Platform": "stripe",
                "Accept": "application/json",
            },
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get_subscriber_revenue(self, subscriber_id: str) -> SubscriptionRevenue:
        """Fetch revenue data for a single subscriber."""
        assert self._client is not None
        resp = await self._client.get(f"/subscribers/{subscriber_id}")
        resp.raise_for_status()
        data = resp.json()
        sub = data["subscriber"]

        entitlements = sub.get("entitlements", {})
        entitlement_id = next(iter(entitlements), None) if entitlements else None

        # Get product from subscriptions
        subscriptions = sub.get("subscriptions", {})
        product_id = next(iter(subscriptions), None) if subscriptions else None

        # Trial detection
        is_trial = False
        trial_start = None
        for s in subscriptions.values():
            if s.get("period_type") == "trial":
                is_trial = True
                trial_start = _parse_dt(s.get("original_purchase_date"))

        billing_issues = any(s.get("billing_issues_detected_at") for s in subscriptions.values())

        return SubscriptionRevenue(
            subscriber_id=subscriber_id,
            product_id=product_id,
            entitlement_id=entitlement_id,
            status=_parse_status(sub),
            mrr_usd=_compute_mrr(sub),
            ltv_usd=_compute_ltv(sub),
            first_seen=_parse_dt(sub.get("first_seen")),
            last_seen=_parse_dt(sub.get("last_seen")),
            renewal_count=sub.get("other_purchases", {}).get("count", 0),
            is_trial=is_trial,
            trial_start=trial_start,
            billing_issues=billing_issues,
        )

    async def list_subscribers(
        self,
        limit: int = 100,
        start_after_id: str | None = None,
    ) -> tuple[list[str], str | None]:
        """List subscriber IDs (paginated). Returns (ids, next_cursor)."""
        assert self._client is not None
        params: dict[str, Any] = {"limit": min(limit, 100)}
        if start_after_id:
            params["start_after_id"] = start_after_id

        # RC v2 subscribers list endpoint
        resp = await self._client.get(
            "/subscribers",
            params=params,
            headers={"X-Platform": "stripe"},
        )
        resp.raise_for_status()
        data = resp.json()
        subscribers = data.get("subscribers", [])
        ids = [s["app_user_id"] for s in subscribers if "app_user_id" in s]
        cursor = data.get("next_cursor")
        return ids, cursor
