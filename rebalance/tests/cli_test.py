import json
import subprocess
import sys
from unittest.mock import patch

import pytest

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
