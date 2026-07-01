import unittest

import pytest
import yfinance as yf

from rebalance import Asset, Price


def _live_yfinance_price(ticker: str) -> Price:
    quote = yf.Ticker(ticker)
    metadata = quote.history_metadata or {}
    price = metadata.get("regularMarketPrice", quote.fast_info["lastPrice"])
    currency = metadata.get("currency", quote.fast_info["currency"])
    return Price(price, currency=currency)


@pytest.mark.integration
class TestAsset(unittest.TestCase):
    def test_interface(self):
        """
        Test the interface of Asset class.

        Primary methods.
        """

        ticker = "VCN.TO"
        quantity = 2
        asset = Asset(ticker, quantity)

        # import sys
        # print(ticker_info["lastPrice"], ticker_info["currency"], file=sys.stderr)
        # print(asset, file=sys.stderr)
        live_price = _live_yfinance_price(ticker)
        self.assertEqual(asset.quantity, quantity)
        self.assertEqual(asset.price, live_price.price)
        self.assertEqual(asset.ticker, ticker)
        self.assertEqual(asset.currency, live_price.currency)
        self.assertEqual(asset.market_value(), live_price.price * quantity)

    def test_interface2(self):
        """
        Test the interface of Asset class. Part 2.

        Mainly related to currency conversion.
        """

        ticker = "TSLA"  # currency: USD
        asset = Asset(ticker)
        quantity = 5
        asset.quantity = quantity

        self.assertEqual(asset.quantity, quantity)

        price = _live_yfinance_price(ticker)

        self.assertEqual(asset.price_in("CAD"), price.price_in("CAD"))
        self.assertEqual(asset.market_value(), price.price * quantity)
        self.assertEqual(asset.market_value_in("CAD"), price.price_in("CAD") * quantity)

    def test_interface3(self):
        """
        Test the interface of Asset class. Part 3.

        Mainly related to buying units.
        """

        ticker = "ZAG.TO"  # currency: CAD
        quantity = 10
        asset = Asset(ticker, quantity)

        price = _live_yfinance_price(ticker)

        to_buy = 4
        self.assertEqual(asset.cost_of(to_buy), price.price * to_buy)
        self.assertEqual(
            asset.cost_of(to_buy, currency="USD"), price.price_in("USD") * to_buy
        )

        self.assertEqual(asset.buy(to_buy), price.price * to_buy)
        self.assertEqual(asset.quantity, quantity + to_buy)

        self.assertEqual(
            asset.buy(to_buy, currency="USD"), price.price_in("USD") * to_buy
        )
        self.assertEqual(asset.quantity, quantity + to_buy + to_buy)
