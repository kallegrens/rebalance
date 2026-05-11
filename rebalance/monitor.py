"""rebalance-monitor — cron-friendly band-drift checker.

Fetches current prices for a portfolio and checks whether any asset has
drifted outside its range-based rebalancing band (±1 std dev from target
weight).  Designed to run on a schedule (cron / k8s CronJob) and emit a
WARNING log signal when action is needed.

Exit codes:
  0 — completed successfully (triggers fired or not — triggering is not an error)
  1 — failed to load/parse the portfolio file
"""

import argparse
import json
import sys

from loguru import logger
from pydantic import ValidationError

from rebalance.band_checker import check_bands
from rebalance.band_rendering import build_band_rebalance_report
from rebalance.band_targets import build_band_rebalance_plan, cash_inclusive_allocation
from rebalance.loader import load_portfolio, load_portfolio_config
from rebalance.logging_setup import setup_logging
from rebalance.notifications import notify_failure, notify_rebalance_trigger
from rebalance.rebalancing_helper import DEFAULT_OBJECTIVE, SUPPORTED_OBJECTIVES


def main() -> None:
    setup_logging()

    parser = argparse.ArgumentParser(
        description="Check whether a portfolio needs rebalancing based on band-drift rules."
    )
    parser.add_argument("portfolio", help="Path to the portfolio JSON file")
    parser.add_argument(
        "--trade-non-triggered",
        dest="lock_non_triggered",
        action="store_false",
        default=True,
        help=(
            "Allow non-triggered assets to participate in residual target allocation."
        ),
    )
    parser.add_argument(
        "--objective",
        choices=SUPPORTED_OBJECTIVES,
        default=DEFAULT_OBJECTIVE,
        help="Optimizer objective to use for trade selection.",
    )
    parser.add_argument(
        "--json",
        dest="json_path",
        metavar="PATH",
        help="Also write a JSON representation of the rebalance table to PATH.",
    )
    args = parser.parse_args()

    try:
        portfolio, target_allocation = load_portfolio(args.portfolio)
        config = load_portfolio_config(args.portfolio)
    except FileNotFoundError as e:
        logger.error("Portfolio file not found: {}", args.portfolio)
        notify_failure(e, context=args.portfolio)
        sys.exit(1)
    except ValidationError as e:
        logger.error("Invalid portfolio file:\n{}", e)
        notify_failure(e, context=args.portfolio)
        sys.exit(1)
    except ValueError as e:
        logger.error("Invalid portfolio file: {}", e)
        notify_failure(e, context=args.portfolio)
        sys.exit(1)

    volatilities = {a.ticker: a.volatility for a in config.assets}
    statuses = check_bands(portfolio, target_allocation, volatilities)

    triggers = [s for s in statuses if s.triggered]

    for s in statuses:
        label = s.name or s.ticker
        if s.triggered:
            logger.warning(
                "REBALANCE TRIGGER: {} ({}) — current {:.2f}% is {} band "
                "[{:.2f}%–{:.2f}%], target {:.2f}%",
                s.ticker,
                label,
                s.current_pct,
                "above upper" if s.direction == "above" else "below lower",
                s.lower_band,
                s.upper_band,
                s.target_pct,
            )
        else:
            logger.info(
                "OK: {} ({}) — current {:.2f}% within band [{:.2f}%–{:.2f}%], target {:.2f}%",
                s.ticker,
                label,
                s.current_pct,
                s.lower_band,
                s.upper_band,
                s.target_pct,
            )

    if triggers:
        logger.warning(
            "REBALANCE NOTIFICATION SIGNAL: {} asset(s) outside bands "
            "(notification channel not yet configured)",
            len(triggers),
        )
        notify_rebalance_trigger(triggers)

        logger.info("Computing band-aware rebalancing trades...")
        try:
            plan = None
            if args.json_path is not None:
                plan = build_band_rebalance_plan(
                    portfolio,
                    target_allocation,
                    statuses,
                    lock_non_triggered=args.lock_non_triggered,
                )

            new_units, prices, exchange_history = portfolio.band_rebalance(
                target_allocation,
                statuses,
                verbose=True,
                lock_non_triggered=args.lock_non_triggered,
                objective=args.objective,
                plan=plan,
            )

            if args.json_path is not None and plan is not None:
                cost = {
                    ticker: prices[ticker][0] * new_units[ticker] for ticker in prices
                }
                report = build_band_rebalance_report(
                    portfolio,
                    new_units,
                    prices,
                    cost,
                    exchange_history,
                    cash_inclusive_allocation(portfolio),
                    target_allocation,
                    plan,
                )
                with open(args.json_path, "w", encoding="utf-8") as json_file:
                    json.dump(report, json_file, ensure_ascii=False, indent=2)
                    json_file.write("\n")
        except Exception as e:
            logger.error("Band rebalancing computation failed: {}", e)
    else:
        logger.info(
            "All {} checked asset(s) are within their rebalancing bands.",
            len(statuses),
        )


if __name__ == "__main__":
    main()
