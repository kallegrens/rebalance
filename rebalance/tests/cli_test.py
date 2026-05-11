import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from rebalance import monitor
from rebalance.__main__ import main


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
    def _run_monitor(self, argv):
        portfolio = Mock()
        config = SimpleNamespace(
            assets=[SimpleNamespace(ticker="AAA", volatility=10.0)]
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
            assets=[SimpleNamespace(ticker="AAA", volatility=10.0)]
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
            assets=[SimpleNamespace(ticker="AAA", volatility=10.0)]
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
        ):
            monitor.main()

        assert json.loads(report_path.read_text(encoding="utf-8")) == report
        _, kwargs = portfolio.band_rebalance.call_args
        assert kwargs["plan"] is plan


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
