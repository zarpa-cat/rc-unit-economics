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


def main() -> None:
    app()
