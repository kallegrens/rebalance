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
```

## Portfolio file format

Create a JSON file describing your portfolio:

```json
{
  "name": "My portfolio",
  "selling_allowed": false,
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
