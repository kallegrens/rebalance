import argparse
import sys

from pydantic import ValidationError

from rebalance.loader import load_portfolio


def main():
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
    except FileNotFoundError:
        print(f"Error: portfolio file not found: {args.portfolio}", file=sys.stderr)
        sys.exit(1)
    except ValidationError as e:
        print(f"Error: invalid portfolio file:\n{e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: invalid portfolio file: {e}", file=sys.stderr)
        sys.exit(1)

    portfolio.rebalance(target_allocation, verbose=args.verbose)


if __name__ == "__main__":
    main()
