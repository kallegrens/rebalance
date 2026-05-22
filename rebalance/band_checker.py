"""Range-based rebalancing band checker.

Implements the omfångsbaserad (range-based) rebalancing strategy using
configured volatility bands around each target weight. By default, assets use
1.5 sigma bands, and callers may override the lower and upper sigma values
separately per asset.

Band formulas (example: target 5.5%, volatility 10%, symmetric 1.5 sigma):

    upper_band      = target × (1 + vol × sigma / 100)     = 6.325%   → sell trigger
    lower_band      = target × (1 - vol × sigma / 100)     = 4.675%   → buy trigger
    upper_tolerance = midpoint(target, upper_band)         = 5.9125%  → sell-down target
    lower_tolerance = midpoint(target, lower_band)         = 5.0875%  → buy-up target
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from loguru import logger

from .portfolio import Portfolio

DEFAULT_BAND_SIGMA = 1.5


@dataclass(frozen=True)
class BandSettings:
    """Band configuration for a single asset.

    ``band_sigma`` is the default symmetric multiplier. ``lower_band_sigma`` and
    ``upper_band_sigma`` override it per side when provided.
    """

    volatility_pct: float
    band_sigma: float = DEFAULT_BAND_SIGMA
    lower_band_sigma: float | None = None
    upper_band_sigma: float | None = None

    @property
    def effective_lower_band_sigma(self) -> float:
        return self.lower_band_sigma or self.band_sigma

    @property
    def effective_upper_band_sigma(self) -> float:
        return self.upper_band_sigma or self.band_sigma


def band_settings_by_ticker(assets) -> dict[str, BandSettings | None]:
    """Build per-ticker :class:`BandSettings` from config-like asset objects."""
    settings: dict[str, BandSettings | None] = {}
    for asset in assets:
        volatility_pct = getattr(asset, "volatility", None)
        if volatility_pct is None:
            settings[asset.ticker] = None
            continue

        lower_band_sigma = getattr(asset, "lower_band_sigma", None)
        upper_band_sigma = getattr(asset, "upper_band_sigma", None)
        settings[asset.ticker] = BandSettings(
            volatility_pct=float(volatility_pct),
            band_sigma=float(getattr(asset, "band_sigma", DEFAULT_BAND_SIGMA)),
            lower_band_sigma=(
                float(lower_band_sigma) if lower_band_sigma is not None else None
            ),
            upper_band_sigma=(
                float(upper_band_sigma) if upper_band_sigma is not None else None
            ),
        )
    return settings


@dataclass
class BandStatus:
    """Rebalancing band status for a single asset."""

    ticker: str
    name: str | None
    target_pct: float
    current_pct: float
    volatility_pct: float
    band_sigma: float
    lower_band_sigma: float
    upper_band_sigma: float
    lower_band: float
    upper_band: float
    lower_tolerance: float
    upper_tolerance: float
    triggered: bool
    direction: Literal["above", "below"] | None


def _resolve_band_settings(
    value: float | BandSettings | None,
) -> BandSettings | None:
    if value is None:
        return None
    if isinstance(value, BandSettings):
        return value
    return BandSettings(volatility_pct=float(value))


def check_bands(
    portfolio: Portfolio,
    target_allocation: Mapping[str, float],
    band_settings: Mapping[str, float | BandSettings | None],
) -> list[BandStatus]:
    """Check whether any asset has drifted outside its rebalancing band.

    Assets without a volatility value are skipped (a warning is logged for each
    one).

    Args:
        portfolio: Portfolio with current prices and quantities.
        target_allocation: Mapping of ticker → target weight (%).
        band_settings: Mapping of ticker → annualised volatility (%) for the
            default 1.5-sigma behavior, or :class:`BandSettings` to override the
            symmetric/asymmetric sigma multipliers per asset.

    Returns:
        List of :class:`BandStatus` for every asset that *has* a volatility
        value, ordered by ticker.  Check ``triggered`` to find actionable ones.
    """
    # Use total portfolio value (assets + cash) as the denominator so that
    # target allocations and current allocations share the same basis.  Cash
    # is part of the portfolio and its presence dilutes asset weights the same
    # way a new investment does.
    common = portfolio._common_currency
    total_value = portfolio.value(common)
    if total_value <= 0:
        raise ValueError("Portfolio total value must be positive to check bands.")
    current_allocation = {
        ticker: asset.market_value_in(common) / total_value * 100.0
        for ticker, asset in portfolio.assets.items()
    }
    results: list[BandStatus] = []

    for ticker, target_pct in sorted(target_allocation.items()):
        settings = _resolve_band_settings(band_settings.get(ticker))
        if settings is None:
            logger.warning(
                "Skipping band check for {} — no volatility configured", ticker
            )
            continue

        current_pct = current_allocation.get(ticker, 0.0)
        vol_decimal = settings.volatility_pct / 100.0
        lower_sigma = settings.effective_lower_band_sigma
        upper_sigma = settings.effective_upper_band_sigma

        upper_band = target_pct * (1.0 + vol_decimal * upper_sigma)
        lower_band = target_pct * (1.0 - vol_decimal * lower_sigma)
        upper_tolerance = (target_pct + upper_band) / 2.0
        lower_tolerance = (target_pct + lower_band) / 2.0

        if target_pct == 0.0:
            triggered = current_pct > 1e-9
            direction: Literal["above", "below"] | None = "above" if triggered else None
        elif current_pct >= upper_band:
            triggered = True
            direction = "above"
        elif current_pct <= lower_band:
            triggered = True
            direction = "below"
        else:
            triggered = False
            direction = None

        asset = portfolio.assets.get(ticker)
        results.append(
            BandStatus(
                ticker=ticker,
                name=asset.name if asset is not None else None,
                target_pct=target_pct,
                current_pct=current_pct,
                volatility_pct=settings.volatility_pct,
                band_sigma=settings.band_sigma,
                lower_band_sigma=lower_sigma,
                upper_band_sigma=upper_sigma,
                lower_band=lower_band,
                upper_band=upper_band,
                lower_tolerance=lower_tolerance,
                upper_tolerance=upper_tolerance,
                triggered=triggered,
                direction=direction,
            )
        )

    return results
