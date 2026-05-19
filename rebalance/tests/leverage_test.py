from types import SimpleNamespace

import pytest

from rebalance.leverage import build_financing_adjustment, build_leverage_report
from rebalance.schemas import PortfolioConfig


class FakeAsset:
    def __init__(self, ticker: str, value: float, name: str | None = None):
        self.ticker = ticker
        self.value = value
        self.name = name

    def market_value_in(self, currency: str) -> float:
        del currency
        return self.value


class FakePortfolio:
    def __init__(self, assets: dict[str, float], cash: float = 0.0):
        self.common_currency = "SEK"
        self.assets = {
            ticker: FakeAsset(ticker, value, name=f"Asset {ticker}")
            for ticker, value in assets.items()
        }
        self.cash = {"SEK": SimpleNamespace(amount=cash, currency="SEK")}

    def market_value(self, currency: str) -> float:
        del currency
        return sum(asset.value for asset in self.assets.values())

    def cash_value(self, currency: str) -> float:
        del currency
        return self.cash["SEK"].amount


def _config(**overrides) -> PortfolioConfig:
    base = {
        "name": "test",
        "common_currency": "SEK",
        "assets": [
            {
                "ticker": "AAA",
                "quantity": 1,
                "target_allocation": 60.0,
                "isin": "AAAISIN",
                "lending_value": 70.0,
                "extended_lending_value": 85.0,
                "instrument_type": "other",
            },
            {
                "ticker": "BBB",
                "quantity": 1,
                "target_allocation": 40.0,
                "isin": "BBBISIN",
                "lending_value": 80.0,
                "extended_lending_value": 90.0,
                "instrument_type": "other",
            },
        ],
        "leverage": {
            "provider": "nordnet",
            "margin_debt": {"amount": 200.0, "currency": "SEK"},
        },
    }
    base.update(overrides)
    return PortfolioConfig.model_validate(base)


def test_build_leverage_report_uses_exact_extended_lending_values():
    portfolio = FakePortfolio({"AAA": 600.0, "BBB": 400.0})

    report = build_leverage_report(portfolio, _config())

    assert report["configured"] is True
    assert report["weighted_lending_value_pct"] == pytest.approx(87.0)
    assert report["portfolio_lending_value"] == pytest.approx(870.0)
    assert report["bracket_credit_limit"] == pytest.approx(348.0)
    assert report["bracket_max_borrowing_ratio_pct"] == pytest.approx(34.8)
    assert report["current_leverage"] == pytest.approx(1.25)
    assert report["target_debt"] == pytest.approx(296.0)
    assert report["debt_delta_to_target"] == pytest.approx(96.0)
    assert report["recommended_debt_delta"] == pytest.approx(96.0)
    assert report["action"] == "increase"
    assert report["composition_qualified"] is True
    assert report["composition_evaluation"] == "verified"


def test_build_leverage_report_uses_partial_fallback_for_missing_values():
    config = _config(
        assets=[
            {
                "ticker": "AAA",
                "quantity": 1,
                "target_allocation": 60.0,
                "lending_value": 70.0,
                "extended_lending_value": 85.0,
                "instrument_type": "other",
            },
            {"ticker": "BBB", "quantity": 1, "target_allocation": 40.0},
        ]
    )
    portfolio = FakePortfolio({"AAA": 600.0, "BBB": 400.0})

    report = build_leverage_report(portfolio, config)

    assert report["weighted_lending_value_pct"] == pytest.approx(82.6)
    assert report["applied_lending_value_basis"] == "mixed"
    assert "missing_lending_value" in report["warnings"]
    by_ticker = {position["ticker"]: position for position in report["positions"]}
    assert by_ticker["BBB"]["applied_lending_value"] == pytest.approx(79.0)
    assert by_ticker["BBB"]["applied_lending_value_basis"] == "fallback"


def test_build_leverage_report_falls_back_when_extended_cap_fails():
    config = _config(
        assets=[
            {
                "ticker": "AAA",
                "quantity": 1,
                "target_allocation": 60.0,
                "lending_value": 70.0,
                "extended_lending_value": 85.0,
                "instrument_type": "etf",
            },
            {
                "ticker": "BBB",
                "quantity": 1,
                "target_allocation": 40.0,
                "lending_value": 80.0,
                "extended_lending_value": 90.0,
                "instrument_type": "other",
            },
        ]
    )
    portfolio = FakePortfolio({"AAA": 600.0, "BBB": 400.0})

    report = build_leverage_report(portfolio, config)

    assert report["composition_qualified"] is False
    assert report["composition_evaluation"] == "failed"
    assert report["weighted_lending_value_pct"] == pytest.approx(74.0)
    assert "position_above_etf_interest_discount_cap" in report["warnings"]
    by_ticker = {position["ticker"]: position for position in report["positions"]}
    assert by_ticker["AAA"]["applied_lending_value_basis"] == "ordinary"
    assert by_ticker["BBB"]["applied_lending_value_basis"] == "ordinary"


def test_build_leverage_report_uses_approved_holdings_denominator_for_caps():
    config = _config(
        assets=[
            {
                "ticker": "AAA",
                "quantity": 1,
                "target_allocation": 75.0,
                "lending_value": 70.0,
                "extended_lending_value": 85.0,
                "instrument_type": "etf",
            },
            {
                "ticker": "BBB",
                "quantity": 1,
                "target_allocation": 25.0,
                "lending_value": 80.0,
                "extended_lending_value": 85.0,
                "instrument_type": "fund",
            },
        ]
    )
    portfolio = FakePortfolio({"AAA": 150.0, "BBB": 50.0}, cash=800.0)

    report = build_leverage_report(portfolio, config)

    assert report["approved_holdings_value"] == pytest.approx(200.0)
    assert report["composition_qualified"] is False
    assert "position_above_etf_interest_discount_cap" in report["warnings"]
    by_ticker = {position["ticker"]: position for position in report["positions"]}
    assert by_ticker["AAA"]["approved_holdings_weight_pct"] == pytest.approx(75.0)
    assert by_ticker["BBB"]["approved_holdings_weight_pct"] == pytest.approx(25.0)


def test_build_leverage_report_bracket_limit_uses_only_70_plus_holdings():
    config = _config(
        assets=[
            {
                "ticker": "AAA",
                "quantity": 1,
                "target_allocation": 60.0,
                "lending_value": 80.0,
                "extended_lending_value": 85.0,
                "instrument_type": "other",
            },
            {
                "ticker": "BBB",
                "quantity": 1,
                "target_allocation": 40.0,
                "lending_value": 50.0,
                "extended_lending_value": 50.0,
                "instrument_type": "other",
            },
        ]
    )
    portfolio = FakePortfolio({"AAA": 600.0, "BBB": 400.0})

    report = build_leverage_report(portfolio, config)

    assert report["weighted_lending_value_pct"] == pytest.approx(71.0)
    assert report["plus_eligible_lending_value"] == pytest.approx(510.0)
    assert report["bracket_credit_limit"] == pytest.approx(204.0)
    by_ticker = {position["ticker"]: position for position in report["positions"]}
    assert by_ticker["AAA"]["counts_toward_bracket_limit"] is True
    assert by_ticker["BBB"]["counts_toward_bracket_limit"] is False


def test_build_leverage_report_holds_above_target_during_drawdown():
    config = _config(
        leverage={
            "provider": "nordnet",
            "margin_debt": {"amount": 300.0, "currency": "SEK"},
            "drawdown_from_ath_pct": 5.0,
        }
    )
    portfolio = FakePortfolio({"AAA": 600.0, "BBB": 400.0})

    report = build_leverage_report(portfolio, config)

    assert report["debt_delta_to_target"] < 0.0
    assert report["headroom_to_bracket"] > 0.0
    assert report["action"] == "hold"
    assert report["recommended_debt_delta"] == pytest.approx(0.0)


def test_build_leverage_report_flags_opportunistic_zone_above_bracket():
    config = _config(
        leverage={
            "provider": "nordnet",
            "margin_debt": {"amount": 400.0, "currency": "SEK"},
            "drawdown_from_ath_pct": 14.0,
        }
    )
    portfolio = FakePortfolio({"AAA": 600.0, "BBB": 400.0})

    report = build_leverage_report(portfolio, config)

    assert report["headroom_to_bracket"] < 0.0
    assert report["strict_bracket_delta"] < 0.0
    assert report["action"] == "opportunistic_zone"
    assert report["recommended_debt_delta"] == pytest.approx(0.0)


def test_build_leverage_report_without_config_is_not_configured():
    portfolio = FakePortfolio({"AAA": 600.0, "BBB": 400.0})
    config = PortfolioConfig.model_validate(
        {
            "name": "test",
            "common_currency": "SEK",
            "assets": [
                {"ticker": "AAA", "quantity": 1, "target_allocation": 60.0},
                {"ticker": "BBB", "quantity": 1, "target_allocation": 40.0},
            ],
        }
    )

    report = build_leverage_report(portfolio, config)

    assert report["configured"] is False
    assert report["action"] == "not_configured"


def test_build_leverage_report_can_project_margin_debt_delta():
    portfolio = FakePortfolio({"AAA": 600.0, "BBB": 400.0})

    report = build_leverage_report(portfolio, _config(), margin_debt_delta=50.0)

    assert report["configured_margin_debt"] == pytest.approx(200.0)
    assert report["margin_debt"] == pytest.approx(250.0)
    assert report["margin_debt_delta"] == pytest.approx(50.0)
    assert report["debt_basis"] == "projected"
    assert report["current_leverage"] == pytest.approx(1000.0 / 750.0)


def test_build_financing_adjustment_draws_credit_for_increase_action():
    portfolio = FakePortfolio({"AAA": 600.0, "BBB": 400.0})
    report = build_leverage_report(portfolio, _config())

    adjustment = build_financing_adjustment(report)

    assert adjustment["action"] == "draw"
    assert adjustment["amount"] == pytest.approx(96.0)
    assert adjustment["applied_cash_delta"] == pytest.approx(96.0)
    assert adjustment["margin_debt_delta"] == pytest.approx(96.0)
    assert adjustment["included_in_trade_plan"] is True


def test_build_financing_adjustment_reserves_cash_for_repayment():
    config = _config(
        leverage={
            "provider": "nordnet",
            "margin_debt": {"amount": 400.0, "currency": "SEK"},
        }
    )
    portfolio = FakePortfolio({"AAA": 600.0, "BBB": 400.0})
    report = build_leverage_report(portfolio, config)

    adjustment = build_financing_adjustment(report)

    assert report["action"] == "decrease_to_bracket"
    assert adjustment["action"] == "repay"
    assert adjustment["amount"] == pytest.approx(52.0)
    assert adjustment["applied_cash_delta"] == pytest.approx(-52.0)
    assert adjustment["margin_debt_delta"] == pytest.approx(-52.0)
    assert adjustment["included_in_trade_plan"] is True


def test_build_financing_adjustment_skips_hold_action():
    config = _config(
        leverage={
            "provider": "nordnet",
            "margin_debt": {"amount": 300.0, "currency": "SEK"},
            "drawdown_from_ath_pct": 5.0,
        }
    )
    portfolio = FakePortfolio({"AAA": 600.0, "BBB": 400.0})
    report = build_leverage_report(portfolio, config)

    adjustment = build_financing_adjustment(report)

    assert adjustment["action"] == "none"
    assert adjustment["included_in_trade_plan"] is False
