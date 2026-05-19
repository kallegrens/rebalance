from types import SimpleNamespace
from unittest.mock import patch

import pytest

from rebalance.withdrawal_planning import (
    WithdrawalPlanningResult,
    WithdrawalRequest,
    compute_max_withdrawal,
    detect_withdrawal_request,
    plan_withdrawal,
)


class TrialPortfolio:
    def __init__(self, cash: float = 0.0, value: float = 1_000_000.0):
        self.common_currency = "SEK"
        self.cash_amount = cash
        self.portfolio_value = value
        self.cash_adjustments: list[tuple[float, str]] = []
        self.band_rebalance_calls: list[tuple[tuple, dict]] = []

    def __deepcopy__(self, memo):
        del memo
        return TrialPortfolio(self.cash_amount, self.portfolio_value)

    def add_cash(self, amount: float, currency: str) -> None:
        self.cash_amount += amount
        self.cash_adjustments.append((amount, currency))

    def cash_value(self, currency: str) -> float:
        del currency
        return self.cash_amount

    def value(self, currency: str) -> float:
        del currency
        return self.portfolio_value

    def band_rebalance(self, *args, **kwargs):
        self.band_rebalance_calls.append((args, kwargs))
        return {"AAA": -10}, {"AAA": [100.0, "SEK"]}, []


def _status(triggered: bool = True):
    return SimpleNamespace(
        ticker="AAA",
        name="Asset A",
        triggered=triggered,
        current_pct=70.0 if triggered else 50.0,
        direction="above" if triggered else None,
        lower_band=45.0,
        upper_band=55.0,
        target_pct=50.0,
    )


def _current_report(**overrides):
    report = {
        "configured": True,
        "basis": "current",
        "common_currency": "SEK",
        "configured_margin_debt": 471_328.0,
        "margin_debt": 471_328.0,
        "action": "hold",
        "recommended_debt_delta": 0.0,
    }
    report.update(overrides)
    return report


def test_detect_withdrawal_request_rejects_double_counted_negative_cash():
    portfolio = TrialPortfolio(cash=-50_000.0)

    with pytest.raises(ValueError, match="double-count"):
        detect_withdrawal_request(portfolio, 300_000.0)


def test_plan_withdrawal_iterates_until_post_rebalance_bracket_is_safe():
    portfolio = TrialPortfolio()
    request = WithdrawalRequest(
        amount=300_000.0,
        currency="SEK",
        source="cli",
        cash_delta=-300_000.0,
    )
    first_post = _current_report(
        basis="post_rebalance",
        action="decrease_to_bracket",
        recommended_debt_delta=-70_721.0,
    )
    second_post = _current_report(
        basis="post_rebalance",
        action="hold",
        recommended_debt_delta=0.0,
        margin_debt=400_607.0,
        margin_debt_delta=-70_721.0,
    )

    with (
        patch(
            "rebalance.withdrawal_planning.check_bands",
            return_value=[_status()],
        ),
        patch(
            "rebalance.withdrawal_planning.build_band_rebalance_plan",
            return_value=SimpleNamespace(),
        ),
        patch(
            "rebalance.withdrawal_planning.build_leverage_report",
            side_effect=[first_post, second_post],
        ),
    ):
        result = plan_withdrawal(
            portfolio,
            SimpleNamespace(),
            {"AAA": 50.0},
            {"AAA": 10.0},
            request,
            _current_report(),
            lock_non_triggered=True,
            objective="max-relative-error",
        )

    assert result.feasible is True
    assert result.iterations == 2
    assert result.repayment_amount == pytest.approx(70_721.0)
    assert result.financing_adjustment is not None
    assert result.planning_portfolio is not None
    assert result.financing_adjustment["action"] == "repay"
    assert result.financing_adjustment["margin_debt_delta"] == pytest.approx(-70_721.0)
    assert result.planning_portfolio.cash_adjustments == [
        (-300_000.0, "SEK"),
        (-70_721.0, "SEK"),
    ]


def test_plan_withdrawal_does_not_repay_when_post_rebalance_is_safe():
    portfolio = TrialPortfolio()
    request = WithdrawalRequest(
        amount=100_000.0,
        currency="SEK",
        source="cli",
        cash_delta=-100_000.0,
    )
    post_report = _current_report(
        basis="post_rebalance",
        action="hold",
        recommended_debt_delta=0.0,
    )

    with (
        patch(
            "rebalance.withdrawal_planning.check_bands",
            return_value=[_status()],
        ),
        patch(
            "rebalance.withdrawal_planning.build_band_rebalance_plan",
            return_value=SimpleNamespace(),
        ),
        patch(
            "rebalance.withdrawal_planning.build_leverage_report",
            return_value=post_report,
        ),
    ):
        result = plan_withdrawal(
            portfolio,
            SimpleNamespace(),
            {"AAA": 50.0},
            {"AAA": 10.0},
            request,
            _current_report(),
            lock_non_triggered=True,
            objective="max-relative-error",
        )

    assert result.feasible is True
    assert result.iterations == 1
    assert result.repayment_amount == pytest.approx(0.0)
    assert result.financing_adjustment is not None
    assert result.planning_portfolio is not None
    assert result.financing_adjustment["action"] == "none"
    assert result.planning_portfolio.cash_adjustments == [(-100_000.0, "SEK")]


def test_plan_withdrawal_grosses_up_repayment_for_collateral_shrinkage():
    portfolio = TrialPortfolio()
    request = WithdrawalRequest(
        amount=300_000.0,
        currency="SEK",
        source="cli",
        cash_delta=-300_000.0,
    )
    first_post = _current_report(
        basis="post_rebalance",
        action="decrease_to_bracket",
        recommended_debt_delta=-70_721.0,
        bracket_max_borrowing_ratio_pct=30.0,
    )
    second_post = _current_report(
        basis="post_rebalance",
        action="hold",
        recommended_debt_delta=0.0,
    )

    with (
        patch(
            "rebalance.withdrawal_planning.check_bands",
            return_value=[_status()],
        ),
        patch(
            "rebalance.withdrawal_planning.build_band_rebalance_plan",
            return_value=SimpleNamespace(),
        ),
        patch(
            "rebalance.withdrawal_planning.build_leverage_report",
            side_effect=[first_post, second_post],
        ),
    ):
        result = plan_withdrawal(
            portfolio,
            SimpleNamespace(),
            {"AAA": 50.0},
            {"AAA": 10.0},
            request,
            _current_report(),
            lock_non_triggered=True,
            objective="max-relative-error",
        )

    assert result.feasible is True
    assert result.iterations == 2
    assert result.repayment_amount == pytest.approx(70_721.0 / 0.70)


def test_compute_max_withdrawal_uses_planner_as_feasibility_oracle():
    portfolio = TrialPortfolio(value=100_000.0)
    current_report = _current_report(configured_margin_debt=10_000.0)

    def fake_plan(*args, **kwargs):
        request = args[4]
        return WithdrawalPlanningResult(
            request=request,
            feasible=request.amount <= 72_500.0,
            reason="ok" if request.amount <= 72_500.0 else "too much",
        )

    with patch("rebalance.withdrawal_planning.plan_withdrawal", side_effect=fake_plan):
        result = compute_max_withdrawal(
            portfolio,
            SimpleNamespace(),
            {"AAA": 50.0},
            {"AAA": 10.0},
            current_report,
            lock_non_triggered=True,
            objective="max-relative-error",
            tolerance=500.0,
        )

    assert result.feasible is True
    assert 72_000.0 <= result.amount <= 72_500.0
    assert result.currency == "SEK"
    assert result.include_new_credit_draw is False
