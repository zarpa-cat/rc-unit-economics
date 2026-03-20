"""CLI for rc-unit-economics."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Annotated

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from .analyzer import UnitEconomicsAnalyzer
from .cost_reader import CostReader
from .models import PortfolioSummary, SubscriberEconomics

app = typer.Typer(
    name="rcue",
    help="Per-subscriber unit economics for RevenueCat agent-native SaaS.",
    no_args_is_help=True,
)
console = Console()


def _get_api_key(api_key: str | None) -> str:
    key = api_key or os.environ.get("RC_API_KEY", "")
    if not key:
        console.print("[red]Error:[/red] RC_API_KEY not set. Pass --api-key or set the env var.")
        raise typer.Exit(1)
    return key


def _get_cost_reader(db_path: str | None, usd_per_credit: float) -> CostReader | None:
    path = db_path or os.environ.get("RCUE_AUDIT_DB", "")
    if not path:
        return None
    return CostReader(db_path=path, usd_per_credit=usd_per_credit)


def _fmt_usd(val: float) -> str:
    if val >= 0:
        return f"[green]${val:.4f}[/green]"
    return f"[red]-${abs(val):.4f}[/red]"


def _fmt_margin(val: float) -> str:
    if val >= 70:
        return f"[green]{val:.1f}%[/green]"
    if val >= 30:
        return f"[yellow]{val:.1f}%[/yellow]"
    return f"[red]{val:.1f}%[/red]"


def _fmt_status(s: SubscriberEconomics) -> str:
    status = s.revenue.status.value
    if s.revenue.is_trial:
        return f"[blue]{status}(trial)[/blue]"
    if s.revenue.billing_issues:
        return f"[red]{status}(billing!)[/red]"
    return status


def _print_portfolio(portfolio: PortfolioSummary, as_json: bool = False) -> None:
    if as_json:
        out = {
            "total_subscribers": portfolio.total_subscribers,
            "profitable": portfolio.profitable_count,
            "unprofitable": portfolio.unprofitable_count,
            "zero_cost": portfolio.zero_cost_count,
            "total_revenue_usd": portfolio.total_revenue_usd,
            "total_cost_usd": portfolio.total_cost_usd,
            "gross_profit_usd": portfolio.total_gross_profit_usd,
            "portfolio_margin_pct": portfolio.portfolio_margin_pct,
            "mrr_usd": portfolio.mrr_usd,
            "monthly_cost_run_rate_usd": portfolio.monthly_cost_run_rate_usd,
        }
        console.print(json.dumps(out, indent=2))
        return

    console.print()
    console.print("[bold]Portfolio Summary[/bold]")
    console.print(f"  Subscribers:   {portfolio.total_subscribers}")
    console.print(f"  Profitable:    [green]{portfolio.profitable_count}[/green]")
    console.print(f"  Unprofitable:  [red]{portfolio.unprofitable_count}[/red]")
    console.print(f"  Zero cost:     {portfolio.zero_cost_count}")
    console.print()
    console.print(f"  Total LTV revenue:  ${portfolio.total_revenue_usd:.4f}")
    console.print(f"  Total cost:         ${portfolio.total_cost_usd:.4f}")
    console.print(f"  Gross profit:       {_fmt_usd(portfolio.total_gross_profit_usd)}")
    console.print(f"  Portfolio margin:   {_fmt_margin(portfolio.portfolio_margin_pct)}")
    console.print()
    console.print(f"  MRR:                ${portfolio.mrr_usd:.4f}/mo")
    console.print(f"  Cost run rate:      ${portfolio.monthly_cost_run_rate_usd:.4f}/mo")
    net_mrr = portfolio.mrr_usd - portfolio.monthly_cost_run_rate_usd
    console.print(f"  Net MRR:            {_fmt_usd(net_mrr)}/mo")
    console.print()


def _print_subscriber_table(portfolio: PortfolioSummary) -> None:
    table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
    table.add_column("Subscriber", style="dim", no_wrap=True)
    table.add_column("Status")
    table.add_column("LTV", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("Gross P/L", justify="right")
    table.add_column("Margin", justify="right")
    table.add_column("Ops", justify="right")

    for s in sorted(portfolio.subscribers, key=lambda x: x.gross_profit_usd, reverse=True):
        sub_id = s.subscriber_id
        if len(sub_id) > 20:
            sub_id = sub_id[:17] + "..."
        table.add_row(
            sub_id,
            _fmt_status(s),
            f"${s.revenue.ltv_usd:.4f}",
            f"${s.total_cost_usd:.4f}",
            _fmt_usd(s.gross_profit_usd),
            _fmt_margin(s.gross_margin_pct),
            str(s.operation_count),
        )
    console.print(table)


@app.command()
def analyze(
    subscriber_ids: Annotated[list[str] | None, typer.Argument()] = None,
    api_key: Annotated[str | None, typer.Option("--api-key", "-k", help="RC API key")] = None,
    audit_db: Annotated[
        str | None, typer.Option("--audit-db", help="Path to billing meter SQLite DB")
    ] = None,
    usd_per_credit: Annotated[
        float, typer.Option("--usd-per-credit", help="USD cost per credit unit")
    ] = 0.001,
    show_table: Annotated[bool, typer.Option("--table/--no-table")] = True,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Analyze unit economics for one or more subscribers."""
    from .rc_client import RCClient

    rc_api_key = _get_api_key(api_key)
    cost_reader = _get_cost_reader(audit_db, usd_per_credit)
    all_costs = cost_reader.load_all() if cost_reader else []

    if not subscriber_ids:
        # Use all subscribers known from the cost reader
        if cost_reader:
            subscriber_ids = cost_reader.known_subscriber_ids()
        if not subscriber_ids:
            console.print("[yellow]No subscriber IDs specified and no audit DB found.[/yellow]")
            console.print("Specify subscriber IDs as arguments or provide --audit-db.")
            raise typer.Exit(1)

    async def _fetch() -> list:
        async with RCClient(rc_api_key) as rc:
            revenues = []
            for sid in subscriber_ids:
                try:
                    rev = await rc.get_subscriber_revenue(sid)
                    revenues.append(rev)
                except Exception as e:
                    console.print(f"[yellow]Warning:[/yellow] Could not fetch {sid}: {e}")
            return revenues

    revenues = asyncio.run(_fetch())
    analyzer = UnitEconomicsAnalyzer()
    portfolio = analyzer.build_portfolio(revenues, all_costs)

    _print_portfolio(portfolio, as_json=as_json)
    if show_table and not as_json:
        _print_subscriber_table(portfolio)


@app.command()
def subscriber(
    subscriber_id: str,
    api_key: Annotated[str | None, typer.Option("--api-key", "-k")] = None,
    audit_db: Annotated[str | None, typer.Option("--audit-db")] = None,
    usd_per_credit: Annotated[float, typer.Option("--usd-per-credit")] = 0.001,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show detailed unit economics for a single subscriber."""
    from .rc_client import RCClient

    rc_api_key = _get_api_key(api_key)
    cost_reader = _get_cost_reader(audit_db, usd_per_credit)

    async def _fetch():
        async with RCClient(rc_api_key) as rc:
            return await rc.get_subscriber_revenue(subscriber_id)

    revenue = asyncio.run(_fetch())
    costs = cost_reader.load_for_subscriber(subscriber_id) if cost_reader else []
    analyzer = UnitEconomicsAnalyzer()
    econ = analyzer.analyze_subscriber(revenue, costs)

    if as_json:
        console.print(
            json.dumps(
                {
                    "subscriber_id": econ.subscriber_id,
                    "status": econ.revenue.status.value,
                    "ltv_usd": econ.revenue.ltv_usd,
                    "mrr_usd": econ.revenue.mrr_usd,
                    "total_cost_usd": econ.total_cost_usd,
                    "gross_profit_usd": econ.gross_profit_usd,
                    "gross_margin_pct": econ.gross_margin_pct,
                    "operation_count": econ.operation_count,
                    "cost_per_operation": econ.cost_per_operation,
                    "monthly_cost_usd": econ.monthly_cost_usd,
                    "is_profitable": econ.is_profitable,
                },
                indent=2,
            )
        )
        return

    console.print(f"\n[bold]Subscriber:[/bold] {subscriber_id}")
    console.print(f"  Status:          {_fmt_status(econ)}")
    console.print(f"  LTV revenue:     ${econ.revenue.ltv_usd:.4f}")
    console.print(f"  MRR:             ${econ.revenue.mrr_usd:.4f}/mo")
    console.print(f"  Total cost:      ${econ.total_cost_usd:.4f}")
    console.print(f"  Gross profit:    {_fmt_usd(econ.gross_profit_usd)}")
    console.print(f"  Gross margin:    {_fmt_margin(econ.gross_margin_pct)}")
    console.print(f"  Operations:      {econ.operation_count}")
    console.print(f"  Cost/op:         ${econ.cost_per_operation:.6f}")
    console.print(f"  Monthly cost:    ${econ.monthly_cost_usd:.4f}/mo")

    if costs:
        console.print("\n  [bold]Recent operations:[/bold]")
        for op in sorted(costs, key=lambda c: c.timestamp, reverse=True)[:10]:
            ts = op.timestamp.strftime("%Y-%m-%d %H:%M")
            console.print(
                f"    {ts}  {op.operation:<30} {op.amount:>5} credits  ${op.usd_cost:.6f}"
            )
    console.print()


@app.command()
def breakeven(
    usd_per_credit: Annotated[float, typer.Option("--usd-per-credit")] = 0.001,
    ops_per_subscriber_per_month: Annotated[
        int, typer.Option("--ops-per-month", help="Expected operations per subscriber/month")
    ] = 30,
    credits_per_op: Annotated[
        int, typer.Option("--credits-per-op", help="Average credits per operation")
    ] = 10,
    monthly_price_usd: Annotated[
        float, typer.Option("--price", help="Monthly subscription price in USD")
    ] = 9.99,
    overhead_usd: Annotated[
        float, typer.Option("--overhead", help="Monthly fixed overhead in USD")
    ] = 0.0,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Calculate break-even point for subscription pricing."""
    cost_per_subscriber = ops_per_subscriber_per_month * credits_per_op * usd_per_credit
    gross_per_subscriber = monthly_price_usd - cost_per_subscriber
    margin_pct = (gross_per_subscriber / monthly_price_usd) * 100 if monthly_price_usd > 0 else 0.0

    if overhead_usd > 0 and gross_per_subscriber > 0:
        break_even_subscribers = overhead_usd / gross_per_subscriber
    else:
        break_even_subscribers = 0.0

    if as_json:
        console.print(
            json.dumps(
                {
                    "monthly_price_usd": monthly_price_usd,
                    "cost_per_subscriber_usd": cost_per_subscriber,
                    "gross_per_subscriber_usd": gross_per_subscriber,
                    "gross_margin_pct": margin_pct,
                    "overhead_usd": overhead_usd,
                    "break_even_subscribers": break_even_subscribers,
                },
                indent=2,
            )
        )
        return

    console.print("\n[bold]Break-Even Analysis[/bold]")
    console.print(f"  Monthly subscription price:  ${monthly_price_usd:.2f}")
    console.print(f"  Ops/subscriber/month:        {ops_per_subscriber_per_month}")
    console.print(f"  Credits/op:                  {credits_per_op}")
    console.print(f"  USD/credit:                  ${usd_per_credit:.6f}")
    console.print()
    console.print(f"  Cost/subscriber/month:       ${cost_per_subscriber:.4f}")
    console.print(f"  Gross/subscriber/month:      {_fmt_usd(gross_per_subscriber)}")
    console.print(f"  Gross margin:                {_fmt_margin(margin_pct)}")

    if overhead_usd > 0:
        console.print(f"  Fixed overhead/month:        ${overhead_usd:.2f}")
        if gross_per_subscriber > 0:
            console.print(f"  Break-even subscribers:      {break_even_subscribers:.1f}")
        else:
            console.print("  Break-even:                  [red]Never (negative margin)[/red]")
    console.print()


@app.command()
def cohort(
    api_key: Annotated[str | None, typer.Option("--api-key", "-k", help="RC API key")] = None,
    audit_db: Annotated[
        str | None, typer.Option("--audit-db", help="Path to billing meter SQLite DB")
    ] = None,
    usd_per_credit: Annotated[float, typer.Option("--usd-per-credit")] = 0.001,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show unit economics grouped by entitlement/plan cohort."""
    from .cohort import CohortAnalyzer
    from .rc_client import RCClient

    rc_api_key = _get_api_key(api_key)
    cost_reader = _get_cost_reader(audit_db, usd_per_credit)
    all_costs = cost_reader.load_all() if cost_reader else []

    subscriber_ids = cost_reader.known_subscriber_ids() if cost_reader else []
    if not subscriber_ids:
        console.print("[yellow]No subscribers found in audit DB.[/yellow]")
        raise typer.Exit(1)

    async def _fetch():
        async with RCClient(rc_api_key) as rc:
            revenues = []
            for sid in subscriber_ids:
                try:
                    revenues.append(await rc.get_subscriber_revenue(sid))
                except Exception:
                    pass
            return revenues

    from .analyzer import UnitEconomicsAnalyzer

    revenues = asyncio.run(_fetch())
    analyzer = UnitEconomicsAnalyzer()
    portfolio = analyzer.build_portfolio(revenues, all_costs)
    ca = CohortAnalyzer()
    cohorts = ca.group_by_cohort(portfolio)

    if as_json:
        out = {
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
                "avg_ops_per_subscriber": c.avg_ops_per_subscriber,
            }
            for cid, c in sorted(cohorts.items(), key=lambda x: x[1].gross_profit_usd, reverse=True)
        }
        console.print(json.dumps(out, indent=2))
        return

    console.print()
    console.print("[bold]Cohort Summary[/bold]")
    table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
    table.add_column("Cohort", no_wrap=True)
    table.add_column("Subs", justify="right")
    table.add_column("Profitable", justify="right")
    table.add_column("Revenue", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("Gross P/L", justify="right")
    table.add_column("Margin", justify="right")
    table.add_column("MRR", justify="right")
    table.add_column("Avg cost/sub", justify="right")

    for cid, c in sorted(cohorts.items(), key=lambda x: x[1].gross_profit_usd, reverse=True):
        table.add_row(
            cid,
            str(c.subscriber_count),
            f"{c.profitable_count}/{c.subscriber_count}",
            f"${c.total_revenue_usd:.4f}",
            f"${c.total_cost_usd:.4f}",
            _fmt_usd(c.gross_profit_usd),
            _fmt_margin(c.gross_margin_pct),
            f"${c.mrr_usd:.4f}/mo",
            f"${c.avg_cost_per_subscriber_usd:.4f}",
        )
    console.print(table)


@app.command()
def alert(
    api_key: Annotated[str | None, typer.Option("--api-key", "-k", help="RC API key")] = None,
    audit_db: Annotated[
        str | None, typer.Option("--audit-db", help="Path to billing meter SQLite DB")
    ] = None,
    usd_per_credit: Annotated[float, typer.Option("--usd-per-credit")] = 0.001,
    margin_floor: Annotated[
        float, typer.Option("--margin-floor", help="Alert if gross margin below this %")
    ] = 20.0,
    monthly_cost_ceiling: Annotated[
        float | None, typer.Option("--cost-ceiling", help="Alert if monthly cost exceeds this USD")
    ] = None,
    include_trends: Annotated[
        bool, typer.Option("--trends/--no-trends", help="Show cost trend direction")
    ] = True,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Flag subscribers that breach margin or cost thresholds."""
    from .analyzer import UnitEconomicsAnalyzer
    from .cohort import CohortAnalyzer
    from .rc_client import RCClient

    rc_api_key = _get_api_key(api_key)
    cost_reader = _get_cost_reader(audit_db, usd_per_credit)
    all_costs = cost_reader.load_all() if cost_reader else []
    subscriber_ids = cost_reader.known_subscriber_ids() if cost_reader else []

    if not subscriber_ids:
        console.print("[yellow]No subscribers found in audit DB.[/yellow]")
        raise typer.Exit(1)

    async def _fetch():
        async with RCClient(rc_api_key) as rc:
            revenues = []
            for sid in subscriber_ids:
                try:
                    revenues.append(await rc.get_subscriber_revenue(sid))
                except Exception:
                    pass
            return revenues

    revenues = asyncio.run(_fetch())
    analyzer = UnitEconomicsAnalyzer()
    portfolio = analyzer.build_portfolio(revenues, all_costs)
    ca = CohortAnalyzer()
    alerts = ca.find_alerts(
        portfolio, margin_floor_pct=margin_floor, monthly_cost_ceiling_usd=monthly_cost_ceiling
    )

    if not alerts:
        if not as_json:
            console.print(
                f"\n[green]✓ No alerts.[/green] All subscribers above {margin_floor:.0f}% margin floor.\n"
            )
        else:
            console.print(json.dumps({"alerts": []}, indent=2))
        return

    # Compute trends for alerted subscribers if requested
    trends: dict[str, str] = {}
    if include_trends:
        sub_map = {s.subscriber_id: s for s in portfolio.subscribers}
        for a in alerts:
            sub = sub_map.get(a.subscriber_id)
            if sub:
                trend = ca.compute_trend(sub)
                trends[a.subscriber_id] = trend.slope_label

    if as_json:
        out = [
            {
                "subscriber_id": a.subscriber_id,
                "severity": a.severity,
                "reason": a.reason,
                "gross_margin_pct": a.gross_margin_pct,
                "monthly_cost_usd": a.monthly_cost_usd,
                "gross_profit_usd": a.gross_profit_usd,
                "cost_trend": trends.get(a.subscriber_id, "n/a"),
            }
            for a in alerts
        ]
        console.print(json.dumps({"alerts": out}, indent=2))
        return

    console.print(f"\n[bold]Alerts[/bold] — {len(alerts)} subscriber(s) require attention\n")
    table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
    table.add_column("Severity")
    table.add_column("Subscriber", no_wrap=True)
    table.add_column("Margin", justify="right")
    table.add_column("Monthly cost", justify="right")
    table.add_column("Gross P/L", justify="right")
    if include_trends:
        table.add_column("Cost trend")
    table.add_column("Reason")

    for a in alerts:
        sev_label = "[red]CRITICAL[/red]" if a.severity == "critical" else "[yellow]warn[/yellow]"
        sub_id = a.subscriber_id[:20] + "..." if len(a.subscriber_id) > 20 else a.subscriber_id
        row = [
            sev_label,
            sub_id,
            _fmt_margin(a.gross_margin_pct),
            f"${a.monthly_cost_usd:.4f}/mo",
            _fmt_usd(a.gross_profit_usd),
        ]
        if include_trends:
            row.append(trends.get(a.subscriber_id, "—"))
        row.append(a.reason)
        table.add_row(*row)

    console.print(table)
    console.print()


@app.command()
def trend(
    subscriber_id: str,
    api_key: Annotated[str | None, typer.Option("--api-key", "-k")] = None,
    audit_db: Annotated[str | None, typer.Option("--audit-db")] = None,
    usd_per_credit: Annotated[float, typer.Option("--usd-per-credit")] = 0.001,
    bucket_days: Annotated[int, typer.Option("--bucket-days", help="Days per time bucket")] = 7,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show cost trend over time for a specific subscriber."""
    from .analyzer import UnitEconomicsAnalyzer
    from .cohort import CohortAnalyzer
    from .rc_client import RCClient

    rc_api_key = _get_api_key(api_key)
    cost_reader = _get_cost_reader(audit_db, usd_per_credit)

    async def _fetch():
        async with RCClient(rc_api_key) as rc:
            return await rc.get_subscriber_revenue(subscriber_id)

    revenue = asyncio.run(_fetch())
    costs = cost_reader.load_for_subscriber(subscriber_id) if cost_reader else []
    analyzer = UnitEconomicsAnalyzer()
    econ = analyzer.analyze_subscriber(revenue, costs)
    ca = CohortAnalyzer()
    t = ca.compute_trend(econ, bucket_days=bucket_days)

    if as_json:
        console.print(
            json.dumps(
                {
                    "subscriber_id": t.subscriber_id,
                    "direction": t.direction,
                    "cost_delta_usd": t.cost_delta_usd,
                    "at_risk": t.at_risk,
                    "buckets": [
                        {
                            "period": b.period_label,
                            "cost_usd": b.total_cost_usd,
                            "ops": b.operation_count,
                        }
                        for b in t.buckets
                    ],
                },
                indent=2,
            )
        )
        return

    dir_map = {
        "rising": "[red]↑ rising[/red]",
        "falling": "[green]↓ falling[/green]",
        "flat": "[blue]→ stable[/blue]",
        "insufficient_data": "[dim]— insufficient data[/dim]",
    }
    console.print(f"\n[bold]Cost Trend:[/bold] {subscriber_id}")
    console.print(f"  Direction:    {dir_map.get(t.direction, t.direction)}")
    console.print(f"  Cost delta:   {t.slope_label}")
    console.print(f"  At risk:      {'[red]YES[/red]' if t.at_risk else '[green]no[/green]'}")

    if t.buckets:
        console.print("\n  [bold]Buckets:[/bold]")
        for b in t.buckets:
            bar_len = int(b.total_cost_usd * 500)  # scale for display
            bar = "█" * min(bar_len, 40)
            console.print(
                f"    {b.period_label}  {bar:<40} ${b.total_cost_usd:.6f}  ({b.operation_count} ops)"
            )
    console.print()


@app.command()
def forecast(
    subscriber_ids: Annotated[list[str] | None, typer.Argument()] = None,
    audit_db: Annotated[
        str | None, typer.Option("--audit-db", help="Path to billing meter SQLite DB")
    ] = None,
    usd_per_credit: Annotated[float, typer.Option("--usd-per-credit")] = 0.001,
    months: Annotated[int, typer.Option("--months", help="Forecast horizon in months")] = 12,
    scenario: Annotated[
        str,
        typer.Option(
            "--scenario",
            help="Churn scenario: optimistic | realistic | pessimistic | all",
        ),
    ] = "all",
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Forecast LTV for subscribers under churn scenarios."""
    from .analyzer import UnitEconomicsAnalyzer
    from .forecast import LTVForecaster

    cost_reader = _get_cost_reader(audit_db, usd_per_credit)
    all_costs = cost_reader.load_all() if cost_reader else []

    if not subscriber_ids:
        if cost_reader:
            subscriber_ids = cost_reader.known_subscriber_ids()
        if not subscriber_ids:
            console.print("[yellow]No subscriber IDs specified and no audit DB found.[/yellow]")
            raise typer.Exit(1)

    # Build synthetic portfolio from cost data only (no RC API needed)
    analyzer = UnitEconomicsAnalyzer()
    portfolio = analyzer.build_portfolio([], all_costs)

    # Filter to requested subscriber IDs if specified
    if subscriber_ids:
        sid_set = set(subscriber_ids)
        from .models import PortfolioSummary

        portfolio = PortfolioSummary(
            subscribers=[s for s in portfolio.subscribers if s.subscriber_id in sid_set]
        )

    forecaster = LTVForecaster()
    scenario_map = {s.label: s for s in forecaster.DEFAULT_SCENARIOS}

    if scenario == "all":
        pfs = forecaster.all_scenarios(portfolio, months=months)
    elif scenario in scenario_map:
        pfs = [forecaster.forecast_portfolio(portfolio, scenario_map[scenario], months=months)]
    else:
        console.print(
            f"[red]Unknown scenario:[/red] {scenario}. Use optimistic, realistic, pessimistic, or all."
        )
        raise typer.Exit(1)

    if as_json:
        out = [
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
            for pf in pfs
        ]
        console.print(json.dumps(out, indent=2))
        return

    for pf in pfs:
        console.print(
            f"\n[bold]{months}-Month Forecast — {pf.scenario.label}[/bold] "
            f"({pf.scenario.monthly_churn_rate * 100:.0f}%/mo churn)"
        )
        table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
        table.add_column("Subscriber", no_wrap=True)
        table.add_column("MRR", justify="right")
        table.add_column("Proj LTV", justify="right")
        table.add_column("Proj Cost", justify="right")
        table.add_column("Proj Profit", justify="right")
        table.add_column("Break-even", justify="right")

        for sf in pf.subscriber_forecasts:
            sub_id = (
                sf.subscriber_id[:20] + "..." if len(sf.subscriber_id) > 20 else sf.subscriber_id
            )
            table.add_row(
                sub_id,
                f"${sf.mrr_usd:.4f}",
                f"${sf.projected_ltv_usd:.4f}",
                f"${sf.projected_cost_usd:.4f}",
                _fmt_usd(sf.projected_profit_usd),
                str(sf.break_even_month) if sf.break_even_month else "—",
            )
        console.print(table)
        console.print(
            f"  Portfolio: Revenue ${pf.projected_revenue_usd:.4f} | "
            f"Cost ${pf.projected_cost_usd:.4f} | "
            f"Profit {_fmt_usd(pf.projected_profit_usd)} | "
            f"Survivors {pf.expected_survivors:.1f}"
        )
    console.print()


@app.command()
def report(
    subscriber_ids: Annotated[list[str] | None, typer.Argument()] = None,
    audit_db: Annotated[
        str | None, typer.Option("--audit-db", help="Path to billing meter SQLite DB")
    ] = None,
    usd_per_credit: Annotated[float, typer.Option("--usd-per-credit")] = 0.001,
    months: Annotated[int, typer.Option("--months", help="Forecast horizon in months")] = 12,
    fmt: Annotated[
        str, typer.Option("--format", help="Output format: markdown | json")
    ] = "markdown",
    output: Annotated[
        str | None, typer.Option("--output", "-o", help="Write output to file")
    ] = None,
) -> None:
    """Generate a full portfolio report (markdown or JSON)."""
    from .analyzer import UnitEconomicsAnalyzer
    from .models import PortfolioSummary
    from .report import PortfolioReporter

    cost_reader = _get_cost_reader(audit_db, usd_per_credit)
    all_costs = cost_reader.load_all() if cost_reader else []

    if not subscriber_ids:
        if cost_reader:
            subscriber_ids = cost_reader.known_subscriber_ids()

    analyzer = UnitEconomicsAnalyzer()
    portfolio = analyzer.build_portfolio([], all_costs)

    if subscriber_ids:
        sid_set = set(subscriber_ids)
        portfolio = PortfolioSummary(
            subscribers=[s for s in portfolio.subscribers if s.subscriber_id in sid_set]
        )

    reporter = PortfolioReporter(portfolio)

    if fmt == "json":
        content = reporter.as_json(months=months)
    else:
        content = reporter.as_markdown(months=months)

    if output:
        import pathlib

        pathlib.Path(output).write_text(content, encoding="utf-8")
        console.print(f"[green]Report written to:[/green] {output}")
    else:
        console.print(content)


def main() -> None:
    app()
