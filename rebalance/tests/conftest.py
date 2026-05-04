from pathlib import Path
from unittest.mock import patch

import pytest

from rebalance.money import Price, _cached_fx_rate


@pytest.fixture
def project_root() -> Path:
    """Absolute path to the project root (contains portfolios/)."""
    return Path(__file__).parent.parent.parent


@pytest.fixture
def mock_price_fetchers():
    """Patch price-fetching backends and FX rates to return fixed deterministic values.

    Avoids real network calls in unit tests, making them fast and reliable.
    Patches at the rebalance.asset level (where names are bound after import).
    FX rates all return 1.0 (same-currency assumption sufficient for most unit tests).
    """
    with (
        patch(
            "rebalance.asset.fetch_yfinance_price",
            return_value=Price(100.0, "USD"),
        ),
        patch(
            "rebalance.asset.fetch_nasdaq_nordic_price",
            return_value=Price(150.0, "SEK"),
        ),
        patch(
            "rebalance.money.fetch_fx_rate",
            return_value=1.0,
        ),
    ):
        _cached_fx_rate.cache_clear()
        yield
        _cached_fx_rate.cache_clear()
