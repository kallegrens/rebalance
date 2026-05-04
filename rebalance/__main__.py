import argparse
import sys

from loguru import logger
from pydantic import ValidationError

from rebalance.loader import load_portfolio
from rebalance.logging_setup import setup_logging
from rebalance.notifications import notify_failure


def main():
    setup_logging()

    parser = argparse.ArgumentParser(
        description="Rebalance a portfolio defined in a JSON file."
    )
    parser.add_argument("portfolio", help="Path to the portfolio JSON file")
    parser.add_argument(
        "--verbose", action="store_true", help="Print detailed rebalancing output"
    )
    args = parser.parse_args()

    try:
        portfolio, target_allocation = load_portfolio(args.portfolio)
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

    try:
        portfolio.rebalance(target_allocation, verbose=args.verbose)
    except Exception as e:
        logger.exception("Rebalancing failed unexpectedly")
        notify_failure(e, context=args.portfolio)
        sys.exit(1)


if __name__ == "__main__":
    main()
