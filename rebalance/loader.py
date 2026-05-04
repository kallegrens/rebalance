import json

from rebalance import Portfolio
from rebalance.asset import Asset
from rebalance.schemas import PortfolioConfig


def load_portfolio(json_path: str) -> tuple:
    """Load a Portfolio and target allocation from a JSON file.

    Returns a (Portfolio, target_allocation) tuple where target_allocation
    is a dict mapping ticker -> float percentage.

    Raises:
        pydantic.ValidationError: if the JSON does not match the expected schema.
        FileNotFoundError: if the JSON file does not exist.
    """
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    config = PortfolioConfig.model_validate(data)

    p = Portfolio()
    p.selling_allowed = config.selling_allowed

    for asset in config.assets:
        kwargs = {}
        if asset.nasdaq_nordic_id is not None:
            kwargs["nasdaq_nordic_id"] = asset.nasdaq_nordic_id
            kwargs["nasdaq_nordic_asset_class"] = asset.nasdaq_nordic_asset_class
        p.add_asset(Asset(asset.ticker, asset.quantity, **kwargs))

    for cash in config.cash:
        p.add_cash(cash.amount, cash.currency)

    target_allocation = {a.ticker: a.target_allocation for a in config.assets}

    return p, target_allocation
