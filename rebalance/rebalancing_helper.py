import copy

import cvxpy as cp
import numpy as np


def rebalance(portfolio, target_allocation):
    """
    Rebalances the portfolio using the specified target allocation, the portfolio's current allocation,
    and the available cash.

    Args:
        portfolio (:class:`.Portfolio`): Object of portfolio to rebalance.
        target_allocation (Dict[str, float]): Target asset allocation of the portfolio (in %). The keys of the dictionary are the tickers of the assets.

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

    # If selling is allowed, "sell everything" in new portfolio
    if portfolio.selling_allowed:
        balanced_portfolio._sell_everything()

    # Convert all cash to one currency
    balanced_portfolio._combine_cash()

    # Solve MIQP
    new_units = rebalance_optimizer(balanced_portfolio, target_allocation)

    # When selling is allowed, _sell_everything() was called on balanced_portfolio,
    # so the optimizer sees zero holdings and returns absolute (target) units.
    # Convert to delta units relative to the original holdings so that buy_asset()
    # (which adds to existing positions) does the right thing.
    if portfolio.selling_allowed:
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

    # Make necessary currency conversions
    exchange_history = balanced_portfolio._smart_exchange(currency_cost)

    # Buy new units
    prices = {}
    cost = {}
    for ticker, asset in balanced_portfolio.assets.items():
        prices[ticker] = [asset.price, asset.currency]  # price and currency of price
        cost[ticker] = balanced_portfolio.buy_asset(ticker, new_units[ticker])

    return balanced_portfolio, new_units, prices, cost, exchange_history


def rebalance_optimizer(portfolio, target_alloc):
    """
    Solves a Mixed-Integer Quadratic Program (MIQP) for portfolio rebalancing.

    Variables are in **share space** (units to buy per asset). Integer-constrained
    variables are used for non-fractional assets (ETFs, stocks); continuous
    variables are used for fractional assets (mutual funds).

    The objective minimises the squared deviation from the target allocation,
    using the post-investment total value ``V = V_curr + cash`` as a fixed
    constant. This makes the problem a clean QP/MIQP with no nonlinear denominator.

    Args:
        portfolio (:class:`.Portfolio`): Portfolio after optional sell-everything and
            cash consolidation into the common currency.
        target_alloc (Dict[str, float]): Target allocation in percent, keyed by ticker.

    Returns:
        Dict[str, int | float]: Shares to buy per ticker (negative = sell).
    """
    common_currency = portfolio._common_currency
    tickers = list(portfolio.assets.keys())
    n = len(tickers)

    # Prices and current market values in common currency
    prices = np.array([portfolio.assets[t].price_in(common_currency) for t in tickers])
    current_values = np.array(
        [portfolio.assets[t].market_value_in(common_currency) for t in tickers]
    )
    total_cash = portfolio.cash[common_currency].amount

    # Total portfolio value after investing all available cash (fixed constant).
    # Using this as a denominator makes the objective purely linear in the share variables.
    portfolio_value = float(np.sum(current_values) + total_cash)

    # Target market value per asset
    target_weights = np.array([target_alloc[t] / 100.0 for t in tickers])
    target_values = target_weights * portfolio_value  # shape (n,)

    # Build one decision variable per asset.
    # Integer-constrained for ETFs/stocks; continuous for mutual funds.
    share_vars = []
    for ticker in tickers:
        asset = portfolio.assets[ticker]
        if asset.fractional:
            share_vars.append(cp.Variable())
        else:
            share_vars.append(cp.Variable(integer=True))

    # Objective: minimise sum of absolute dollar deviations from target (L1 norm).
    # This compiles to a Mixed-Integer Linear Program (MILP) which HiGHS solves natively.
    # residual_i = target_value_i - current_value_i - price_i * shares_i
    residuals = [
        float(target_values[i] - current_values[i]) - float(prices[i]) * share_vars[i]
        for i in range(n)
    ]
    objective = cp.Minimize(cp.norm1(cp.hstack(residuals)))

    # Budget constraint: total spend ≤ available cash
    spend = sum(float(prices[i]) * share_vars[i] for i in range(n))
    constraints = [spend <= total_cash]

    # Per-asset bounds
    for i, ticker in enumerate(tickers):
        asset = portfolio.assets[ticker]
        if portfolio.selling_allowed:
            # Can sell at most what we hold; can't go short
            constraints.append(share_vars[i] >= -asset.quantity)
        else:
            # Buy-only: non-negative shares
            constraints.append(share_vars[i] >= 0)

    prob = cp.Problem(objective, constraints)
    prob.solve(solver=cp.HIGHS)

    if prob.status not in (cp.OPTIMAL, cp.OPTIMAL_INACCURATE):
        raise RuntimeError(
            f"MIQP solver did not find an optimal solution (status: {prob.status})"
        )

    result = {}
    for i, ticker in enumerate(tickers):
        asset = portfolio.assets[ticker]
        raw = float(share_vars[i].value)
        if asset.fractional:
            result[ticker] = round(raw, 3)
        else:
            result[ticker] = int(round(raw))

    return result
