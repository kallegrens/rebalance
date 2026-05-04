from pydantic import BaseModel, Field, field_validator, model_validator


class CashConfig(BaseModel):
    amount: float = Field(ge=0)
    currency: str

    @field_validator("currency")
    @classmethod
    def normalise_currency(cls, v: str) -> str:
        v = v.upper()
        if len(v) != 3:
            raise ValueError(f"currency must be a 3-character code, got {v!r}")
        return v


class AssetConfig(BaseModel):
    ticker: str
    quantity: int = 0
    target_allocation: float = Field(ge=0, le=100)
    name: str | None = None
    isin: str | None = None
    volatility: float | None = None
    nasdaq_nordic_id: str | None = None
    nasdaq_nordic_asset_class: str | None = None

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
    cash: list[CashConfig] = []
    assets: list[AssetConfig] = Field(min_length=1)

    @field_validator("common_currency")
    @classmethod
    def normalise_common_currency(cls, v: str) -> str:
        v = v.upper()
        if len(v) != 3:
            raise ValueError(f"common_currency must be a 3-character code, got {v!r}")
        return v

    @model_validator(mode="after")
    def allocations_sum_to_100(self) -> "PortfolioConfig":
        total = sum(a.target_allocation for a in self.assets)
        if abs(total - 100.0) > 0.01:
            raise ValueError(
                f"target_allocation values must sum to 100, got {total:.4f}"
            )
        return self
