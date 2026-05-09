import unittest

import numpy as np
import pytest
import yfinance as yf

from rebalance import Asset, Cash, Portfolio


@pytest.mark.integration
class TestPortfolio(unittest.TestCase):
    def test_cash_interface(self):
        """
        Test portfolio's interface related to Cash class.

        Adding cash of different currency individually to the portfolio.
        """
        p = Portfolio()
        amount1 = 500.15
        p.add_cash(amount1, "cad")
        amount2 = 200.00
        p.add_cash(amount2, "usd")

        self.assertEqual(p.cash["CAD"].amount, amount1)
        self.assertEqual(p.cash["USD"].amount, amount2)

    def test_cash_interface2(self):
        """
        Test portfolio's interface related to Cash class.

        Collectively adding cash to the portfolio.
        """
        p = Portfolio()
        amounts = [500.15, 200.00]
        currencies = ["CAD", "USD"]
        p.easy_add_cash(amounts, currencies)

        self.assertEqual(p.cash[currencies[0]].amount, amounts[0])
        self.assertEqual(p.cash[currencies[1]].amount, amounts[1])

    def test_asset_interface(self):
        """
        Test portfolio's interface related to Asset class.

        Adding assets individually to the portfolio.
        """
        p = Portfolio()

        ticker = "VCN.TO"
        quantity = 2
        asset = Asset(ticker=ticker, quantity=quantity)

        p.add_asset(asset)
        self.assertEqual(asset.ticker, p.assets[ticker].ticker)
        self.assertEqual(asset.quantity, p.assets[ticker].quantity)
        self.assertEqual(asset.price, p.assets[ticker].price)

        ticker = "ZAG.TO"
        quantity = 20
        asset2 = Asset(ticker=ticker, quantity=quantity)
        p.add_asset(asset2)

        self.assertEqual(asset2.ticker, p.assets[ticker].ticker)
        self.assertEqual(asset2.quantity, p.assets[ticker].quantity)
        self.assertEqual(asset2.price, p.assets[ticker].price)

    def test_asset_interface2(self):
        """
        Test portfolio's interface related to Asset class.

        Collectively adding assets to the portfolio.
        """

        p = Portfolio()

        tickers = ["VCN.TO", "ZAG.TO"]
        quantities = [2, 20]
        p.easy_add_assets(tickers=tickers, quantities=quantities)

        n = len(tickers)
        for i in range(n):
            self.assertEqual(tickers[i], p.assets[tickers[i]].ticker)
            self.assertEqual(quantities[i], p.assets[tickers[i]].quantity)
            self.assertEqual(
                yf.Ticker(tickers[i]).fast_info["lastPrice"], p.assets[tickers[i]].price
            )

    def test_portfolio_value(self):
        """
        Test total market value, total cash value, and total value methods.
        """

        p = Portfolio()

        tickers = ["VCN.TO", "ZAG.TO", "XAW.TO", "TSLA"]
        quantities = [2, 20, 10, 4]
        p.easy_add_assets(tickers=tickers, quantities=quantities)

        mv = p.market_value("CAD")

        total_mv = np.sum([asset.market_value_in("CAD") for asset in p.assets.values()])

        self.assertAlmostEqual(mv, total_mv, 1)

        amounts = [500.15, 200.00]
        currencies = ["CAD", "USD"]
        p.easy_add_cash(amounts, currencies)

        cv = p.cash_value("CAD")

        usd_to_cad = Cash(1, "USD").exchange_rate("CAD")
        total_cv = np.sum(amounts[0] + amounts[1] * usd_to_cad)
        self.assertAlmostEqual(cv, total_cv, 1)

        self.assertAlmostEqual(p.value("CAD"), total_mv + total_cv, 1)

    def test_asset_allocation(self):
        """
        Test asset allocation method.
        """
        p = Portfolio()

        tickers = ["VCN.TO", "ZAG.TO", "XAW.TO", "TSLA"]
        quantities = [2, 20, 10, 4]
        p.easy_add_assets(tickers=tickers, quantities=quantities)

        asset_alloc = p.asset_allocation()
        self.assertAlmostEqual(sum(asset_alloc.values()), 100.0, 7)

        prices = [
            yf.Ticker(ticker).fast_info["lastPrice"]
            * Cash(1, yf.Ticker(ticker).fast_info["currency"]).exchange_rate("CAD")
            for ticker in tickers
        ]
        total = np.sum(np.asarray(quantities) * np.asarray(prices))
        n = len(tickers)
        for i in range(n):
            self.assertAlmostEqual(
                asset_alloc[tickers[i]], quantities[i] * prices[i] / total * 100.0, 1
            )

    def test_exchange(self):
        """
        Test currency exchange in Portfolio.
        """

        p = Portfolio()

        amounts = [500.15, 200.00]
        currencies = ["CAD", "USD"]
        p.easy_add_cash(amounts, currencies)

        cad_to_usd = Cash(1, "CAD").exchange_rate("USD")

        p.exchange_currency(to_currency="CAD", from_currency="USD", to_amount=100)
        self.assertAlmostEqual(p.cash["CAD"].amount, 500.15 + 100.0, 1)
        self.assertAlmostEqual(p.cash["USD"].amount, 200.0 - 100.0 * cad_to_usd, 1)

        p.exchange_currency(from_currency="USD", to_currency="CAD", from_amount=50)
        self.assertAlmostEqual(p.cash["CAD"].amount, 500.15 + 100 + 50 / cad_to_usd, 1)
        self.assertAlmostEqual(p.cash["USD"].amount, 200.0 - 100.0 * cad_to_usd - 50, 1)

        # error handling:
        with self.assertRaises(Exception):  # noqa: B017
            p.exchange_currency(
                to_currency="CAD", from_currency="USD", to_amount=100, from_amount=20
            )

        # error handling
        with self.assertRaises(Exception):  # noqa: B017
            p.exchange_currency(to_currency="CAD", from_currency="USD")

    def test_rebalancing(self):
        """
        Test rebalancing algorithm.

        This might break over time as prices increase.
        If we have enough cash though, the optimizer should ideally
        be able to match the target asset allocation
        pretty closely
        """

        p = Portfolio()

        tickers = ["XBB.TO", "XIC.TO", "ITOT", "IEFA", "IEMG"]
        quantities = [36, 64, 32, 8, 7]
        p.easy_add_assets(tickers=tickers, quantities=quantities)
        p.add_cash(3000, "USD")
        p.add_cash(515.21, "CAD")
        p.add_cash(5.00, "GBP")
        p.selling_allowed = True

        self.assertTrue(p.selling_allowed)

        # different order than tickers.
        # rebalance method should be able to handle such a case
        target_asset_alloc = {
            "XBB.TO": 20,
            "XIC.TO": 20,
            "IEFA": 20,
            "ITOT": 36,
            "IEMG": 4,
        }

        initial_value = p.value("CAD")
        (_, _, _, max_diff) = p.rebalance(target_asset_alloc, verbose=True)
        final_value = p.value("CAD")
        self.assertAlmostEqual(initial_value, final_value, 1)
        self.assertLessEqual(max_diff, 2.0)

        # Error handling
        with self.assertRaises(Exception):  # noqa: B017
            target_asset_alloc = {
                "XBB.TO": 20,
                "XIC.TO": 20,
                "IEFA": 20,
            }
            p.rebalance(target_asset_alloc)

    def test_rebalancing2(self):
        """
        Test rebalancing algorithm. Part 2.

        Cash is not in the same currency as the assets.
        """
        p = Portfolio()

        p.add_cash(200.0, "USD")
        p.add_cash(250.0, "GBP")

        tickers = ["VCN.TO", "XAW.TO", "ZAG.TO"]
        quantities = [5, 12, 20]
        p.easy_add_assets(tickers=tickers, quantities=quantities)

        target_asset_alloc = {
            "VCN.TO": 40.0,
            "ZAG.TO": 40.0,
            "XAW.TO": 20.0,
        }

        initial_value = p.value("CAD")
        p.selling_allowed = False
        (_, prices, _, _) = p.rebalance(target_asset_alloc, verbose=True)
        final_value = p.value("CAD")
        self.assertAlmostEqual(initial_value, final_value, -1)

        # The prices should be in the tickers' currency
        for ticker in tickers:
            self.assertEqual(prices[ticker][1], "CAD")

        # Since there was no CAD to start off with,
        # there should be none after rebalacing either
        # (i.e. amount converted to CAD should be the amount used to purchase CAD assets)
        self.assertAlmostEqual(p.cash["CAD"].amount, 0.0, 1)


class TestMixedFractionalRebalancing(unittest.TestCase):
    """Unit tests for mixed integer/fractional rebalancing using mocked prices."""

    def _make_portfolio(self):
        from unittest.mock import patch

        from rebalance.asset import Asset
        from rebalance.money import Price

        p = Portfolio()
        p.selling_allowed = False
        p.common_currency = "USD"

        with patch(
            "rebalance.asset.fetch_yfinance_price", return_value=Price(100.0, "USD")
        ):
            # Two integer ETF assets
            a1 = Asset("ETF_A", quantity=10)
            a2 = Asset("ETF_B", quantity=5)
            # One fractional mutual fund
            a3 = Asset("FUND_C", quantity=2, fractional=True)

        p.add_asset(a1)
        p.add_asset(a2)
        p.add_asset(a3)
        p.add_cash(1000.0, "USD")
        return p

    def test_integer_assets_get_integer_units(self):
        p = self._make_portfolio()
        target = {"ETF_A": 40.0, "ETF_B": 30.0, "FUND_C": 30.0}
        new_units, _, _, _ = p.rebalance(target)
        assert isinstance(new_units["ETF_A"], int)
        assert isinstance(new_units["ETF_B"], int)

    def test_fractional_asset_gets_float_units(self):
        p = self._make_portfolio()
        target = {"ETF_A": 40.0, "ETF_B": 30.0, "FUND_C": 30.0}
        new_units, _, _, _ = p.rebalance(target)
        assert isinstance(new_units["FUND_C"], float)

    def test_budget_not_exceeded(self):
        p = self._make_portfolio()
        # Capture cash before rebalance (portfolio is mutated in-place)
        cash_before = p.cash_value("USD")
        target = {"ETF_A": 40.0, "ETF_B": 30.0, "FUND_C": 30.0}
        new_units, prices, _, _ = p.rebalance(target)
        total_spend = sum(abs(new_units[t]) * prices[t][0] for t in new_units)
        assert total_spend <= cash_before + 1e-4  # allow tiny float slack


@pytest.mark.integration
class TestSolverValidation(unittest.TestCase):
    """Validates that the MIQP solver produces allocations close to what the old
    SLSQP solver would have achieved. Tolerance is intentionally loose (5%) because
    the MIQP is more exact — integer rounding differs — but both should land near
    the target allocation."""

    def _check_portfolio(self, json_path, tol_pct=5.0):
        """Load a portfolio JSON, rebalance, and assert every asset's post-rebalance
        allocation is within ``tol_pct`` percentage points of its target."""
        import os

        full_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "portfolios", json_path
        )
        from rebalance.loader import load_portfolio

        portfolio, target_alloc = load_portfolio(full_path)
        portfolio.rebalance(target_alloc, verbose=False)  # mutates portfolio in-place

        actual_alloc = portfolio.asset_allocation()
        for ticker, target_pct in target_alloc.items():
            actual_pct = actual_alloc.get(ticker, 0.0)
            diff = abs(actual_pct - target_pct)
            self.assertLessEqual(
                diff,
                tol_pct,
                msg=f"{ticker}: target={target_pct:.2f}% actual={actual_pct:.2f}% diff={diff:.2f}%",
            )

    def test_allweather_zino(self):
        self._check_portfolio("allweather_zino.json")

    def test_kids_allweather(self):
        self._check_portfolio("kids_allweather.json")
