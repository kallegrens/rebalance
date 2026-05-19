import unittest
from unittest.mock import patch

import numpy as np
import pytest
import yfinance as yf
from rich.console import Console

from rebalance import Asset, Cash, Portfolio
from rebalance.band_targets import build_band_rebalance_plan, cash_inclusive_allocation
from rebalance.money import Price
from rebalance.rebalancing_helper import SUPPORTED_OBJECTIVES, rebalance_optimizer


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
        self.assertAlmostEqual(initial_value, final_value, delta=1.0)
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

        from rebalance.asset import Asset

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

    def test_verbose_fractional_delta_units_use_two_decimals(self):
        p = Portfolio()
        p.common_currency = "USD"
        p.selling_allowed = False

        with patch(
            "rebalance.asset.fetch_yfinance_price", return_value=Price(100.0, "USD")
        ):
            p.add_asset(Asset("FUND_C", quantity=2, fractional=True))

        p.add_cash(50.0, "USD")
        console = Console(record=True, width=120)

        with patch("rebalance.portfolio._console", console):
            p.rebalance({"FUND_C": 100.0}, verbose=True)

        output = console.export_text()

        assert "0.50" in output
        assert "0.500" not in output


class TestSellingAllowed:
    """Unit tests for rebalance() with selling_allowed=True using mocked prices.

    Ensures the _sell_everything() → optimizer → delta-conversion path is exercised
    without network access.
    """

    def _make_portfolio(self, mock_price_fetchers):
        from rebalance.asset import Asset

        p = Portfolio()
        p.common_currency = "USD"
        p.selling_allowed = True

        with patch(
            "rebalance.asset.fetch_yfinance_price", return_value=Price(100.0, "USD")
        ):
            p.add_asset(Asset("ETF_A", quantity=80))
            p.add_asset(Asset("ETF_B", quantity=20))

        p.add_cash(0.0, "USD")
        return p

    def test_selling_reduces_overweight_asset(self, mock_price_fetchers):
        """When selling is allowed, an overweight asset should be sold (negative Δ Units)."""
        # ETF_A: 80 units @ $100 = 80% of $10000, target 50% → must sell
        p = self._make_portfolio(mock_price_fetchers)
        new_units, _, _, _ = p.rebalance({"ETF_A": 50.0, "ETF_B": 50.0})
        assert new_units["ETF_A"] < 0, "Overweight asset should be sold"

    def test_value_conserved(self, mock_price_fetchers):
        """Total portfolio value must be the same before and after rebalancing."""
        p = self._make_portfolio(mock_price_fetchers)
        initial_value = p.value("USD")
        p.rebalance({"ETF_A": 50.0, "ETF_B": 50.0})
        assert p.value("USD") == pytest.approx(initial_value, abs=1e-2)

    def test_allocation_near_target(self, mock_price_fetchers):
        """Post-rebalance allocation should land close to the 50/50 target."""
        p = self._make_portfolio(mock_price_fetchers)
        p.rebalance({"ETF_A": 50.0, "ETF_B": 50.0})
        alloc = p.asset_allocation()
        assert alloc["ETF_A"] == pytest.approx(50.0, abs=2.0)
        assert alloc["ETF_B"] == pytest.approx(50.0, abs=2.0)


@pytest.mark.integration
class TestSolverValidation(unittest.TestCase):
    """Validates that the MILP solver produces allocations close to what the old
    SLSQP solver would have achieved. Tolerance is intentionally loose (5%) because
    the MILP is more exact — integer rounding differs — but both should land near
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
        self._check_portfolio("allweather_zino_redacted.json")


class TestConversionCost:
    """Unit tests for Nordnet-style FX fee (0.25% on each non-SEK transaction).

    All prices and FX rates are mocked so these run without network access:
      - yfinance assets → Price(100.0, "USD"), FX rates all 1.0
      - nasdaq_nordic assets → Price(150.0, "SEK")
    """

    def test_buy_non_sek_deducts_sek_with_fee(self, mock_price_fetchers):
        """Buying a USD asset deducts SEK = cost * fx * (1 + fee), never touches USD cash."""
        p = Portfolio()
        p.common_currency = "SEK"
        p.conversion_cost = 0.0025
        p.add_cash(2000.0, "SEK")
        p.add_asset(Asset("ETF", quantity=0))

        p.buy_asset("ETF", 5)

        # cost = 5 * 100 = 500 USD → deduct 500 * 1.0 * 1.0025 = 501.25 SEK
        assert p.cash["SEK"].amount == pytest.approx(2000.0 - 501.25)
        assert "USD" not in p.cash

    def test_sell_non_sek_credits_sek_with_fee_reduction(self, mock_price_fetchers):
        """Selling a USD asset credits SEK = |cost| * fx * (1 - fee), never touches USD cash."""
        p = Portfolio()
        p.common_currency = "SEK"
        p.conversion_cost = 0.0025
        p.add_cash(2000.0, "SEK")
        p.add_asset(Asset("ETF", quantity=10))

        p.buy_asset("ETF", -5)

        # cost = -500 USD → add -(-500) * 1.0 * 0.9975 = 498.75 SEK
        assert p.cash["SEK"].amount == pytest.approx(2000.0 + 498.75)
        assert "USD" not in p.cash

    def test_buy_sek_asset_no_fee_applied(self, mock_price_fetchers):
        """Buying a SEK-denominated asset deducts SEK directly — no conversion fee."""
        p = Portfolio()
        p.common_currency = "SEK"
        p.conversion_cost = 0.0025
        p.add_cash(2000.0, "SEK")
        p.add_asset(
            Asset(
                "VIR10SEK",
                quantity=0,
                nasdaq_nordic_id="TX4856348",
                nasdaq_nordic_asset_class="ETN/ETC",
            )
        )

        p.buy_asset("VIR10SEK", 5)

        # cost = 5 * 150 = 750 SEK; currency == common_currency → no fee
        assert p.cash["SEK"].amount == pytest.approx(2000.0 - 750.0)

    def test_no_conversion_cost_uses_native_currency(self, mock_price_fetchers):
        """Without conversion_cost (default 0), buying a USD asset deducts from USD cash."""
        p = Portfolio()
        p.common_currency = "SEK"
        # conversion_cost defaults to 0.0
        p.add_cash(2000.0, "SEK")
        p.add_cash(1000.0, "USD")
        p.add_asset(Asset("ETF", quantity=0))

        p.buy_asset("ETF", 5)

        # cost = 500 USD → deducted from USD, SEK untouched
        assert p.cash["USD"].amount == pytest.approx(1000.0 - 500.0)
        assert p.cash["SEK"].amount == pytest.approx(2000.0)

    def test_rebalance_only_sek_remains(self, mock_price_fetchers):
        """After rebalancing with conversion_cost > 0, only SEK cash bucket exists."""
        p = Portfolio()
        p.common_currency = "SEK"
        p.conversion_cost = 0.0025
        p.add_cash(2000.0, "SEK")
        p.add_asset(Asset("ETF_A", quantity=0))
        p.add_asset(Asset("ETF_B", quantity=0))

        p.rebalance({"ETF_A": 50.0, "ETF_B": 50.0})

        assert "USD" not in p.cash
        assert p.cash["SEK"].amount >= 0

    def test_rebalance_budget_not_exceeded_with_fee(self, mock_price_fetchers):
        """Total SEK spent (units * price_in_SEK * (1 + fee)) must not exceed initial cash."""
        p = Portfolio()
        p.common_currency = "SEK"
        p.conversion_cost = 0.0025
        initial_sek = 1000.0
        p.add_cash(initial_sek, "SEK")
        p.add_asset(Asset("ETF_A", quantity=0))
        p.add_asset(Asset("ETF_B", quantity=0))

        new_units, prices, _, _ = p.rebalance({"ETF_A": 50.0, "ETF_B": 50.0})

        # Each non-SEK purchase costs price * units * (1 + fee) in SEK
        fee = p.conversion_cost
        sek_spent = sum(
            max(0, new_units[t]) * prices[t][0] * (1 + fee) for t in new_units
        )
        assert sek_spent <= initial_sek + 1e-4  # allow tiny float slack
        assert p.cash["SEK"].amount >= -1e-4


class TestCourtageProfile:
    def test_buy_same_currency_asset_deducts_courtage(self, mock_price_fetchers):
        p = Portfolio()
        p.common_currency = "SEK"
        p.courtage_profile = "nordnet_sweden"
        p.add_cash(2000.0, "SEK")
        p.add_asset(
            Asset(
                "VIR10SEK",
                quantity=0,
                nasdaq_nordic_id="TX4856348",
                nasdaq_nordic_asset_class="ETN/ETC",
            )
        )

        p.buy_asset("VIR10SEK", 5)

        assert p.cash["SEK"].amount == pytest.approx(2000.0 - 750.0 - 9.0)

    def test_buy_fractional_same_currency_asset_is_courtage_free(self):
        p = Portfolio()
        p.common_currency = "SEK"
        p.courtage_profile = "nordnet_sweden"
        p.add_cash(2000.0, "SEK")

        with patch(
            "rebalance.asset.fetch_yfinance_price", return_value=Price(100.0, "SEK")
        ):
            p.add_asset(Asset("FUND_A", quantity=0, fractional=True))

        p.buy_asset("FUND_A", 5)

        assert p.cash["SEK"].amount == pytest.approx(2000.0 - 500.0)

    def test_rebalance_budget_not_exceeded_with_courtage(self, mock_price_fetchers):
        p = Portfolio()
        p.common_currency = "USD"
        p.courtage_profile = "nordnet_sweden"
        initial_usd = 1000.0
        p.add_cash(initial_usd, "USD")
        p.add_asset(Asset("ETF_A", quantity=0))
        p.add_asset(Asset("ETF_B", quantity=0))

        new_units, prices, _, _ = p.rebalance({"ETF_A": 50.0, "ETF_B": 50.0})

        total_spend = 0.0
        for ticker, units in new_units.items():
            amount = max(0, units) * prices[ticker][0]
            if amount > 0:
                total_spend += amount + 9.0

        assert total_spend <= initial_usd + 1e-4
        assert p.cash["USD"].amount >= -1e-4

    def test_verbose_table_shows_split_courtage_and_fx_fee_columns(
        self, mock_price_fetchers
    ):
        p = Portfolio()
        p.common_currency = "SEK"
        p.conversion_cost = 0.25 / 100.0
        p.courtage_profile = "nordnet_sweden"
        p.selling_allowed = False

        with patch(
            "rebalance.asset.fetch_yfinance_price", return_value=Price(100.0, "USD")
        ):
            p.add_asset(Asset("ETF_A", quantity=0))

        p.add_cash(1000.0, "SEK")
        console = Console(record=True, width=160)

        with patch("rebalance.portfolio._console", console):
            p.rebalance({"ETF_A": 100.0}, verbose=True)

        output = console.export_text()

        assert "Courtage" in output
        assert "Courtage Fee SEK" in output
        assert "FX Fee SEK" in output
        assert "Mini" in output
        assert "9" in output


class TestBandRebalance:
    """Unit tests for Portfolio.band_rebalance() using mocked prices.

    All assets priced at 100.0 USD, FX rates all 1.0.
    """

    def _make_portfolio(self, holdings, cash_usd=0.0):
        """Build a portfolio from {ticker: quantity} dict with mocked prices."""
        from rebalance.asset import Asset

        p = Portfolio()
        p.common_currency = "USD"
        p.selling_allowed = False  # band_rebalance handles selling internally

        with patch(
            "rebalance.asset.fetch_yfinance_price", return_value=Price(100.0, "USD")
        ):
            for ticker, qty in holdings.items():
                p.add_asset(Asset(ticker, quantity=qty))

        if cash_usd:
            p.add_cash(cash_usd, "USD")
        return p

    def _make_statuses(self, p, target_allocation, volatilities):
        """Build BandStatus list using real check_bands (no network needed)."""
        from rebalance.band_checker import check_bands

        return check_bands(p, target_allocation, volatilities)

    def test_triggered_above_asset_is_sold(self, mock_price_fetchers):
        """An asset above its upper band gets a negative Δ Units (sold)."""
        # AAAA: 70 units @ $100 = $7000 / $10000 = 70%, target 50%, vol 10%
        # → upper_band = 55% → triggered above → must sell
        # BBBB: 30 units @ $100 = $3000 / $10000 = 30%, target 50%
        p = self._make_portfolio({"AAAA": 70, "BBBB": 30})
        target = {"AAAA": 50.0, "BBBB": 50.0}
        vols = {"AAAA": 10.0, "BBBB": 10.0}
        statuses = self._make_statuses(p, target, vols)

        new_units, _, _ = p.band_rebalance(target, statuses)

        assert new_units["AAAA"] < 0, "Triggered-above asset should be sold"

    def test_non_triggered_asset_is_frozen_by_default(self, mock_price_fetchers):
        """A non-triggered asset should not be bought or sold by default."""
        # AAAA: 60% (triggered above: target 40%, vol 10% → upper_band 44%)
        # BBBB: 30% (triggered below: target 40%, vol 10% → lower_band 36%)
        # CCCC: 10% (within band: target 20%, vol 100% → lower_band 0% → not triggered)
        p = self._make_portfolio({"AAAA": 60, "BBBB": 30, "CCCC": 10})
        target = {"AAAA": 40.0, "BBBB": 40.0, "CCCC": 20.0}
        vols = {"AAAA": 10.0, "BBBB": 10.0, "CCCC": 100.0}
        statuses = self._make_statuses(p, target, vols)

        not_triggered = [s for s in statuses if not s.triggered]
        assert any(s.ticker == "CCCC" for s in not_triggered), (
            "CCCC should be within bands (lower_band=0%, current=10%)"
        )

        new_units, _, _ = p.band_rebalance(target, statuses)

        for s in not_triggered:
            assert new_units[s.ticker] == 0, (
                f"Non-triggered asset {s.ticker} must be frozen"
            )

    def test_effective_target_is_tolerance_midpoint(self, mock_price_fetchers):
        """Post-rebalance allocation for triggered assets lands near the tolerance midpoint."""
        # AAAA: 70% triggered above, target 50%, vol 10% → upper_tolerance = 52.5%
        # BBBB: 30% triggered below, target 50%, vol 10% → lower_tolerance = 47.5%
        p = self._make_portfolio({"AAAA": 70, "BBBB": 30})
        target = {"AAAA": 50.0, "BBBB": 50.0}
        vols = {"AAAA": 10.0, "BBBB": 10.0}
        statuses = self._make_statuses(p, target, vols)

        p.band_rebalance(target, statuses)

        alloc = p.asset_allocation()
        # Integer rounding means we can't hit exactly 52.5% / 47.5%, but we should
        # land within the tolerance band (between original target and band edge).
        assert alloc["AAAA"] <= 55.0 + 0.5, "AAAA should be sold below upper_band"
        assert alloc["AAAA"] >= 50.0 - 0.5, (
            "AAAA should not be sold past original target"
        )
        assert alloc["BBBB"] >= 45.0 - 0.5, "BBBB should be bought above lower_band"

    def test_relative_l2_normalizes_band_errors_by_span(self, mock_price_fetchers):
        """Band-aware relative-l2 should optimize in normalized band space."""
        p = self._make_portfolio({"A": 0, "B": 0}, cash_usd=350.0)

        trades = rebalance_optimizer(
            p,
            {"A": 15.0, "B": 85.0},
            band_limits={"A": (0.0, 31.0), "B": (45.0, 86.0)},
            objective="relative-l2",
        )

        assert trades == {"A": 0, "B": 3}

    def test_budget_not_exceeded(self, mock_price_fetchers):
        """Total spend (buys - sells) must not exceed cash + sell proceeds."""
        p = self._make_portfolio({"AAAA": 70, "BBBB": 30}, cash_usd=500.0)
        target = {"AAAA": 50.0, "BBBB": 50.0}
        vols = {"AAAA": 10.0, "BBBB": 10.0}
        statuses = self._make_statuses(p, target, vols)

        initial_value = p.value("USD")
        p.band_rebalance(target, statuses)
        final_value = p.value("USD")

        assert final_value == pytest.approx(initial_value, abs=1e-2)

    def test_large_cash_injection_uses_tradable_band_capacity(
        self, mock_price_fetchers
    ):
        """Large deposits should use tradable band capacity without frozen buffers."""
        p = self._make_portfolio({"AAAA": 50, "BBBB": 10, "CCCC": 0}, cash_usd=100000.0)
        target = {"AAAA": 70.0, "BBBB": 10.0, "CCCC": 20.0}
        vols = {"AAAA": 2.0, "BBBB": 50.0, "CCCC": 10.0}
        statuses = self._make_statuses(p, target, vols)

        p.band_rebalance(target, statuses)

        assert 0.0 <= p.cash["USD"].amount <= 100.0
        allocation = cash_inclusive_allocation(p)
        assert allocation["AAAA"] == pytest.approx(69.9, abs=0.2)
        for status in statuses:
            assert allocation[status.ticker] >= status.lower_band - 0.1
            assert allocation[status.ticker] <= status.upper_band + 0.1
        assert allocation["CCCC"] > target["CCCC"]

    def test_zero_holding_triggered_below_starts_from_json_target(
        self, mock_price_fetchers
    ):
        """New positions use the JSON target before residual allocation."""
        p = self._make_portfolio({"SELL": 70, "BUY": 20, "NEW": 0})
        target = {"SELL": 45.0, "BUY": 40.0, "NEW": 15.0}
        vols = {"SELL": 10.0, "BUY": 10.0, "NEW": 10.0}
        statuses = self._make_statuses(p, target, vols)

        plan = build_band_rebalance_plan(p, target, statuses)

        new_status = plan.status_by_ticker["NEW"]
        assert new_status.direction == "below"
        assert new_status.lower_tolerance == pytest.approx(14.25)
        assert plan.effective_targets["NEW"] == pytest.approx(14.963414634146341)
        assert plan.effective_targets["NEW"] > new_status.lower_tolerance
        assert plan.effective_targets["NEW"] < target["NEW"]
        assert plan.effective_targets["SELL"] == pytest.approx(47.08536585365854)
        assert plan.effective_targets["BUY"] == pytest.approx(37.951219512195124)
        normalized_reductions = {
            ticker: (initial_target - plan.effective_targets[ticker])
            / (initial_target - plan.status_by_ticker[ticker].lower_band)
            for ticker, initial_target in {
                "SELL": plan.status_by_ticker["SELL"].upper_tolerance,
                "BUY": plan.status_by_ticker["BUY"].lower_tolerance,
                "NEW": target["NEW"],
            }.items()
        }
        assert (
            len(set(round(value, 10) for value in normalized_reductions.values())) == 1
        )

    def test_zero_holding_target_keeps_lower_midpoint_before_extra_sells(
        self, mock_price_fetchers
    ):
        """New positions should not absorb all excess below their lower midpoint."""
        p = self._make_portfolio({"SELL": 70, "BUY": 10, "NEW": 0, "LOCK": 20})
        target = {"SELL": 50.0, "BUY": 15.0, "NEW": 15.0, "LOCK": 20.0}
        vols = {"SELL": 10.0, "BUY": 10.0, "NEW": 10.0, "LOCK": 100.0}
        statuses = self._make_statuses(p, target, vols)

        plan = build_band_rebalance_plan(p, target, statuses, lock_non_triggered=True)

        new_status = plan.status_by_ticker["NEW"]
        sell_status = plan.status_by_ticker["SELL"]
        assert plan.locked_tickers == {"LOCK"}
        assert new_status.direction == "below"
        assert sell_status.direction == "above"
        assert plan.effective_targets["NEW"] > new_status.lower_tolerance
        assert plan.effective_targets["NEW"] < target["NEW"]
        assert plan.effective_targets["SELL"] < sell_status.upper_tolerance
        assert plan.effective_targets["SELL"] == pytest.approx(51.15384615384615)
        assert plan.effective_targets["BUY"] == pytest.approx(14.115384615384615)
        assert plan.effective_targets["NEW"] == pytest.approx(14.73076923076923)
        assert sum(plan.effective_targets.values()) == pytest.approx(100.0)
        normalized_reductions = {
            ticker: (initial_target - plan.effective_targets[ticker])
            / (initial_target - plan.status_by_ticker[ticker].lower_band)
            for ticker, initial_target in {
                "SELL": sell_status.upper_tolerance,
                "BUY": plan.status_by_ticker["BUY"].lower_tolerance,
                "NEW": target["NEW"],
            }.items()
        }
        assert (
            len(set(round(value, 10) for value in normalized_reductions.values())) == 1
        )

    def test_wind_down_asset_is_sold_to_zero(self, mock_price_fetchers):
        """A target-zero asset with holdings is fully liquidated."""
        p = self._make_portfolio({"AAAA": 10, "BBBB": 90})
        target = {"AAAA": 0.0, "BBBB": 100.0}
        vols = {"AAAA": 10.0, "BBBB": 10.0}
        statuses = self._make_statuses(p, target, vols)
        plan = build_band_rebalance_plan(p, target, statuses)

        assert plan.forced_trades == {"AAAA": -10.0}

        new_units, _, _ = p.band_rebalance(target, statuses)

        assert new_units["AAAA"] == -10
        assert p.assets["AAAA"].quantity == 0

    def test_forced_sale_funds_new_asset_without_trading_frozen_asset(
        self, mock_price_fetchers
    ):
        """Wind-down proceeds should become buy capacity while frozen assets stay put."""
        p = self._make_portfolio({"OLD": 10, "NEW": 0, "LOCK": 40})
        target = {"OLD": 0.0, "NEW": 20.0, "LOCK": 80.0}
        vols = {"OLD": 10.0, "NEW": 100.0, "LOCK": 25.0}
        statuses = self._make_statuses(p, target, vols)

        new_units, _, _ = p.band_rebalance(target, statuses)

        assert new_units["OLD"] == -10
        assert new_units["NEW"] == 10
        assert new_units["LOCK"] == 0
        assert p.cash["USD"].amount == pytest.approx(0.0, abs=1e-4)

    def test_withdrawal_sells_triggered_asset_and_keeps_non_triggered_frozen(
        self, mock_price_fetchers
    ):
        """Withdrawals can be funded from tradable assets without thawing buffers."""
        p = self._make_portfolio({"SELL": 60, "LOCK": 30}, cash_usd=-1000.0)
        target = {"SELL": 50.0, "LOCK": 40.0}
        vols = {"SELL": 20.0, "LOCK": 20.0}
        statuses = self._make_statuses(p, target, vols)

        new_units, _, _ = p.band_rebalance(target, statuses)

        assert new_units["SELL"] == -12
        assert new_units["LOCK"] == 0
        assert p.cash["USD"].amount == pytest.approx(200.0, abs=1e-4)
        allocation = cash_inclusive_allocation(p)
        assert allocation["SELL"] == pytest.approx(60.0, abs=0.1)
        assert allocation["LOCK"] == pytest.approx(37.5, abs=0.1)

    def test_valid_withdrawal_rebalance_preserves_value(self, mock_price_fetchers):
        """Negative cash is allowed when the post-withdrawal value is positive."""
        p = self._make_portfolio({"AAAA": 70, "BBBB": 30}, cash_usd=-1000.0)
        target = {"AAAA": 50.0, "BBBB": 50.0}
        vols = {"AAAA": 10.0, "BBBB": 10.0}
        statuses = self._make_statuses(p, target, vols)
        initial_value = p.value("USD")

        p.band_rebalance(target, statuses)

        assert p.value("USD") == pytest.approx(initial_value, abs=1e-2)

    @pytest.mark.parametrize("objective", SUPPORTED_OBJECTIVES)
    def test_all_objectives_are_usable(self, mock_price_fetchers, objective):
        """Every selectable objective should solve the same band rebalance shape."""
        p = self._make_portfolio({"AAAA": 70, "BBBB": 30})
        target = {"AAAA": 50.0, "BBBB": 50.0}
        vols = {"AAAA": 10.0, "BBBB": 10.0}
        statuses = self._make_statuses(p, target, vols)
        initial_value = p.value("USD")

        new_units, _, _ = p.band_rebalance(target, statuses, objective=objective)

        assert new_units["AAAA"] < 0
        assert new_units["BBBB"] > 0
        assert p.value("USD") == pytest.approx(initial_value, abs=1e-2)

    @pytest.mark.parametrize("objective", SUPPORTED_OBJECTIVES)
    def test_lock_non_triggered_applies_to_all_objectives(
        self, mock_price_fetchers, objective
    ):
        """Hard non-triggered locks should be enforced independently of objective."""
        p = self._make_portfolio({"AAAA": 60, "BBBB": 30, "CCCC": 10})
        target = {"AAAA": 40.0, "BBBB": 40.0, "CCCC": 20.0}
        vols = {"AAAA": 10.0, "BBBB": 10.0, "CCCC": 100.0}
        statuses = self._make_statuses(p, target, vols)

        new_units, _, _ = p.band_rebalance(
            target,
            statuses,
            lock_non_triggered=True,
            objective=objective,
        )

        assert new_units["CCCC"] == 0

    def test_locked_non_triggered_target_stays_cash_inclusive(
        self, mock_price_fetchers
    ):
        """Locked assets must not be used as normalization buffers."""
        p = self._make_portfolio({"SELL": 60, "BUY": 10, "LOCK": 30})
        target = {"SELL": 40.0, "BUY": 40.0, "LOCK": 20.0}
        vols = {"SELL": 20.0, "BUY": 20.0, "LOCK": 100.0}
        statuses = self._make_statuses(p, target, vols)

        plan = build_band_rebalance_plan(p, target, statuses, lock_non_triggered=True)

        assert plan.locked_tickers == {"LOCK"}
        assert plan.effective_targets["LOCK"] == pytest.approx(
            plan.cash_inclusive_allocation["LOCK"]
        )
        assert sum(plan.effective_targets.values()) == pytest.approx(100.0)
        assert plan.effective_targets["SELL"] == pytest.approx(36.5)
        assert plan.effective_targets["BUY"] == pytest.approx(33.5)

    @pytest.mark.parametrize("objective", SUPPORTED_OBJECTIVES)
    def test_locked_non_triggered_buffer_still_allows_full_deployment(
        self, mock_price_fetchers, objective
    ):
        """Tradable assets should rebalance around a frozen non-triggered asset."""
        p = self._make_portfolio({"SELL": 60, "BUY": 10, "LOCK": 30})
        target = {"SELL": 40.0, "BUY": 40.0, "LOCK": 20.0}
        vols = {"SELL": 20.0, "BUY": 20.0, "LOCK": 100.0}
        statuses = self._make_statuses(p, target, vols)

        new_units, _, _ = p.band_rebalance(
            target,
            statuses,
            lock_non_triggered=True,
            objective=objective,
        )

        assert new_units["LOCK"] == 0
        assert new_units["SELL"] < 0
        assert new_units["BUY"] > 0
        assert p.cash["USD"].amount >= -1e-4
        assert p.cash["USD"].amount <= 100.0
        allocation = cash_inclusive_allocation(p)
        assert allocation["LOCK"] == pytest.approx(30.0, abs=0.1)
        assert 36.0 <= allocation["SELL"] <= 37.1
        assert 32.0 <= allocation["BUY"] <= 33.1

    def test_cash_inclusive_allocation_keeps_cash_denominator(
        self, mock_price_fetchers
    ):
        """Band output percentages should use the same cash-inclusive basis as checks."""
        p = self._make_portfolio({"AAAA": 60, "LOCK": 20}, cash_usd=2000.0)

        allocation = cash_inclusive_allocation(p)

        assert allocation["LOCK"] == pytest.approx(20.0)
        assert p.asset_allocation()["LOCK"] == pytest.approx(25.0)

    def test_band_verbose_uses_cash_inclusive_new_allocation(
        self, mock_price_fetchers, monkeypatch
    ):
        """The rendered New % column should not switch to an asset-only denominator."""
        p = self._make_portfolio({"SELL": 60, "LOCK": 20}, cash_usd=2000.0)
        target = {"SELL": 40.0, "LOCK": 60.0}
        vols = {"SELL": 20.0, "LOCK": 100.0}
        statuses = self._make_statuses(p, target, vols)
        captured_allocation = {}

        def capture_render(
            _balanced_portfolio,
            _new_units,
            _prices,
            _cost,
            _exchange_history,
            new_allocation,
            _target_allocation,
            _plan,
        ):
            captured_allocation.update(new_allocation)

        monkeypatch.setattr(
            "rebalance.portfolio.render_band_rebalance_table", capture_render
        )

        p.band_rebalance(
            target,
            statuses,
            verbose=True,
        )

        assert captured_allocation["LOCK"] == pytest.approx(20.0, abs=0.1)
        assert p.asset_allocation()["LOCK"] > captured_allocation["LOCK"]
