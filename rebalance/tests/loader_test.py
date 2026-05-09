import json

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

    def test_fractional_defaults_to_false(self):
        config = PortfolioConfig.model_validate(_valid_portfolio())
        assert config.assets[0].fractional is False

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
                }
            ]
        )
        config = PortfolioConfig.model_validate(data)
        assert config.assets[0].isin == "CA46432F1099"
        assert config.assets[0].volatility == pytest.approx(4.2)


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
        with pytest.raises(ValidationError) as exc_info:
            PortfolioConfig.model_validate(data)
        assert "amount" in str(exc_info.value)

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
