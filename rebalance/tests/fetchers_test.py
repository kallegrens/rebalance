from unittest.mock import Mock, patch

import pytest

from rebalance.fetchers import fetch_yfinance_price


@patch("rebalance.fetchers.yf.Ticker")
def test_fetch_yfinance_price_prefers_regular_market_price(mock_ticker):
    ticker = Mock()
    ticker.fast_info = {"lastPrice": 373.5, "currency": "GBp"}
    ticker.history_metadata = {"regularMarketPrice": 420.0}
    mock_ticker.return_value = ticker

    price = fetch_yfinance_price("BHMGL.XC")

    assert price.price == pytest.approx(4.2)
    assert price.currency == "GBP"
