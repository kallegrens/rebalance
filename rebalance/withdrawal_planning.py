from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from rebalance.band_checker import BandSettings, check_bands
from rebalance.band_targets import build_band_rebalance_plan
from rebalance.leverage import build_leverage_report

DEFAULT_WITHDRAWAL_TOLERANCE = 1.0
DEFAULT_MAX_WITHDRAWAL_TOLERANCE = 1000.0
DEFAULT_REPAYMENT_ITERATIONS = 12
DEFAULT_MAX_WITHDRAWAL_ITERATIONS = 24


@dataclass(frozen=True)
class WithdrawalRequest:
    amount: float
    currency: str
    source: str
    cash_delta: float

    def to_report(self) -> dict[str, Any]:
        return {
            "amount": self.amount,
            "currency": self.currency,
            "source": self.source,
            "cash_delta": self.cash_delta,
        }


@dataclass
class WithdrawalPlanningResult:
    request: WithdrawalRequest
    feasible: bool
    reason: str
    repayment_amount: float = 0.0
    iterations: int = 0
    tolerance: float = DEFAULT_WITHDRAWAL_TOLERANCE
    planning_portfolio: Any | None = None
    statuses: list[Any] = field(default_factory=list)
    plan: Any | None = None
    new_units: dict[str, int | float] | None = None
    prices: dict[str, list] | None = None
    exchange_history: list[Any] = field(default_factory=list)
    leverage_report: dict[str, Any] | None = None
    financing_adjustment: dict[str, Any] | None = None
    triggered_count: int = 0
    trade_plan_built: bool = False

    @property
    def margin_debt_delta(self) -> float:
        return -self.repayment_amount

    @property
    def total_cash_needed(self) -> float:
        return self.request.amount + self.repayment_amount

    def to_report(self) -> dict[str, Any]:
        report: dict[str, Any] = {
            "configured": True,
            "feasible": self.feasible,
            "reason": self.reason,
            "requested_amount": self.request.amount,
            "requested_amount_currency": self.request.currency,
            "source": self.request.source,
            "withdrawal_cash_delta": self.request.cash_delta,
            "withdrawal_cash_delta_currency": self.request.currency,
            "required_debt_repayment": self.repayment_amount,
            "required_debt_repayment_currency": self.request.currency,
            "margin_debt_delta": self.margin_debt_delta,
            "total_cash_needed": self.total_cash_needed,
            "total_cash_needed_currency": self.request.currency,
            "iterations": self.iterations,
            "tolerance": self.tolerance,
            "triggered_count": self.triggered_count,
            "trade_plan_built": self.trade_plan_built,
        }
        if self.leverage_report is not None:
            report["post_leverage_action"] = self.leverage_report.get("action")
            report["post_headroom_to_bracket"] = self.leverage_report.get(
                "headroom_to_bracket"
            )
        return report


@dataclass
class MaxWithdrawalResult:
    amount: float
    currency: str
    feasible: bool
    reason: str
    tolerance: float
    iterations: int
    upper_bound: float
    exact: bool
    limiting_reason: str | None
    lock_non_triggered: bool
    objective: str
    include_new_credit_draw: bool = False

    def to_report(self) -> dict[str, Any]:
        return {
            "configured": True,
            "amount": self.amount,
            "currency": self.currency,
            "feasible": self.feasible,
            "reason": self.reason,
            "tolerance": self.tolerance,
            "iterations": self.iterations,
            "upper_bound": self.upper_bound,
            "exact": self.exact,
            "limiting_reason": self.limiting_reason,
            "lock_non_triggered": self.lock_non_triggered,
            "objective": self.objective,
            "include_new_credit_draw": self.include_new_credit_draw,
        }


def detect_withdrawal_request(
    portfolio,
    explicit_amount: float | None,
    *,
    tolerance: float = DEFAULT_WITHDRAWAL_TOLERANCE,
) -> WithdrawalRequest | None:
    """Return a withdrawal request from CLI input or negative common cash."""
    currency = portfolio.common_currency
    cash_value_method = getattr(portfolio, "cash_value", None)
    cash_value = 0.0
    if callable(cash_value_method):
        try:
            cash_value = float(cash_value_method(currency))
        except (TypeError, ValueError):
            cash_value = 0.0

    if explicit_amount is not None:
        amount = float(explicit_amount)
        if amount <= tolerance:
            raise ValueError("Withdrawal amount must be positive.")
        if cash_value < -tolerance:
            raise ValueError(
                "Do not pass --withdrawal when the portfolio already has negative "
                f"{currency} cash; that would double-count the withdrawal."
            )
        return WithdrawalRequest(
            amount=amount,
            currency=currency,
            source="cli",
            cash_delta=-amount,
        )

    if cash_value < -tolerance:
        return WithdrawalRequest(
            amount=abs(cash_value),
            currency=currency,
            source="negative_cash",
            cash_delta=0.0,
        )

    return None


def _configured_debt_from_report(leverage_report: dict[str, Any]) -> float:
    return float(
        leverage_report.get(
            "configured_margin_debt", leverage_report.get("margin_debt", 0.0)
        )
        or 0.0
    )


def _required_repayment_from_report(
    leverage_report: dict[str, Any],
    *,
    tolerance: float,
) -> float:
    action = leverage_report.get("action")
    recommended_delta = float(leverage_report.get("recommended_debt_delta", 0.0) or 0.0)
    if action in {"decrease", "decrease_to_bracket"} and recommended_delta < -tolerance:
        return abs(recommended_delta)
    return 0.0


def _next_repayment_amount(
    repayment_amount: float,
    post_repayment: float,
    leverage_report: dict[str, Any],
    *,
    configured_debt: float,
    tolerance: float,
) -> float:
    bracket_ratio = (
        float(leverage_report.get("bracket_max_borrowing_ratio_pct", 0.0) or 0.0)
        / 100.0
    )
    retained_collateral_ratio = 1.0 - bracket_ratio
    if retained_collateral_ratio > tolerance / 100.0:
        increment = post_repayment / retained_collateral_ratio
    else:
        increment = post_repayment

    return min(configured_debt, repayment_amount + max(post_repayment, increment))


def _repayment_financing_adjustment(
    currency: str,
    repayment_amount: float,
    *,
    tolerance: float,
    reason: str | None = None,
) -> dict[str, Any]:
    adjustment: dict[str, Any] = {
        "type": "nordnet_credit",
        "label": "Nordnet credit",
        "action": "none",
        "amount": 0.0,
        "currency": currency,
        "recommended_debt_delta": -repayment_amount,
        "applied_cash_delta": 0.0,
        "margin_debt_delta": 0.0,
        "source_action": "withdrawal_planning",
        "included_in_trade_plan": False,
        "reason": "No Nordnet credit repayment is needed for this withdrawal.",
    }
    if repayment_amount <= tolerance:
        return adjustment

    adjustment.update(
        {
            "action": "repay",
            "amount": repayment_amount,
            "recommended_debt_delta": -repayment_amount,
            "applied_cash_delta": -repayment_amount,
            "margin_debt_delta": -repayment_amount,
            "included_in_trade_plan": True,
            "reason": reason
            or "Reserve SEK to repay Nordnet credit after funding the withdrawal.",
        }
    )
    return adjustment


def _base_withdrawal_report(
    request: WithdrawalRequest,
    repayment_amount: float,
    *,
    feasible: bool = True,
    reason: str = "Withdrawal planning is in progress.",
    iterations: int = 0,
    tolerance: float = DEFAULT_WITHDRAWAL_TOLERANCE,
) -> dict[str, Any]:
    return {
        "configured": True,
        "feasible": feasible,
        "reason": reason,
        "requested_amount": request.amount,
        "requested_amount_currency": request.currency,
        "source": request.source,
        "withdrawal_cash_delta": request.cash_delta,
        "withdrawal_cash_delta_currency": request.currency,
        "required_debt_repayment": repayment_amount,
        "required_debt_repayment_currency": request.currency,
        "margin_debt_delta": -repayment_amount,
        "total_cash_needed": request.amount + repayment_amount,
        "total_cash_needed_currency": request.currency,
        "iterations": iterations,
        "tolerance": tolerance,
    }


def _portfolio_for_trial(
    portfolio,
    request: WithdrawalRequest,
    repayment_amount: float,
    *,
    tolerance: float,
):
    trial = copy.deepcopy(portfolio)
    if abs(request.cash_delta) > tolerance:
        trial.add_cash(request.cash_delta, request.currency)
    if repayment_amount > tolerance:
        trial.add_cash(-repayment_amount, request.currency)
    return trial


def _run_withdrawal_trial(
    portfolio,
    config,
    target_allocation: Mapping[str, float],
    band_settings: Mapping[str, float | BandSettings | None],
    request: WithdrawalRequest,
    repayment_amount: float,
    *,
    lock_non_triggered: bool,
    objective: str,
    tolerance: float,
    iteration: int,
) -> WithdrawalPlanningResult:
    trial = _portfolio_for_trial(
        portfolio, request, repayment_amount, tolerance=tolerance
    )
    financing_adjustment = _repayment_financing_adjustment(
        request.currency,
        repayment_amount,
        tolerance=tolerance,
    )
    withdrawal_report = _base_withdrawal_report(
        request,
        repayment_amount,
        iterations=iteration,
        tolerance=tolerance,
    )
    statuses = check_bands(trial, target_allocation, band_settings)
    triggers = [status for status in statuses if status.triggered]
    cash_shortfall = float(trial.cash_value(request.currency)) < -tolerance
    should_build_trade_plan = bool(triggers) or cash_shortfall
    plan = None
    new_units = None
    prices = None
    exchange_history: list[Any] = []

    if should_build_trade_plan:
        plan = build_band_rebalance_plan(
            trial,
            dict(target_allocation),
            statuses,
            lock_non_triggered=lock_non_triggered,
            financing_adjustment=financing_adjustment,
            withdrawal_plan=withdrawal_report,
        )
        logger.disable("rebalance.portfolio")
        try:
            new_units, prices, exchange_history = trial.band_rebalance(
                dict(target_allocation),
                statuses,
                verbose=False,
                lock_non_triggered=lock_non_triggered,
                objective=objective,
                plan=plan,
            )
        finally:
            logger.enable("rebalance.portfolio")

    leverage_report = build_leverage_report(
        trial,
        config,
        basis="post_rebalance" if should_build_trade_plan else "post_withdrawal",
        margin_debt_delta=-repayment_amount,
    )
    return WithdrawalPlanningResult(
        request=request,
        feasible=True,
        reason="Withdrawal trial completed.",
        repayment_amount=repayment_amount,
        iterations=iteration,
        tolerance=tolerance,
        planning_portfolio=trial,
        statuses=statuses,
        plan=plan,
        new_units=new_units,
        prices=prices,
        exchange_history=exchange_history,
        leverage_report=leverage_report,
        financing_adjustment=financing_adjustment,
        triggered_count=len(triggers),
        trade_plan_built=should_build_trade_plan,
    )


def plan_withdrawal(
    portfolio,
    config,
    target_allocation: Mapping[str, float],
    band_settings: Mapping[str, float | BandSettings | None],
    request: WithdrawalRequest,
    current_leverage_report: dict[str, Any] | None = None,
    *,
    lock_non_triggered: bool,
    objective: str,
    tolerance: float = DEFAULT_WITHDRAWAL_TOLERANCE,
    max_iterations: int = DEFAULT_REPAYMENT_ITERATIONS,
) -> WithdrawalPlanningResult:
    """Plan a withdrawal and any repayment needed for post-trade leverage safety."""
    current_report = current_leverage_report or build_leverage_report(
        portfolio, config, basis="current"
    )
    configured_debt = _configured_debt_from_report(current_report)
    repayment_amount = _required_repayment_from_report(
        current_report, tolerance=tolerance
    )

    latest: WithdrawalPlanningResult | None = None
    for iteration in range(1, max_iterations + 1):
        if repayment_amount > configured_debt + tolerance:
            return WithdrawalPlanningResult(
                request=request,
                feasible=False,
                reason="Withdrawal would require repaying more Nordnet debt than is configured.",
                repayment_amount=repayment_amount,
                iterations=iteration,
                tolerance=tolerance,
                leverage_report=latest.leverage_report if latest else current_report,
                financing_adjustment=_repayment_financing_adjustment(
                    request.currency,
                    min(repayment_amount, configured_debt),
                    tolerance=tolerance,
                ),
            )

        try:
            latest = _run_withdrawal_trial(
                portfolio,
                config,
                target_allocation,
                band_settings,
                request,
                repayment_amount,
                lock_non_triggered=lock_non_triggered,
                objective=objective,
                tolerance=tolerance,
                iteration=iteration,
            )
        except Exception as exc:
            return WithdrawalPlanningResult(
                request=request,
                feasible=False,
                reason=str(exc),
                repayment_amount=repayment_amount,
                iterations=iteration,
                tolerance=tolerance,
                financing_adjustment=_repayment_financing_adjustment(
                    request.currency,
                    repayment_amount,
                    tolerance=tolerance,
                ),
            )

        if (
            latest.leverage_report is not None
            and latest.leverage_report.get("action") == "invalid"
        ):
            latest.feasible = False
            latest.reason = latest.leverage_report.get(
                "reason", "Post-withdrawal leverage report is invalid."
            )
            return latest

        post_repayment = _required_repayment_from_report(
            latest.leverage_report or {}, tolerance=tolerance
        )
        if post_repayment <= tolerance:
            latest.reason = "Withdrawal plan keeps projected Nordnet debt within the configured leverage policy."
            latest.financing_adjustment = _repayment_financing_adjustment(
                request.currency,
                repayment_amount,
                tolerance=tolerance,
            )
            return latest

        repayment_amount = _next_repayment_amount(
            repayment_amount,
            post_repayment,
            latest.leverage_report or {},
            configured_debt=configured_debt,
            tolerance=tolerance,
        )

    assert latest is not None
    latest.feasible = False
    latest.reason = "Withdrawal repayment solver did not converge."
    return latest


def _max_withdrawal_upper_bound(
    portfolio,
    current_leverage_report: dict[str, Any],
) -> float:
    value_method = getattr(portfolio, "value", None)
    common_currency = portfolio.common_currency
    portfolio_value = (
        float(value_method(common_currency)) if callable(value_method) else 0.0
    )
    configured_debt = _configured_debt_from_report(current_leverage_report)
    return max(0.0, portfolio_value - configured_debt)


def compute_max_withdrawal(
    portfolio,
    config,
    target_allocation: Mapping[str, float],
    band_settings: Mapping[str, float | BandSettings | None],
    current_leverage_report: dict[str, Any],
    *,
    lock_non_triggered: bool,
    objective: str,
    tolerance: float = DEFAULT_MAX_WITHDRAWAL_TOLERANCE,
    max_iterations: int = DEFAULT_MAX_WITHDRAWAL_ITERATIONS,
) -> MaxWithdrawalResult:
    """Return the largest no-new-credit withdrawal found by binary search."""
    currency = portfolio.common_currency
    upper_bound = _max_withdrawal_upper_bound(portfolio, current_leverage_report)
    if upper_bound <= tolerance:
        return MaxWithdrawalResult(
            amount=0.0,
            currency=currency,
            feasible=False,
            reason="No positive withdrawal is available after reserving configured margin debt.",
            tolerance=tolerance,
            iterations=0,
            upper_bound=upper_bound,
            exact=True,
            limiting_reason="Portfolio equity is below the withdrawal tolerance.",
            lock_non_triggered=lock_non_triggered,
            objective=objective,
        )

    low = 0.0
    high = upper_bound
    iterations = 0
    limiting_reason: str | None = None
    last_success: WithdrawalPlanningResult | None = None

    while high - low > tolerance and iterations < max_iterations:
        iterations += 1
        candidate = (low + high) / 2.0
        request = WithdrawalRequest(
            amount=candidate,
            currency=currency,
            source="max_withdrawal_trial",
            cash_delta=-candidate,
        )
        result = plan_withdrawal(
            portfolio,
            config,
            target_allocation,
            band_settings,
            request,
            current_leverage_report,
            lock_non_triggered=lock_non_triggered,
            objective=objective,
            tolerance=DEFAULT_WITHDRAWAL_TOLERANCE,
        )
        if result.feasible:
            low = candidate
            last_success = result
        else:
            high = candidate
            limiting_reason = result.reason

    exact = high - low <= tolerance
    reason = "Maximum safe withdrawal found with no new Nordnet credit draw."
    if last_success is None:
        reason = "No feasible withdrawal was found under the current constraints."
    return MaxWithdrawalResult(
        amount=low,
        currency=currency,
        feasible=last_success is not None,
        reason=reason,
        tolerance=tolerance,
        iterations=iterations,
        upper_bound=upper_bound,
        exact=exact,
        limiting_reason=limiting_reason,
        lock_non_triggered=lock_non_triggered,
        objective=objective,
    )
