import json
from concurrent.futures import ThreadPoolExecutor

import requests
from loguru import logger
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from rebalance import Portfolio
from rebalance.asset import Asset
from rebalance.schemas import PortfolioConfig


def _make_session() -> requests.Session:
    """Return a requests Session with exponential-backoff retry on transient errors."""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _build_asset(asset_config, session: requests.Session) -> Asset:
    kwargs: dict = {"session": session, "fractional": asset_config.fractional}
    if asset_config.nasdaq_nordic_id is not None:
        kwargs["nasdaq_nordic_id"] = asset_config.nasdaq_nordic_id
        kwargs["nasdaq_nordic_asset_class"] = asset_config.nasdaq_nordic_asset_class
    if asset_config.name is not None:
        kwargs["name"] = asset_config.name
    return Asset(asset_config.ticker, asset_config.quantity, **kwargs)


def load_portfolio(json_path: str) -> tuple:
    """Load a Portfolio and target allocation from a JSON file.

    Returns a (Portfolio, target_allocation) tuple where target_allocation
    is a dict mapping ticker -> float percentage.

    Price fetching is parallelised across assets using a thread pool.

    Raises:
        pydantic.ValidationError: if the JSON does not match the expected schema.
        FileNotFoundError: if the JSON file does not exist.
    """
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    config = PortfolioConfig.model_validate(data)
    logger.info("Loading portfolio '{}' ({} assets)", config.name, len(config.assets))
    session = _make_session()

    p = Portfolio()
    p.selling_allowed = config.selling_allowed
    p.common_currency = config.common_currency

    max_workers = min(len(config.assets), 8)
    logger.info("Fetching prices for {} assets...", len(config.assets))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        assets = list(executor.map(lambda a: _build_asset(a, session), config.assets))

    for asset in assets:
        p.add_asset(asset)

    for cash in config.cash:
        p.add_cash(cash.amount, cash.currency)

    target_allocation = {a.ticker: a.target_allocation for a in config.assets}

    logger.info(
        "Portfolio loaded: {} assets, {} cash positions", len(assets), len(config.cash)
    )
    return p, target_allocation
