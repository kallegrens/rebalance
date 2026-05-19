import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from rebalance import monitor
from rebalance.__main__ import main
from rebalance.band_targets import RebalancePlan
from rebalance.withdrawal_planning import WithdrawalPlanningResult, WithdrawalRequest


class PlanningPortfolio:
    def __init__(self):
        self.common_currency = "SEK"
        self.cash_adjustments = []
        self.band_rebalance_calls = []
        self.copied_from = None

    def __deepcopy__(self, memo):
        del memo
        copied = PlanningPortfolio()
        copied.copied_from = self
        return copied

    def add_cash(self, amount, currency):
        self.cash_adjustments.append((amount, currency))

    def band_rebalance(self, *args, **kwargs):
        self.band_rebalance_calls.append((args, kwargs))
        return {"AAA": 2}, {"AAA": [10.0, "SEK"]}, []


def _portfolio_json(**overrides):
    base = {
        "name": "test",
        "selling_allowed": False,
        "cash": [{"amount": 1000.0, "currency": "USD"}],
        "assets": [
            {"ticker": "AAA", "quantity": 10, "target_allocation": 60.0},
            {"ticker": "BBB", "quantity": 5, "target_allocation": 40.0},
        ],
    }
    base.update(overrides)
    return base


class TestCLI:
    def test_basic_run(self, mock_price_fetchers, tmp_path):
        path = tmp_path / "p.json"
        path.write_text(json.dumps(_portfolio_json()))
        with patch("sys.argv", ["rebalance", str(path)]):
            main()

    def test_verbose_run(self, mock_price_fetchers, tmp_path):
        path = tmp_path / "p.json"
        path.write_text(json.dumps(_portfolio_json()))
        with patch("sys.argv", ["rebalance", str(path), "--verbose"]):
            main()

    def test_objective_arg(self, mock_price_fetchers, tmp_path):
        path = tmp_path / "p.json"
        path.write_text(json.dumps(_portfolio_json()))
        with patch("sys.argv", ["rebalance", str(path), "--objective", "relative-l2"]):
            main()

    def test_missing_file_exits_1(self):
        with patch("sys.argv", ["rebalance", "/no/such/file.json"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 1

    def test_invalid_portfolio_exits_1(self, tmp_path):
        # Single asset with 50% — fails the "must sum to 100" validator before any network call
        bad = {
            "name": "bad",
            "assets": [{"ticker": "X", "quantity": 5, "target_allocation": 50.0}],
        }
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(bad))
        with patch("sys.argv", ["rebalance", str(path)]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 1


class TestMonitorCLI:
    def test_log_leverage_summary_logs_configured_report(self):
        report = {
            "configured": True,
            "basis": "post_rebalance",
            "common_currency": "SEK",
            "assets_value": 1207283.08,
            "cash_value": -0.61,
            "gross_portfolio_value": 1207282.47,
            "equity": 735954.07,
            "current_leverage": 1.64,
            "target_leverage": 1.37,
            "margin_debt": 471328.4,
            "bracket_credit_limit": 375241.66,
            "weighted_lending_value_pct": 81.49,
            "action": "decrease_to_bracket",
            "recommended_debt_delta": -96086.74,
            "current_borrowing_ratio_pct": 39.01,
            "drawdown_from_ath_pct": 1.67,
            "reason": "Debt is above the current tier-1 bracket ceiling.",
        }

        with patch("rebalance.monitor.logger") as logger:
            monitor._log_leverage_summary(report)

        logger.info.assert_any_call(
            "LEVERAGE ({}): current {:.2f}x vs target {:.2f}x | debt {:,.0f} {} | bracket {:,.0f} {} | weighted lending {:.2f}%",
            "post_rebalance",
            1.64,
            1.37,
            471328.4,
            "SEK",
            375241.66,
            "SEK",
            81.49,
        )
        logger.info.assert_any_call(
            "LEVERAGE INPUTS ({}): assets {:,.0f} {} | cash {:,.0f} {} | gross {:,.0f} {} | equity {:,.0f} {}",
            "post_rebalance",
            1207283.08,
            "SEK",
            -0.61,
            "SEK",
            1207282.47,
            "SEK",
            735954.07,
            "SEK",
        )
        logger.info.assert_any_call(
            "LEVERAGE STATUS ({}): action {} | recommended debt delta {:+,.0f} {} | borrowing {:.2f}% | drawdown {}",
            "post_rebalance",
            "decrease_to_bracket",
            -96086.74,
            "SEK",
            39.01,
            "1.67%",
        )
        logger.info.assert_any_call(
            "LEVERAGE REASON ({}): {}",
            "post_rebalance",
            "Debt is above the current tier-1 bracket ceiling.",
        )

    def test_log_leverage_summary_skips_unconfigured_report(self):
        with patch("rebalance.monitor.logger") as logger:
            monitor._log_leverage_summary({"configured": False})

        logger.info.assert_not_called()
        logger.warning.assert_not_called()

    def _run_monitor(self, argv):
        portfolio = Mock()
        config = SimpleNamespace(
            assets=[SimpleNamespace(ticker="AAA", volatility=10.0)],
            leverage=None,
        )
        status = SimpleNamespace(
            ticker="AAA",
            name="AAA",
            triggered=True,
            current_pct=70.0,
            direction="above",
            lower_band=45.0,
            upper_band=55.0,
            target_pct=50.0,
        )

        with (
            patch("sys.argv", argv),
            patch(
                "rebalance.monitor.load_portfolio",
                return_value=(portfolio, {"AAA": 50.0}),
            ),
            patch("rebalance.monitor.load_portfolio_config", return_value=config),
            patch("rebalance.monitor.check_bands", return_value=[status]),
            patch("rebalance.monitor.notify_rebalance_trigger"),
        ):
            monitor.main()

        return portfolio

    def test_monitor_freezes_non_triggered_by_default(self):
        portfolio = self._run_monitor(["rebalance-monitor", "p.json"])

        _, kwargs = portfolio.band_rebalance.call_args
        assert kwargs["lock_non_triggered"] is True

    def test_monitor_trade_non_triggered_opt_out(self):
        portfolio = self._run_monitor(
            ["rebalance-monitor", "p.json", "--trade-non-triggered"]
        )

        _, kwargs = portfolio.band_rebalance.call_args
        assert kwargs["lock_non_triggered"] is False

    def test_monitor_without_json_does_not_prebuild_plan(self):
        portfolio = Mock()
        config = SimpleNamespace(
            assets=[SimpleNamespace(ticker="AAA", volatility=10.0)],
            leverage=None,
        )
        status = SimpleNamespace(
            ticker="AAA",
            name="AAA",
            triggered=True,
            current_pct=70.0,
            direction="above",
            lower_band=45.0,
            upper_band=55.0,
            target_pct=50.0,
        )

        with (
            patch("sys.argv", ["rebalance-monitor", "p.json"]),
            patch(
                "rebalance.monitor.load_portfolio",
                return_value=(portfolio, {"AAA": 50.0}),
            ),
            patch("rebalance.monitor.load_portfolio_config", return_value=config),
            patch("rebalance.monitor.check_bands", return_value=[status]),
            patch("rebalance.monitor.notify_rebalance_trigger"),
            patch("rebalance.monitor.build_band_rebalance_plan") as build_plan,
        ):
            monitor.main()

        build_plan.assert_not_called()

    def test_monitor_logs_current_and_post_rebalance_leverage(self):
        portfolio = Mock()
        portfolio.band_rebalance.return_value = (
            {"AAA": 2},
            {"AAA": [10.0, "USD"]},
            [],
        )
        config = SimpleNamespace(
            assets=[SimpleNamespace(ticker="AAA", volatility=10.0)],
            leverage=None,
        )
        status = SimpleNamespace(
            ticker="AAA",
            name="AAA",
            triggered=True,
            current_pct=70.0,
            direction="above",
            lower_band=45.0,
            upper_band=55.0,
            target_pct=50.0,
        )
        current_report = {"configured": True, "basis": "current"}
        post_report = {"configured": True, "basis": "post_rebalance"}

        with (
            patch("sys.argv", ["rebalance-monitor", "p.json"]),
            patch(
                "rebalance.monitor.load_portfolio",
                return_value=(portfolio, {"AAA": 50.0}),
            ),
            patch("rebalance.monitor.load_portfolio_config", return_value=config),
            patch("rebalance.monitor.check_bands", return_value=[status]),
            patch("rebalance.monitor.notify_rebalance_trigger"),
            patch(
                "rebalance.monitor.build_leverage_report",
                side_effect=[current_report, post_report],
            ) as build_report,
            patch("rebalance.monitor._log_leverage_summary") as log_summary,
        ):
            monitor.main()

        assert build_report.call_args_list == [
            ((portfolio, config), {"basis": "current"}),
            (
                (portfolio, config),
                {"basis": "post_rebalance", "margin_debt_delta": 0.0},
            ),
        ]
        assert log_summary.call_args_list == [
            ((current_report,), {}),
            ((post_report,), {}),
        ]

    def test_monitor_applies_leverage_cashflow_to_planning_portfolio(self):
        portfolio = PlanningPortfolio()
        config = SimpleNamespace(
            assets=[SimpleNamespace(ticker="AAA", volatility=10.0)],
            leverage=SimpleNamespace(provider="nordnet"),
        )
        status = SimpleNamespace(
            ticker="AAA",
            name="AAA",
            triggered=True,
            current_pct=45.0,
            direction="below",
            lower_band=45.0,
            upper_band=55.0,
            target_pct=50.0,
        )
        current_report = {
            "configured": True,
            "basis": "current",
            "common_currency": "SEK",
            "action": "increase",
            "recommended_debt_delta": 100.0,
            "configured_margin_debt": 200.0,
            "margin_debt": 200.0,
            "reason": "Increase leverage toward target.",
        }
        post_report = {"configured": True, "basis": "post_rebalance"}
        plan = SimpleNamespace()

        with (
            patch("sys.argv", ["rebalance-monitor", "p.json"]),
            patch(
                "rebalance.monitor.load_portfolio",
                return_value=(portfolio, {"AAA": 50.0}),
            ),
            patch("rebalance.monitor.load_portfolio_config", return_value=config),
            patch(
                "rebalance.monitor.check_bands", return_value=[status]
            ) as check_bands,
            patch("rebalance.monitor.notify_rebalance_trigger"),
            patch(
                "rebalance.monitor.build_leverage_report",
                side_effect=[current_report, post_report],
            ) as build_report,
            patch(
                "rebalance.monitor.build_band_rebalance_plan",
                return_value=plan,
            ) as build_plan,
            patch("rebalance.monitor._log_leverage_summary"),
        ):
            monitor.main()

        planning_portfolio = check_bands.call_args.args[0]
        assert planning_portfolio is not portfolio
        assert planning_portfolio.cash_adjustments == [(100.0, "SEK")]
        assert planning_portfolio.band_rebalance_calls[0][1]["plan"] is plan
        build_plan_kwargs = build_plan.call_args.kwargs
        assert build_plan.call_args.args[:3] == (
            planning_portfolio,
            {"AAA": 50.0},
            [status],
        )
        assert build_plan_kwargs["financing_adjustment"]["action"] == "draw"
        assert build_report.call_args_list[-1] == (
            (planning_portfolio, config),
            {"basis": "post_rebalance", "margin_debt_delta": 100.0},
        )

    def test_monitor_rejects_removed_lock_flag(self):
        with patch("sys.argv", ["rebalance-monitor", "p.json", "--lock-non-triggered"]):
            with pytest.raises(SystemExit) as exc_info:
                monitor.main()
        assert exc_info.value.code == 2

    def test_monitor_json_requires_output_path(self):
        with patch("sys.argv", ["rebalance-monitor", "p.json", "--json"]):
            with pytest.raises(SystemExit) as exc_info:
                monitor.main()
        assert exc_info.value.code == 2

    def test_monitor_json_writes_report_to_file(self, tmp_path):
        portfolio = Mock()
        portfolio.common_currency = "USD"
        portfolio.conversion_cost = 0.0
        portfolio.cash = {}
        portfolio.band_rebalance.return_value = (
            {"AAA": 2},
            {"AAA": [10.0, "USD"]},
            [],
        )
        config = SimpleNamespace(
            assets=[SimpleNamespace(ticker="AAA", volatility=10.0)],
            leverage=None,
        )
        status = SimpleNamespace(
            ticker="AAA",
            name="AAA",
            triggered=True,
            current_pct=70.0,
            direction="above",
            lower_band=45.0,
            upper_band=55.0,
            target_pct=50.0,
        )
        plan = SimpleNamespace()
        report = {"rows": [{"ticker": "AAA"}], "summary": {"x": 1}}
        bands_report = [{"ticker": "AAA", "triggered": True}]
        current_leverage_report = {"action": "not_configured", "basis": "current"}
        post_leverage_report = {
            "action": "not_configured",
            "basis": "post_rebalance",
        }
        report_path = tmp_path / "report.json"

        with (
            patch(
                "sys.argv",
                ["rebalance-monitor", "p.json", "--json", str(report_path)],
            ),
            patch(
                "rebalance.monitor.load_portfolio",
                return_value=(portfolio, {"AAA": 50.0}),
            ),
            patch("rebalance.monitor.load_portfolio_config", return_value=config),
            patch("rebalance.monitor.check_bands", return_value=[status]),
            patch("rebalance.monitor.notify_rebalance_trigger"),
            patch("rebalance.monitor.build_band_rebalance_plan", return_value=plan),
            patch(
                "rebalance.monitor.cash_inclusive_allocation",
                return_value={"AAA": 50.0},
            ),
            patch(
                "rebalance.monitor.build_band_rebalance_report",
                return_value=report,
            ),
            patch(
                "rebalance.monitor.build_band_status_report",
                return_value=bands_report,
            ),
            patch(
                "rebalance.monitor.build_leverage_report",
                side_effect=[current_leverage_report, post_leverage_report],
            ),
        ):
            monitor.main()

        written = json.loads(report_path.read_text(encoding="utf-8"))
        assert written["rows"] == [{"ticker": "AAA"}]
        assert written["bands"] == bands_report
        assert written["leverage_current"] == current_leverage_report
        assert written["leverage"] == post_leverage_report
        assert written["financing_adjustment"]["action"] == "none"
        _, kwargs = portfolio.band_rebalance.call_args
        assert kwargs["plan"] is plan

    def test_monitor_json_writes_report_without_trigger(self, tmp_path):
        portfolio = Mock()
        config = SimpleNamespace(
            assets=[SimpleNamespace(ticker="AAA", volatility=10.0)],
            leverage=None,
        )
        status = SimpleNamespace(
            ticker="AAA",
            name="AAA",
            triggered=False,
            current_pct=50.0,
            direction=None,
            lower_band=45.0,
            upper_band=55.0,
            target_pct=50.0,
        )
        report = {
            "common_currency": "USD",
            "rows": [],
            "bands": [{"ticker": "AAA", "triggered": False}],
            "leverage": {"action": "not_configured"},
        }
        current_leverage_report = {"action": "not_configured", "basis": "current"}
        post_leverage_report = {
            "action": "not_configured",
            "basis": "post_financing",
        }
        report_path = tmp_path / "report.json"

        with (
            patch(
                "sys.argv",
                ["rebalance-monitor", "p.json", "--json", str(report_path)],
            ),
            patch(
                "rebalance.monitor.load_portfolio",
                return_value=(portfolio, {"AAA": 50.0}),
            ),
            patch("rebalance.monitor.load_portfolio_config", return_value=config),
            patch("rebalance.monitor.check_bands", return_value=[status]),
            patch("rebalance.monitor.empty_monitor_report", return_value=report),
            patch(
                "rebalance.monitor.build_leverage_report",
                side_effect=[current_leverage_report, post_leverage_report],
            ),
        ):
            monitor.main()

        assert json.loads(report_path.read_text(encoding="utf-8")) == {
            **report,
            "leverage_current": current_leverage_report,
        }
        portfolio.band_rebalance.assert_not_called()

    def test_monitor_explicit_withdrawal_uses_withdrawal_planner(self, tmp_path):
        portfolio = Mock()
        portfolio.common_currency = "SEK"
        portfolio.cash_value.return_value = 0.0
        planning_portfolio = Mock()
        planning_portfolio.common_currency = "SEK"
        config = SimpleNamespace(
            assets=[SimpleNamespace(ticker="AAA", volatility=10.0)],
            leverage=SimpleNamespace(provider="nordnet"),
        )
        status = SimpleNamespace(
            ticker="AAA",
            name="AAA",
            triggered=True,
            current_pct=70.0,
            direction="above",
            lower_band=45.0,
            upper_band=55.0,
            target_pct=50.0,
        )
        plan = RebalancePlan(
            effective_targets={"AAA": 50.0},
            sellable_tickers={"AAA"},
            locked_tickers=set(),
            forced_trades={},
            cash_inclusive_allocation={"AAA": 70.0},
            assets_only_allocation={"AAA": 70.0},
            status_by_ticker={"AAA": status},
        )
        request = WithdrawalRequest(
            amount=300_000.0,
            currency="SEK",
            source="cli",
            cash_delta=-300_000.0,
        )
        financing_adjustment = {
            "type": "nordnet_credit",
            "label": "Nordnet credit",
            "action": "repay",
            "amount": 70_000.0,
            "currency": "SEK",
            "recommended_debt_delta": -70_000.0,
            "applied_cash_delta": -70_000.0,
            "margin_debt_delta": -70_000.0,
            "included_in_trade_plan": True,
            "reason": "Reserve SEK to repay Nordnet credit.",
        }
        withdrawal_result = WithdrawalPlanningResult(
            request=request,
            feasible=True,
            reason="Withdrawal plan keeps projected Nordnet debt within policy.",
            repayment_amount=70_000.0,
            iterations=2,
            planning_portfolio=planning_portfolio,
            statuses=[status],
            plan=plan,
            new_units={"AAA": -10},
            prices={"AAA": [10.0, "SEK"]},
            exchange_history=[],
            leverage_report={"configured": True, "basis": "post_rebalance"},
            financing_adjustment=financing_adjustment,
            triggered_count=1,
            trade_plan_built=True,
        )
        current_leverage_report = {"configured": True, "basis": "current"}
        report = {"rows": [], "summary": {}}
        report_path = tmp_path / "withdrawal.json"

        with (
            patch(
                "sys.argv",
                [
                    "rebalance-monitor",
                    "p.json",
                    "--withdrawal",
                    "300000",
                    "--json",
                    str(report_path),
                ],
            ),
            patch(
                "rebalance.monitor.load_portfolio",
                return_value=(portfolio, {"AAA": 50.0}),
            ),
            patch("rebalance.monitor.load_portfolio_config", return_value=config),
            patch("rebalance.monitor.notify_rebalance_trigger"),
            patch(
                "rebalance.monitor.plan_withdrawal", return_value=withdrawal_result
            ) as planner,
            patch(
                "rebalance.monitor.build_band_rebalance_report",
                return_value=report,
            ),
            patch(
                "rebalance.monitor.build_band_status_report",
                return_value=[{"ticker": "AAA", "triggered": True}],
            ),
            patch(
                "rebalance.monitor.cash_inclusive_allocation",
                return_value={"AAA": 50.0},
            ),
            patch("rebalance.monitor.render_band_rebalance_table") as render_table,
            patch(
                "rebalance.monitor.build_leverage_report",
                return_value=current_leverage_report,
            ),
            patch("rebalance.monitor._log_leverage_summary"),
        ):
            monitor.main()

        request_arg = planner.call_args.args[4]
        assert request_arg.amount == 300_000.0
        assert request_arg.source == "cli"
        written = json.loads(report_path.read_text(encoding="utf-8"))
        assert written["withdrawal_plan"]["requested_amount"] == 300_000.0
        assert written["withdrawal_plan"]["required_debt_repayment"] == 70_000.0
        assert written["financing_adjustment"] == financing_adjustment
        render_table.assert_called_once()

    def test_monitor_negative_cash_uses_withdrawal_planner(self):
        portfolio = Mock()
        portfolio.common_currency = "SEK"
        portfolio.cash_value.return_value = -300_000.0
        planning_portfolio = Mock()
        planning_portfolio.common_currency = "SEK"
        config = SimpleNamespace(
            assets=[SimpleNamespace(ticker="AAA", volatility=10.0)],
            leverage=None,
        )
        withdrawal_result = WithdrawalPlanningResult(
            request=WithdrawalRequest(
                amount=300_000.0,
                currency="SEK",
                source="negative_cash",
                cash_delta=0.0,
            ),
            feasible=True,
            reason="Withdrawal plan keeps projected Nordnet debt within policy.",
            planning_portfolio=planning_portfolio,
            statuses=[],
            leverage_report={"configured": False, "basis": "post_withdrawal"},
            financing_adjustment={"action": "none"},
            trade_plan_built=False,
        )

        with (
            patch("sys.argv", ["rebalance-monitor", "p.json"]),
            patch(
                "rebalance.monitor.load_portfolio",
                return_value=(portfolio, {"AAA": 50.0}),
            ),
            patch("rebalance.monitor.load_portfolio_config", return_value=config),
            patch(
                "rebalance.monitor.plan_withdrawal", return_value=withdrawal_result
            ) as planner,
            patch(
                "rebalance.monitor.build_leverage_report",
                return_value={"configured": False, "basis": "current"},
            ),
            patch("rebalance.monitor._log_leverage_summary"),
        ):
            monitor.main()

        request_arg = planner.call_args.args[4]
        assert request_arg.amount == 300_000.0
        assert request_arg.source == "negative_cash"
        assert request_arg.cash_delta == 0.0

    def test_monitor_rejects_explicit_withdrawal_when_cash_is_already_negative(self):
        portfolio = Mock()
        portfolio.common_currency = "SEK"
        portfolio.cash_value.return_value = -300_000.0
        config = SimpleNamespace(
            assets=[SimpleNamespace(ticker="AAA", volatility=10.0)],
            leverage=None,
        )

        with (
            patch(
                "sys.argv",
                ["rebalance-monitor", "p.json", "--withdrawal", "300000"],
            ),
            patch(
                "rebalance.monitor.load_portfolio",
                return_value=(portfolio, {"AAA": 50.0}),
            ),
            patch("rebalance.monitor.load_portfolio_config", return_value=config),
            patch("rebalance.monitor.notify_failure"),
            patch(
                "rebalance.monitor.build_leverage_report",
                return_value={"configured": False, "basis": "current"},
            ),
            patch("rebalance.monitor._log_leverage_summary"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                monitor.main()

        assert exc_info.value.code == 1

    def test_monitor_json_includes_max_withdrawal(self, tmp_path):
        portfolio = Mock()
        portfolio.common_currency = "SEK"
        portfolio.cash_value.return_value = 0.0
        config = SimpleNamespace(
            assets=[SimpleNamespace(ticker="AAA", volatility=10.0)],
            leverage=None,
        )
        status = SimpleNamespace(
            ticker="AAA",
            name="AAA",
            triggered=False,
            current_pct=50.0,
            direction=None,
            lower_band=45.0,
            upper_band=55.0,
            target_pct=50.0,
        )
        report = {
            "common_currency": "SEK",
            "rows": [],
            "bands": [{"ticker": "AAA", "triggered": False}],
            "leverage": {"action": "not_configured"},
        }
        max_report = {
            "configured": True,
            "amount": 700_000.0,
            "currency": "SEK",
            "feasible": True,
            "reason": "Maximum safe withdrawal found with no new Nordnet credit draw.",
            "tolerance": 1_000.0,
        }
        report_path = tmp_path / "max.json"

        with (
            patch(
                "sys.argv",
                [
                    "rebalance-monitor",
                    "p.json",
                    "--max-withdrawal",
                    "--json",
                    str(report_path),
                ],
            ),
            patch(
                "rebalance.monitor.load_portfolio",
                return_value=(portfolio, {"AAA": 50.0}),
            ),
            patch("rebalance.monitor.load_portfolio_config", return_value=config),
            patch("rebalance.monitor.check_bands", return_value=[status]),
            patch("rebalance.monitor.empty_monitor_report", return_value=report),
            patch(
                "rebalance.monitor.build_leverage_report",
                side_effect=[
                    {"configured": False, "basis": "current"},
                    {"configured": False, "basis": "post_financing"},
                ],
            ),
            patch(
                "rebalance.monitor.compute_max_withdrawal",
                return_value=SimpleNamespace(to_report=lambda: max_report),
            ),
        ):
            monitor.main()

        written = json.loads(report_path.read_text(encoding="utf-8"))
        assert written["max_withdrawal"] == max_report


@pytest.mark.integration
class TestCLIIntegration:
    def test_subprocess_basic(self, project_root):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "rebalance",
                str(project_root / "portfolios" / "allweather_zino_redacted.json"),
            ],
            capture_output=True,
            text=True,
            cwd=str(project_root),
        )
        assert result.returncode == 0, result.stderr

    def test_subprocess_verbose(self, project_root):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "rebalance",
                str(project_root / "portfolios" / "allweather_zino_redacted.json"),
                "--verbose",
            ],
            capture_output=True,
            text=True,
            cwd=str(project_root),
        )
        assert result.returncode == 0, result.stderr
        assert "Name" in result.stdout
