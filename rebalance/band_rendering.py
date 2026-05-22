"""Rich rendering helpers for band-aware rebalancing output."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypedDict

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .courtage import (
    TradeFeeBreakdown,
    resolve_courtage_profile,
    trade_fee_breakdown,
    uses_common_currency_settlement,
)
from .courtage import (
    amount_in_common_currency as _converted_amount_in_common_currency,
)

_console = Console()
_BAR_INNER = 23


class _ColumnSummaries(TypedDict):
    common_amount_total: float
    common_amount_currency: str
    courtage_fee_total: float
    courtage_fee_currency: str
    fx_fee_total: float
    fx_fee_currency: str
    common_fee_total: float
    common_fee_currency: str
    old_pct_total: float
    cash_inclusive_pct_total: float
    new_pct_total: float
    orig_target_total: float | None
    eff_target_total: float


class _BandRebalanceRow(TypedDict):
    ticker: str
    name: str | None
    band_marker: str
    band_cell: str
    row_style: str
    trade: str
    trade_cell: str
    price: float
    price_currency: str
    delta_units: int | float
    delta_units_cell: str
    amount: float
    amount_currency: str
    amount_cell: str
    courtage_class: str
    courtage_class_cell: str
    amount_common_currency: int
    amount_common_currency_currency: str
    amount_common_currency_cell: str
    fx_fee_common_currency: int
    fx_fee_common_currency_cell: str
    courtage_fee_common_currency: int
    courtage_fee_common_currency_cell: str
    fee_common_currency: int
    fee_common_currency_currency: str
    fee_common_currency_cell: str
    old_pct: float
    cash_inclusive_pct: float
    new_pct: float
    new_pct_cell: str
    original_target_optimizer_band_distance_pp: float | None
    original_target_optimizer_band_distance_cell: str
    original_target_pct: float | None
    original_target_cell: str
    effective_target_pct: float
    band_bar: Text
    band_bar_plain: str


def band_bar(
    prev_pct: float,
    current_pct: float,
    new_pct: float,
    orig_target: float | None,
    eff_target: float,
    status,
    inner: int = _BAR_INNER,
) -> Text:
    """Render a compact horizontal bar showing band positions."""
    lower_band = status.lower_band
    upper_band = status.upper_band
    span = upper_band - lower_band
    if span < 1e-9:
        return Text("─" * (inner + 2))

    def to_inner_idx(fraction: float) -> int:
        return max(0, min(inner - 1, int(fraction * inner)))

    def to_idx(value: float) -> int:
        return to_inner_idx((value - lower_band) / span)

    def midpoint_idx(fraction: float) -> int:
        return to_inner_idx(fraction)

    chars: list[str] = ["─"] * inner
    chars[midpoint_idx(0.25)] = "┤"
    chars[midpoint_idx(0.50)] = "│"
    chars[midpoint_idx(0.75)] = "├"

    if orig_target is not None:
        if status.direction == "above":
            orig_idx = midpoint_idx(0.75)
        elif status.direction == "below" and current_pct <= 1e-9:
            orig_idx = midpoint_idx(0.50)
        elif status.direction == "below":
            orig_idx = midpoint_idx(0.25)
        else:
            orig_idx = to_idx(max(lower_band, min(upper_band, orig_target)))
        if chars[orig_idx] in {"─", "┤", "│", "├"}:
            chars[orig_idx] = "◇"

    chars[to_idx(eff_target)] = "◎"
    chars[to_idx(max(status.lower_band, min(status.upper_band, new_pct)))] = "○"

    overflows_left = current_pct < lower_band
    overflows_right = current_pct > upper_band
    if not overflows_left and not overflows_right:
        chars[to_idx(current_pct)] = "●"

    prev_clipped = max(lower_band, min(upper_band, prev_pct))
    if chars[to_idx(prev_clipped)] == "─":
        chars[to_idx(prev_clipped)] = "◌"

    text = Text()
    text.append(
        "◀" if overflows_left else "[",
        style="bold red" if overflows_left else "dim",
    )
    for char in chars:
        if char == "●":
            if current_pct > status.upper_band:
                style = "bold red"
            elif current_pct < status.lower_band:
                style = "bold green"
            else:
                style = "bold yellow"
            text.append(char, style=style)
        elif char == "◌":
            text.append(char, style="dim white")
        elif char == "○":
            text.append(char, style="bright_white")
        elif char == "◇":
            text.append(char, style="magenta")
        elif char == "◎":
            text.append(char, style="cyan")
        elif char in ("┤", "├"):
            text.append(char, style="dim white")
        elif char == "│":
            text.append(char, style="white")
        else:
            text.append(char, style="dim")
    text.append(
        "▶" if overflows_right else "]",
        style="bold red" if overflows_right else "dim",
    )
    return text


def _format_trade(
    quantity: int | float,
    amount: float,
    *,
    pending: bool = False,
) -> tuple[str, str, str]:
    quantity_fmt = f"{quantity:,d}" if isinstance(quantity, int) else f"{quantity:,.2f}"
    trade_label = "[#ffb300]PENDING[/#ffb300]" if pending else None
    if quantity > 0:
        return (
            f"[green]{quantity_fmt}[/green]",
            _format_amount(amount),
            trade_label or "[green]BUY[/green]",
        )
    if quantity < 0:
        return (
            f"[red]{quantity_fmt}[/red]",
            _format_amount(amount),
            trade_label or "[red]SELL[/red]",
        )
    return (
        f"[dim]{quantity_fmt}[/dim]",
        _format_amount(amount),
        trade_label or "[dim]—[/dim]",
    )


def _band_indicator(status, target: float) -> tuple[str, str, str]:
    if status is not None and status.direction == "above":
        return "▲", "[red]▲[/red]", ""
    if (
        status is not None
        and status.direction == "below"
        and target > 0.0
        and status.current_pct <= 1e-9
    ):
        return "◆", "[blue]◆[/blue]", ""
    if status is not None and status.direction == "below":
        return "▼", "[green]▼[/green]", ""
    return "", "", "dim" if target == 0.0 else ""


def _band_cell(status, target: float) -> tuple[str, str]:
    _, rich_marker, row_style = _band_indicator(status, target)
    return rich_marker, row_style


def _band_marker(status, target: float) -> tuple[str, str]:
    plain_marker, _, row_style = _band_indicator(status, target)
    return plain_marker, row_style


def _original_intended_target(
    status,
    target: float,
    *,
    locked: bool,
) -> float | None:
    if status is None or target <= 0.0:
        return None
    if status.direction == "above":
        return status.upper_tolerance
    if status.direction == "below":
        if status.current_pct <= 1e-9:
            return target
        return status.lower_tolerance
    if locked:
        return status.current_pct
    return None


def _column_summaries(
    common_amounts: Mapping[str, float | int],
    courtage_fees: Mapping[str, float | int],
    fx_fees: Mapping[str, float | int],
    common_fees: Mapping[str, float | int],
    new_allocation: Mapping[str, float],
    plan,
    original_targets: Mapping[str, float | None],
    *,
    common_currency: str,
) -> _ColumnSummaries:
    has_original_targets = any(
        target is not None for target in original_targets.values()
    )

    return {
        "common_amount_total": float(sum(common_amounts.values())),
        "common_amount_currency": common_currency,
        "courtage_fee_total": float(sum(courtage_fees.values())),
        "courtage_fee_currency": common_currency,
        "fx_fee_total": float(sum(fx_fees.values())),
        "fx_fee_currency": common_currency,
        "common_fee_total": float(sum(common_fees.values())),
        "common_fee_currency": common_currency,
        "old_pct_total": float(sum(plan.assets_only_allocation.values())),
        "cash_inclusive_pct_total": float(sum(plan.cash_inclusive_allocation.values())),
        "new_pct_total": float(sum(new_allocation.values())),
        "orig_target_total": (
            float(
                sum(
                    target for target in original_targets.values() if target is not None
                )
            )
            if has_original_targets
            else None
        ),
        "eff_target_total": float(sum(plan.effective_targets.values())),
    }


def _amount_in_common_currency(
    amount: float,
    currency: str,
    common_currency: str,
    conversion_cost: float,
) -> float:
    del conversion_cost
    return _converted_amount_in_common_currency(amount, currency, common_currency)


def _whole_common_amount(
    amount: float,
    currency: str,
    common_currency: str,
    conversion_cost: float,
) -> int:
    return int(
        round(
            _amount_in_common_currency(
                amount,
                currency,
                common_currency,
                conversion_cost,
            )
        )
    )


def _common_currency_fee(
    amount: float,
    currency: str,
    common_currency: str,
    conversion_cost: float,
    courtage_profile: str | None = None,
    *,
    courtage_exempt: bool = False,
) -> float:
    return trade_fee_breakdown(
        amount,
        currency,
        common_currency,
        conversion_cost,
        courtage_profile,
        courtage_exempt=courtage_exempt,
    ).total_fee


def _whole_common_fee(
    amount: float,
    currency: str,
    common_currency: str,
    conversion_cost: float,
    courtage_profile: str | None = None,
    *,
    courtage_exempt: bool = False,
) -> int:
    return int(
        round(
            _common_currency_fee(
                amount,
                currency,
                common_currency,
                conversion_cost,
                courtage_profile,
                courtage_exempt=courtage_exempt,
            )
        )
    )


def _format_total_amount(
    total_amount: float | None, currency: str | None
) -> tuple[str, str]:
    if total_amount is None or currency is None:
        return "[dim]—[/dim]", "[dim]mixed[/dim]"

    return _format_amount(total_amount), currency


def _format_amount(amount: float, decimals: int = 0) -> str:
    amount_fmt = f"{amount:,.{decimals}f}"
    if amount > 0:
        return f"[green]{amount_fmt}[/green]"
    if amount < 0:
        return f"[red]{amount_fmt}[/red]"
    return f"[dim]{amount_fmt}[/dim]"


def _trade_label(quantity: int | float, *, pending: bool = False) -> str:
    if pending:
        return "PENDING"
    if quantity > 0:
        return "BUY"
    if quantity < 0:
        return "SELL"
    return "—"


def _active_financing_adjustment(plan) -> dict | None:
    adjustment = getattr(plan, "financing_adjustment", None)
    if not adjustment or adjustment.get("action") == "none":
        return None
    if not adjustment.get("included_in_trade_plan"):
        return None
    return adjustment


def _financing_trade_cell(action: str) -> str:
    if action == "draw":
        return "[green]DRAW[/green]"
    if action == "repay":
        return "[red]REPAY[/red]"
    return "[dim]—[/dim]"


def _build_financing_rows(plan, common_currency: str) -> list[dict]:
    adjustment = _active_financing_adjustment(plan)
    if adjustment is None:
        return []

    cash_delta = float(adjustment["applied_cash_delta"])
    return [
        {
            "type": adjustment["type"],
            "label": adjustment["label"],
            "action": adjustment["action"],
            "trade": adjustment["action"].upper(),
            "amount": cash_delta,
            "amount_currency": common_currency,
            "amount_common_currency": int(round(cash_delta)),
            "amount_common_currency_currency": common_currency,
            "margin_debt_delta": adjustment["margin_debt_delta"],
            "recommended_debt_delta": adjustment["recommended_debt_delta"],
            "reason": adjustment["reason"],
        }
    ]


def _active_withdrawal_plan(plan) -> dict | None:
    withdrawal = getattr(plan, "withdrawal_plan", None)
    if not withdrawal or not withdrawal.get("feasible", True):
        return None
    requested_amount = float(withdrawal.get("requested_amount", 0.0) or 0.0)
    if requested_amount <= 1e-9:
        return None
    return withdrawal


def _withdrawal_cash_delta(plan) -> float:
    withdrawal = _active_withdrawal_plan(plan)
    if withdrawal is None:
        return 0.0

    cash_delta = withdrawal.get("withdrawal_cash_delta")
    if cash_delta is not None:
        return float(cash_delta)

    requested_amount = float(withdrawal.get("requested_amount", 0.0) or 0.0)
    return -requested_amount


def _build_withdrawal_rows(plan, common_currency: str) -> list[dict]:
    withdrawal = _active_withdrawal_plan(plan)
    if withdrawal is None:
        return []

    amount = -float(withdrawal["requested_amount"])
    return [
        {
            "type": "external_withdrawal",
            "label": "Withdrawal",
            "action": "withdraw",
            "trade": "WITHDRAW",
            "amount": amount,
            "amount_currency": common_currency,
            "amount_common_currency": int(round(amount)),
            "amount_common_currency_currency": common_currency,
            "source": withdrawal.get("source"),
            "reason": withdrawal.get(
                "reason", "External withdrawal included in planning cash."
            ),
        }
    ]


def _withdrawal_trade_cell() -> str:
    return "[red]WITHDRAW[/red]"


def _band_distance_pp(status, reference_pct: float, value_pct: float) -> float | None:
    if status is None:
        return None

    span = status.upper_band - status.lower_band
    if span <= 1e-9:
        return None

    return abs(value_pct - reference_pct) / span * 100.0


def _optimizer_result_pct(
    balanced_portfolio,
    plan,
    ticker: str,
    common_amount: float,
    fee_breakdowns: Mapping[str, TradeFeeBreakdown],
    *,
    fallback_pct: float,
) -> float:
    value_method = getattr(balanced_portfolio, "value", None)
    if not callable(value_method):
        return fallback_pct

    pre_trade_total = float(value_method(balanced_portfolio.common_currency)) + sum(
        float(fee_breakdown.total_fee) for fee_breakdown in fee_breakdowns.values()
    )
    if pre_trade_total <= 1e-9:
        return fallback_pct

    current_value = plan.cash_inclusive_allocation[ticker] / 100.0 * pre_trade_total
    return (current_value + common_amount) / pre_trade_total * 100.0


def _build_band_rebalance_rows(
    balanced_portfolio,
    new_units: dict[str, int | float],
    prices: dict[str, list],
    cost: dict[str, float],
    new_allocation: dict[str, float],
    target_allocation: dict[str, float],
    plan,
) -> tuple[list[_BandRebalanceRow], _ColumnSummaries]:
    common_currency = balanced_portfolio.common_currency
    courtage_profile = getattr(balanced_portfolio, "courtage_profile", None)
    common_amounts = {
        ticker: _whole_common_amount(
            cost[ticker],
            prices[ticker][1],
            common_currency,
            balanced_portfolio.conversion_cost,
        )
        for ticker in cost
    }
    common_amounts_exact = {
        ticker: _amount_in_common_currency(
            cost[ticker],
            prices[ticker][1],
            common_currency,
            balanced_portfolio.conversion_cost,
        )
        for ticker in cost
    }
    fee_breakdowns = {
        ticker: trade_fee_breakdown(
            cost[ticker],
            prices[ticker][1],
            common_currency,
            balanced_portfolio.conversion_cost,
            resolve_courtage_profile(
                courtage_profile,
                getattr(balanced_portfolio.assets[ticker], "courtage_profile", None),
            ),
            courtage_exempt=getattr(
                balanced_portfolio.assets[ticker], "fractional", False
            ),
        )
        for ticker in cost
    }
    common_fees = {
        ticker: int(round(fee_breakdowns[ticker].total_fee)) for ticker in cost
    }
    fx_fees = {ticker: int(round(fee_breakdowns[ticker].fx_fee)) for ticker in cost}
    courtage_fees = {
        ticker: int(round(fee_breakdowns[ticker].courtage_fee)) for ticker in cost
    }
    courtage_classes = {
        ticker: fee_breakdowns[ticker].courtage_class for ticker in cost
    }
    original_targets: dict[str, float | None] = {}
    rows: list[_BandRebalanceRow] = []

    for ticker in balanced_portfolio.assets:
        asset = balanced_portfolio.assets[ticker]
        pending = getattr(asset, "pending", False)
        status = plan.status_by_ticker.get(ticker)
        target = target_allocation[ticker]
        orig_target = _original_intended_target(
            status,
            target,
            locked=ticker in plan.locked_tickers,
        )
        original_targets[ticker] = orig_target
        band_marker, band_cell, row_style = _band_indicator(status, target)
        quantity = new_units[ticker]
        amount = cost[ticker]
        quantity_str, amount_str, trade_str = _format_trade(
            quantity,
            amount,
            pending=pending,
        )
        common_amount = common_amounts[ticker]
        courtage_class = courtage_classes[ticker]
        fx_fee = fx_fees[ticker]
        courtage_fee = courtage_fees[ticker]
        common_fee = common_fees[ticker]
        eff_target = plan.effective_targets[ticker]
        new_pct = new_allocation[ticker]
        optimizer_result_pct = _optimizer_result_pct(
            balanced_portfolio,
            plan,
            ticker,
            common_amounts_exact[ticker],
            fee_breakdowns,
            fallback_pct=new_pct,
        )
        original_target_optimizer_band_distance_pp = (
            _band_distance_pp(status, orig_target, optimizer_result_pct)
            if orig_target is not None
            else None
        )
        bar = (
            band_bar(
                plan.assets_only_allocation[ticker],
                plan.cash_inclusive_allocation[ticker],
                new_pct,
                orig_target,
                eff_target,
                status,
            )
            if status is not None and target > 0.0
            else Text("—", style="dim")
        )

        rows.append(
            {
                "ticker": ticker,
                "name": asset.name,
                "band_marker": band_marker,
                "band_cell": band_cell,
                "row_style": row_style,
                "trade": _trade_label(quantity, pending=pending),
                "trade_cell": trade_str,
                "price": prices[ticker][0],
                "price_currency": prices[ticker][1],
                "delta_units": quantity,
                "delta_units_cell": quantity_str,
                "amount": amount,
                "amount_currency": prices[ticker][1],
                "amount_cell": amount_str,
                "courtage_class": courtage_class,
                "courtage_class_cell": (
                    courtage_class if courtage_class != "—" else "[dim]—[/dim]"
                ),
                "amount_common_currency": common_amount,
                "amount_common_currency_currency": common_currency,
                "amount_common_currency_cell": _format_amount(common_amount),
                "fx_fee_common_currency": fx_fee,
                "fx_fee_common_currency_cell": _format_amount(fx_fee),
                "courtage_fee_common_currency": courtage_fee,
                "courtage_fee_common_currency_cell": _format_amount(courtage_fee),
                "fee_common_currency": common_fee,
                "fee_common_currency_currency": common_currency,
                "fee_common_currency_cell": _format_amount(common_fee),
                "old_pct": plan.assets_only_allocation[ticker],
                "cash_inclusive_pct": plan.cash_inclusive_allocation[ticker],
                "new_pct": new_pct,
                "new_pct_cell": (
                    f"[yellow]{new_pct:.2f}[/yellow]"
                    if abs(new_pct - eff_target) > 0.5
                    else f"{new_pct:.2f}"
                ),
                "original_target_optimizer_band_distance_pp": original_target_optimizer_band_distance_pp,
                "original_target_optimizer_band_distance_cell": (
                    f"{original_target_optimizer_band_distance_pp:.2f}"
                    if original_target_optimizer_band_distance_pp is not None
                    else "[dim]—[/dim]"
                ),
                "original_target_pct": orig_target,
                "original_target_cell": (
                    f"[magenta]{orig_target:.2f}[/magenta]"
                    if orig_target is not None
                    else "[dim]—[/dim]"
                ),
                "effective_target_pct": eff_target,
                "band_bar": bar,
                "band_bar_plain": bar.plain,
            }
        )

    summaries = _column_summaries(
        common_amounts,
        courtage_fees,
        fx_fees,
        common_fees,
        new_allocation,
        plan,
        original_targets,
        common_currency=common_currency,
    )
    rows.sort(
        key=lambda row: (
            row["original_target_optimizer_band_distance_pp"] is None,
            -(row["original_target_optimizer_band_distance_pp"] or 0.0),
        )
    )
    return rows, summaries


def build_band_rebalance_report(
    balanced_portfolio,
    new_units: dict[str, int | float],
    prices: dict[str, list],
    cost: dict[str, float],
    exchange_history: list,
    new_allocation: dict[str, float],
    target_allocation: dict[str, float],
    plan,
) -> dict:
    rows, summaries = _build_band_rebalance_rows(
        balanced_portfolio,
        new_units,
        prices,
        cost,
        new_allocation,
        target_allocation,
        plan,
    )
    common_currency = balanced_portfolio.common_currency
    withdrawal_rows = _build_withdrawal_rows(plan, common_currency)
    withdrawal_cash_delta = _withdrawal_cash_delta(plan)
    financing_rows = _build_financing_rows(plan, common_currency)
    financing_cash_delta = float(
        sum(row["amount_common_currency"] for row in financing_rows)
    )

    return {
        "common_currency": common_currency,
        "rows": [
            {
                "ticker": row["ticker"],
                "name": row["name"],
                "band_marker": row["band_marker"],
                "trade": row["trade"],
                "row_style": row["row_style"],
                "price": row["price"],
                "price_currency": row["price_currency"],
                "delta_units": row["delta_units"],
                "amount": row["amount"],
                "amount_currency": row["amount_currency"],
                "courtage_class": row["courtage_class"],
                "amount_common_currency": row["amount_common_currency"],
                "amount_common_currency_currency": row[
                    "amount_common_currency_currency"
                ],
                "fx_fee_common_currency": row["fx_fee_common_currency"],
                "courtage_fee_common_currency": row["courtage_fee_common_currency"],
                "fee_common_currency": row["fee_common_currency"],
                "fee_common_currency_currency": row["fee_common_currency_currency"],
                "old_pct": row["old_pct"],
                "cash_inclusive_pct": row["cash_inclusive_pct"],
                "new_pct": row["new_pct"],
                "original_target_optimizer_band_distance_pp": row[
                    "original_target_optimizer_band_distance_pp"
                ],
                "original_target_pct": row["original_target_pct"],
                "effective_target_pct": row["effective_target_pct"],
                "band_bar": row["band_bar_plain"],
            }
            for row in rows
        ],
        "withdrawal_rows": withdrawal_rows,
        "financing_rows": financing_rows,
        "summary": {
            "amount_common_currency_total": summaries["common_amount_total"],
            "amount_common_currency_currency": summaries["common_amount_currency"],
            "withdrawal_cash_delta": withdrawal_cash_delta,
            "withdrawal_cash_delta_currency": common_currency,
            "financing_cash_delta": financing_cash_delta,
            "financing_cash_delta_currency": common_currency,
            "net_external_cash_delta": withdrawal_cash_delta + financing_cash_delta,
            "net_external_cash_delta_currency": common_currency,
            "fx_fee_common_currency_total": float(
                sum(row["fx_fee_common_currency"] for row in rows)
            ),
            "courtage_fee_common_currency_total": float(
                sum(row["courtage_fee_common_currency"] for row in rows)
            ),
            "fee_common_currency_total": summaries["common_fee_total"],
            "fee_common_currency_currency": summaries["common_fee_currency"],
            "old_pct_total": summaries["old_pct_total"],
            "cash_inclusive_pct_total": summaries["cash_inclusive_pct_total"],
            "new_pct_total": summaries["new_pct_total"],
            "original_target_pct_total": summaries["orig_target_total"],
            "effective_target_pct_total": summaries["eff_target_total"],
        },
        "exchange_history": [
            {
                "from_amount": from_amount,
                "from_currency": from_currency,
                "to_amount": to_amount,
                "to_currency": to_currency,
                "rate": rate,
            }
            for from_amount, from_currency, to_amount, to_currency, rate in exchange_history
        ],
        "remaining_cash": [
            {"amount": cash.amount, "currency": cash.currency}
            for cash in balanced_portfolio.cash.values()
        ],
    }


def render_band_rebalance_table(
    balanced_portfolio,
    new_units: dict[str, int | float],
    prices: dict[str, list],
    cost: dict[str, float],
    exchange_history: list,
    new_allocation: dict[str, float],
    target_allocation: dict[str, float],
    plan,
) -> None:
    """Render the band-aware rebalance result table and cash summary."""
    show_names = any(
        asset.name is not None for asset in balanced_portfolio.assets.values()
    )
    rows, summaries = _build_band_rebalance_rows(
        balanced_portfolio,
        new_units,
        prices,
        cost,
        new_allocation,
        target_allocation,
        plan,
    )

    table = Table(show_header=True, header_style="bold")
    table.add_column("Band", justify="center")
    table.add_column("Trade", justify="center")
    if show_names:
        table.add_column("Name", max_width=35, no_wrap=True, overflow="ellipsis")
    else:
        table.add_column("Ticker")
    table.add_column("Price", justify="right")
    table.add_column("Δ Units", justify="right")
    table.add_column("Amount", justify="right")
    table.add_column("CCY")
    table.add_column("Courtage", justify="center")
    table.add_column(
        f"Courtage Fee {balanced_portfolio.common_currency}",
        justify="right",
    )
    table.add_column(f"FX Fee {balanced_portfolio.common_currency}", justify="right")
    table.add_column(f"Amount {balanced_portfolio.common_currency}", justify="right")
    table.add_column("[dim white]◌[/dim white] Old %", justify="right")
    table.add_column("[yellow]●[/yellow] Cash-Inclusive %", justify="right")
    table.add_column("[bright_white]○[/bright_white] New %", justify="right")
    table.add_column("Orig->Opt Band pp", justify="right")
    table.add_column("[magenta]◇[/magenta] Orig Target %", justify="right")
    table.add_column("[cyan]◎[/cyan] Eff Target %", justify="right")
    table.add_column(
        "[ lower [dim white]┤[/dim white] lower-mid [white]│[/white] target "
        "[dim white]├[/dim white] upper-mid ] upper  ([dim white]◌[/dim white] "
        "prev  [yellow]●[/yellow] old  [bright_white]○[/bright_white] new  "
        "[magenta]◇[/magenta] orig.target  [cyan]◎[/cyan] eff.target)"
    )

    for row in rows:
        asset_label = (row["name"] or row["ticker"]) if show_names else row["ticker"]
        table.add_row(
            row["band_cell"],
            row["trade_cell"],
            asset_label,
            f"{row['price']:,.2f}",
            row["delta_units_cell"],
            row["amount_cell"],
            row["price_currency"],
            row["courtage_class_cell"],
            row["courtage_fee_common_currency_cell"],
            row["fx_fee_common_currency_cell"],
            row["amount_common_currency_cell"],
            f"{row['old_pct']:.2f}",
            f"{row['cash_inclusive_pct']:.2f}",
            row["new_pct_cell"],
            row["original_target_optimizer_band_distance_cell"],
            row["original_target_cell"],
            f"{row['effective_target_pct']:.2f}",
            row["band_bar"],
            style=row["row_style"],
        )

    for row in _build_withdrawal_rows(plan, balanced_portfolio.common_currency):
        amount_cell = _format_amount(row["amount_common_currency"])
        table.add_row(
            "",
            _withdrawal_trade_cell(),
            row["label"],
            "[dim]—[/dim]",
            "[dim]—[/dim]",
            amount_cell,
            row["amount_currency"],
            "[dim]—[/dim]",
            "[dim]0[/dim]",
            "[dim]0[/dim]",
            amount_cell,
            "[dim]—[/dim]",
            "[dim]—[/dim]",
            "[dim]—[/dim]",
            "[dim]—[/dim]",
            "[dim]—[/dim]",
            "[dim]—[/dim]",
            Text(row["reason"], style="dim"),
        )

    for row in _build_financing_rows(plan, balanced_portfolio.common_currency):
        amount_cell = _format_amount(row["amount_common_currency"])
        table.add_row(
            "",
            _financing_trade_cell(row["action"]),
            row["label"],
            "[dim]—[/dim]",
            "[dim]—[/dim]",
            amount_cell,
            row["amount_currency"],
            "[dim]—[/dim]",
            "[dim]0[/dim]",
            "[dim]0[/dim]",
            amount_cell,
            "[dim]—[/dim]",
            "[dim]—[/dim]",
            "[dim]—[/dim]",
            "[dim]—[/dim]",
            "[dim]—[/dim]",
            "[dim]—[/dim]",
            Text(row["reason"], style="dim"),
        )

    amount_total_str, _ = _format_total_amount(
        summaries["common_amount_total"], summaries["common_amount_currency"]
    )
    courtage_total_str, _ = _format_total_amount(
        summaries["courtage_fee_total"], summaries["courtage_fee_currency"]
    )
    fx_total_str, _ = _format_total_amount(
        summaries["fx_fee_total"], summaries["fx_fee_currency"]
    )
    orig_target_total = summaries["orig_target_total"]
    table.add_row(
        "",
        "",
        "Total",
        "",
        "",
        "",
        "",
        "",
        courtage_total_str,
        fx_total_str,
        amount_total_str,
        f"{summaries['old_pct_total']:.2f}",
        f"{summaries['cash_inclusive_pct_total']:.2f}",
        f"{summaries['new_pct_total']:.2f}",
        "[dim]—[/dim]",
        f"{orig_target_total:.2f}" if orig_target_total is not None else "[dim]—[/dim]",
        f"{summaries['eff_target_total']:.2f}",
        "",
        style="bold",
    )

    _console.print()
    _console.print(table)
    _render_exchange_history(exchange_history)
    _render_remaining_cash(balanced_portfolio)


def _render_exchange_history(exchange_history: list) -> None:
    if not exchange_history:
        return
    noun = "conversions are" if len(exchange_history) > 1 else "conversion is"
    _console.print(
        f"\nBefore making the above purchases, the following currency {noun} required:"
    )
    for from_amount, from_currency, to_amount, to_currency, rate in exchange_history:
        _console.print(
            f"  {from_amount:.0f} {from_currency} → {to_amount:.0f} {to_currency} "
            f"at a rate of {rate:.4f}"
        )


def _render_remaining_cash(balanced_portfolio) -> None:
    _console.print("\nRemaining cash:")
    if uses_common_currency_settlement(
        getattr(balanced_portfolio, "_conversion_cost", 0.0),
        getattr(balanced_portfolio, "courtage_profile", None),
        getattr(balanced_portfolio, "assets", {}).values(),
    ):
        common = balanced_portfolio._common_currency
        _console.print(f"  {balanced_portfolio.cash[common].amount:,.0f} {common}")
    else:
        for cash in balanced_portfolio.cash.values():
            _console.print(f"  {cash.amount:,.0f} {cash.currency}")
