"""LTV forecasting with churn scenarios."""

from __future__ import annotations

from dataclasses import dataclass

from .models import PortfolioSummary, SubscriberEconomics


@dataclass
class ChurnScenario:
    label: str  # "optimistic" / "realistic" / "pessimistic"
    monthly_churn_rate: float  # 0.0–1.0 fraction lost per month


@dataclass
class SubscriberForecast:
    subscriber_id: str
    mrr_usd: float
    monthly_cost_usd: float
    net_mrr_usd: float  # mrr - monthly_cost
    scenario: ChurnScenario
    months: int
    projected_ltv_usd: float
    projected_cost_usd: float
    projected_profit_usd: float
    break_even_month: int | None  # month when cumulative profit first turns positive


@dataclass
class PortfolioForecast:
    scenario: ChurnScenario
    months: int
    subscriber_forecasts: list[SubscriberForecast]

    @property
    def projected_revenue_usd(self) -> float:
        return sum(sf.projected_ltv_usd for sf in self.subscriber_forecasts)

    @property
    def projected_cost_usd(self) -> float:
        return sum(sf.projected_cost_usd for sf in self.subscriber_forecasts)

    @property
    def projected_profit_usd(self) -> float:
        return sum(sf.projected_profit_usd for sf in self.subscriber_forecasts)

    @property
    def expected_survivors(self) -> float:
        """Expected number of subscribers still active at end of forecast period."""
        n = len(self.subscriber_forecasts)
        if n == 0:
            return 0.0
        survival_rate = (1.0 - self.scenario.monthly_churn_rate) ** self.months
        return n * survival_rate


class LTVForecaster:
    DEFAULT_SCENARIOS = [
        ChurnScenario("optimistic", 0.02),  # 2%/mo churn
        ChurnScenario("realistic", 0.05),  # 5%/mo churn
        ChurnScenario("pessimistic", 0.10),  # 10%/mo churn
    ]

    def forecast_subscriber(
        self,
        sub: SubscriberEconomics,
        scenario: ChurnScenario,
        months: int = 12,
    ) -> SubscriberForecast:
        """Forecast LTV for a single subscriber using geometric series.

        Each month t, the probability the subscriber is still active is (1 - churn)^t.
        Revenue contribution at month t: mrr * (1 - churn)^t
        Total projected LTV = sum_{t=1}^{months} mrr * (1 - churn)^t
                            = mrr * (1 - churn) * (1 - (1-churn)^months) / churn   (churn > 0)
                            = mrr * months                                           (churn == 0)
        """
        mrr = sub.revenue.mrr_usd
        monthly_cost = sub.monthly_cost_usd
        churn = scenario.monthly_churn_rate

        if churn == 0.0:
            # No churn: every month contributes fully
            projected_ltv = mrr * months
            projected_cost = monthly_cost * months
        elif churn >= 1.0:
            # Full churn: subscriber lost immediately, zero future value
            projected_ltv = 0.0
            projected_cost = 0.0
        else:
            survival = 1.0 - churn
            # Geometric series: survival + survival^2 + ... + survival^months
            geo_sum = survival * (1.0 - survival**months) / churn
            projected_ltv = mrr * geo_sum
            projected_cost = monthly_cost * geo_sum

        projected_profit = projected_ltv - projected_cost

        # Find break-even month: first t where cumulative profit > 0
        break_even_month: int | None = None
        cumulative_profit = 0.0
        for t in range(1, months + 1):
            if churn == 0.0:
                factor = 1.0
            elif churn >= 1.0:
                factor = 0.0
            else:
                factor = (1.0 - churn) ** t
            cumulative_profit += (mrr - monthly_cost) * factor
            if cumulative_profit > 0 and break_even_month is None:
                break_even_month = t

        return SubscriberForecast(
            subscriber_id=sub.subscriber_id,
            mrr_usd=mrr,
            monthly_cost_usd=monthly_cost,
            net_mrr_usd=mrr - monthly_cost,
            scenario=scenario,
            months=months,
            projected_ltv_usd=projected_ltv,
            projected_cost_usd=projected_cost,
            projected_profit_usd=projected_profit,
            break_even_month=break_even_month,
        )

    def forecast_portfolio(
        self,
        portfolio: PortfolioSummary,
        scenario: ChurnScenario,
        months: int = 12,
    ) -> PortfolioForecast:
        """Forecast LTV for all subscribers in the portfolio."""
        forecasts = [
            self.forecast_subscriber(sub, scenario, months) for sub in portfolio.subscribers
        ]
        return PortfolioForecast(
            scenario=scenario,
            months=months,
            subscriber_forecasts=forecasts,
        )

    def all_scenarios(
        self,
        portfolio: PortfolioSummary,
        months: int = 12,
    ) -> list[PortfolioForecast]:
        """Run forecast for all three default churn scenarios."""
        return [self.forecast_portfolio(portfolio, s, months) for s in self.DEFAULT_SCENARIOS]
