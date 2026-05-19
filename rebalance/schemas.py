from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .courtage import normalize_courtage_profile


class CashConfig(BaseModel):
    amount: float
    currency: str

    @field_validator("currency")
    @classmethod
    def normalise_currency(cls, v: str) -> str:
        v = v.upper()
        if len(v) != 3:
            raise ValueError(f"currency must be a 3-character code, got {v!r}")
        return v


class DebtConfig(BaseModel):
    amount: float = Field(ge=0.0)
    currency: str

    @field_validator("currency")
    @classmethod
    def normalise_currency(cls, v: str) -> str:
        v = v.upper()
        if len(v) != 3:
            raise ValueError(f"currency must be a 3-character code, got {v!r}")
        return v


class LeverageConfig(BaseModel):
    provider: Literal["nordnet"] = "nordnet"
    policy: str = "advanced_portfolio_article"
    margin_debt: list[DebtConfig] = []
    target_leverage: float = Field(default=1.37, ge=1.0)
    drawdown_from_ath_pct: float | None = Field(default=None, ge=0.0)
    drawdown_threshold_pct: float = Field(default=13.7, ge=0.0)
    discount_limit_pct_of_lending_value: float = Field(default=40.0, ge=0.0, le=100.0)
    fallback_weighted_lending_value_pct: float = Field(default=79.0, ge=0.0, le=100.0)
    use_extended_lending_values: bool = True
    approved_security_min_lending_value_pct: float = Field(
        default=70.0, ge=0.0, le=100.0
    )
    etf_interest_discount_max_weight_pct: float = Field(default=20.0, ge=0.0, le=100.0)
    etf_extended_lending_max_weight_pct: float = Field(default=20.0, ge=0.0, le=100.0)
    fund_interest_discount_max_weight_pct: float = Field(default=60.0, ge=0.0, le=100.0)
    fund_extended_lending_max_weight_pct: float = Field(default=60.0, ge=0.0, le=100.0)

    @field_validator("provider", mode="before")
    @classmethod
    def normalise_provider(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("margin_debt", mode="before")
    @classmethod
    def normalise_margin_debt(cls, v):
        if v is None:
            return []
        if isinstance(v, dict):
            return [v]
        return v


class AssetConfig(BaseModel):
    ticker: str
    quantity: float = 0
    fractional: bool = False
    target_allocation: float = Field(ge=0, le=100)
    name: str | None = None
    isin: str | None = None
    volatility: float | None = Field(default=None, ge=1.0, le=100.0)
    lending_value: float | None = Field(default=None, ge=0.0, le=100.0)
    extended_lending_value: float | None = Field(default=None, ge=0.0, le=100.0)
    interest_discount_eligible: bool | None = None
    instrument_type: str | None = None
    nasdaq_nordic_id: str | None = None
    nasdaq_nordic_asset_class: str | None = None

    @field_validator("instrument_type")
    @classmethod
    def normalise_instrument_type(cls, v: str | None) -> str | None:
        if v is None:
            return None
        normalized = v.strip().lower().replace("-", "_").replace(" ", "_")
        return normalized or None

    @model_validator(mode="after")
    def quantity_integer_when_not_fractional(self) -> "AssetConfig":
        if not self.fractional and self.quantity != int(self.quantity):
            raise ValueError(
                f"non-fractional assets require an integer quantity, "
                f"got {self.quantity} for ticker={self.ticker!r}"
            )
        return self

    @model_validator(mode="after")
    def nasdaq_fields_consistent(self) -> "AssetConfig":
        if self.nasdaq_nordic_id is not None and self.nasdaq_nordic_asset_class is None:
            raise ValueError(
                f"nasdaq_nordic_asset_class is required when nasdaq_nordic_id is set "
                f"(ticker={self.ticker!r})"
            )
        return self


class PortfolioConfig(BaseModel):
    name: str
    selling_allowed: bool = False
    common_currency: str = "EUR"
    conversion_cost: float = Field(default=0.0, ge=0.0, lt=100.0)
    courtage_profile: str | None = None
    cash: list[CashConfig] = []
    leverage: LeverageConfig | None = None
    assets: list[AssetConfig] = Field(min_length=1)

    @field_validator("common_currency")
    @classmethod
    def normalise_common_currency(cls, v: str) -> str:
        v = v.upper()
        if len(v) != 3:
            raise ValueError(f"common_currency must be a 3-character code, got {v!r}")
        return v

    @field_validator("courtage_profile")
    @classmethod
    def normalise_courtage_profile(cls, v: str | None) -> str | None:
        return normalize_courtage_profile(v)

    @model_validator(mode="after")
    def allocations_sum_to_100(self) -> "PortfolioConfig":
        total = sum(a.target_allocation for a in self.assets)
        if abs(total - 100.0) > 0.01:
            raise ValueError(
                f"target_allocation values must sum to 100, got {total:.4f}"
            )
        return self
