from __future__ import annotations

from dataclasses import dataclass

from .money import Cash


@dataclass(frozen=True)
class CourtageTier:
    name: str
    minimum_fee: float
    rate: float


@dataclass(frozen=True)
class CourtageQuote:
    class_name: str
    fee: float
    minimum_fee: float
    rate: float


@dataclass(frozen=True)
class CourtageSegment:
    class_name: str
    lower_notional: float
    upper_notional: float
    slope: float
    intercept: float


@dataclass(frozen=True)
class TradeFeeBreakdown:
    common_amount: float
    fx_fee: float
    courtage_fee: float
    total_fee: float
    courtage_class: str


_NO_COURTAGE_CLASS = "—"
_COURTAGE_PROFILES: dict[str, tuple[CourtageTier, ...]] = {
    "nordnet_germany_uk": (
        CourtageTier("Mini", 9.0, 0.25 / 100.0),
        CourtageTier("Liten", 49.0, 0.15 / 100.0),
        CourtageTier("Mellan", 69.0, 0.089 / 100.0),
        CourtageTier("Fast", 99.0, 0.079 / 100.0),
    ),
    "nordnet_stockholm": (
        CourtageTier("Mini", 1.0, 0.25 / 100.0),
        CourtageTier("Liten", 39.0, 0.15 / 100.0),
        CourtageTier("Mellan", 69.0, 0.069 / 100.0),
        CourtageTier("Fast", 99.0, 0.0),
    ),
}


def _notional_at_fee(rate: float, fee: float) -> float:
    if rate <= 0.0:
        return float("inf")
    return fee / rate


def normalize_courtage_profile(profile: str | None) -> str | None:
    if profile is None:
        return None
    normalized = profile.strip().lower().replace("-", "_").replace(" ", "_")
    if not normalized:
        return None
    if normalized not in _COURTAGE_PROFILES:
        choices = ", ".join(sorted(_COURTAGE_PROFILES))
        raise ValueError(
            f"Unknown courtage profile {profile!r}. Choose one of: {choices}."
        )
    return normalized


def resolve_courtage_profile(
    courtage_profile: str | None,
    asset_courtage_profile: str | None = None,
) -> str | None:
    if asset_courtage_profile is not None:
        return normalize_courtage_profile(asset_courtage_profile)
    return normalize_courtage_profile(courtage_profile)


def uses_common_currency_settlement(
    conversion_cost: float,
    courtage_profile: str | None,
    assets=None,
) -> bool:
    if float(conversion_cost) > 0.0:
        return True
    if normalize_courtage_profile(courtage_profile) is not None:
        return True
    if assets is None:
        return False
    return any(
        normalize_courtage_profile(getattr(asset, "courtage_profile", None)) is not None
        for asset in assets
    )


def get_courtage_tiers(courtage_profile: str | None) -> tuple[CourtageTier, ...]:
    normalized = normalize_courtage_profile(courtage_profile)
    if normalized is None:
        return ()
    return _COURTAGE_PROFILES[normalized]


def amount_in_common_currency(
    amount: float, currency: str, common_currency: str
) -> float:
    return Cash(amount, currency).amount_in(common_currency)


def quote_courtage(
    notional: float,
    courtage_profile: str | None,
    *,
    courtage_exempt: bool = False,
) -> CourtageQuote:
    positive_notional = abs(float(notional))
    tiers = get_courtage_tiers(courtage_profile)
    if positive_notional <= 0.0 or not tiers or courtage_exempt:
        return CourtageQuote(_NO_COURTAGE_CLASS, 0.0, 0.0, 0.0)

    best_fee = float("inf")
    best_index = -1
    best_tier = tiers[0]
    for index, tier in enumerate(tiers):
        fee = max(tier.minimum_fee, tier.rate * positive_notional)
        if fee < best_fee or (abs(fee - best_fee) <= 1e-9 and index > best_index):
            best_fee = fee
            best_index = index
            best_tier = tier

    return CourtageQuote(
        best_tier.name,
        best_fee,
        best_tier.minimum_fee,
        best_tier.rate,
    )


def trade_fee_breakdown(
    amount: float,
    currency: str,
    common_currency: str,
    conversion_cost: float,
    courtage_profile: str | None,
    *,
    courtage_exempt: bool = False,
) -> TradeFeeBreakdown:
    converted_amount = amount_in_common_currency(amount, currency, common_currency)
    common_amount = abs(converted_amount)
    fx_fee = 0.0
    if conversion_cost > 0.0 and currency.upper() != common_currency.upper():
        fx_fee = common_amount * conversion_cost
    courtage = quote_courtage(
        common_amount,
        courtage_profile,
        courtage_exempt=courtage_exempt,
    )
    return TradeFeeBreakdown(
        common_amount=common_amount,
        fx_fee=fx_fee,
        courtage_fee=courtage.fee,
        total_fee=fx_fee + courtage.fee,
        courtage_class=courtage.class_name,
    )


def courtage_segments(
    courtage_profile: str | None, max_notional: float
) -> tuple[CourtageSegment, ...]:
    tiers = get_courtage_tiers(courtage_profile)
    positive_max = max(0.0, float(max_notional))
    if positive_max <= 0.0 or not tiers:
        return (CourtageSegment(_NO_COURTAGE_CLASS, 0.0, 0.0, 0.0, 0.0),)

    segments: list[CourtageSegment] = [
        CourtageSegment(_NO_COURTAGE_CLASS, 0.0, 0.0, 0.0, 0.0)
    ]

    def append_segment(
        class_name: str,
        lower_notional: float,
        upper_notional: float,
        slope: float,
        intercept: float,
    ) -> None:
        lower = max(0.0, lower_notional)
        upper = min(positive_max, upper_notional)
        if upper + 1e-9 < lower:
            return
        segments.append(CourtageSegment(class_name, lower, upper, slope, intercept))

    first_tier = tiers[0]
    if first_tier.rate <= 0.0:
        append_segment(first_tier.name, 0.0, positive_max, 0.0, first_tier.minimum_fee)
        return tuple(segments)

    append_segment(
        first_tier.name,
        0.0,
        _notional_at_fee(first_tier.rate, first_tier.minimum_fee),
        0.0,
        first_tier.minimum_fee,
    )

    for current_tier, next_tier in zip(tiers[:-1], tiers[1:], strict=True):
        next_minimum_crossover = _notional_at_fee(
            current_tier.rate,
            next_tier.minimum_fee,
        )
        append_segment(
            current_tier.name,
            _notional_at_fee(current_tier.rate, current_tier.minimum_fee),
            next_minimum_crossover,
            current_tier.rate,
            0.0,
        )
        if next_tier.rate <= 0.0:
            append_segment(
                next_tier.name,
                next_minimum_crossover,
                positive_max,
                0.0,
                next_tier.minimum_fee,
            )
            return tuple(segments)

        append_segment(
            next_tier.name,
            next_minimum_crossover,
            _notional_at_fee(next_tier.rate, next_tier.minimum_fee),
            0.0,
            next_tier.minimum_fee,
        )

    last_tier = tiers[-1]
    append_segment(
        last_tier.name,
        _notional_at_fee(last_tier.rate, last_tier.minimum_fee),
        positive_max,
        last_tier.rate,
        0.0,
    )
    return tuple(segments)
