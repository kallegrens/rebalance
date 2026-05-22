from __future__ import annotations

from typing import Any

from rebalance.money import Cash
from rebalance.schemas import AssetConfig, LeverageConfig, PortfolioConfig


def _amount_in_common_currency(
    amount: float, currency: str, common_currency: str
) -> float:
    return Cash(amount, currency).amount_in(common_currency)


def _cash_report(portfolio) -> list[dict[str, float | str]]:
    return [
        {"amount": cash.amount, "currency": cash.currency}
        for cash in portfolio.cash.values()
    ]


def _asset_value(asset, common_currency: str) -> float:
    return float(asset.market_value_in(common_currency))


def _asset_configs_by_ticker(config: PortfolioConfig) -> dict[str, AssetConfig]:
    return {asset.ticker: asset for asset in config.assets}


def _is_approved_security(asset_config: AssetConfig | None) -> bool:
    return (
        asset_config is not None
        and asset_config.interest_discount_eligible is not False
        and asset_config.lending_value is not None
        and asset_config.lending_value > 0.0
    )


def _counts_toward_discount_bracket(
    asset_config: AssetConfig | None,
    leverage: LeverageConfig,
) -> bool:
    if asset_config is None:
        return False
    if not _is_approved_security(asset_config):
        return False
    lending_value = asset_config.lending_value
    if lending_value is None:
        return False
    return lending_value >= leverage.approved_security_min_lending_value_pct


def _approved_holdings_weight_pct(
    value: float,
    approved_holdings_value: float,
    *,
    is_approved_security: bool,
) -> float | None:
    if not is_approved_security or approved_holdings_value <= 0.0:
        return None
    return value / approved_holdings_value * 100.0


def _margin_debt_value(leverage: LeverageConfig, common_currency: str) -> float:
    return sum(
        _amount_in_common_currency(debt.amount, debt.currency, common_currency)
        for debt in leverage.margin_debt
    )


def _position_warnings(
    asset_config: AssetConfig | None,
    approved_holdings_weight_pct: float | None,
    leverage: LeverageConfig,
) -> list[str]:
    warnings: list[str] = []
    if asset_config is None:
        return ["missing_asset_config"]

    if asset_config.lending_value is None:
        warnings.append("missing_lending_value")
    if (
        leverage.use_extended_lending_values
        and asset_config.extended_lending_value is None
    ):
        warnings.append("missing_extended_lending_value")
    if asset_config.interest_discount_eligible is False:
        warnings.append("not_interest_discount_eligible")

    instrument_type = asset_config.instrument_type
    if instrument_type is None:
        warnings.append("missing_instrument_type")
        return warnings

    if approved_holdings_weight_pct is None:
        return warnings

    if instrument_type in {"etf", "stock", "equity", "share"}:
        if approved_holdings_weight_pct > leverage.etf_interest_discount_max_weight_pct:
            warnings.append("position_above_etf_interest_discount_cap")
    elif instrument_type == "fund":
        if (
            approved_holdings_weight_pct
            > leverage.fund_interest_discount_max_weight_pct
        ):
            warnings.append("position_above_fund_interest_discount_cap")

    return warnings


def _warnings_disqualify_extended_lending(warnings: list[str]) -> bool:
    return any(
        warning.startswith("position_above_")
        or warning == "not_interest_discount_eligible"
        for warning in warnings
    )


def _applied_lending_value_pct(
    asset_config: AssetConfig | None,
    leverage: LeverageConfig,
    *,
    use_extended_values: bool,
) -> tuple[float, str]:
    fallback = leverage.fallback_weighted_lending_value_pct
    if asset_config is None:
        return fallback, "fallback"

    if use_extended_values and asset_config.extended_lending_value is not None:
        return asset_config.extended_lending_value, "extended"
    if asset_config.lending_value is not None:
        return asset_config.lending_value, "ordinary"
    if asset_config.extended_lending_value is not None:
        return asset_config.extended_lending_value, "extended_without_composition"
    return fallback, "fallback"


def _composition_evaluation(
    position_reports: list[dict[str, Any]], composition_qualified: bool
) -> str:
    warnings = {
        warning for position in position_reports for warning in position["warnings"]
    }
    if not composition_qualified:
        return "failed"
    if "missing_instrument_type" in warnings:
        return "assumed"
    return "verified"


def _action_report(
    leverage: LeverageConfig,
    *,
    margin_debt: float,
    target_debt: float,
    bracket_credit_limit: float,
) -> tuple[str, float, str]:
    tolerance = 1.0
    debt_delta_to_target = target_debt - margin_debt
    strict_bracket_delta = bracket_credit_limit - margin_debt
    drawdown = leverage.drawdown_from_ath_pct
    threshold = leverage.drawdown_threshold_pct

    if margin_debt > bracket_credit_limit + tolerance:
        if drawdown is not None and drawdown > threshold:
            return (
                "opportunistic_zone",
                0.0,
                "Debt is above the current tier-1 bracket, but the article treats drawdowns above the Nordnet threshold as an opportunistic/hold zone.",
            )
        return (
            "decrease_to_bracket",
            strict_bracket_delta,
            "Debt is above the current tier-1 bracket ceiling.",
        )

    if debt_delta_to_target > tolerance:
        return (
            "increase",
            min(debt_delta_to_target, max(0.0, strict_bracket_delta)),
            "Current leverage is below the article target.",
        )

    if debt_delta_to_target < -tolerance:
        if drawdown is not None and drawdown > 0.0:
            return (
                "hold",
                0.0,
                "Current leverage is above target during a drawdown; the article says to hold rather than add capital to reduce leverage.",
            )
        return (
            "decrease",
            debt_delta_to_target,
            "Current leverage is above the article target outside a configured drawdown.",
        )

    return "hold", 0.0, "Current leverage is at the article target."


def build_leverage_report(
    portfolio,
    config: PortfolioConfig,
    *,
    basis: str = "current",
    margin_debt_delta: float = 0.0,
) -> dict[str, Any]:
    """Return Nordnet leverage diagnostics for the current portfolio state."""
    common_currency = portfolio.common_currency
    leverage = config.leverage
    if leverage is None:
        return {
            "configured": False,
            "provider": None,
            "basis": basis,
            "common_currency": common_currency,
            "action": "not_configured",
            "reason": "No leverage configuration is present in the portfolio JSON.",
        }

    assets_value = float(portfolio.market_value(common_currency))
    cash_value = float(portfolio.cash_value(common_currency))
    gross_portfolio_value = assets_value + cash_value
    configured_margin_debt = _margin_debt_value(leverage, common_currency)
    margin_debt = max(0.0, configured_margin_debt + margin_debt_delta)
    equity = gross_portfolio_value - margin_debt
    if gross_portfolio_value <= 0.0 or equity <= 0.0:
        return {
            "configured": True,
            "provider": leverage.provider,
            "policy": leverage.policy,
            "basis": basis,
            "common_currency": common_currency,
            "gross_portfolio_value": gross_portfolio_value,
            "margin_debt": margin_debt,
            "configured_margin_debt": configured_margin_debt,
            "margin_debt_delta": margin_debt - configured_margin_debt,
            "debt_basis": "projected"
            if abs(margin_debt_delta) > 1e-9
            else "configured",
            "equity": equity,
            "action": "invalid",
            "reason": "Gross portfolio value and equity must be positive to analyze leverage.",
        }

    asset_configs = _asset_configs_by_ticker(config)
    held_positions: list[tuple[str, Any, AssetConfig | None, float, bool]] = []
    approved_holdings_value = 0.0

    for ticker, asset in portfolio.assets.items():
        value = _asset_value(asset, common_currency)
        if value <= 1e-9:
            continue
        asset_config = asset_configs.get(ticker)
        is_approved_security = _is_approved_security(asset_config)
        if is_approved_security:
            approved_holdings_value += value
        held_positions.append(
            (ticker, asset, asset_config, value, is_approved_security)
        )

    position_reports: list[dict[str, Any]] = []
    total_warnings: list[str] = []
    first_pass_warnings: dict[str, list[str]] = {}

    for ticker, _asset, asset_config, value, is_approved_security in held_positions:
        approved_weight_pct = _approved_holdings_weight_pct(
            value,
            approved_holdings_value,
            is_approved_security=is_approved_security,
        )
        warnings = _position_warnings(asset_config, approved_weight_pct, leverage)
        first_pass_warnings[ticker] = warnings
        total_warnings.extend(warnings)

    if approved_holdings_value <= 0.0:
        total_warnings.append("no_approved_holdings")

    composition_qualified = approved_holdings_value > 0.0 and not any(
        _warnings_disqualify_extended_lending(warnings)
        for warnings in first_pass_warnings.values()
    )
    use_extended_values = leverage.use_extended_lending_values and composition_qualified

    portfolio_lending_value = 0.0
    plus_eligible_lending_value = 0.0
    lending_basis_counts: dict[str, int] = {}
    for ticker, asset, asset_config, value, is_approved_security in held_positions:
        approved_weight_pct = _approved_holdings_weight_pct(
            value,
            approved_holdings_value,
            is_approved_security=is_approved_security,
        )
        applied_lending_value_pct, basis_name = _applied_lending_value_pct(
            asset_config,
            leverage,
            use_extended_values=use_extended_values,
        )
        lending_basis_counts[basis_name] = lending_basis_counts.get(basis_name, 0) + 1
        portfolio_lending_value += value * applied_lending_value_pct / 100.0
        counts_toward_bracket_limit = _counts_toward_discount_bracket(
            asset_config,
            leverage,
        )
        if counts_toward_bracket_limit:
            plus_eligible_lending_value += value * applied_lending_value_pct / 100.0
        warnings = first_pass_warnings.get(ticker, [])
        position_reports.append(
            {
                "ticker": ticker,
                "name": getattr(asset, "name", None),
                "isin": asset_config.isin if asset_config is not None else None,
                "value": value,
                "value_currency": common_currency,
                "portfolio_weight_pct": value / gross_portfolio_value * 100.0,
                "approved_holdings_weight_pct": approved_weight_pct,
                "lending_value": (
                    asset_config.lending_value if asset_config is not None else None
                ),
                "extended_lending_value": (
                    asset_config.extended_lending_value
                    if asset_config is not None
                    else None
                ),
                "applied_lending_value": applied_lending_value_pct,
                "applied_lending_value_basis": basis_name,
                "instrument_type": (
                    asset_config.instrument_type if asset_config is not None else None
                ),
                "counts_toward_bracket_limit": counts_toward_bracket_limit,
                "warnings": warnings,
            }
        )

    weighted_lending_value_pct = (
        portfolio_lending_value / gross_portfolio_value * 100.0
        if gross_portfolio_value > 0.0
        else 0.0
    )
    bracket_credit_limit = (
        plus_eligible_lending_value
        * leverage.discount_limit_pct_of_lending_value
        / 100.0
    )
    bracket_max_borrowing_ratio = bracket_credit_limit / gross_portfolio_value
    bracket_max_leverage = (
        1.0 / (1.0 - bracket_max_borrowing_ratio)
        if bracket_max_borrowing_ratio < 1.0
        else None
    )
    current_leverage = gross_portfolio_value / equity
    current_borrowing_ratio = margin_debt / gross_portfolio_value
    target_debt = (leverage.target_leverage - 1.0) * equity
    debt_delta_to_target = target_debt - margin_debt
    headroom_to_bracket = bracket_credit_limit - margin_debt
    action, recommended_debt_delta, reason = _action_report(
        leverage,
        margin_debt=margin_debt,
        target_debt=target_debt,
        bracket_credit_limit=bracket_credit_limit,
    )

    if not lending_basis_counts:
        applied_basis = "none"
    elif len(lending_basis_counts) == 1:
        applied_basis = next(iter(lending_basis_counts))
    else:
        applied_basis = "mixed"

    return {
        "configured": True,
        "provider": leverage.provider,
        "policy": leverage.policy,
        "basis": basis,
        "common_currency": common_currency,
        "assets_value": assets_value,
        "cash_value": cash_value,
        "cash": _cash_report(portfolio),
        "gross_portfolio_value": gross_portfolio_value,
        "margin_debt": margin_debt,
        "configured_margin_debt": configured_margin_debt,
        "margin_debt_delta": margin_debt - configured_margin_debt,
        "debt_basis": "projected" if abs(margin_debt_delta) > 1e-9 else "configured",
        "equity": equity,
        "current_leverage": current_leverage,
        "target_leverage": leverage.target_leverage,
        "target_debt": target_debt,
        "debt_delta_to_target": debt_delta_to_target,
        "recommended_debt_delta": recommended_debt_delta,
        "current_borrowing_ratio_pct": current_borrowing_ratio * 100.0,
        "approved_holdings_value": approved_holdings_value,
        "portfolio_lending_value": portfolio_lending_value,
        "plus_eligible_lending_value": plus_eligible_lending_value,
        "weighted_lending_value_pct": weighted_lending_value_pct,
        "applied_lending_value_basis": applied_basis,
        "bracket_credit_limit": bracket_credit_limit,
        "bracket_max_borrowing_ratio_pct": bracket_max_borrowing_ratio * 100.0,
        "bracket_max_leverage": bracket_max_leverage,
        "headroom_to_bracket": headroom_to_bracket,
        "strict_bracket_delta": headroom_to_bracket,
        "drawdown_from_ath_pct": leverage.drawdown_from_ath_pct,
        "drawdown_threshold_pct": leverage.drawdown_threshold_pct,
        "composition_qualified": composition_qualified,
        "composition_evaluation": _composition_evaluation(
            position_reports, composition_qualified
        ),
        "action": action,
        "reason": reason,
        "positions": position_reports,
        "warnings": sorted(set(total_warnings)),
    }


def build_financing_adjustment(
    leverage_report: dict[str, Any],
    *,
    tolerance: float = 1.0,
) -> dict[str, Any]:
    """Translate a leverage report into a trade-plan cash-flow adjustment."""
    currency = leverage_report.get("common_currency")
    source_action = leverage_report.get("action", "not_configured")
    recommended_delta = float(leverage_report.get("recommended_debt_delta", 0.0) or 0.0)

    adjustment: dict[str, Any] = {
        "type": "nordnet_credit",
        "label": "Nordnet credit",
        "action": "none",
        "amount": 0.0,
        "currency": currency,
        "recommended_debt_delta": recommended_delta,
        "applied_cash_delta": 0.0,
        "margin_debt_delta": 0.0,
        "source_action": source_action,
        "included_in_trade_plan": False,
        "reason": "No leverage financing adjustment is needed for this plan.",
    }

    if not leverage_report.get("configured"):
        adjustment["reason"] = "No leverage configuration is present."
        return adjustment
    if source_action in {"invalid", "hold", "opportunistic_zone", "not_configured"}:
        adjustment["reason"] = leverage_report.get("reason", adjustment["reason"])
        return adjustment
    if abs(recommended_delta) <= tolerance:
        adjustment["reason"] = (
            "Recommended leverage debt delta is below the planning tolerance."
        )
        return adjustment

    configured_margin_debt = float(
        leverage_report.get(
            "configured_margin_debt", leverage_report.get("margin_debt", 0.0)
        )
        or 0.0
    )
    currency_label = currency or "cash"
    if source_action == "increase" and recommended_delta > 0.0:
        applied_delta = recommended_delta
        adjustment.update(
            {
                "action": "draw",
                "amount": abs(applied_delta),
                "applied_cash_delta": applied_delta,
                "margin_debt_delta": applied_delta,
                "included_in_trade_plan": True,
                "reason": f"Draw Nordnet credit and add it to available {currency_label} before rebalancing.",
            }
        )
        return adjustment

    if source_action in {"decrease", "decrease_to_bracket"} and recommended_delta < 0.0:
        applied_delta = max(recommended_delta, -configured_margin_debt)
        adjustment.update(
            {
                "action": "repay",
                "amount": abs(applied_delta),
                "applied_cash_delta": applied_delta,
                "margin_debt_delta": applied_delta,
                "included_in_trade_plan": True,
                "reason": "Reserve SEK for a payment to the Nordnet credit account before rebalancing.",
            }
        )
        return adjustment

    adjustment["reason"] = leverage_report.get("reason", adjustment["reason"])
    return adjustment


def build_band_status_report(statuses) -> list[dict[str, Any]]:
    return [
        {
            "ticker": status.ticker,
            "name": status.name,
            "target_pct": status.target_pct,
            "current_pct": status.current_pct,
            "volatility_pct": status.volatility_pct,
            "band_sigma": status.band_sigma,
            "lower_band_sigma": status.lower_band_sigma,
            "upper_band_sigma": status.upper_band_sigma,
            "lower_band": status.lower_band,
            "upper_band": status.upper_band,
            "lower_tolerance": status.lower_tolerance,
            "upper_tolerance": status.upper_tolerance,
            "triggered": status.triggered,
            "direction": status.direction,
        }
        for status in statuses
    ]


def empty_monitor_report(
    portfolio,
    config: PortfolioConfig,
    statuses,
    *,
    basis: str = "current",
    leverage_report: dict[str, Any] | None = None,
    financing_adjustment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    leverage_report = leverage_report or build_leverage_report(
        portfolio, config, basis=basis
    )
    return {
        "common_currency": portfolio.common_currency,
        "rows": [],
        "financing_rows": [],
        "summary": {},
        "exchange_history": [],
        "remaining_cash": _cash_report(portfolio),
        "bands": build_band_status_report(statuses),
        "leverage_current": leverage_report if basis == "current" else None,
        "leverage": leverage_report,
        "financing_adjustment": financing_adjustment,
    }
