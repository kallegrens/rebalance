"""Helpers for rebalance target planning.

Normal rebalancing and band-aware rebalancing now share the same plan shape.
Band mode adds constraints and display metadata; full mode uses the same plan
contract with empty constraints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RebalancePlan:
    """Inputs derived for the optimizer and optional renderers."""

    effective_targets: dict[str, float]
    sellable_tickers: set[str] | None
    locked_tickers: set[str]
    forced_trades: dict[str, float]
    band_limits: dict[str, tuple[float, float]] = field(default_factory=dict)
    diluted_allocation: dict[str, float] = field(default_factory=dict)
    assets_only_allocation: dict[str, float] = field(default_factory=dict)
    status_by_ticker: dict[str, Any] = field(default_factory=dict)


BandRebalancePlan = RebalancePlan


def reorder_target_allocation(
    portfolio, target_allocation: dict[str, float]
) -> dict[str, float]:
    """Return targets in portfolio asset order and validate ticker coverage."""
    ordered: dict[str, float] = {}
    try:
        for ticker in portfolio.assets:
            ordered[ticker] = target_allocation[ticker]
    except KeyError as err:
        raise ValueError(
            "target_allocation not compatible with the assets of the portfolio."
        ) from err
    return ordered


def build_rebalance_plan(
    portfolio,
    target_allocation: dict[str, float],
) -> RebalancePlan:
    """Build an unconstrained plan for a normal full rebalance."""
    ordered_targets = reorder_target_allocation(portfolio, target_allocation)
    return RebalancePlan(
        effective_targets=ordered_targets,
        sellable_tickers=None,
        locked_tickers=set(),
        forced_trades={},
    )


def status_lookup(statuses) -> dict[str, Any]:
    """Return band statuses keyed by ticker."""
    return {status.ticker: status for status in statuses}


def allocation_snapshots(portfolio) -> tuple[dict[str, float], dict[str, float]]:
    """Return diluted and assets-only allocation snapshots before trading."""
    common = portfolio._common_currency
    total_value = portfolio.value(common)
    if total_value <= 0:
        raise ValueError(
            "Portfolio total value after cash must be positive to rebalance."
        )

    diluted = {
        ticker: asset.market_value_in(common) / total_value * 100.0
        for ticker, asset in portfolio.assets.items()
    }

    assets_total = max(1.0, portfolio.market_value(common))
    assets_only = {
        ticker: asset.market_value_in(common) / assets_total * 100.0
        for ticker, asset in portfolio.assets.items()
    }

    return diluted, assets_only


def cash_inclusive_allocation(portfolio) -> dict[str, float]:
    """Return asset weights using total portfolio value, including cash."""
    diluted, _ = allocation_snapshots(portfolio)
    return diluted


def initial_effective_targets(
    assets,
    target_allocation: dict[str, float],
    statuses: dict[str, Any],
    diluted_allocation: dict[str, float],
) -> dict[str, float]:
    """Choose the first target for each asset before residual allocation."""
    targets: dict[str, float] = {}
    for ticker, asset in assets.items():
        status = statuses.get(ticker)
        target = target_allocation[ticker]

        if target == 0.0:
            targets[ticker] = 0.0
        elif asset.quantity == 0:
            targets[ticker] = target
        elif status is not None and status.direction == "above":
            targets[ticker] = status.upper_tolerance
        elif status is not None and status.direction == "below":
            targets[ticker] = status.lower_tolerance
        else:
            targets[ticker] = diluted_allocation[ticker]

    return targets


def _reduce_targets(
    targets: dict[str, float],
    tickers: list[str],
    weights: dict[str, float],
    amount: float,
    floors: dict[str, float] | None = None,
) -> dict[str, float]:
    """Reduce targets by amount without pushing selected targets below floors."""
    floors = floors or {}
    result = dict(targets)
    remaining = {
        ticker for ticker in tickers if result[ticker] > floors.get(ticker, 0.0) + 1e-9
    }
    remaining_amount = amount

    while remaining_amount > 1e-9 and remaining:
        total_weight = sum(weights.get(ticker, 0.0) for ticker in remaining)
        if total_weight <= 1e-9:
            total_weight = float(len(remaining))
            active_weights = {ticker: 1.0 for ticker in remaining}
        else:
            active_weights = weights

        applied_total = 0.0
        for ticker in list(remaining):
            reduction = (
                remaining_amount * active_weights.get(ticker, 0.0) / total_weight
            )
            floor = floors.get(ticker, 0.0)
            applied = min(reduction, result[ticker] - floor)
            result[ticker] -= applied
            applied_total += applied
            if result[ticker] <= floor + 1e-9:
                result[ticker] = floor
                remaining.remove(ticker)

        if applied_total <= 1e-9:
            break
        remaining_amount -= applied_total

    return result


def _target_excess(targets: dict[str, float]) -> float:
    return sum(targets.values()) - 100.0


def forced_liquidation_trades(assets, target_allocation: dict[str, float]):
    """Return exact trades required to wind target-zero positions down."""
    return {
        ticker: -asset.quantity
        for ticker, asset in assets.items()
        if target_allocation[ticker] == 0.0 and asset.quantity > 0
    }


def _increase_targets(
    targets: dict[str, float],
    tickers: list[str],
    weights: dict[str, float],
    amount: float,
    ceilings: dict[str, float],
) -> dict[str, float]:
    """Increase targets by amount without pushing selected targets past ceilings."""
    result = dict(targets)
    remaining = {
        ticker for ticker in tickers if result[ticker] < ceilings[ticker] - 1e-9
    }
    remaining_amount = amount

    while remaining_amount > 1e-9 and remaining:
        total_weight = sum(weights.get(ticker, 0.0) for ticker in remaining)
        if total_weight <= 1e-9:
            total_weight = float(len(remaining))
            active_weights = {ticker: 1.0 for ticker in remaining}
        else:
            active_weights = weights

        applied_total = 0.0
        for ticker in list(remaining):
            addition = remaining_amount * active_weights.get(ticker, 0.0) / total_weight
            applied = min(addition, ceilings[ticker] - result[ticker])
            result[ticker] += applied
            applied_total += applied
            if result[ticker] >= ceilings[ticker] - 1e-9:
                result[ticker] = ceilings[ticker]
                remaining.remove(ticker)

        if applied_total <= 1e-9:
            break
        remaining_amount -= applied_total

    return result


def _residual_target_floors(
    tickers: list[str], statuses: dict[str, Any]
) -> dict[str, float]:
    """Return lower feasible targets for residual allocation recipients."""
    floors = {}
    for ticker in tickers:
        status = statuses.get(ticker)
        floors[ticker] = max(0.0, status.lower_band) if status is not None else 0.0
    return floors


def _residual_target_ceilings(
    tickers: list[str], statuses: dict[str, Any]
) -> dict[str, float]:
    """Return upper feasible targets for residual allocation recipients."""
    ceilings = {}
    for ticker in tickers:
        status = statuses.get(ticker)
        ceilings[ticker] = status.upper_band if status is not None else 100.0
    return ceilings


def allocate_residual_to_tradable_targets(
    effective_targets: dict[str, float],
    target_allocation: dict[str, float],
    statuses: dict[str, Any],
    locked_tickers: set[str] | None = None,
) -> dict[str, float]:
    """Allocate target residual only across tradable positive-target assets."""
    locked_tickers = locked_tickers or set()
    eligible = [
        ticker
        for ticker in effective_targets
        if target_allocation[ticker] > 0.0 and ticker not in locked_tickers
    ]
    residual = 100.0 - sum(effective_targets.values())
    if abs(residual) <= 1e-9 or not eligible:
        if residual < -1e-9:
            raise ValueError(
                "Band rebalance plan is infeasible: frozen assets and required "
                "targets exceed 100% with no tradable assets to reduce."
            )
        return dict(effective_targets)

    weights = {ticker: target_allocation[ticker] for ticker in eligible}
    if residual > 0:
        ceilings = _residual_target_ceilings(eligible, statuses)
        return _increase_targets(
            effective_targets, eligible, weights, residual, ceilings
        )

    floors = _residual_target_floors(eligible, statuses)
    reduced = _reduce_targets(
        effective_targets, eligible, weights, -residual, floors=floors
    )
    if _target_excess(reduced) > 1e-6:
        raise ValueError(
            "Band rebalance plan is infeasible: withdrawal or locked allocations "
            "would require trading frozen non-triggered assets."
        )
    return reduced


def build_sellable_tickers(
    assets,
    target_allocation: dict[str, float],
    statuses: dict[str, Any],
    diluted_allocation: dict[str, float],
    effective_targets: dict[str, float],
    locked_tickers: set[str] | None = None,
) -> set[str]:
    """Return tickers that may be sold during band-aware optimization."""
    locked_tickers = locked_tickers or set()
    sellable = {
        ticker for ticker, status in statuses.items() if status.direction == "above"
    }
    sellable.update(
        ticker
        for ticker, asset in assets.items()
        if target_allocation[ticker] == 0.0 and asset.quantity > 0
    )
    sellable.update(
        ticker
        for ticker in assets
        if diluted_allocation.get(ticker, 0.0) > effective_targets.get(ticker, 0.0)
    )
    return sellable - locked_tickers


def build_band_limits(
    target_allocation: dict[str, float], statuses: dict[str, Any]
) -> dict[str, tuple[float, float]]:
    """Return hard optimizer band constraints for positive-target assets."""
    return {
        ticker: (status.lower_band, status.upper_band)
        for ticker, status in statuses.items()
        if target_allocation.get(ticker, 0.0) > 0.0
    }


def build_locked_tickers(
    assets,
    target_allocation: dict[str, float],
    statuses: dict[str, Any],
    lock_non_triggered: bool,
) -> set[str]:
    """Return tickers that must not be traded when ``lock_non_triggered`` is set.

    A ticker is locked when:
    * ``lock_non_triggered`` is True, AND
    * the asset is not triggered (direction is None) AND has a positive target.
    """
    if not lock_non_triggered:
        return set()
    return {
        ticker
        for ticker, target in target_allocation.items()
        if target > 0.0
        and assets[ticker].quantity > 0
        and (statuses.get(ticker) is None or statuses[ticker].direction is None)
    }


def build_band_rebalance_plan(
    portfolio,
    target_allocation: dict[str, float],
    statuses,
    lock_non_triggered: bool = True,
) -> RebalancePlan:
    """Build all derived inputs for a band-aware rebalance."""
    target_allocation = reorder_target_allocation(portfolio, target_allocation)
    statuses_by_ticker = status_lookup(statuses)
    diluted_allocation, assets_only_allocation = allocation_snapshots(portfolio)
    locked_tickers = build_locked_tickers(
        portfolio.assets, target_allocation, statuses_by_ticker, lock_non_triggered
    )
    forced_trades = forced_liquidation_trades(portfolio.assets, target_allocation)
    effective_targets = initial_effective_targets(
        portfolio.assets, target_allocation, statuses_by_ticker, diluted_allocation
    )
    effective_targets = allocate_residual_to_tradable_targets(
        effective_targets,
        target_allocation,
        statuses_by_ticker,
        locked_tickers,
    )
    sellable_tickers = build_sellable_tickers(
        portfolio.assets,
        target_allocation,
        statuses_by_ticker,
        diluted_allocation,
        effective_targets,
        locked_tickers,
    )
    band_limits = build_band_limits(target_allocation, statuses_by_ticker)

    return RebalancePlan(
        effective_targets=effective_targets,
        sellable_tickers=sellable_tickers,
        locked_tickers=locked_tickers,
        forced_trades=forced_trades,
        band_limits=band_limits,
        diluted_allocation=diluted_allocation,
        assets_only_allocation=assets_only_allocation,
        status_by_ticker=statuses_by_ticker,
    )
