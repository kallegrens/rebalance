# Rebalance

A calculator that tells you how to split your investment amongst your portfolio's assets based on your target asset allocation.

To use it, install the package and write a portfolio JSON file as described below.

## Installation

```bash
pip install rebalance
```

## Usage

```bash
rebalance portfolios/my_portfolio.json
```

## Band-aware monitoring

The monitor command checks configured volatility bands using the total portfolio
value, including cash. Additions and withdrawals should therefore be represented
in the portfolio cash before running the monitor.

When a band rebalance is triggered, assets with a `0` target allocation are sold
to zero and their proceeds become buy capacity. New positive-target assets aim
for their JSON target, triggered assets aim for their band tolerance midpoint,
and non-triggered existing assets are frozen by default to avoid unnecessary
trades. Any residual target budget is spread only across tradable positive-target
assets; frozen non-triggered assets stay frozen. If the tradable assets cannot
use all available cash without leaving their bands, the remainder stays as cash.

```bash
rebalance-monitor portfolios/my_portfolio.json
rebalance-monitor portfolios/my_portfolio.json --trade-non-triggered
rebalance-monitor portfolios/my_portfolio.json --withdrawal 300000 --json report.json
rebalance-monitor portfolios/my_portfolio.json --max-withdrawal --json report.json
```

Use `--withdrawal AMOUNT` to plan a withdrawal in the portfolio common currency.
The monitor sells enough assets to fund the withdrawal and, when Nordnet leverage
is configured, also reserves any credit repayment needed so the projected
post-trade debt remains inside the selected leverage policy. A negative common
currency cash balance without `--withdrawal` is treated as an already-recorded
withdrawal request; do not use both at the same time. Use `--max-withdrawal` to
estimate the largest no-new-credit withdrawal that the same planner can fund
while staying inside the policy.

## Portfolio file format

Create a JSON file describing your portfolio:

```json
{
  "name": "My portfolio",
  "selling_allowed": false,
  "common_currency": "SEK",
  "conversion_cost": 0.25,
  "courtage_profile": "nordnet_sweden",
  "cash": [
    {"amount": 3000.0, "currency": "USD"},
    {"amount": 200.0, "currency": "CAD"}
  ],
  "assets": [
    {"ticker": "XBB.TO", "quantity": 36, "target_allocation": 20},
    {"ticker": "XIC.TO", "quantity": 64, "target_allocation": 20},
    {"ticker": "ITOT",   "quantity": 32, "target_allocation": 36},
    {"ticker": "IEFA",   "quantity": 8,  "target_allocation": 20},
    {"ticker": "IEMG",   "quantity": 7,  "target_allocation": 4}
  ]
}
```

Set `courtage_profile` to `nordnet_sweden` to apply the built-in 9/49/69/99
class schedule in the portfolio common currency. Courtage is added on top of
any `conversion_cost` FX spread and is shown in verbose rebalance output under
the `Courtage`, `Courtage Fee <CCY>`, and `FX Fee <CCY>` columns. Assets marked
with `fractional: true` are treated as courtage-free mutual funds.

## Nordnet leverage monitoring

`rebalance-monitor --json PATH` writes a JSON report on every run. When no band
has triggered, the trade rows are empty but the report still includes current
band statuses and leverage diagnostics.

Withdrawal-aware reports include top-level `withdrawal_plan` and, when requested,
`max_withdrawal`. Trade reports also include synthetic `withdrawal_rows` and
`financing_rows` entries so the external withdrawal and any Nordnet credit
repayment are visible next to the asset trades without being modeled as assets.

Nordnet margin debt is configured explicitly under `leverage`; do not represent
it as negative cash. Cash remains available for deposits, withdrawals, and normal
rebalancing capacity, while leverage analysis treats margin debt as a separate
liability.

```json
{
  "name": "My portfolio",
  "common_currency": "SEK",
  "leverage": {
    "provider": "nordnet",
    "margin_debt": {"amount": 370000.0, "currency": "SEK"},
    "drawdown_from_ath_pct": 0.0,
    "target_leverage": 1.37
  },
  "assets": [
    {
      "ticker": "0P00018JII.ST",
      "quantity": 109,
      "target_allocation": 1.9,
      "lending_value": 80.0,
      "extended_lending_value": 85.0,
      "instrument_type": "fund"
    }
  ]
}
```

For Nordnet's advanced-portfolio policy, the default target leverage is `1.37x`.
The default fallback weighted lending value is `79%`, and the tier-1 discount
limit is `40%` of lending value, giving the article's fallback borrowing-ratio
ceiling of `31.6%`. If assets define `lending_value` and
`extended_lending_value`, the monitor computes the current weighted lending value
from actual live holdings instead of using the fallback.

For Portfoljbelaning Plus level 1, the diversification check uses only approved
holdings, meaning positions with a positive ordinary `lending_value`. The
default caps match Nordnet's published rules: one approved stock or ETF may be
at most `20%` of approved holdings, one approved fund may be at most `60%`, and
the discount bracket is applied to positions whose ordinary `lending_value` is
at least `70%`. If you want to model level 2 instead, override the JSON config
to use `25/75` issuer caps and a `60%` discount limit.

## Example output

```text
 Ticker      Ask     Quantity      Amount    Currency     Old allocation   New allocation     Target allocation
                      to buy         ($)                      (%)              (%)                 (%)
---------------------------------------------------------------------------------------------------------------
  XBB.TO    33.43       30         1002.90      CAD          17.52            19.99               20.00
  XIC.TO    24.27       27          655.29      CAD          22.61            20.01               20.00
    ITOT    69.38       10          693.80      USD          43.93            35.88               36.00
    IEFA    57.65       20         1153.00      USD           9.13            19.88               20.00
    IEMG    49.14        0            0.00      USD           6.81             4.24                4.00

Largest discrepancy between the new and the target asset allocation is 0.24 %.

Before making the above purchases, the following currency conversion is required:
    1072.88 USD to 1458.19 CAD at a rate of 1.3591.

Remaining cash:
    80.32 USD.
    0.00 CAD.
```
