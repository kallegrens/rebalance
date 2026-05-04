"""Price-fetching backends for Asset.

Each function returns a :class:`.money.Price` instance.
"""

import requests
import yfinance as yf
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .money import Price


def fetch_yfinance_price(ticker: str) -> Price:
    """Fetch the latest price for *ticker* via yfinance.

    yfinance manages its own curl_cffi session internally; passing an external
    session is not supported.

    Args:
        ticker (str): Yahoo Finance ticker symbol.

    Returns:
        Price: Last traded price with its native currency.
    """
    info = yf.Ticker(ticker).fast_info
    return Price(info["lastPrice"], info["currency"])


@retry(
    retry=retry_if_exception_type((requests.ConnectionError, requests.HTTPError)),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    stop=stop_after_attempt(3),
    before_sleep=lambda rs: logger.warning(
        "Retrying {} (attempt {}): {}",
        rs.fn.__name__ if rs.fn is not None else "?",
        rs.attempt_number,
        rs.outcome.exception() if rs.outcome is not None else "unknown error",
    ),
    reraise=True,
)
def fetch_nasdaq_nordic_price(
    instrument_id: str, asset_class: str, session=None
) -> Price:
    """Fetch the latest price for a Nasdaq Nordic instrument.

    Retries up to 3 times with exponential backoff (1s, 2s, 4s capped at 10s)
    on connection errors or HTTP 5xx/429 responses.

    Args:
        instrument_id (str): Nasdaq Nordic instrument ID (e.g. ``"TX4856348"``).
        asset_class (str): Asset class string (e.g. ``"ETN/ETC"``, ``"ETF"``,
            ``"Share"``).
        session: Optional requests session. When ``None`` a plain
            ``requests.get`` is used.

    Returns:
        Price: Last traded price with its native currency.

    Raises:
        requests.HTTPError: if the API returns a non-2xx status after all retries.
    """
    url = f"https://api.nasdaq.com/api/nordic/instruments/{instrument_id}/info"
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    get = session.get if session is not None else requests.get
    response = get(
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
