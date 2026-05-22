"""Unit tests for band_checker.check_bands()."""

import pytest

from rebalance import Asset, Portfolio
from rebalance.band_checker import DEFAULT_BAND_SIGMA, BandSettings, check_bands


def _make_portfolio(assets: list[tuple[str, float, float, str | None]]) -> Portfolio:
    """Build a Portfolio from (ticker, quantity, price_usd, name) tuples.

    Uses mock_price_fetchers fixture to avoid network calls — caller must
    ensure the fixture is active.  All assets priced in USD at the given price.
    """
    p = Portfolio()
    p.common_currency = "USD"
    for ticker, qty, _price, name in assets:
        a = Asset.__new__(Asset)
        from rebalance.money import Price

        a._ticker = ticker
        a._quantity = float(qty)
        a._fractional = True
        a._name = name
        a._price = Price(_price, "USD")
        p.add_asset(a)
    return p


class TestCheckBands:
    def test_asset_within_bands_not_triggered(self):
        """Asset exactly at target weight is not triggered."""
        # 100 units @ $10 = $1000 total; target 100% → current 100%
        p = _make_portfolio([("AAAA", 100, 10.0, "Asset A")])
        target = {"AAAA": 100.0}
        vols = {"AAAA": 10.0}

        statuses = check_bands(p, target, vols)

        assert len(statuses) == 1
        s = statuses[0]
        assert s.ticker == "AAAA"
        assert not s.triggered
        assert s.direction is None

    def test_asset_above_upper_band_triggers(self):
        """Asset drifted above upper band triggers with direction='above'."""
        # AAAA: 200 units @ $10 = $2000 out of $3000 = 66.7%, target 50%, vol 10%
        # → default upper_band = 57.5% → triggered above
        # BBBB: $1000/$3000 = 33.3%, target 50%, vol 40%
        # → default lower_band = 20% → 33.3% > 20%, NOT triggered
        p = _make_portfolio(
            [
                ("AAAA", 200, 10.0, "Asset A"),  # $2000
                ("BBBB", 100, 10.0, "Asset B"),  # $1000  → total $3000
            ]
        )
        target = {"AAAA": 50.0, "BBBB": 50.0}
        vols = {"AAAA": 10.0, "BBBB": 40.0}

        statuses = check_bands(p, target, vols)
        by_ticker = {s.ticker: s for s in statuses}

        assert by_ticker["AAAA"].triggered
        assert by_ticker["AAAA"].direction == "above"
        assert not by_ticker["BBBB"].triggered

    def test_asset_below_lower_band_triggers(self):
        """Asset drifted below lower band triggers with direction='below'."""
        # AAAA: $500 / $3000 = 16.7%, target 50%, vol 10% → lower_band = 42.5%
        p = _make_portfolio(
            [
                ("AAAA", 50, 10.0, "Asset A"),  # $500
                ("BBBB", 250, 10.0, "Asset B"),  # $2500 → total $3000
            ]
        )
        target = {"AAAA": 50.0, "BBBB": 50.0}
        vols = {"AAAA": 10.0, "BBBB": 10.0}

        statuses = check_bands(p, target, vols)
        by_ticker = {s.ticker: s for s in statuses}

        assert by_ticker["AAAA"].triggered
        assert by_ticker["AAAA"].direction == "below"

    def test_exactly_at_upper_band_boundary_triggers(self):
        """Asset exactly at the upper band boundary is triggered."""
        # target 10%, vol 10%, default sigma 1.5 → upper_band = 11.5%
        # Need current = 11.5% exactly: asset value = 11.5, rest = 88.5 → total 100
        p = _make_portfolio(
            [
                ("AAAA", 11.5, 1.0, None),  # $11.5
                ("BBBB", 88.5, 1.0, None),  # $88.5 → total $100
            ]
        )
        target = {"AAAA": 10.0, "BBBB": 90.0}
        vols = {"AAAA": 10.0, "BBBB": 10.0}

        statuses = check_bands(p, target, vols)
        by_ticker = {s.ticker: s for s in statuses}

        assert by_ticker["AAAA"].triggered
        assert by_ticker["AAAA"].direction == "above"

    def test_tolerance_bands_computed_correctly(self):
        """Default tolerance bands are the midpoint between target and band edge."""
        # target 5.5%, vol 10%, sigma 1.5
        p = _make_portfolio(
            [
                ("IRIS", 55, 1.0, "Captor Iris Bond"),  # $55
                ("REST", 945, 1.0, None),  # $945 → total $1000
            ]
        )
        target = {"IRIS": 5.5, "REST": 94.5}
        vols = {"IRIS": 10.0, "REST": 10.0}

        statuses = check_bands(p, target, vols)
        by_ticker = {s.ticker: s for s in statuses}
        s = by_ticker["IRIS"]

        assert s.band_sigma == pytest.approx(DEFAULT_BAND_SIGMA)
        assert s.lower_band_sigma == pytest.approx(DEFAULT_BAND_SIGMA)
        assert s.upper_band_sigma == pytest.approx(DEFAULT_BAND_SIGMA)
        assert s.upper_band == pytest.approx(6.325)
        assert s.lower_band == pytest.approx(4.675)
        assert s.upper_tolerance == pytest.approx(5.9125)
        assert s.lower_tolerance == pytest.approx(5.0875)

    def test_symmetric_band_sigma_override(self):
        """A per-asset band_sigma override widens both sides symmetrically."""
        p = _make_portfolio(
            [
                ("IRIS", 55, 1.0, "Captor Iris Bond"),
                ("REST", 945, 1.0, None),
            ]
        )
        target = {"IRIS": 5.5, "REST": 94.5}
        settings = {
            "IRIS": BandSettings(volatility_pct=10.0, band_sigma=2.5),
            "REST": BandSettings(volatility_pct=10.0),
        }

        statuses = check_bands(p, target, settings)
        s = {status.ticker: status for status in statuses}["IRIS"]

        assert s.band_sigma == pytest.approx(2.5)
        assert s.lower_band_sigma == pytest.approx(2.5)
        assert s.upper_band_sigma == pytest.approx(2.5)
        assert s.upper_band == pytest.approx(6.875)
        assert s.lower_band == pytest.approx(4.125)
        assert s.upper_tolerance == pytest.approx(6.1875)
        assert s.lower_tolerance == pytest.approx(4.8125)

    def test_asymmetric_band_sigma_override(self):
        """Per-side sigma overrides let upper and lower bands differ."""
        p = _make_portfolio(
            [
                ("IRIS", 55, 1.0, "Captor Iris Bond"),
                ("REST", 945, 1.0, None),
            ]
        )
        target = {"IRIS": 5.5, "REST": 94.5}
        settings = {
            "IRIS": BandSettings(
                volatility_pct=10.0,
                band_sigma=1.5,
                lower_band_sigma=1.5,
                upper_band_sigma=2.5,
            ),
            "REST": BandSettings(volatility_pct=10.0),
        }

        statuses = check_bands(p, target, settings)
        s = {status.ticker: status for status in statuses}["IRIS"]

        assert s.band_sigma == pytest.approx(1.5)
        assert s.lower_band_sigma == pytest.approx(1.5)
        assert s.upper_band_sigma == pytest.approx(2.5)
        assert s.upper_band == pytest.approx(6.875)
        assert s.lower_band == pytest.approx(4.675)
        assert s.upper_tolerance == pytest.approx(6.1875)
        assert s.lower_tolerance == pytest.approx(5.0875)

    def test_asset_without_volatility_skipped(self):
        """Assets with no volatility are excluded from results."""
        p = _make_portfolio(
            [
                ("AAAA", 100, 10.0, None),
                ("BBBB", 100, 10.0, None),
            ]
        )
        target = {"AAAA": 50.0, "BBBB": 50.0}
        vols: dict[str, float | None] = {"AAAA": 10.0, "BBBB": None}

        statuses = check_bands(p, target, vols)

        tickers = [s.ticker for s in statuses]
        assert "AAAA" in tickers
        assert "BBBB" not in tickers

    def test_all_without_volatility_returns_empty(self):
        """If no assets have volatility, result is an empty list."""
        p = _make_portfolio([("AAAA", 100, 10.0, None)])
        target = {"AAAA": 100.0}
        vols: dict[str, float | None] = {"AAAA": None}

        statuses = check_bands(p, target, vols)

        assert statuses == []

    def test_multiple_assets_mixed_results(self):
        """Only triggered assets have triggered=True; others are False."""
        # AAAA: 70% current, target 50%, vol 10% → upper band 55% → triggered
        # BBBB: 30% current, target 50%, vol 10% → lower band 45% → triggered
        # (same portfolio as above, both triggered)
        p = _make_portfolio(
            [
                ("AAAA", 70, 1.0, None),
                ("BBBB", 30, 1.0, None),
            ]
        )
        target = {"AAAA": 50.0, "BBBB": 50.0}
        vols = {"AAAA": 10.0, "BBBB": 10.0}

        statuses = check_bands(p, target, vols)
        by_ticker = {s.ticker: s for s in statuses}

        assert by_ticker["AAAA"].triggered
        assert by_ticker["AAAA"].direction == "above"
        assert by_ticker["BBBB"].triggered
        assert by_ticker["BBBB"].direction == "below"

    def test_asset_name_propagated(self):
        """BandStatus.name is taken from the Asset.name property."""
        p = _make_portfolio([("AAAA", 100, 10.0, "My Fund")])
        target = {"AAAA": 100.0}
        vols = {"AAAA": 17.5}

        statuses = check_bands(p, target, vols)

        assert statuses[0].name == "My Fund"

    def test_asset_name_none_when_not_set(self):
        """BandStatus.name is None when Asset has no name."""
        p = _make_portfolio([("AAAA", 100, 10.0, None)])
        target = {"AAAA": 100.0}
        vols = {"AAAA": 17.5}

        statuses = check_bands(p, target, vols)

        assert statuses[0].name is None

    def test_zero_target_with_no_holding_is_not_triggered(self):
        """A fully wound-down target-zero asset should not trigger forever."""
        p = _make_portfolio(
            [
                ("ZERO", 0, 10.0, None),
                ("CORE", 100, 10.0, None),
            ]
        )
        target = {"ZERO": 0.0, "CORE": 100.0}
        vols = {"ZERO": 10.0, "CORE": 10.0}

        statuses = check_bands(p, target, vols)
        by_ticker = {s.ticker: s for s in statuses}

        assert not by_ticker["ZERO"].triggered
        assert by_ticker["ZERO"].direction is None

    def test_non_positive_total_value_raises(self):
        """Over-withdrawals should fail with a clear denominator error."""
        p = _make_portfolio([("AAAA", 100, 10.0, None)])
        p.add_cash(-1000.0, "USD")

        with pytest.raises(ValueError, match="positive"):
            check_bands(p, {"AAAA": 100.0}, {"AAAA": 10.0})
