# rc-unit-economics

**Per-subscriber unit economics for RevenueCat agent-native SaaS.**

Cross-reference RevenueCat subscription revenue with operation costs from [agent-billing-meter](https://github.com/zarpa-cat/agent-billing-meter) to answer the question every agent-operated SaaS needs to answer: *which of my subscribers are actually profitable?*

```
$ rcue analyze user_123 user_456 user_789 --audit-db ~/.abm/audit.db

Portfolio Summary
  Subscribers:   3
  Profitable:    2
  Unprofitable:  1
  Zero cost:     0

  Total LTV revenue:  $29.97
  Total cost:         $0.18
  Gross profit:       $29.79
  Portfolio margin:   99.4%

  MRR:                $19.98/mo
  Cost run rate:      $0.09/mo
  Net MRR:            $19.89/mo

 Subscriber        Status     LTV       Cost     Gross P/L   Margin  Ops
 ──────────────────────────────────────────────────────────────────────────
 user_123          active     $9.9900   $0.0500  $9.9400     99.5%   5
 user_456          active     $9.9900   $0.0700  $9.9200     99.3%   7
 user_789          active     $9.9900   $0.0600  -$9.9300    -0.6%   6
```

---

## Why

Agent-native SaaS has a cost structure that traditional SaaS doesn't: every subscriber triggers real inference spend, and that spend varies by usage. A subscriber who uses your agent heavily costs more to serve than one who barely touches it — but both pay the same subscription price.

RevenueCat gives you the revenue side. [agent-billing-meter](https://github.com/zarpa-cat/agent-billing-meter) gives you the cost side. This library crosses them.

---

## Installation

```bash
pip install rc-unit-economics
# or
uv add rc-unit-economics
```

---

## Quick Start

```bash
# Set your RC API key
export RC_API_KEY=rc_sk_...
export RCUE_AUDIT_DB=~/.abm/audit.db   # path to agent-billing-meter audit log

# Analyze specific subscribers
rcue analyze user_123 user_456

# Analyze a single subscriber in detail
rcue subscriber user_123

# Break-even analysis (no RC key needed)
rcue breakeven --price 9.99 --ops-per-month 30 --credits-per-op 10 --usd-per-credit 0.001

# JSON output for scripting
rcue analyze user_123 --json
```

---

## Commands

### `rcue analyze`

Analyze unit economics for one or more subscribers. Fetches revenue from RC, reads costs from the billing meter audit log, produces a portfolio summary + per-subscriber table.

```
Options:
  --api-key, -k       RC API key (or RC_API_KEY env var)
  --audit-db          Path to agent-billing-meter SQLite DB (or RCUE_AUDIT_DB env var)
  --usd-per-credit    USD conversion rate per credit (default: 0.001)
  --table/--no-table  Show per-subscriber table (default: table)
  --json              Output as JSON
```

If no subscriber IDs are provided, all subscribers known from the audit DB are analyzed.

### `rcue subscriber`

Detailed breakdown for a single subscriber, including recent operation history.

### `rcue breakeven`

Calculate break-even point for your subscription pricing given operation costs. No RC connection needed.

```
Options:
  --price             Monthly subscription price in USD (default: 9.99)
  --ops-per-month     Expected operations per subscriber/month (default: 30)
  --credits-per-op    Average credits per operation (default: 10)
  --usd-per-credit    USD cost per credit unit (default: 0.001)
  --overhead          Monthly fixed overhead in USD (default: 0)
```

---

## Python API

```python
import asyncio
from rc_unit_economics import RCClient, CostReader, UnitEconomicsAnalyzer

async def main():
    reader = CostReader(db_path="~/.abm/audit.db", usd_per_credit=0.001)
    costs = reader.load_all()

    async with RCClient(api_key="rc_sk_...") as rc:
        revenue = await rc.get_subscriber_revenue("user_123")

    analyzer = UnitEconomicsAnalyzer()
    econ = analyzer.analyze_subscriber(revenue, costs)

    print(f"Subscriber: {econ.subscriber_id}")
    print(f"LTV: ${econ.revenue.ltv_usd:.2f}")
    print(f"Cost: ${econ.total_cost_usd:.4f}")
    print(f"Margin: {econ.gross_margin_pct:.1f}%")
    print(f"Profitable: {econ.is_profitable}")

asyncio.run(main())
```

---

## Credit-to-USD Conversion

Set `--usd-per-credit` to your actual inference cost per credit unit. This depends on your pricing model:

| Scenario | Typical rate |
|---|---|
| GPT-4o mini, 1 credit = 1K tokens | ~$0.00015 |
| Claude Haiku, 1 credit = 1K tokens | ~$0.00025 |
| 1 credit = $0.001 fixed | $0.001 |
| Credits priced at $10/1000 | $0.01 |

The default ($0.001/credit) is a reasonable starting point. Calibrate against your actual invoices.

---

## Related Projects

| Repo | Role |
|---|---|
| [rc-entitlement-gate](https://github.com/zarpa-cat/rc-entitlement-gate) | Entitlement checking with TTL cache |
| [agent-billing-meter](https://github.com/zarpa-cat/agent-billing-meter) | Credit debit + audit log (the cost source) |
| [churnwall](https://github.com/zarpa-cat/churnwall) | Churn risk scoring + retention |
| [rc-agent-ops](https://github.com/zarpa-cat/rc-agent-ops) | Integration layer for all three |

---

## License

MIT
