import copy
from dataclasses import dataclass

import cvxpy as cp
import numpy as np

DEFAULT_OBJECTIVE = "relative-l1"
SUPPORTED_OBJECTIVES = (
    "absolute-l1",
    "relative-l1",
    "relative-l2",
    "max-relative-error",
)

_OBJECTIVE_ALIASES = {
    "absolute_l1": "absolute-l1",
    "dollar-l1": "absolute-l1",
    "dollar_l1": "absolute-l1",
    "l1": "relative-l1",
    "relative_l1": "relative-l1",
    "weighted-l1": "relative-l1",
    "weighted_l1": "relative-l1",
    "weighted-relative-l1": "relative-l1",
    "weighted_relative_l1": "relative-l1",
    "l2": "relative-l2",
    "relative_l2": "relative-l2",
    "weighted-l2": "relative-l2",
    "weighted_l2": "relative-l2",
    "weighted-relative-l2": "relative-l2",
    "weighted_relative_l2": "relative-l2",
    "linf": "max-relative-error",
    "l-infinity": "max-relative-error",
    "max-relative": "max-relative-error",
    "max_relative": "max-relative-error",
    "max_relative_error": "max-relative-error",
    "minimax": "max-relative-error",
}

_RELATIVE_L2_BREAKPOINTS = (
    0.0,
    0.005,
    0.01,
    0.02,
    0.05,
    0.1,
    0.2,
    0.5,
    1.0,
    2.0,
    5.0,
    10.0,
)
_MAX_RELATIVE_ERROR_TOLERANCE = 1e-6


@dataclass(frozen=True)
class _OptimizerInputs:
    common_currency: str
    tickers: list[str]
    prices: np.ndarray
    current_values: np.ndarray
    total_cash: float
    portfolio_value: float
    target_weights: np.ndarray
    target_values: np.ndarray


def rebalance(
    portfolio,
    target_allocation,
    sellable_tickers=None,
    band_limits=None,
    locked_tickers=None,
    forced_trades=None,
    objective=DEFAULT_OBJECTIVE,
):
    """
    Rebalances the portfolio using the specified target allocation, the portfolio's current allocation,
    and the available cash.

    Args:
        portfolio (:class:`.Portfolio`): Object of portfolio to rebalance.
        target_allocation (Dict[str, float]): Target asset allocation of the portfolio (in %). The keys of the dictionary are the tickers of the assets.
        sellable_tickers (set[str] | None): When provided, only the listed tickers may be
            sold; all others are buy-only regardless of ``portfolio.selling_allowed``.
            When ``None`` (default), the portfolio-wide ``selling_allowed`` flag governs.
        band_limits (Dict[str, tuple[float, float]] | None): Maps ticker to
            ``(lower_band_pct, upper_band_pct)``. When provided, hard constraints are
            added so the post-trade allocation of each listed asset falls within its band.
        locked_tickers (set[str] | None): Tickers that must not be traded at all
            (share_vars forced to zero). Used to freeze non-triggered assets.
        forced_trades (Dict[str, float] | None): Exact delta units required for
            selected tickers. Used by band rebalancing to force target-zero
            wind-downs.
        objective (str): Optimizer objective. Supported values are
            ``absolute-l1``, ``relative-l1``, ``relative-l2`` and
            ``max-relative-error``.

    Returns:
        (tuple): tuple containing:
            * new_units (Dict[str, int | float]): Units of each asset to buy. Integer
              for non-fractional assets, float for fractional assets.
            * prices (Dict[str, [float, str]]): The keys of the dictionary are the tickers of the assets. Each value of the dictionary is a 2-entry list. The first entry is the price of the asset during the rebalancing computation. The second entry is the currency of the asset.
            * cost (Dict[str, float]): Market value of each asset to buy. The keys of the dictionary are the tickers of the assets.
            * exchange_rates (Dict[str, float]): The keys of the dictionary are currencies. Each value is the exchange rate to USD during the rebalancing computation.
    """

    # Make a new instance of portfolio
    # This is the one that is going to be rebalanced
    # We do not modify the current portfolio
    balanced_portfolio = copy.deepcopy(portfolio)

    # Global sell-everything only when no per-asset override is provided.
    # With sellable_tickers, the optimizer handles selling via per-asset constraints.
    if portfolio.selling_allowed and sellable_tickers is None:
        balanced_portfolio._sell_everything()

    # Convert all cash to one currency
    balanced_portfolio._combine_cash()

    # Solve the selected mixed-integer-compatible objective.
    new_units = rebalance_optimizer(
        balanced_portfolio,
        target_allocation,
        sellable_tickers=sellable_tickers,
        band_limits=band_limits,
        locked_tickers=locked_tickers,
        forced_trades=forced_trades,
        objective=objective,
    )

    # When global sell-everything was used, the optimizer saw zero holdings and
    # returned absolute (target) units.  Convert back to delta units so that
    # buy_asset() (which adds to existing positions) does the right thing.
    # With per-asset sellable_tickers the optimizer already works in delta space.
    if portfolio.selling_allowed and sellable_tickers is None:
        for ticker in new_units:
            original_qty = portfolio.assets[ticker].quantity
            delta = new_units[ticker] - original_qty
            if portfolio.assets[ticker].fractional:
                new_units[ticker] = round(float(delta), 3)
            else:
                new_units[ticker] = int(round(float(delta)))

    # Accumulate cost per currency
    currency_cost = {}
    for ticker, units in new_units.items():
        asset = portfolio.assets[ticker]
        c = asset.cost_of(units)
        if asset.currency not in currency_cost:
            currency_cost[asset.currency] = c
        else:
            currency_cost[asset.currency] += c

    # Since we converted the cash to one common currency for the rebalancing calculation, revert back
    balanced_portfolio.cash = copy.deepcopy(portfolio.cash)

    # Since we might have sold all assets for the rebalancing calculation, revert back
    balanced_portfolio._assets = copy.deepcopy(portfolio.assets)

    # Make necessary currency conversions.
    # When a conversion_cost is set, the broker (e.g. Nordnet) handles FX
    # automatically and charges the fee at execution time; buy_asset() already
    # accounts for this by deducting from the common currency directly.
    conversion_cost = getattr(portfolio, "_conversion_cost", 0.0)
    if conversion_cost > 0:
        exchange_history = []
    else:
        exchange_history = balanced_portfolio._smart_exchange(currency_cost)

    # Buy new units
    prices = {}
    cost = {}
    for ticker, asset in balanced_portfolio.assets.items():
        prices[ticker] = [asset.price, asset.currency]  # price and currency of price
        cost[ticker] = balanced_portfolio.buy_asset(ticker, new_units[ticker])

    return balanced_portfolio, new_units, prices, cost, exchange_history


def normalize_objective(objective: str) -> str:
    """Return canonical objective name or raise a helpful ValueError."""
    normalized = objective.strip().lower().replace("_", "-")
    normalized = _OBJECTIVE_ALIASES.get(normalized, normalized)
    if normalized not in SUPPORTED_OBJECTIVES:
        choices = ", ".join(SUPPORTED_OBJECTIVES)
        raise ValueError(
            f"Unsupported objective '{objective}'. Choose one of: {choices}."
        )
    return normalized


def _optimizer_inputs(portfolio, target_alloc) -> _OptimizerInputs:
    common_currency = portfolio._common_currency
    tickers = list(portfolio.assets.keys())
    prices = np.array(
        [portfolio.assets[ticker].price_in(common_currency) for ticker in tickers]
    )
    current_values = np.array(
        [
            portfolio.assets[ticker].market_value_in(common_currency)
            for ticker in tickers
        ]
    )
    cash_entry = portfolio.cash.get(common_currency)
    total_cash = cash_entry.amount if cash_entry is not None else 0.0
    portfolio_value = float(np.sum(current_values) + total_cash)
    if portfolio_value <= 0:
        raise ValueError(
            "Portfolio total value after cash must be positive to rebalance."
        )

    target_weights = np.array([target_alloc[ticker] / 100.0 for ticker in tickers])
    target_values = target_weights * portfolio_value

    return _OptimizerInputs(
        common_currency=common_currency,
        tickers=tickers,
        prices=prices,
        current_values=current_values,
        total_cash=total_cash,
        portfolio_value=portfolio_value,
        target_weights=target_weights,
        target_values=target_values,
    )


def _share_variables(portfolio, tickers: list[str]) -> list[cp.Variable]:
    variables = []
    for ticker in tickers:
        asset = portfolio.assets[ticker]
        variables.append(
            cp.Variable() if asset.fractional else cp.Variable(integer=True)
        )
    return variables


def _absolute_residuals(inputs: _OptimizerInputs, share_vars):
    residuals = []
    for i in range(len(inputs.tickers)):
        residuals.append(
            float(inputs.target_values[i] - inputs.current_values[i])
            - float(inputs.prices[i]) * share_vars[i]
        )
    return residuals


def _relative_residuals(inputs: _OptimizerInputs, share_vars):
    residuals = []
    for i, raw in enumerate(_absolute_residuals(inputs, share_vars)):
        scale = (
            float(inputs.target_values[i])
            if inputs.target_weights[i] > 0
            else inputs.portfolio_value
        )
        residuals.append(raw / scale)
    return residuals


def _relative_l1_objective(inputs: _OptimizerInputs, share_vars):
    residuals = _relative_residuals(inputs, share_vars)
    return cp.Minimize(cp.norm1(cp.hstack(residuals)))


def _absolute_l1_objective(inputs: _OptimizerInputs, share_vars):
    residuals = _absolute_residuals(inputs, share_vars)
    return cp.Minimize(cp.norm1(cp.hstack(residuals)))


def _max_relative_error_constraints(inputs: _OptimizerInputs, share_vars):
    max_error = cp.Variable(nonneg=True)
    constraints = []
    for residual in _relative_residuals(inputs, share_vars):
        constraints.append(residual <= max_error)
        constraints.append(-residual <= max_error)
    return max_error, constraints


def _max_relative_error_objective(inputs: _OptimizerInputs, share_vars):
    max_error, constraints = _max_relative_error_constraints(inputs, share_vars)
    return cp.Minimize(max_error), constraints


def _relative_l2_objective(inputs: _OptimizerInputs, share_vars):
    """Build a mixed-integer-safe piecewise-linear relative L2 approximation."""
    constraints = []
    losses = []
    for residual in _relative_residuals(inputs, share_vars):
        error = cp.Variable(nonneg=True)
        loss = cp.Variable(nonneg=True)
        constraints.append(error >= residual)
        constraints.append(error >= -residual)
        for left, right in zip(
            _RELATIVE_L2_BREAKPOINTS[:-1],
            _RELATIVE_L2_BREAKPOINTS[1:],
            strict=True,
        ):
            slope = left + right
            intercept = -left * right
            constraints.append(loss >= slope * error + intercept)
        losses.append(loss)
    return cp.Minimize(sum(losses)), constraints


def _objective(inputs: _OptimizerInputs, share_vars, objective: str):
    objective = normalize_objective(objective)
    if objective == "absolute-l1":
        return _absolute_l1_objective(inputs, share_vars), []
    if objective == "relative-l1":
        return _relative_l1_objective(inputs, share_vars), []
    if objective == "relative-l2":
        return _relative_l2_objective(inputs, share_vars)
    if objective == "max-relative-error":
        return _max_relative_error_objective(inputs, share_vars)
    raise AssertionError(f"Unhandled objective: {objective}")


def _budget_constraint(portfolio, inputs: _OptimizerInputs, share_vars):
    spend = sum(
        float(inputs.prices[i]) * share_vars[i] for i in range(len(inputs.tickers))
    )
    fee_term = 0
    conversion_cost = getattr(portfolio, "_conversion_cost", 0.0)
    if conversion_cost > 0:
        for i, ticker in enumerate(inputs.tickers):
            if (
                portfolio.assets[ticker].currency.upper()
                != inputs.common_currency.upper()
            ):
                fee_term = fee_term + conversion_cost * float(
                    inputs.prices[i]
                ) * cp.abs(share_vars[i])
    return spend + fee_term <= inputs.total_cash


def _sell_constraints(portfolio, tickers: list[str], share_vars, sellable_tickers):
    constraints = []
    for i, ticker in enumerate(tickers):
        asset = portfolio.assets[ticker]
        global_sell = portfolio.selling_allowed and sellable_tickers is None
        per_asset_sell = sellable_tickers is not None and ticker in sellable_tickers
        if global_sell or per_asset_sell:
            constraints.append(share_vars[i] >= -asset.quantity)
        else:
            constraints.append(share_vars[i] >= 0)
    return constraints


def _lock_constraints(tickers: list[str], share_vars, locked_tickers) -> list:
    """Force share_vars to zero for tickers that must not be traded."""
    if not locked_tickers:
        return []
    return [
        share_vars[i] == 0
        for i, ticker in enumerate(tickers)
        if ticker in locked_tickers
    ]


def _forced_trade_constraints(tickers: list[str], share_vars, forced_trades) -> list:
    """Force exact delta units for tickers with required trades."""
    if not forced_trades:
        return []
    return [
        share_vars[i] == forced_trades[ticker]
        for i, ticker in enumerate(tickers)
        if ticker in forced_trades
    ]


def _band_constraints(inputs: _OptimizerInputs, share_vars, band_limits):
    constraints = []
    if not band_limits:
        return constraints

    for i, ticker in enumerate(inputs.tickers):
        if ticker not in band_limits:
            continue
        lower_pct, upper_pct = band_limits[ticker]
        new_value = (
            float(inputs.current_values[i]) + float(inputs.prices[i]) * share_vars[i]
        )
        constraints.append(new_value >= lower_pct / 100.0 * inputs.portfolio_value)
        constraints.append(new_value <= upper_pct / 100.0 * inputs.portfolio_value)
    return constraints


def _solve(objective, constraints) -> cp.Problem:
    problem = cp.Problem(objective, constraints)
    problem.solve(solver=cp.HIGHS)

    if problem.status not in (cp.OPTIMAL, cp.OPTIMAL_INACCURATE):
        raise RuntimeError(
            f"MILP solver did not find an optimal solution (status: {problem.status})"
        )
    return problem


def _solve_max_relative_error(
    inputs: _OptimizerInputs, share_vars, constraints
) -> None:
    max_error, max_error_constraints = _max_relative_error_constraints(
        inputs, share_vars
    )
    first_pass_constraints = [*constraints, *max_error_constraints]
    _solve(cp.Minimize(max_error), first_pass_constraints)

    max_error_value = float(max_error.value)
    second_pass_constraints = [
        *first_pass_constraints,
        max_error <= max_error_value + _MAX_RELATIVE_ERROR_TOLERANCE,
    ]
    _solve(_relative_l1_objective(inputs, share_vars), second_pass_constraints)


def _rounded_solution(
    portfolio, tickers: list[str], share_vars
) -> dict[str, int | float]:
    result = {}
    for i, ticker in enumerate(tickers):
        asset = portfolio.assets[ticker]
        raw = float(share_vars[i].value)
        if asset.fractional:
            result[ticker] = round(raw, 3)
        else:
            result[ticker] = int(round(raw))
    return result


def rebalance_optimizer(
    portfolio,
    target_alloc,
    sellable_tickers=None,
    band_limits=None,
    locked_tickers=None,
    forced_trades=None,
    objective=DEFAULT_OBJECTIVE,
):
    """
    Solves a Mixed-Integer Linear Program (MILP) for portfolio rebalancing.

    Variables are in **share space** (units to buy per asset). Integer-constrained
    variables are used for non-fractional assets (ETFs, stocks); continuous
    variables are used for fractional assets (mutual funds).

    The objective can be selected at runtime. All supported objectives are kept
    mixed-integer-compatible for HiGHS. ``relative-l2`` uses a piecewise-linear
    convex approximation of squared relative error because the installed solver
    stack cannot solve true mixed-integer quadratic programs.

    Args:
        portfolio (:class:`.Portfolio`): Portfolio after optional sell-everything and
            cash consolidation into the common currency.
        target_alloc (Dict[str, float]): Target allocation in percent, keyed by ticker.
        objective (str): One of ``absolute-l1``, ``relative-l1``, ``relative-l2``
            or ``max-relative-error``.

    Returns:
        Dict[str, int | float]: Shares to buy per ticker (negative = sell).
    """
    objective = normalize_objective(objective)
    inputs = _optimizer_inputs(portfolio, target_alloc)
    share_vars = _share_variables(portfolio, inputs.tickers)
    constraints = [_budget_constraint(portfolio, inputs, share_vars)]
    constraints.extend(
        _sell_constraints(portfolio, inputs.tickers, share_vars, sellable_tickers)
    )
    constraints.extend(_band_constraints(inputs, share_vars, band_limits))
    constraints.extend(_lock_constraints(inputs.tickers, share_vars, locked_tickers))
    constraints.extend(
        _forced_trade_constraints(inputs.tickers, share_vars, forced_trades)
    )

    try:
        if objective == "max-relative-error":
            _solve_max_relative_error(inputs, share_vars, constraints)
        else:
            objective_expr, objective_constraints = _objective(
                inputs, share_vars, objective
            )
            constraints.extend(objective_constraints)
            _solve(objective_expr, constraints)
    except RuntimeError as exc:
        if inputs.total_cash < 0 and locked_tickers:
            raise RuntimeError(
                "Band rebalance is infeasible: the withdrawal cannot be funded "
                "without trading locked non-triggered assets."
            ) from exc
        raise
    return _rounded_solution(portfolio, inputs.tickers, share_vars)
