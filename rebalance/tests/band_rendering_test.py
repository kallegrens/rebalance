from types import SimpleNamespace
from unittest.mock import patch

from rebalance.band_rendering import (
    _amount_in_common_currency,
    _band_cell,
    _column_summaries,
    _common_currency_fee,
    _format_trade,
    _original_intended_target,
    _whole_common_amount,
    _whole_common_fee,
    band_bar,
    build_band_rebalance_report,
)


def _status(**overrides):
    defaults = {
        "direction": None,
        "current_pct": 50.0,
        "lower_tolerance": 45.0,
        "upper_tolerance": 55.0,
        "lower_band": 40.0,
        "upper_band": 60.0,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_new_asset_uses_blue_diamond_marker():
    status = SimpleNamespace(direction="below", current_pct=0.0)

    marker, row_style = _band_cell(status, 12.5)

    assert marker == "[blue]◆[/blue]"
    assert row_style == ""


def test_existing_underweight_asset_keeps_down_marker():
    status = SimpleNamespace(direction="below", current_pct=2.5)

    marker, row_style = _band_cell(status, 12.5)

    assert marker == "[green]▼[/green]"
    assert row_style == ""


def test_original_target_for_new_asset_is_json_target():
    status = _status(direction="below", current_pct=0.0)

    target = _original_intended_target(status, 12.5, locked=False)

    assert target == 12.5


def test_original_target_for_locked_non_triggered_asset_is_unmoved():
    status = _status(direction=None, current_pct=47.25)

    target = _original_intended_target(status, 50.0, locked=True)

    assert target == 47.25


def test_original_target_omits_unlocked_non_triggered_asset():
    status = _status(direction=None, current_pct=47.25)

    target = _original_intended_target(status, 50.0, locked=False)

    assert target is None


def test_original_target_for_triggered_asset_uses_tolerance_midpoint():
    above = _status(direction="above", upper_tolerance=56.5)
    below = _status(direction="below", current_pct=42.0, lower_tolerance=43.5)

    assert _original_intended_target(above, 50.0, locked=False) == 56.5
    assert _original_intended_target(below, 50.0, locked=False) == 43.5


def test_band_bar_shows_original_target_marker():
    status = _status(direction="above", current_pct=65.0, upper_tolerance=55.0)

    bar = band_bar(52.0, 65.0, 50.0, 55.0, 50.0, status)

    assert "◇" in bar.plain


def test_band_bar_snaps_above_original_target_to_upper_midpoint():
    status = _status(
        direction="above",
        current_pct=1.98,
        lower_band=1.105,
        upper_band=1.495,
        upper_tolerance=1.3975,
    )

    bar = band_bar(2.16, 1.98, 1.36, 1.3975, 1.36, status)

    assert bar.plain.index("◇") == 1 + round(0.75 * (23 - 1))


def test_band_bar_snaps_new_asset_original_target_to_target_marker():
    status = _status(
        direction="below",
        current_pct=0.0,
        lower_band=4.81,
        upper_band=6.39,
        lower_tolerance=5.205,
    )

    bar = band_bar(0.0, 0.0, 5.40, 5.60, 5.42, status)

    assert bar.plain.index("◇") == 1 + round(0.50 * (23 - 1))


def test_format_trade_uses_two_decimals_for_fractional_units():
    quantity_markup, _, _ = _format_trade(1.234, 100.0)

    assert quantity_markup == "[green]1.23[/green]"


def test_column_summaries_total_single_currency_amount_and_percentages():
    plan = SimpleNamespace(
        assets_only_allocation={"AAA": 60.0, "BBB": 40.0},
        diluted_allocation={"AAA": 48.0, "BBB": 32.0},
        effective_targets={"AAA": 58.0, "BBB": 42.0},
    )

    summaries = _column_summaries(
        common_amounts={"AAA": 150.0, "BBB": -50.0},
        common_fees={"AAA": 3.0, "BBB": 1.0},
        new_allocation={"AAA": 50.0, "BBB": 35.0},
        plan=plan,
        original_targets={"AAA": 55.0, "BBB": 40.0},
        common_currency="USD",
    )

    assert summaries == {
        "common_amount_total": 100.0,
        "common_amount_currency": "USD",
        "common_fee_total": 4.0,
        "common_fee_currency": "USD",
        "old_pct_total": 100.0,
        "diluted_pct_total": 80.0,
        "new_pct_total": 85.0,
        "orig_target_total": 95.0,
        "eff_target_total": 100.0,
    }


def test_column_summaries_convert_mixed_currency_amount_total_to_base_currency():
    plan = SimpleNamespace(
        assets_only_allocation={"AAA": 100.0},
        diluted_allocation={"AAA": 75.0},
        effective_targets={"AAA": 100.0},
    )

    def fake_exchange_rate(self, currency):
        rates = {
            ("USD", "EUR"): 0.8,
            ("CAD", "EUR"): 0.6,
            ("USD", "USD"): 1.0,
            ("CAD", "CAD"): 1.0,
            ("EUR", "EUR"): 1.0,
        }
        return rates[(self.currency, currency)]

    with patch(
        "rebalance.band_rendering.Cash.exchange_rate",
        autospec=True,
        side_effect=fake_exchange_rate,
    ):
        common_amounts = {
            "AAA": _amount_in_common_currency(150.0, "USD", "EUR", 0.0),
            "BBB": _amount_in_common_currency(50.0, "CAD", "EUR", 0.0),
        }
        summaries = _column_summaries(
            common_amounts=common_amounts,
            common_fees={"AAA": 0.0},
            new_allocation={"AAA": 75.0},
            plan=plan,
            original_targets={"AAA": None},
            common_currency="EUR",
        )

    assert summaries["common_amount_total"] == 150.0
    assert summaries["common_amount_currency"] == "EUR"
    assert summaries["common_fee_total"] == 0.0
    assert summaries["orig_target_total"] is None


def test_amount_in_common_currency_applies_conversion_cost_to_non_base_assets():
    def fake_exchange_rate(self, currency):
        rates = {
            ("USD", "EUR"): 0.8,
            ("EUR", "EUR"): 1.0,
        }
        return rates[(self.currency, currency)]

    with patch(
        "rebalance.band_rendering.Cash.exchange_rate",
        autospec=True,
        side_effect=fake_exchange_rate,
    ):
        buy_amount = _amount_in_common_currency(100.0, "USD", "EUR", 0.02)
        sell_amount = _amount_in_common_currency(-100.0, "USD", "EUR", 0.02)

    assert buy_amount == 80.0
    assert sell_amount == -80.0


def test_common_currency_fee_is_positive_for_buys_and_sells():
    def fake_exchange_rate(self, currency):
        rates = {
            ("USD", "EUR"): 0.8,
            ("EUR", "EUR"): 1.0,
        }
        return rates[(self.currency, currency)]

    with patch(
        "rebalance.band_rendering.Cash.exchange_rate",
        autospec=True,
        side_effect=fake_exchange_rate,
    ):
        buy_fee = _common_currency_fee(100.0, "USD", "EUR", 0.02)
        sell_fee = _common_currency_fee(-100.0, "USD", "EUR", 0.02)

    assert buy_fee == 1.6
    assert sell_fee == 1.6


def test_whole_common_amount_rounds_to_integer():
    def fake_exchange_rate(self, currency):
        rates = {
            ("USD", "EUR"): 0.8,
            ("EUR", "EUR"): 1.0,
        }
        return rates[(self.currency, currency)]

    with patch(
        "rebalance.band_rendering.Cash.exchange_rate",
        autospec=True,
        side_effect=fake_exchange_rate,
    ):
        assert _whole_common_amount(100.0, "USD", "EUR", 0.02) == 80
        assert _whole_common_amount(-100.0, "USD", "EUR", 0.02) == -80


def test_whole_common_fee_rounds_to_integer():
    def fake_exchange_rate(self, currency):
        rates = {
            ("USD", "EUR"): 0.8,
            ("EUR", "EUR"): 1.0,
        }
        return rates[(self.currency, currency)]

    with patch(
        "rebalance.band_rendering.Cash.exchange_rate",
        autospec=True,
        side_effect=fake_exchange_rate,
    ):
        assert _whole_common_fee(100.0, "USD", "EUR", 0.02) == 2
        assert _whole_common_fee(-100.0, "USD", "EUR", 0.02) == 2


def test_build_band_rebalance_report_contains_rows_and_summary():
    portfolio = SimpleNamespace(
        common_currency="USD",
        conversion_cost=0.0,
        assets={"AAA": SimpleNamespace(name="Asset A")},
        cash={"USD": SimpleNamespace(amount=12.5, currency="USD")},
    )
    plan = SimpleNamespace(
        locked_tickers=set(),
        status_by_ticker={"AAA": _status(direction="below", current_pct=0.0)},
        assets_only_allocation={"AAA": 0.0},
        diluted_allocation={"AAA": 0.0},
        effective_targets={"AAA": 10.0},
    )

    with patch("rebalance.band_rendering.Cash.exchange_rate", return_value=1.0):
        report = build_band_rebalance_report(
            portfolio,
            new_units={"AAA": 10},
            prices={"AAA": [12.0, "USD"]},
            cost={"AAA": 120.0},
            exchange_history=[(50.0, "EUR", 55.0, "USD", 1.1)],
            new_allocation={"AAA": 9.8},
            target_allocation={"AAA": 10.0},
            plan=plan,
        )

    assert report["common_currency"] == "USD"
    assert report["rows"][0]["ticker"] == "AAA"
    assert "label" not in report["rows"][0]
    assert report["rows"][0]["band_marker"] == "◆"
    assert report["rows"][0]["amount_common_currency"] == 120
    assert report["rows"][0]["fee_common_currency"] == 0
    assert report["summary"]["amount_common_currency_total"] == 120
    assert report["summary"]["fee_common_currency_total"] == 0
    assert report["exchange_history"][0]["to_currency"] == "USD"
    assert report["remaining_cash"][0] == {"amount": 12.5, "currency": "USD"}
