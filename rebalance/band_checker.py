"""Range-based rebalancing band checker.

Implements the omfångsbaserad (range-based) rebalancing strategy:
each asset is allowed to drift at most ±1 standard deviation from its
target weight before a rebalancing trigger is fired.

Band formulas (example: target 5.5%, volatility 10%):

  upper_band      = target × (1 + vol/100)   = 6.05%   → sell trigger
  lower_band      = target × (1 - vol/100)   = 4.95%   → buy trigger
  upper_tolerance = target × (1 + vol/200)   = 5.775%  → sell-down target
  lower_tolerance = target × (1 - vol/200)   = 5.225%  → buy-up target

Reference: "Avancerad Portfölj - detaljer om förvaltning", Del 1.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from loguru import logger

from .portfolio import Portfolio


@dataclass
class BandStatus:
    """Rebalancing band status for a single asset."""

    ticker: str
    name: str | None
    target_pct: float
    current_pct: float
    volatility_pct: float
    lower_band: float
    upper_band: float
    lower_tolerance: float
    upper_tolerance: float
    triggered: bool
    direction: Literal["above", "below"] | None


def check_bands(
    portfolio: Portfolio,
    target_allocation: Mapping[str, float],
    volatilities: Mapping[str, float | None],
) -> list[BandStatus]:
    """Check whether any asset has drifted outside its rebalancing band.

    Assets without a volatility value are skipped (a warning is logged for
    each one).

    Args:
        portfolio: Portfolio with current prices and quantities.
        target_allocation: Mapping of ticker → target weight (%).
        volatilities: Mapping of ticker → annualised volatility (%), or None.

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
        vol = volatilities.get(ticker)
        if vol is None:
            logger.warning(
                "Skipping band check for {} — no volatility configured", ticker
            )
            continue

        current_pct = current_allocation.get(ticker, 0.0)
        vol_decimal = vol / 100.0

        upper_band = target_pct * (1.0 + vol_decimal)
        lower_band = target_pct * (1.0 - vol_decimal)
        upper_tolerance = target_pct * (1.0 + vol_decimal / 2.0)
        lower_tolerance = target_pct * (1.0 - vol_decimal / 2.0)

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
                volatility_pct=vol,
                lower_band=lower_band,
                upper_band=upper_band,
                lower_tolerance=lower_tolerance,
                upper_tolerance=upper_tolerance,
                triggered=triggered,
                direction=direction,
            )
        )

    return results
