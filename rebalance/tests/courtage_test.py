from types import SimpleNamespace

import pytest

from rebalance.courtage import (
    amount_in_common_currency,
    courtage_segments,
    normalize_courtage_profile,
    quote_courtage,
    resolve_courtage_profile,
    trade_fee_breakdown,
    uses_common_currency_settlement,
)


def test_normalize_courtage_profile_accepts_canonical_names():
    assert normalize_courtage_profile("Nordnet Germany UK") == "nordnet_germany_uk"
    assert normalize_courtage_profile("nordnet-germany-uk") == "nordnet_germany_uk"
    assert normalize_courtage_profile("nordnet_stockholm") == "nordnet_stockholm"
    assert normalize_courtage_profile("Nordnet Stockholm") == "nordnet_stockholm"


@pytest.mark.parametrize(
    ("notional", "expected_class", "expected_fee"),
    [
        (0.0, "—", 0.0),
        (100.0, "Mini", 9.0),
        (3600.0, "Mini", 9.0),
        (3601.0, "Mini", 9.0025),
        (19600.0, "Liten", 49.0),
        (32666.6666667, "Liten", 49.0),
        (46000.0, "Mellan", 69.0),
        (77528.0898876, "Mellan", 69.0),
        (111235.9550562, "Fast", 99.0),
        (200000.0, "Fast", 158.0),
    ],
)
def test_quote_courtage_selects_expected_class_and_fee(
    notional, expected_class, expected_fee
):
    quote = quote_courtage(notional, "nordnet_germany_uk")

    assert quote.class_name == expected_class
    assert quote.fee == pytest.approx(expected_fee)


@pytest.mark.parametrize(
    ("notional", "expected_class", "expected_fee"),
    [
        (0.0, "—", 0.0),
        (100.0, "Mini", 1.0),
        (400.0, "Mini", 1.0),
        (401.0, "Mini", 1.0025),
        (15600.0, "Liten", 39.0),
        (26000.0, "Liten", 39.0),
        (46000.0, "Mellan", 69.0),
        (100000.0, "Mellan", 69.0),
        (143478.2608696, "Fast", 99.0),
        (200000.0, "Fast", 99.0),
    ],
)
def test_quote_courtage_selects_expected_class_and_fee_for_swedish_stock_profile(
    notional, expected_class, expected_fee
):
    quote = quote_courtage(notional, "nordnet_stockholm")

    assert quote.class_name == expected_class
    assert quote.fee == pytest.approx(expected_fee)


def test_trade_fee_breakdown_combines_fx_and_courtage(monkeypatch):
    def fake_exchange_rate(self, currency):
        rates = {
            ("USD", "SEK"): 10.0,
            ("SEK", "SEK"): 1.0,
        }
        return rates[(self.currency, currency)]

    monkeypatch.setattr(
        "rebalance.courtage.Cash.exchange_rate",
        fake_exchange_rate,
    )

    breakdown = trade_fee_breakdown(
        amount=500.0,
        currency="USD",
        common_currency="SEK",
        conversion_cost=0.0025,
        courtage_profile="nordnet_germany_uk",
    )

    assert breakdown.common_amount == pytest.approx(5000.0)
    assert breakdown.fx_fee == pytest.approx(12.5)
    assert breakdown.courtage_class == "Mini"
    assert breakdown.courtage_fee == pytest.approx(12.5)
    assert breakdown.total_fee == pytest.approx(25.0)


def test_trade_fee_breakdown_skips_courtage_for_exempt_assets(monkeypatch):
    def fake_exchange_rate(self, currency):
        rates = {
            ("USD", "SEK"): 10.0,
            ("SEK", "SEK"): 1.0,
        }
        return rates[(self.currency, currency)]

    monkeypatch.setattr(
        "rebalance.courtage.Cash.exchange_rate",
        fake_exchange_rate,
    )

    breakdown = trade_fee_breakdown(
        amount=500.0,
        currency="USD",
        common_currency="SEK",
        conversion_cost=0.0025,
        courtage_profile="nordnet_germany_uk",
        courtage_exempt=True,
    )

    assert breakdown.common_amount == pytest.approx(5000.0)
    assert breakdown.fx_fee == pytest.approx(12.5)
    assert breakdown.courtage_class == "—"
    assert breakdown.courtage_fee == pytest.approx(0.0)
    assert breakdown.total_fee == pytest.approx(12.5)


def test_quote_courtage_skips_exempt_assets():
    quote = quote_courtage(100000.0, "nordnet_germany_uk", courtage_exempt=True)

    assert quote.class_name == "—"
    assert quote.fee == pytest.approx(0.0)


def test_resolve_courtage_profile_prefers_asset_override():
    assert (
        resolve_courtage_profile("nordnet_germany_uk", "nordnet_stockholm")
        == "nordnet_stockholm"
    )


def test_amount_in_common_currency_preserves_sign(monkeypatch):
    def fake_exchange_rate(self, currency):
        rates = {
            ("USD", "SEK"): 10.0,
            ("SEK", "SEK"): 1.0,
        }
        return rates[(self.currency, currency)]

    monkeypatch.setattr(
        "rebalance.courtage.Cash.exchange_rate",
        fake_exchange_rate,
    )

    assert amount_in_common_currency(50.0, "USD", "SEK") == pytest.approx(500.0)
    assert amount_in_common_currency(-50.0, "USD", "SEK") == pytest.approx(-500.0)


def test_courtage_segments_follow_expected_piecewise_schedule():
    segments = courtage_segments("nordnet_germany_uk", 200000.0)

    assert [segment.class_name for segment in segments] == [
        "—",
        "Mini",
        "Mini",
        "Liten",
        "Liten",
        "Mellan",
        "Mellan",
        "Fast",
        "Fast",
    ]
    assert segments[1].upper_notional == pytest.approx(3600.0)
    assert segments[2].lower_notional == pytest.approx(3600.0)
    assert segments[2].upper_notional == pytest.approx(19600.0)
    assert segments[5].upper_notional == pytest.approx(77528.0898876)
    assert segments[-2].lower_notional == pytest.approx(111235.9550562)
    assert segments[-2].upper_notional == pytest.approx(125316.4556962)
    assert segments[-1].lower_notional == pytest.approx(125316.4556962)
    assert segments[-1].upper_notional == pytest.approx(200000.0)


def test_stock_courtage_segments_follow_expected_piecewise_schedule():
    segments = courtage_segments("nordnet_stockholm", 200000.0)

    assert [segment.class_name for segment in segments] == [
        "—",
        "Mini",
        "Mini",
        "Liten",
        "Liten",
        "Mellan",
        "Mellan",
        "Fast",
    ]
    assert segments[1].upper_notional == pytest.approx(400.0)
    assert segments[2].lower_notional == pytest.approx(400.0)
    assert segments[2].upper_notional == pytest.approx(15600.0)
    assert segments[5].upper_notional == pytest.approx(100000.0)
    assert segments[-1].lower_notional == pytest.approx(143478.2608696)
    assert segments[-1].upper_notional == pytest.approx(200000.0)


def test_uses_common_currency_settlement_when_courtage_is_enabled():
    assert uses_common_currency_settlement(0.0, None) is False
    assert uses_common_currency_settlement(0.0025, None) is True
    assert uses_common_currency_settlement(0.0, "nordnet_germany_uk") is True
    assert (
        uses_common_currency_settlement(
            0.0,
            None,
            [SimpleNamespace(courtage_profile="nordnet_stockholm")],
        )
        is True
    )
