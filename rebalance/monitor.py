"""rebalance-monitor — cron-friendly band-drift checker.

Fetches current prices for a portfolio and checks whether any asset has
drifted outside its configured range-based rebalancing band around the target
weight. By default, assets use 1.5 sigma bands, and per-asset JSON overrides
may widen or narrow the lower and upper sides separately. Designed to run on a
schedule (cron / k8s CronJob) and emit a WARNING log signal when action is
needed.

Exit codes:
  0 — completed successfully (triggers fired or not — triggering is not an error)
  1 — failed to load/parse the portfolio file
"""

import argparse
import copy
import json
import sys
from dataclasses import replace

from loguru import logger
from pydantic import ValidationError

from rebalance.band_checker import band_settings_by_ticker, check_bands
from rebalance.band_rendering import (
    build_band_rebalance_report,
    render_band_rebalance_table,
)
from rebalance.band_targets import build_band_rebalance_plan, cash_inclusive_allocation
from rebalance.courtage import amount_in_common_currency
from rebalance.leverage import (
    build_band_status_report,
    build_financing_adjustment,
    build_leverage_report,
    empty_monitor_report,
)
from rebalance.loader import load_portfolio, load_portfolio_config
from rebalance.logging_setup import setup_logging
from rebalance.notifications import notify_failure, notify_rebalance_trigger
from rebalance.rebalancing_helper import (
    DEFAULT_OBJECTIVE,
    OBJECTIVE_ENV_VAR,
    SUPPORTED_OBJECTIVES,
    objective_default_from_env,
)
from rebalance.withdrawal_planning import (
    compute_max_withdrawal,
    detect_withdrawal_request,
    plan_withdrawal,
)


def _write_json_report(path: str, report: dict) -> None:
    with open(path, "w", encoding="utf-8") as json_file:
        json.dump(report, json_file, ensure_ascii=False, indent=2)
        json_file.write("\n")


def _build_notification_trade_previews(
    portfolio,
    new_units: dict[str, int | float],
    prices: dict[str, list],
    *,
    limit: int = 3,
) -> list[dict[str, object]]:
    common_currency = portfolio.common_currency
    assets = getattr(portfolio, "assets", {})
    previews: list[dict[str, object]] = []

    for ticker, delta_units in new_units.items():
        delta_value = float(delta_units)
        if abs(delta_value) <= 1e-9:
            continue

        price, price_currency = prices[ticker]
        amount = float(price) * delta_value
        amount_currency = common_currency
        try:
            amount_common_currency = amount_in_common_currency(
                amount,
                price_currency,
                common_currency,
            )
        except Exception:
            amount_common_currency = amount
            amount_currency = price_currency

        asset = assets.get(ticker)
        previews.append(
            {
                "ticker": ticker,
                "name": getattr(asset, "name", None),
                "delta_units": delta_units,
                "amount_common_currency": amount_common_currency,
                "amount_currency": amount_currency,
                "pending": bool(getattr(asset, "pending", False)),
            }
        )

    previews.sort(
        key=lambda trade: abs(float(trade["amount_common_currency"])),
        reverse=True,
    )
    return previews[:limit]


def _log_leverage_summary(report: dict) -> None:
    if not report.get("configured"):
        return

    basis = report.get("basis", "current")

    if report.get("action") == "invalid":
        logger.warning(
            "LEVERAGE ({}): {}",
            basis,
            report.get("reason", "invalid report"),
        )
        return

    common_currency = report["common_currency"]
    drawdown = report.get("drawdown_from_ath_pct")
    drawdown_text = f"{drawdown:.2f}%" if drawdown is not None else "n/a"

    logger.info(
        "LEVERAGE ({}): current {:.2f}x vs target {:.2f}x | debt {:,.0f} {} | bracket {:,.0f} {} | weighted lending {:.2f}%",
        basis,
        report["current_leverage"],
        report["target_leverage"],
        report["margin_debt"],
        common_currency,
        report["bracket_credit_limit"],
        common_currency,
        report["weighted_lending_value_pct"],
    )
    logger.info(
        "LEVERAGE INPUTS ({}): assets {:,.0f} {} | cash {:,.0f} {} | gross {:,.0f} {} | equity {:,.0f} {}",
        basis,
        report["assets_value"],
        common_currency,
        report["cash_value"],
        common_currency,
        report["gross_portfolio_value"],
        common_currency,
        report["equity"],
        common_currency,
    )
    logger.info(
        "LEVERAGE STATUS ({}): action {} | recommended debt delta {:+,.0f} {} | borrowing {:.2f}% | drawdown {}",
        basis,
        report["action"],
        report["recommended_debt_delta"],
        common_currency,
        report["current_borrowing_ratio_pct"],
        drawdown_text,
    )
    logger.info("LEVERAGE REASON ({}): {}", basis, report["reason"])


def _log_financing_adjustment(adjustment: dict) -> None:
    if adjustment.get("action") == "none":
        return

    currency = adjustment["currency"]
    logger.info(
        "LEVERAGE CASHFLOW: {} {:,.0f} {} | debt delta {:+,.0f} {} | {}",
        adjustment["action"].upper(),
        adjustment["amount"],
        currency,
        adjustment["margin_debt_delta"],
        currency,
        adjustment["reason"],
    )


def _log_withdrawal_plan(report: dict) -> None:
    if not report.get("configured"):
        return

    currency = report["requested_amount_currency"]
    logger.info(
        "WITHDRAWAL PLAN: withdraw {:,.0f} {} | repay credit {:,.0f} {} | total cash needed {:,.0f} {} | feasible {}",
        report["requested_amount"],
        currency,
        report["required_debt_repayment"],
        currency,
        report["total_cash_needed"],
        currency,
        report["feasible"],
    )
    logger.info("WITHDRAWAL REASON: {}", report["reason"])


def _log_max_withdrawal(report: dict | None) -> None:
    if not report or not report.get("configured"):
        return

    logger.info(
        "MAX WITHDRAWAL: {:,.0f} {} | tolerance {:,.0f} {} | feasible {} | {}",
        report["amount"],
        report["currency"],
        report["tolerance"],
        report["currency"],
        report["feasible"],
        report["reason"],
    )


def _log_band_statuses(statuses) -> list:
    triggers = [status for status in statuses if status.triggered]

    for status in statuses:
        label = status.name or status.ticker
        if status.triggered:
            logger.warning(
                "REBALANCE TRIGGER: {} ({}) — current {:.2f}% is {} band "
                "[{:.2f}%–{:.2f}%], target {:.2f}%",
                status.ticker,
                label,
                status.current_pct,
                "above upper" if status.direction == "above" else "below lower",
                status.lower_band,
                status.upper_band,
                status.target_pct,
            )
        else:
            logger.info(
                "OK: {} ({}) — current {:.2f}% within band [{:.2f}%–{:.2f}%], target {:.2f}%",
                status.ticker,
                label,
                status.current_pct,
                status.lower_band,
                status.upper_band,
                status.target_pct,
            )

    return triggers


def _portfolio_with_financing_adjustment(portfolio, adjustment: dict):
    cash_delta = float(adjustment.get("applied_cash_delta", 0.0) or 0.0)
    if abs(cash_delta) <= 1e-9:
        return portfolio

    adjusted = copy.deepcopy(portfolio)
    adjusted.add_cash(cash_delta, adjustment["currency"])
    return adjusted


def main() -> None:
    setup_logging()

    parser = argparse.ArgumentParser(
        description="Check whether a portfolio needs rebalancing based on band-drift rules."
    )
    parser.add_argument("portfolio", help="Path to the portfolio JSON file")
    parser.add_argument(
        "--trade-non-triggered",
        dest="lock_non_triggered",
        action="store_false",
        default=True,
        help=(
            "Allow non-triggered assets to participate in residual target allocation."
        ),
    )
    parser.add_argument(
        "--objective",
        choices=SUPPORTED_OBJECTIVES,
        default=None,
        help=(
            "Optimizer objective to use for trade selection. Defaults to "
            f"${OBJECTIVE_ENV_VAR} when set, otherwise {DEFAULT_OBJECTIVE}."
        ),
    )
    parser.add_argument(
        "--withdrawal",
        type=float,
        metavar="AMOUNT",
        help="Plan a withdrawal in the portfolio common currency.",
    )
    parser.add_argument(
        "--max-withdrawal",
        action="store_true",
        help="Estimate the largest withdrawal that stays within leverage policy.",
    )
    parser.add_argument(
        "--json",
        dest="json_path",
        metavar="PATH",
        help="Also write a JSON representation of the rebalance table to PATH.",
    )
    args = parser.parse_args()

    if args.objective is None:
        try:
            args.objective = objective_default_from_env()
        except ValueError as e:
            parser.error(f"{OBJECTIVE_ENV_VAR}: {e}")

    try:
        portfolio, target_allocation = load_portfolio(args.portfolio)
        config = load_portfolio_config(args.portfolio)
    except FileNotFoundError as e:
        logger.error("Portfolio file not found: {}", args.portfolio)
        notify_failure(e, context=args.portfolio)
        sys.exit(1)
    except ValidationError as e:
        logger.error("Invalid portfolio file:\n{}", e)
        notify_failure(e, context=args.portfolio)
        sys.exit(1)
    except ValueError as e:
        logger.error("Invalid portfolio file: {}", e)
        notify_failure(e, context=args.portfolio)
        sys.exit(1)

    current_leverage_report = build_leverage_report(portfolio, config, basis="current")
    _log_leverage_summary(current_leverage_report)

    band_settings = band_settings_by_ticker(config.assets)
    try:
        withdrawal_request = detect_withdrawal_request(portfolio, args.withdrawal)
    except ValueError as e:
        logger.error("Invalid withdrawal request: {}", e)
        notify_failure(e, context=args.portfolio)
        sys.exit(1)

    max_withdrawal_report = None
    if args.max_withdrawal:
        try:
            max_withdrawal = compute_max_withdrawal(
                portfolio,
                config,
                target_allocation,
                band_settings,
                current_leverage_report,
                lock_non_triggered=args.lock_non_triggered,
                objective=args.objective,
            )
            max_withdrawal_report = max_withdrawal.to_report()
            _log_max_withdrawal(max_withdrawal_report)
        except Exception as e:
            logger.error("Max withdrawal computation failed: {}", e)
            notify_failure(e, context=args.portfolio)
            sys.exit(1)

    if withdrawal_request is not None:
        withdrawal_result = plan_withdrawal(
            portfolio,
            config,
            target_allocation,
            band_settings,
            withdrawal_request,
            current_leverage_report,
            lock_non_triggered=args.lock_non_triggered,
            objective=args.objective,
        )
        withdrawal_report = withdrawal_result.to_report()
        _log_withdrawal_plan(withdrawal_report)
        if withdrawal_result.financing_adjustment is not None:
            _log_financing_adjustment(withdrawal_result.financing_adjustment)

        if not withdrawal_result.feasible:
            logger.error("Withdrawal planning failed: {}", withdrawal_result.reason)
            notify_failure(
                RuntimeError(withdrawal_result.reason), context=args.portfolio
            )
            sys.exit(1)

        statuses = withdrawal_result.statuses
        triggers = _log_band_statuses(statuses)

        if triggers:
            logger.warning(
                "REBALANCE NOTIFICATION SIGNAL: {} asset(s) outside bands "
                "(notification channel not yet configured)",
                len(triggers),
            )

        leverage_report = withdrawal_result.leverage_report or build_leverage_report(
            withdrawal_result.planning_portfolio,
            config,
            basis="post_withdrawal",
            margin_debt_delta=withdrawal_result.margin_debt_delta,
        )
        trade_previews: list[dict[str, object]] = []
        if withdrawal_result.trade_plan_built:
            new_units = withdrawal_result.new_units
            prices = withdrawal_result.prices
            if new_units is not None and prices is not None:
                trade_previews = _build_notification_trade_previews(
                    withdrawal_result.planning_portfolio,
                    new_units,
                    prices,
                )

        if triggers:
            notify_rebalance_trigger(
                triggers,
                context=args.portfolio,
                portfolio_name=getattr(config, "name", None),
                trade_previews=trade_previews,
                leverage_report=leverage_report,
            )

        _log_leverage_summary(leverage_report)

        if withdrawal_result.trade_plan_built:
            logger.info("Computed withdrawal-aware rebalancing trades.")
            if withdrawal_result.plan is not None:
                plan = replace(
                    withdrawal_result.plan,
                    withdrawal_plan=withdrawal_report,
                    financing_adjustment=withdrawal_result.financing_adjustment,
                )
                new_units = withdrawal_result.new_units
                prices = withdrawal_result.prices
                assert new_units is not None
                assert prices is not None
                cost = {
                    ticker: prices[ticker][0] * new_units[ticker] for ticker in prices
                }
                render_band_rebalance_table(
                    withdrawal_result.planning_portfolio,
                    new_units,
                    prices,
                    cost,
                    withdrawal_result.exchange_history,
                    cash_inclusive_allocation(withdrawal_result.planning_portfolio),
                    target_allocation,
                    plan,
                )
                if args.json_path is not None:
                    report = build_band_rebalance_report(
                        withdrawal_result.planning_portfolio,
                        new_units,
                        prices,
                        cost,
                        withdrawal_result.exchange_history,
                        cash_inclusive_allocation(withdrawal_result.planning_portfolio),
                        target_allocation,
                        plan,
                    )
                    report["bands"] = build_band_status_report(statuses)
                    report["leverage_current"] = current_leverage_report
                    report["leverage"] = leverage_report
                    report["financing_adjustment"] = (
                        withdrawal_result.financing_adjustment
                    )
                    report["withdrawal_plan"] = withdrawal_report
                    if max_withdrawal_report is not None:
                        report["max_withdrawal"] = max_withdrawal_report
                    _write_json_report(args.json_path, report)
        else:
            logger.info(
                "No asset trades are needed to fund the withdrawal under current constraints."
            )
            if args.json_path is not None:
                report = empty_monitor_report(
                    withdrawal_result.planning_portfolio,
                    config,
                    statuses,
                    basis="post_withdrawal",
                    leverage_report=leverage_report,
                    financing_adjustment=withdrawal_result.financing_adjustment,
                )
                report["leverage_current"] = current_leverage_report
                report["withdrawal_plan"] = withdrawal_report
                if max_withdrawal_report is not None:
                    report["max_withdrawal"] = max_withdrawal_report
                _write_json_report(args.json_path, report)
        return

    financing_adjustment = build_financing_adjustment(current_leverage_report)
    planning_portfolio = _portfolio_with_financing_adjustment(
        portfolio,
        financing_adjustment,
    )
    _log_financing_adjustment(financing_adjustment)

    statuses = check_bands(planning_portfolio, target_allocation, band_settings)
    triggers = _log_band_statuses(statuses)

    if triggers:
        logger.warning(
            "REBALANCE NOTIFICATION SIGNAL: {} asset(s) outside bands "
            "(notification channel not yet configured)",
            len(triggers),
        )

        logger.info("Computing band-aware rebalancing trades...")
        try:
            plan = None
            if args.json_path is not None or financing_adjustment.get(
                "included_in_trade_plan"
            ):
                plan = build_band_rebalance_plan(
                    planning_portfolio,
                    target_allocation,
                    statuses,
                    lock_non_triggered=args.lock_non_triggered,
                    financing_adjustment=financing_adjustment,
                )

            new_units, prices, exchange_history = planning_portfolio.band_rebalance(
                target_allocation,
                statuses,
                verbose=True,
                lock_non_triggered=args.lock_non_triggered,
                objective=args.objective,
                plan=plan,
            )

            leverage_report = build_leverage_report(
                planning_portfolio,
                config,
                basis="post_rebalance",
                margin_debt_delta=financing_adjustment["margin_debt_delta"],
            )
            trade_previews = _build_notification_trade_previews(
                planning_portfolio,
                new_units,
                prices,
            )
            notify_rebalance_trigger(
                triggers,
                context=args.portfolio,
                portfolio_name=getattr(config, "name", None),
                trade_previews=trade_previews,
                leverage_report=current_leverage_report,
            )
            _log_leverage_summary(leverage_report)

            if args.json_path is not None and plan is not None:
                cost = {
                    ticker: prices[ticker][0] * new_units[ticker] for ticker in prices
                }
                report = build_band_rebalance_report(
                    planning_portfolio,
                    new_units,
                    prices,
                    cost,
                    exchange_history,
                    cash_inclusive_allocation(planning_portfolio),
                    target_allocation,
                    plan,
                )
                report["bands"] = build_band_status_report(statuses)
                report["leverage_current"] = current_leverage_report
                report["leverage"] = leverage_report
                report["financing_adjustment"] = financing_adjustment
                if max_withdrawal_report is not None:
                    report["max_withdrawal"] = max_withdrawal_report
                _write_json_report(args.json_path, report)
        except Exception as e:
            logger.error("Band rebalancing computation failed: {}", e)
            notify_rebalance_trigger(
                triggers,
                context=args.portfolio,
                portfolio_name=getattr(config, "name", None),
                leverage_report=current_leverage_report,
            )
    else:
        logger.info(
            "All {} checked asset(s) are within their rebalancing bands.",
            len(statuses),
        )
        if args.json_path is not None:
            leverage_report = build_leverage_report(
                planning_portfolio,
                config,
                basis="post_financing",
                margin_debt_delta=financing_adjustment["margin_debt_delta"],
            )
            report = empty_monitor_report(
                planning_portfolio,
                config,
                statuses,
                basis="post_financing",
                leverage_report=leverage_report,
                financing_adjustment=financing_adjustment,
            )
            report["leverage_current"] = current_leverage_report
            if max_withdrawal_report is not None:
                report["max_withdrawal"] = max_withdrawal_report
            _log_leverage_summary(report["leverage"])
            _write_json_report(args.json_path, report)
        else:
            leverage_report = build_leverage_report(
                planning_portfolio,
                config,
                basis="post_financing",
                margin_debt_delta=financing_adjustment["margin_debt_delta"],
            )
            _log_leverage_summary(leverage_report)


if __name__ == "__main__":
    main()
