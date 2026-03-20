"""Portfolio report generator: markdown and JSON output."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from .cohort import CohortAnalyzer
from .forecast import LTVForecaster
from .models import PortfolioSummary


class PortfolioReporter:
    def __init__(self, portfolio: PortfolioSummary) -> None:
        self.portfolio = portfolio
        self._cohort_analyzer = CohortAnalyzer()
        self._forecaster = LTVForecaster()

    def _md_table(self, headers: list[str], rows: list[list[str]]) -> str:
        """Build a simple markdown table."""
        sep = "| " + " | ".join("---" for _ in headers) + " |"
        header = "| " + " | ".join(headers) + " |"
        body = "\n".join("| " + " | ".join(row) + " |" for row in rows)
        if body:
            return f"{header}\n{sep}\n{body}"
        return f"{header}\n{sep}\n| *(none)* |"

    def as_markdown(self, months: int = 12) -> str:
        p = self.portfolio
        now = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")

        lines: list[str] = []
        lines.append("# Portfolio Report")
        lines.append(f"Generated: {now}")
        lines.append("")

        # Summary table
        lines.append("## Summary")
        summary_rows = [
            ["Total subscribers", str(p.total_subscribers)],
            ["Profitable", str(p.profitable_count)],
            ["Unprofitable", str(p.unprofitable_count)],
            ["Zero-cost", str(p.zero_cost_count)],
            ["Total revenue (LTV)", f"${p.total_revenue_usd:.4f}"],
            ["Total cost", f"${p.total_cost_usd:.4f}"],
            ["Gross profit", f"${p.total_gross_profit_usd:.4f}"],
            ["Portfolio margin", f"{p.portfolio_margin_pct:.1f}%"],
            ["MRR", f"${p.mrr_usd:.4f}/mo"],
            ["Monthly cost run rate", f"${p.monthly_cost_run_rate_usd:.4f}/mo"],
            ["Net MRR", f"${p.mrr_usd - p.monthly_cost_run_rate_usd:.4f}/mo"],
        ]
        lines.append(self._md_table(["Metric", "Value"], summary_rows))
        lines.append("")

        # Top Profitable
        lines.append("## Top Profitable")
        top = p.top_profitable(5)
        if top:
            prof_rows = [
                [
                    s.subscriber_id,
                    f"${s.revenue.ltv_usd:.4f}",
                    f"${s.total_cost_usd:.4f}",
                    f"${s.gross_profit_usd:.4f}",
                    f"{s.gross_margin_pct:.1f}%",
                ]
                for s in top
            ]
            lines.append(
                self._md_table(["Subscriber", "LTV", "Cost", "Gross Profit", "Margin"], prof_rows)
            )
        else:
            lines.append("*No profitable subscribers.*")
        lines.append("")

        # At Risk
        lines.append("## At Risk")
        at_risk = [s for s in p.subscribers if not s.is_profitable and s.total_cost_usd > 0]
        if at_risk:
            risk_rows = [
                [
                    s.subscriber_id,
                    f"${s.revenue.ltv_usd:.4f}",
                    f"${s.total_cost_usd:.4f}",
                    f"${s.gross_profit_usd:.4f}",
                    f"{s.gross_margin_pct:.1f}%",
                ]
                for s in sorted(at_risk, key=lambda x: x.gross_profit_usd)
            ]
            lines.append(
                self._md_table(["Subscriber", "LTV", "Cost", "Gross P/L", "Margin"], risk_rows)
            )
        else:
            lines.append("*No at-risk subscribers.*")
        lines.append("")

        # Cohort Breakdown
        lines.append("## Cohort Breakdown")
        cohorts = self._cohort_analyzer.group_by_cohort(p)
        if cohorts:
            cohort_rows = [
                [
                    cid,
                    str(c.subscriber_count),
                    f"${c.total_revenue_usd:.4f}",
                    f"${c.total_cost_usd:.4f}",
                    f"${c.gross_profit_usd:.4f}",
                    f"{c.gross_margin_pct:.1f}%",
                    f"${c.mrr_usd:.4f}/mo",
                ]
                for cid, c in sorted(
                    cohorts.items(), key=lambda x: x[1].gross_profit_usd, reverse=True
                )
            ]
            lines.append(
                self._md_table(
                    ["Cohort", "Subs", "Revenue", "Cost", "Gross P/L", "Margin", "MRR"],
                    cohort_rows,
                )
            )
        else:
            lines.append("*No cohort data.*")
        lines.append("")

        # LTV Forecast
        lines.append(f"## {months}-Month LTV Forecast")
        scenario_forecasts = self._forecaster.all_scenarios(p, months=months)
        for pf in scenario_forecasts:
            lines.append(
                f"### Scenario: {pf.scenario.label} ({pf.scenario.monthly_churn_rate * 100:.0f}% monthly churn)"
            )
            forecast_rows = [
                [
                    sf.subscriber_id,
                    f"${sf.mrr_usd:.4f}",
                    f"${sf.projected_ltv_usd:.4f}",
                    f"${sf.projected_cost_usd:.4f}",
                    f"${sf.projected_profit_usd:.4f}",
                    str(sf.break_even_month) if sf.break_even_month else "never",
                ]
                for sf in pf.subscriber_forecasts
            ]
            if forecast_rows:
                lines.append(
                    self._md_table(
                        [
                            "Subscriber",
                            "MRR",
                            "Projected LTV",
                            "Projected Cost",
                            "Projected Profit",
                            "Break-even Month",
                        ],
                        forecast_rows,
                    )
                )
            else:
                lines.append("*No subscribers.*")
            lines.append(
                f"**Portfolio totals:** Revenue ${pf.projected_revenue_usd:.4f} | Cost ${pf.projected_cost_usd:.4f} | Profit ${pf.projected_profit_usd:.4f} | Expected survivors {pf.expected_survivors:.1f}"
            )
            lines.append("")

        return "\n".join(lines)

    def as_json(self, months: int = 12) -> str:
        p = self.portfolio
        cohorts = self._cohort_analyzer.group_by_cohort(p)
        scenario_forecasts = self._forecaster.all_scenarios(p, months=months)

        summary = {
            "total_subscribers": p.total_subscribers,
            "profitable_count": p.profitable_count,
            "unprofitable_count": p.unprofitable_count,
            "zero_cost_count": p.zero_cost_count,
            "total_revenue_usd": p.total_revenue_usd,
            "total_cost_usd": p.total_cost_usd,
            "gross_profit_usd": p.total_gross_profit_usd,
            "portfolio_margin_pct": p.portfolio_margin_pct,
            "mrr_usd": p.mrr_usd,
            "monthly_cost_run_rate_usd": p.monthly_cost_run_rate_usd,
        }

        cohorts_data = {
            cid: {
                "subscriber_count": c.subscriber_count,
                "profitable_count": c.profitable_count,
                "unprofitable_count": c.unprofitable_count,
                "total_revenue_usd": c.total_revenue_usd,
                "total_cost_usd": c.total_cost_usd,
                "gross_profit_usd": c.gross_profit_usd,
                "gross_margin_pct": c.gross_margin_pct,
                "mrr_usd": c.mrr_usd,
                "avg_cost_per_subscriber_usd": c.avg_cost_per_subscriber_usd,
            }
            for cid, c in cohorts.items()
        }

        forecasts_data = [
            {
                "scenario": pf.scenario.label,
                "monthly_churn_rate": pf.scenario.monthly_churn_rate,
                "months": pf.months,
                "projected_revenue_usd": pf.projected_revenue_usd,
                "projected_cost_usd": pf.projected_cost_usd,
                "projected_profit_usd": pf.projected_profit_usd,
                "expected_survivors": pf.expected_survivors,
                "subscribers": [
                    {
                        "subscriber_id": sf.subscriber_id,
                        "mrr_usd": sf.mrr_usd,
                        "projected_ltv_usd": sf.projected_ltv_usd,
                        "projected_cost_usd": sf.projected_cost_usd,
                        "projected_profit_usd": sf.projected_profit_usd,
                        "break_even_month": sf.break_even_month,
                    }
                    for sf in pf.subscriber_forecasts
                ],
            }
            for pf in scenario_forecasts
        ]

        return json.dumps(
            {
                "summary": summary,
                "cohorts": cohorts_data,
                "forecasts": forecasts_data,
            },
            indent=2,
        )
