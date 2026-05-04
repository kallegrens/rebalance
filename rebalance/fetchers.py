"""Price-fetching backends for Asset.

Each function returns a :class:`.money.Price` instance.
"""

import requests
import yfinance as yf

from .money import Price


def fetch_yfinance_price(ticker: str, session=None) -> Price:
    """Fetch the latest price for *ticker* via yfinance.

    Args:
        ticker (str): Yahoo Finance ticker symbol.
        session: Optional requests session (e.g. a cached session). When
            ``None`` yfinance manages its own session.

    Returns:
        Price: Last traded price with its native currency.
    """
    ticker_obj = (
        yf.Ticker(ticker) if session is None else yf.Ticker(ticker, session=session)
    )
    info = ticker_obj.fast_info
    return Price(info["lastPrice"], info["currency"])


def fetch_nasdaq_nordic_price(instrument_id: str, asset_class: str) -> Price:
    """Fetch the latest price for a Nasdaq Nordic instrument.

    Args:
        instrument_id (str): Nasdaq Nordic instrument ID (e.g. ``"TX4856348"``).
        asset_class (str): Asset class string (e.g. ``"ETN/ETC"``, ``"ETF"``,
            ``"Share"``).

    Returns:
        Price: Last traded price with its native currency.

    Raises:
        requests.HTTPError: if the API returns a non-2xx status.
    """
    url = f"https://api.nasdaq.com/api/nordic/instruments/{instrument_id}/info"
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    response = requests.get(
        url,
        params={"assetClass": asset_class, "lang": "en"},
        headers=headers,
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    header = data["data"]["qdHeader"]
    price_str = header["primaryData"]["lastSalePrice"]  # e.g. "SEK 143,71"
    currency = header["currency"]
    price = float(price_str.split()[-1].replace(",", ".").replace("\xa0", ""))
    return Price(price, currency)
