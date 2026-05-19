from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rich.console import Console

from rebalance.band_rendering import (
    _BAR_INNER,
    _amount_in_common_currency,
    _band_cell,
    _band_distance_pp,
    _column_summaries,
    _common_currency_fee,
    _format_trade,
    _original_intended_target,
    _whole_common_amount,
    _whole_common_fee,
    band_bar,
    build_band_rebalance_report,
    render_band_rebalance_table,
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


def test_band_distance_pp_uses_normalized_band_scale():
    status = _status(lower_band=3.31, upper_band=4.48)

    distance = _band_distance_pp(status, 3.9, 3.81)

    assert distance == pytest.approx(abs(3.81 - 3.9) / (4.48 - 3.31) * 100.0)


def test_band_bar_shows_original_target_marker():
    status = _status(direction="above", current_pct=65.0, upper_tolerance=55.0)

    bar = band_bar(52.0, 65.0, 50.0, 55.0, 50.0, status)

    assert "◇" in bar.plain


def test_band_bar_uses_even_quartile_guide_spacing():
    status = _status(direction=None)

    bar = band_bar(42.0, 49.0, 56.0, None, 44.0, status)

    assert bar.plain.index("┤") == 1 + int(0.25 * _BAR_INNER)
    assert bar.plain.index("│") == 1 + int(0.50 * _BAR_INNER)
    assert bar.plain.index("├") == 1 + int(0.75 * _BAR_INNER)


def test_band_bar_snaps_above_original_target_to_upper_midpoint():
    status = _status(
        direction="above",
        current_pct=1.98,
        lower_band=1.105,
        upper_band=1.495,
        upper_tolerance=1.3975,
    )

    bar = band_bar(2.16, 1.98, 1.36, 1.3975, 1.36, status)

    assert bar.plain.index("◇") == 1 + int(0.75 * _BAR_INNER)


def test_band_bar_snaps_new_asset_original_target_to_target_marker():
    status = _status(
        direction="below",
        current_pct=0.0,
        lower_band=4.81,
        upper_band=6.39,
        lower_tolerance=5.205,
    )

    bar = band_bar(0.0, 0.0, 5.40, 5.60, 5.42, status)

    assert bar.plain.index("◇") == 1 + int(0.50 * _BAR_INNER)


def test_format_trade_uses_two_decimals_for_fractional_units():
    quantity_markup, _, _ = _format_trade(1.234, 100.0)

    assert quantity_markup == "[green]1.23[/green]"


def test_column_summaries_total_single_currency_amount_and_percentages():
    plan = SimpleNamespace(
        assets_only_allocation={"AAA": 60.0, "BBB": 40.0},
        cash_inclusive_allocation={"AAA": 48.0, "BBB": 32.0},
        effective_targets={"AAA": 58.0, "BBB": 42.0},
    )

    summaries = _column_summaries(
        common_amounts={"AAA": 150.0, "BBB": -50.0},
        courtage_fees={"AAA": 2.0, "BBB": 1.0},
        fx_fees={"AAA": 1.0, "BBB": 0.0},
        common_fees={"AAA": 3.0, "BBB": 1.0},
        new_allocation={"AAA": 50.0, "BBB": 35.0},
        plan=plan,
        original_targets={"AAA": 55.0, "BBB": 40.0},
        common_currency="USD",
    )

    assert summaries == {
        "common_amount_total": 100.0,
        "common_amount_currency": "USD",
        "courtage_fee_total": 3.0,
        "courtage_fee_currency": "USD",
        "fx_fee_total": 1.0,
        "fx_fee_currency": "USD",
        "common_fee_total": 4.0,
        "common_fee_currency": "USD",
        "old_pct_total": 100.0,
        "cash_inclusive_pct_total": 80.0,
        "new_pct_total": 85.0,
        "orig_target_total": 95.0,
        "eff_target_total": 100.0,
    }


def test_column_summaries_convert_mixed_currency_amount_total_to_base_currency():
    plan = SimpleNamespace(
        assets_only_allocation={"AAA": 100.0},
        cash_inclusive_allocation={"AAA": 75.0},
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
        "rebalance.courtage.Cash.exchange_rate",
        autospec=True,
        side_effect=fake_exchange_rate,
    ):
        common_amounts = {
            "AAA": _amount_in_common_currency(150.0, "USD", "EUR", 0.0),
            "BBB": _amount_in_common_currency(50.0, "CAD", "EUR", 0.0),
        }
        summaries = _column_summaries(
            common_amounts=common_amounts,
            courtage_fees={"AAA": 0.0},
            fx_fees={"AAA": 0.0},
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
        "rebalance.courtage.Cash.exchange_rate",
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
        "rebalance.courtage.Cash.exchange_rate",
        autospec=True,
        side_effect=fake_exchange_rate,
    ):
        buy_fee = _common_currency_fee(100.0, "USD", "EUR", 0.02)
        sell_fee = _common_currency_fee(-100.0, "USD", "EUR", 0.02)

    assert buy_fee == 1.6
    assert sell_fee == 1.6


def test_common_currency_fee_includes_courtage_when_profile_enabled():
    def fake_exchange_rate(self, currency):
        rates = {
            ("USD", "SEK"): 10.0,
            ("SEK", "SEK"): 1.0,
        }
        return rates[(self.currency, currency)]

    with patch(
        "rebalance.courtage.Cash.exchange_rate",
        autospec=True,
        side_effect=fake_exchange_rate,
    ):
        fee = _common_currency_fee(500.0, "USD", "SEK", 0.0025, "nordnet_sweden")

    assert fee == pytest.approx(25.0)


def test_common_currency_fee_skips_courtage_for_fractional_assets():
    def fake_exchange_rate(self, currency):
        rates = {
            ("USD", "SEK"): 10.0,
            ("SEK", "SEK"): 1.0,
        }
        return rates[(self.currency, currency)]

    with patch(
        "rebalance.courtage.Cash.exchange_rate",
        autospec=True,
        side_effect=fake_exchange_rate,
    ):
        fee = _common_currency_fee(
            500.0,
            "USD",
            "SEK",
            0.0025,
            "nordnet_sweden",
            courtage_exempt=True,
        )

    assert fee == pytest.approx(12.5)


def test_whole_common_amount_rounds_to_integer():
    def fake_exchange_rate(self, currency):
        rates = {
            ("USD", "EUR"): 0.8,
            ("EUR", "EUR"): 1.0,
        }
        return rates[(self.currency, currency)]

    with patch(
        "rebalance.courtage.Cash.exchange_rate",
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
        "rebalance.courtage.Cash.exchange_rate",
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
        cash_inclusive_allocation={"AAA": 0.0},
        effective_targets={"AAA": 10.0},
    )

    with patch("rebalance.courtage.Cash.exchange_rate", return_value=1.0):
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
    assert report["rows"][0]["cash_inclusive_pct"] == 0.0
    assert report["rows"][0][
        "original_target_optimizer_band_distance_pp"
    ] == pytest.approx(1.0)
    assert report["summary"]["amount_common_currency_total"] == 120
    assert report["summary"]["fee_common_currency_total"] == 0
    assert report["summary"]["cash_inclusive_pct_total"] == 0.0
    assert report["exchange_history"][0]["to_currency"] == "USD"
    assert report["remaining_cash"][0] == {"amount": 12.5, "currency": "USD"}


def test_build_band_rebalance_report_includes_financing_rows():
    portfolio = SimpleNamespace(
        common_currency="SEK",
        conversion_cost=0.0,
        assets={"AAA": SimpleNamespace(name="Asset A")},
        cash={"SEK": SimpleNamespace(amount=12.5, currency="SEK")},
    )
    plan = SimpleNamespace(
        locked_tickers=set(),
        status_by_ticker={"AAA": _status(direction="below", current_pct=0.0)},
        assets_only_allocation={"AAA": 0.0},
        cash_inclusive_allocation={"AAA": 0.0},
        effective_targets={"AAA": 10.0},
        financing_adjustment={
            "type": "nordnet_credit",
            "label": "Nordnet credit",
            "action": "draw",
            "amount": 50_000.0,
            "currency": "SEK",
            "recommended_debt_delta": 50_000.0,
            "applied_cash_delta": 50_000.0,
            "margin_debt_delta": 50_000.0,
            "included_in_trade_plan": True,
            "reason": "Draw Nordnet credit and add it to available SEK before rebalancing.",
        },
    )

    report = build_band_rebalance_report(
        portfolio,
        new_units={"AAA": 10},
        prices={"AAA": [12.0, "SEK"]},
        cost={"AAA": 120.0},
        exchange_history=[],
        new_allocation={"AAA": 9.8},
        target_allocation={"AAA": 10.0},
        plan=plan,
    )

    assert report["financing_rows"] == [
        {
            "type": "nordnet_credit",
            "label": "Nordnet credit",
            "action": "draw",
            "trade": "DRAW",
            "amount": 50_000.0,
            "amount_currency": "SEK",
            "amount_common_currency": 50_000,
            "amount_common_currency_currency": "SEK",
            "margin_debt_delta": 50_000.0,
            "recommended_debt_delta": 50_000.0,
            "reason": "Draw Nordnet credit and add it to available SEK before rebalancing.",
        }
    ]
    assert report["summary"]["amount_common_currency_total"] == 120
    assert report["summary"]["financing_cash_delta"] == 50_000.0


def test_build_band_rebalance_report_includes_withdrawal_rows():
    portfolio = SimpleNamespace(
        common_currency="SEK",
        conversion_cost=0.0,
        assets={"AAA": SimpleNamespace(name="Asset A")},
        cash={"SEK": SimpleNamespace(amount=12.5, currency="SEK")},
    )
    plan = SimpleNamespace(
        locked_tickers=set(),
        status_by_ticker={"AAA": _status(direction="below", current_pct=0.0)},
        assets_only_allocation={"AAA": 0.0},
        cash_inclusive_allocation={"AAA": 0.0},
        effective_targets={"AAA": 10.0},
        withdrawal_plan={
            "feasible": True,
            "requested_amount": 300_000.0,
            "requested_amount_currency": "SEK",
            "source": "cli",
            "withdrawal_cash_delta": -300_000.0,
            "reason": "Withdrawal plan keeps projected Nordnet debt within policy.",
        },
    )

    report = build_band_rebalance_report(
        portfolio,
        new_units={"AAA": -10},
        prices={"AAA": [12.0, "SEK"]},
        cost={"AAA": -120.0},
        exchange_history=[],
        new_allocation={"AAA": 9.8},
        target_allocation={"AAA": 10.0},
        plan=plan,
    )

    assert report["withdrawal_rows"] == [
        {
            "type": "external_withdrawal",
            "label": "Withdrawal",
            "action": "withdraw",
            "trade": "WITHDRAW",
            "amount": -300_000.0,
            "amount_currency": "SEK",
            "amount_common_currency": -300_000,
            "amount_common_currency_currency": "SEK",
            "source": "cli",
            "reason": "Withdrawal plan keeps projected Nordnet debt within policy.",
        }
    ]
    assert report["summary"]["withdrawal_cash_delta"] == -300_000.0
    assert report["summary"]["net_external_cash_delta"] == -300_000.0


def test_build_band_rebalance_report_keeps_negative_cash_withdrawal_row_but_zero_summary_delta():
    portfolio = SimpleNamespace(
        common_currency="SEK",
        conversion_cost=0.0,
        assets={"AAA": SimpleNamespace(name="Asset A")},
        cash={"SEK": SimpleNamespace(amount=-300_000.0, currency="SEK")},
    )
    plan = SimpleNamespace(
        locked_tickers=set(),
        status_by_ticker={"AAA": _status(direction="below", current_pct=0.0)},
        assets_only_allocation={"AAA": 0.0},
        cash_inclusive_allocation={"AAA": 0.0},
        effective_targets={"AAA": 10.0},
        withdrawal_plan={
            "feasible": True,
            "requested_amount": 300_000.0,
            "requested_amount_currency": "SEK",
            "source": "negative_cash",
            "withdrawal_cash_delta": 0.0,
            "reason": "Negative cash already represents the withdrawal.",
        },
    )

    report = build_band_rebalance_report(
        portfolio,
        new_units={"AAA": -10},
        prices={"AAA": [12.0, "SEK"]},
        cost={"AAA": -120.0},
        exchange_history=[],
        new_allocation={"AAA": 9.8},
        target_allocation={"AAA": 10.0},
        plan=plan,
    )

    assert report["withdrawal_rows"] == [
        {
            "type": "external_withdrawal",
            "label": "Withdrawal",
            "action": "withdraw",
            "trade": "WITHDRAW",
            "amount": -300_000.0,
            "amount_currency": "SEK",
            "amount_common_currency": -300_000,
            "amount_common_currency_currency": "SEK",
            "source": "negative_cash",
            "reason": "Negative cash already represents the withdrawal.",
        }
    ]
    assert report["summary"]["withdrawal_cash_delta"] == 0.0
    assert report["summary"]["net_external_cash_delta"] == 0.0


def test_build_band_rebalance_report_uses_optimizer_math_for_frozen_distance():
    portfolio = SimpleNamespace(
        common_currency="USD",
        conversion_cost=0.0,
        assets={"AAA": SimpleNamespace(name="Asset A")},
        cash={"USD": SimpleNamespace(amount=0.0, currency="USD")},
        value=lambda currency: 100.0,
    )
    plan = SimpleNamespace(
        locked_tickers={"AAA"},
        status_by_ticker={
            "AAA": _status(
                direction=None, current_pct=40.0, lower_band=30.0, upper_band=50.0
            )
        },
        assets_only_allocation={"AAA": 40.0},
        cash_inclusive_allocation={"AAA": 40.0},
        effective_targets={"AAA": 40.0},
    )

    report = build_band_rebalance_report(
        portfolio,
        new_units={"AAA": 0},
        prices={"AAA": [10.0, "USD"]},
        cost={"AAA": 0.0},
        exchange_history=[],
        new_allocation={"AAA": 39.5},
        target_allocation={"AAA": 50.0},
        plan=plan,
    )

    assert report["rows"][0]["original_target_pct"] == pytest.approx(40.0)
    assert report["rows"][0]["new_pct"] == pytest.approx(39.5)
    assert report["rows"][0][
        "original_target_optimizer_band_distance_pp"
    ] == pytest.approx(0.0)


def test_build_band_rebalance_report_sorts_rows_by_band_distance_descending():
    portfolio = SimpleNamespace(
        common_currency="USD",
        conversion_cost=0.0,
        assets={
            "AAA": SimpleNamespace(name="Asset A"),
            "BBB": SimpleNamespace(name="Asset B"),
            "CCC": SimpleNamespace(name="Asset C"),
        },
        cash={"USD": SimpleNamespace(amount=0.0, currency="USD")},
    )
    plan = SimpleNamespace(
        locked_tickers={"AAA"},
        status_by_ticker={
            "AAA": _status(
                direction=None, current_pct=40.0, lower_band=30.0, upper_band=50.0
            ),
            "BBB": _status(
                direction="below", current_pct=0.0, lower_band=0.0, upper_band=20.0
            ),
            "CCC": _status(
                direction="above", current_pct=5.0, lower_band=0.0, upper_band=0.0
            ),
        },
        assets_only_allocation={"AAA": 40.0, "BBB": 0.0, "CCC": 5.0},
        cash_inclusive_allocation={"AAA": 40.0, "BBB": 0.0, "CCC": 5.0},
        effective_targets={"AAA": 40.0, "BBB": 10.0, "CCC": 0.0},
    )

    report = build_band_rebalance_report(
        portfolio,
        new_units={"AAA": 0, "BBB": 0, "CCC": 0},
        prices={"AAA": [10.0, "USD"], "BBB": [10.0, "USD"], "CCC": [10.0, "USD"]},
        cost={"AAA": 0.0, "BBB": 0.0, "CCC": 0.0},
        exchange_history=[],
        new_allocation={"AAA": 40.0, "BBB": 8.0, "CCC": 0.0},
        target_allocation={"AAA": 50.0, "BBB": 10.0, "CCC": 0.0},
        plan=plan,
    )

    assert [row["ticker"] for row in report["rows"]] == ["BBB", "AAA", "CCC"]


def test_build_band_rebalance_report_includes_courtage_breakdown():
    portfolio = SimpleNamespace(
        common_currency="SEK",
        conversion_cost=0.0025,
        courtage_profile="nordnet_sweden",
        assets={"AAA": SimpleNamespace(name="Asset A")},
        cash={"SEK": SimpleNamespace(amount=12.5, currency="SEK")},
    )
    plan = SimpleNamespace(
        locked_tickers=set(),
        status_by_ticker={"AAA": _status(direction="below", current_pct=0.0)},
        assets_only_allocation={"AAA": 0.0},
        cash_inclusive_allocation={"AAA": 0.0},
        effective_targets={"AAA": 10.0},
    )

    def fake_exchange_rate(self, currency):
        rates = {
            ("USD", "SEK"): 10.0,
            ("SEK", "SEK"): 1.0,
        }
        return rates[(self.currency, currency)]

    with patch(
        "rebalance.courtage.Cash.exchange_rate",
        autospec=True,
        side_effect=fake_exchange_rate,
    ):
        report = build_band_rebalance_report(
            portfolio,
            new_units={"AAA": 10},
            prices={"AAA": [50.0, "USD"]},
            cost={"AAA": 500.0},
            exchange_history=[],
            new_allocation={"AAA": 9.8},
            target_allocation={"AAA": 10.0},
            plan=plan,
        )

    assert report["rows"][0]["courtage_class"] == "Mini"
    assert report["rows"][0]["fx_fee_common_currency"] == 12
    assert report["rows"][0]["courtage_fee_common_currency"] == 12
    assert report["rows"][0]["fee_common_currency"] == 25
    assert report["summary"]["fx_fee_common_currency_total"] == 12.0
    assert report["summary"]["courtage_fee_common_currency_total"] == 12.0
    assert report["summary"]["fee_common_currency_total"] == 25.0


def test_build_band_rebalance_report_skips_courtage_for_fractional_asset():
    portfolio = SimpleNamespace(
        common_currency="SEK",
        conversion_cost=0.0025,
        courtage_profile="nordnet_sweden",
        assets={"AAA": SimpleNamespace(name="Asset A", fractional=True)},
        cash={"SEK": SimpleNamespace(amount=12.5, currency="SEK")},
    )
    plan = SimpleNamespace(
        locked_tickers=set(),
        status_by_ticker={"AAA": _status(direction="below", current_pct=0.0)},
        assets_only_allocation={"AAA": 0.0},
        cash_inclusive_allocation={"AAA": 0.0},
        effective_targets={"AAA": 10.0},
    )

    def fake_exchange_rate(self, currency):
        rates = {
            ("USD", "SEK"): 10.0,
            ("SEK", "SEK"): 1.0,
        }
        return rates[(self.currency, currency)]

    with patch(
        "rebalance.courtage.Cash.exchange_rate",
        autospec=True,
        side_effect=fake_exchange_rate,
    ):
        report = build_band_rebalance_report(
            portfolio,
            new_units={"AAA": 10},
            prices={"AAA": [50.0, "USD"]},
            cost={"AAA": 500.0},
            exchange_history=[],
            new_allocation={"AAA": 9.8},
            target_allocation={"AAA": 10.0},
            plan=plan,
        )

    assert report["rows"][0]["courtage_class"] == "—"
    assert report["rows"][0]["courtage_fee_common_currency"] == 0
    assert report["rows"][0]["fx_fee_common_currency"] == 12
    assert report["rows"][0]["fee_common_currency"] == 12


def test_render_band_rebalance_table_shows_courtage_class():
    portfolio = SimpleNamespace(
        common_currency="SEK",
        conversion_cost=0.0025,
        courtage_profile="nordnet_sweden",
        assets={"AAA": SimpleNamespace(name="Asset A")},
        cash={"SEK": SimpleNamespace(amount=12.5, currency="SEK")},
        _conversion_cost=0.0025,
        _common_currency="SEK",
    )
    plan = SimpleNamespace(
        locked_tickers=set(),
        status_by_ticker={"AAA": _status(direction="below", current_pct=0.0)},
        assets_only_allocation={"AAA": 0.0},
        cash_inclusive_allocation={"AAA": 0.0},
        effective_targets={"AAA": 10.0},
    )
    console = Console(record=True, width=260)

    def fake_exchange_rate(self, currency):
        rates = {
            ("USD", "SEK"): 10.0,
            ("SEK", "SEK"): 1.0,
        }
        return rates[(self.currency, currency)]

    with patch(
        "rebalance.courtage.Cash.exchange_rate",
        autospec=True,
        side_effect=fake_exchange_rate,
    ):
        with patch("rebalance.band_rendering._console", console):
            render_band_rebalance_table(
                portfolio,
                new_units={"AAA": 10},
                prices={"AAA": [50.0, "USD"]},
                cost={"AAA": 500.0},
                exchange_history=[],
                new_allocation={"AAA": 9.8},
                target_allocation={"AAA": 10.0},
                plan=plan,
            )

    output = console.export_text()

    assert "Courtage" in output
    assert "Courtage Fee SEK" in output
    assert "FX Fee SEK" in output
    assert "Orig->Opt Band pp" in output
    assert "Mini" in output


def test_render_band_rebalance_table_shows_financing_row():
    portfolio = SimpleNamespace(
        common_currency="SEK",
        conversion_cost=0.0,
        assets={"AAA": SimpleNamespace(name="Asset A")},
        cash={"SEK": SimpleNamespace(amount=12.5, currency="SEK")},
        _conversion_cost=0.0,
        _common_currency="SEK",
    )
    plan = SimpleNamespace(
        locked_tickers=set(),
        status_by_ticker={"AAA": _status(direction="below", current_pct=0.0)},
        assets_only_allocation={"AAA": 0.0},
        cash_inclusive_allocation={"AAA": 0.0},
        effective_targets={"AAA": 10.0},
        financing_adjustment={
            "type": "nordnet_credit",
            "label": "Nordnet credit",
            "action": "draw",
            "amount": 50_000.0,
            "currency": "SEK",
            "recommended_debt_delta": 50_000.0,
            "applied_cash_delta": 50_000.0,
            "margin_debt_delta": 50_000.0,
            "included_in_trade_plan": True,
            "reason": "Draw Nordnet credit and add it to available SEK before rebalancing.",
        },
    )
    console = Console(record=True, width=260)

    with patch("rebalance.band_rendering._console", console):
        render_band_rebalance_table(
            portfolio,
            new_units={"AAA": 10},
            prices={"AAA": [12.0, "SEK"]},
            cost={"AAA": 120.0},
            exchange_history=[],
            new_allocation={"AAA": 9.8},
            target_allocation={"AAA": 10.0},
            plan=plan,
        )

    output = console.export_text()

    assert "Nordnet credit" in output
    assert "DRAW" in output
    assert "50,000" in output


def test_render_band_rebalance_table_shows_withdrawal_row():
    portfolio = SimpleNamespace(
        common_currency="SEK",
        conversion_cost=0.0,
        assets={"AAA": SimpleNamespace(name="Asset A")},
        cash={"SEK": SimpleNamespace(amount=12.5, currency="SEK")},
        _conversion_cost=0.0,
        _common_currency="SEK",
    )
    plan = SimpleNamespace(
        locked_tickers=set(),
        status_by_ticker={"AAA": _status(direction="below", current_pct=0.0)},
        assets_only_allocation={"AAA": 0.0},
        cash_inclusive_allocation={"AAA": 0.0},
        effective_targets={"AAA": 10.0},
        withdrawal_plan={
            "feasible": True,
            "requested_amount": 300_000.0,
            "requested_amount_currency": "SEK",
            "source": "cli",
            "reason": "Withdrawal plan keeps projected Nordnet debt within policy.",
        },
    )
    console = Console(record=True, width=260)

    with patch("rebalance.band_rendering._console", console):
        render_band_rebalance_table(
            portfolio,
            new_units={"AAA": -10},
            prices={"AAA": [12.0, "SEK"]},
            cost={"AAA": -120.0},
            exchange_history=[],
            new_allocation={"AAA": 9.8},
            target_allocation={"AAA": 10.0},
            plan=plan,
        )

    output = console.export_text()

    assert "Withdrawal" in output
    assert "WITHDRAW" in output
    assert "300,000" in output
