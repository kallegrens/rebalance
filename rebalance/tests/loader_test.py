import json
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from rebalance.loader import load_portfolio
from rebalance.schemas import PortfolioConfig


def _valid_portfolio(**overrides):
    """Return a minimal valid portfolio dict, with optional field overrides."""
    base = {
        "name": "test",
        "selling_allowed": False,
        "cash": [],
        "assets": [
            {"ticker": "XBB.TO", "quantity": 10, "target_allocation": 60.0},
            {"ticker": "XIC.TO", "quantity": 5, "target_allocation": 40.0},
        ],
    }
    base.update(overrides)
    return base


class TestValidPortfolios:
    def test_minimal_valid(self):
        config = PortfolioConfig.model_validate(_valid_portfolio())
        assert config.name == "test"
        assert config.selling_allowed is False
        assert len(config.assets) == 2
        assert config.cash == []

    def test_with_cash(self):
        config = PortfolioConfig.model_validate(
            _valid_portfolio(cash=[{"amount": 1000.0, "currency": "sek"}])
        )
        # currency normalised to uppercase
        assert config.cash[0].currency == "SEK"

    def test_with_nasdaq_nordic_fields(self):
        config = PortfolioConfig.model_validate(
            _valid_portfolio(
                assets=[
                    {
                        "ticker": "VIR10SEK",
                        "quantity": 10,
                        "target_allocation": 100.0,
                        "nasdaq_nordic_id": "TX4856348",
                        "nasdaq_nordic_asset_class": "ETN/ETC",
                    }
                ]
            )
        )
        assert config.assets[0].nasdaq_nordic_id == "TX4856348"

    def test_optional_asset_fields_absent(self):
        config = PortfolioConfig.model_validate(_valid_portfolio())
        assert config.assets[0].name is None
        assert config.assets[0].isin is None
        assert config.assets[0].volatility is None
        assert config.assets[0].lending_value is None
        assert config.assets[0].extended_lending_value is None

    def test_fractional_defaults_to_false(self):
        config = PortfolioConfig.model_validate(_valid_portfolio())
        assert config.assets[0].fractional is False

    def test_pending_defaults_to_false(self):
        config = PortfolioConfig.model_validate(_valid_portfolio())
        assert config.assets[0].pending is False

    def test_fractional_explicit_true(self):
        data = _valid_portfolio(
            assets=[
                {
                    "ticker": "XBB.TO",
                    "quantity": 10,
                    "target_allocation": 60.0,
                    "fractional": True,
                },
                {"ticker": "XIC.TO", "quantity": 5, "target_allocation": 40.0},
            ]
        )
        config = PortfolioConfig.model_validate(data)
        assert config.assets[0].fractional is True
        assert config.assets[1].fractional is False

    def test_pending_explicit_true(self):
        data = _valid_portfolio(
            assets=[
                {
                    "ticker": "XBB.TO",
                    "quantity": 10,
                    "target_allocation": 60.0,
                    "pending": True,
                },
                {"ticker": "XIC.TO", "quantity": 5, "target_allocation": 40.0},
            ]
        )
        config = PortfolioConfig.model_validate(data)
        assert config.assets[0].pending is True
        assert config.assets[1].pending is False

    def test_courtage_profile_is_normalized(self):
        config = PortfolioConfig.model_validate(
            _valid_portfolio(courtage_profile="Nordnet Germany UK")
        )
        assert config.courtage_profile == "nordnet_germany_uk"

    def test_asset_courtage_profile_is_normalized(self):
        config = PortfolioConfig.model_validate(
            _valid_portfolio(
                assets=[
                    {
                        "ticker": "VIR10SEK",
                        "quantity": 10,
                        "target_allocation": 100.0,
                        "courtage_profile": "Nordnet Stockholm",
                    }
                ]
            )
        )
        assert config.assets[0].courtage_profile == "nordnet_stockholm"

    def test_optional_asset_fields_present(self):
        data = _valid_portfolio(
            assets=[
                {
                    "ticker": "XBB.TO",
                    "quantity": 10,
                    "target_allocation": 100.0,
                    "name": "iShares Core Canadian Universe Bond Index ETF",
                    "isin": "CA46432F1099",
                    "volatility": 4.2,
                    "lending_value": 80.0,
                    "extended_lending_value": 85.0,
                    "interest_discount_eligible": True,
                    "instrument_type": "ETF",
                }
            ]
        )
        config = PortfolioConfig.model_validate(data)
        assert config.assets[0].isin == "CA46432F1099"
        assert config.assets[0].volatility == pytest.approx(4.2)
        assert config.assets[0].lending_value == pytest.approx(80.0)
        assert config.assets[0].extended_lending_value == pytest.approx(85.0)
        assert config.assets[0].interest_discount_eligible is True
        assert config.assets[0].instrument_type == "etf"

    def test_leverage_config_accepts_single_margin_debt_object(self):
        config = PortfolioConfig.model_validate(
            _valid_portfolio(
                leverage={
                    "provider": "Nordnet",
                    "margin_debt": {"amount": 12345.0, "currency": "sek"},
                    "drawdown_from_ath_pct": 2.5,
                }
            )
        )

        assert config.leverage is not None
        assert config.leverage.provider == "nordnet"
        assert config.leverage.margin_debt[0].amount == pytest.approx(12345.0)
        assert config.leverage.margin_debt[0].currency == "SEK"
        assert config.leverage.target_leverage == pytest.approx(1.37)
        assert config.leverage.approved_security_min_lending_value_pct == pytest.approx(
            70.0
        )
        assert config.leverage.discount_limit_pct_of_lending_value == pytest.approx(
            40.0
        )
        assert config.leverage.etf_extended_lending_max_weight_pct == pytest.approx(
            20.0
        )
        assert config.leverage.fund_extended_lending_max_weight_pct == pytest.approx(
            60.0
        )

    def test_leverage_config_accepts_margin_debt_list(self):
        config = PortfolioConfig.model_validate(
            _valid_portfolio(
                leverage={
                    "provider": "nordnet",
                    "margin_debt": [
                        {"amount": 1000.0, "currency": "SEK"},
                        {"amount": 100.0, "currency": "EUR"},
                    ],
                }
            )
        )

        assert config.leverage is not None
        assert len(config.leverage.margin_debt) == 2


class TestAssetValidation:
    def test_missing_ticker(self):
        data = _valid_portfolio(assets=[{"quantity": 10, "target_allocation": 100.0}])
        with pytest.raises(ValidationError) as exc_info:
            PortfolioConfig.model_validate(data)
        assert "ticker" in str(exc_info.value)

    def test_wrong_type_quantity(self):
        data = _valid_portfolio(
            assets=[{"ticker": "XBB.TO", "quantity": "ten", "target_allocation": 100.0}]
        )
        with pytest.raises(ValidationError) as exc_info:
            PortfolioConfig.model_validate(data)
        assert "quantity" in str(exc_info.value)

    def test_nasdaq_nordic_id_without_asset_class(self):
        data = _valid_portfolio(
            assets=[
                {
                    "ticker": "VIR10SEK",
                    "quantity": 0,
                    "target_allocation": 100.0,
                    "nasdaq_nordic_id": "TX4856348",
                    # nasdaq_nordic_asset_class intentionally absent
                }
            ]
        )
        with pytest.raises(ValidationError) as exc_info:
            PortfolioConfig.model_validate(data)
        assert "nasdaq_nordic_asset_class" in str(exc_info.value)

    def test_float_quantity_rejected_for_non_fractional_asset(self):
        data = _valid_portfolio(
            assets=[
                {"ticker": "XBB.TO", "quantity": 10.5, "target_allocation": 60.0},
                {"ticker": "XIC.TO", "quantity": 5, "target_allocation": 40.0},
            ]
        )
        with pytest.raises(ValidationError) as exc_info:
            PortfolioConfig.model_validate(data)
        assert "integer" in str(exc_info.value)

    def test_float_quantity_accepted_for_fractional_asset(self):
        data = _valid_portfolio(
            assets=[
                {
                    "ticker": "XBB.TO",
                    "quantity": 10.5,
                    "fractional": True,
                    "target_allocation": 60.0,
                },
                {"ticker": "XIC.TO", "quantity": 5, "target_allocation": 40.0},
            ]
        )
        config = PortfolioConfig.model_validate(data)
        assert config.assets[0].quantity == pytest.approx(10.5)

    def test_lending_value_above_100_rejected(self):
        data = _valid_portfolio(
            assets=[
                {
                    "ticker": "XBB.TO",
                    "quantity": 10,
                    "target_allocation": 100.0,
                    "lending_value": 101.0,
                }
            ]
        )
        with pytest.raises(ValidationError) as exc_info:
            PortfolioConfig.model_validate(data)
        assert "lending_value" in str(exc_info.value)

    def test_negative_margin_debt_rejected(self):
        data = _valid_portfolio(
            leverage={
                "provider": "nordnet",
                "margin_debt": {"amount": -1.0, "currency": "SEK"},
            }
        )
        with pytest.raises(ValidationError) as exc_info:
            PortfolioConfig.model_validate(data)
        assert "margin_debt" in str(exc_info.value)


class TestPortfolioValidation:
    def test_allocations_do_not_sum_to_100(self):
        data = _valid_portfolio(
            assets=[
                {"ticker": "XBB.TO", "quantity": 10, "target_allocation": 30.0},
                {"ticker": "XIC.TO", "quantity": 5, "target_allocation": 30.0},
            ]
        )
        with pytest.raises(ValidationError) as exc_info:
            PortfolioConfig.model_validate(data)
        assert "100" in str(exc_info.value)

    def test_empty_assets_list(self):
        data = _valid_portfolio(assets=[])
        with pytest.raises(ValidationError) as exc_info:
            PortfolioConfig.model_validate(data)
        assert "assets" in str(exc_info.value)

    def test_missing_name(self):
        data = {
            "selling_allowed": False,
            "assets": [
                {"ticker": "XBB.TO", "quantity": 10, "target_allocation": 100.0}
            ],
        }
        with pytest.raises(ValidationError) as exc_info:
            PortfolioConfig.model_validate(data)
        assert "name" in str(exc_info.value)


class TestCashValidation:
    def test_negative_amount(self):
        data = _valid_portfolio(cash=[{"amount": -100.0, "currency": "USD"}])
        config = PortfolioConfig.model_validate(data)
        assert config.cash[0].amount == -100.0

    def test_currency_too_long(self):
        data = _valid_portfolio(cash=[{"amount": 100.0, "currency": "USDD"}])
        with pytest.raises(ValidationError) as exc_info:
            PortfolioConfig.model_validate(data)
        assert "currency" in str(exc_info.value)

    def test_currency_normalised_to_uppercase(self):
        data = _valid_portfolio(cash=[{"amount": 500.0, "currency": "usd"}])
        config = PortfolioConfig.model_validate(data)
        assert config.cash[0].currency == "USD"


class TestLoadPortfolio:
    def test_assets_and_target_allocation(self, mock_price_fetchers, tmp_path):
        path = tmp_path / "p.json"
        path.write_text(json.dumps(_valid_portfolio()))
        portfolio, target = load_portfolio(str(path))
        assert set(portfolio.assets.keys()) == {"XBB.TO", "XIC.TO"}
        assert target == {"XBB.TO": 60.0, "XIC.TO": 40.0}

    def test_cash_loaded(self, mock_price_fetchers, tmp_path):
        path = tmp_path / "p.json"
        path.write_text(
            json.dumps(_valid_portfolio(cash=[{"amount": 500.0, "currency": "USD"}]))
        )
        portfolio, _ = load_portfolio(str(path))
        assert "USD" in portfolio.cash
        assert portfolio.cash["USD"].amount == pytest.approx(500.0)

    def test_selling_allowed_propagated(self, mock_price_fetchers, tmp_path):
        path = tmp_path / "p.json"
        path.write_text(json.dumps(_valid_portfolio(selling_allowed=True)))
        portfolio, _ = load_portfolio(str(path))
        assert portfolio.selling_allowed is True

    def test_common_currency_default(self, mock_price_fetchers, tmp_path):
        path = tmp_path / "p.json"
        path.write_text(json.dumps(_valid_portfolio()))
        portfolio, _ = load_portfolio(str(path))
        assert portfolio.common_currency == "EUR"

    def test_common_currency_override(self, mock_price_fetchers, tmp_path):
        path = tmp_path / "p.json"
        path.write_text(json.dumps(_valid_portfolio(common_currency="SEK")))
        portfolio, _ = load_portfolio(str(path))
        assert portfolio.common_currency == "SEK"

    def test_courtage_profile_propagated(self, mock_price_fetchers, tmp_path):
        path = tmp_path / "p.json"
        path.write_text(
            json.dumps(_valid_portfolio(courtage_profile="nordnet_germany_uk"))
        )
        portfolio, _ = load_portfolio(str(path))
        assert portfolio.courtage_profile == "nordnet_germany_uk"

    def test_asset_courtage_profile_propagated(self, mock_price_fetchers, tmp_path):
        path = tmp_path / "p.json"
        path.write_text(
            json.dumps(
                _valid_portfolio(
                    assets=[
                        {
                            "ticker": "VIR10SEK",
                            "quantity": 10,
                            "target_allocation": 100.0,
                            "nasdaq_nordic_id": "TX4856348",
                            "nasdaq_nordic_asset_class": "ETN/ETC",
                            "courtage_profile": "nordnet_stockholm",
                        }
                    ]
                )
            )
        )
        portfolio, _ = load_portfolio(str(path))
        assert portfolio.assets["VIR10SEK"].courtage_profile == "nordnet_stockholm"

    def test_pending_flag_propagated(self, mock_price_fetchers, tmp_path):
        path = tmp_path / "p.json"
        path.write_text(
            json.dumps(
                _valid_portfolio(
                    assets=[
                        {
                            "ticker": "XBB.TO",
                            "quantity": 10,
                            "target_allocation": 100.0,
                            "pending": True,
                        }
                    ]
                )
            )
        )
        portfolio, _ = load_portfolio(str(path))
        assert portfolio.assets["XBB.TO"].pending is True

    def test_asset_order_preserved(self, mock_price_fetchers, tmp_path):
        """Asset insertion order must match JSON order (optimizer depends on this)."""
        path = tmp_path / "p.json"
        path.write_text(json.dumps(_valid_portfolio()))
        portfolio, target = load_portfolio(str(path))
        assert list(portfolio.assets.keys()) == ["XBB.TO", "XIC.TO"]
        assert list(target.keys()) == ["XBB.TO", "XIC.TO"]

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_portfolio("/nonexistent/path.json")

    def test_validation_error_propagates(self, tmp_path):
        # Single asset with 50% allocation — fails the "must sum to 100" validator
        bad = {
            "name": "bad",
            "assets": [{"ticker": "X", "quantity": 5, "target_allocation": 50.0}],
        }
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(bad))
        with pytest.raises(ValidationError):
            load_portfolio(str(path))

    def test_configures_dedicated_yfinance_cache_before_loading(
        self, mock_price_fetchers, tmp_path, monkeypatch
    ):
        path = tmp_path / "p.json"
        path.write_text(json.dumps(_valid_portfolio()))
        cache_root = tmp_path / "cache"
        monkeypatch.setenv("XDG_CACHE_HOME", str(cache_root))

        with patch("rebalance.loader.yf.set_tz_cache_location") as set_cache_location:
            load_portfolio(str(path))

        expected = cache_root / "rebalance" / "py-yfinance"
        assert expected.is_dir()
        set_cache_location.assert_called_once_with(str(expected))


class TestConversionCostSchema:
    def test_defaults_to_zero(self):
        config = PortfolioConfig.model_validate(_valid_portfolio())
        assert config.conversion_cost == 0.0

    def test_valid_value_accepted(self):
        config = PortfolioConfig.model_validate(_valid_portfolio(conversion_cost=0.25))
        assert config.conversion_cost == pytest.approx(0.25)

    def test_negative_rejected(self):
        with pytest.raises(ValidationError):
            PortfolioConfig.model_validate(_valid_portfolio(conversion_cost=-0.1))

    def test_hundred_or_above_rejected(self):
        with pytest.raises(ValidationError):
            PortfolioConfig.model_validate(_valid_portfolio(conversion_cost=100.0))


class TestConversionCostLoader:
    def test_loaded_as_fraction(self, mock_price_fetchers, tmp_path):
        """conversion_cost=0.25 in JSON should become 0.0025 on the Portfolio object."""
        path = tmp_path / "p.json"
        path.write_text(json.dumps(_valid_portfolio(conversion_cost=0.25)))
        portfolio, _ = load_portfolio(str(path))
        assert portfolio.conversion_cost == pytest.approx(0.0025)

    def test_defaults_to_zero_fraction(self, mock_price_fetchers, tmp_path):
        """Omitting conversion_cost from JSON should leave it at 0.0 on the Portfolio."""
        path = tmp_path / "p.json"
        path.write_text(json.dumps(_valid_portfolio()))
        portfolio, _ = load_portfolio(str(path))
        assert portfolio.conversion_cost == 0.0
