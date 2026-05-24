"""Apprise-backed notification hooks for rebalance.

The rebalance CLI uses two notification events:

- unrecoverable command failures
- band-monitor triggers when one or more assets drift outside their bands

Notifications are routed through Apprise so the application code stays agnostic
about the actual delivery services. The recommended setup is a self-hosted ntfy
destination for phone push and optional e-mail destinations in the same Apprise
configuration.

Configuration sources are resolved in this order:

1. ``REBALANCE_APPRISE_URLS``: whitespace-delimited Apprise URLs
2. ``REBALANCE_APPRISE_CONFIG``: explicit Apprise config file or URL
3. Apprise's default local config discovery paths

Optional tag routing env vars:

- ``REBALANCE_NOTIFY_TAG``
- ``REBALANCE_NOTIFY_FAILURE_TAG``
- ``REBALANCE_NOTIFY_TRIGGER_TAG``

If a config file is discovered via the default Apprise paths and no tag env var
is set, notifications are sent with the tag ``rebalance`` so a shared global
Apprise config does not accidentally notify unrelated destinations.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

_ENV_APPRISE_CONFIG = "REBALANCE_APPRISE_CONFIG"
_ENV_APPRISE_URLS = "REBALANCE_APPRISE_URLS"
_ENV_NOTIFY_TAG = "REBALANCE_NOTIFY_TAG"
_ENV_NOTIFY_FAILURE_TAG = "REBALANCE_NOTIFY_FAILURE_TAG"
_ENV_NOTIFY_TRIGGER_TAG = "REBALANCE_NOTIFY_TRIGGER_TAG"
_DEFAULT_DISCOVERY_TAG = "rebalance"
_MAX_ERROR_SUMMARY_LENGTH = 400
_MAX_TRIGGER_LINES_PER_SECTION = 8
_MAX_NOTIFICATION_TRADES = 3


@dataclass(frozen=True)
class _NotificationSource:
    urls: tuple[str, ...] = ()
    config: str | None = None
    discovered_config: bool = False


def _split_apprise_urls(raw_urls: str | None) -> tuple[str, ...]:
    if raw_urls is None:
        return ()
    return tuple(part.strip() for part in raw_urls.split() if part.strip())


def _default_config_candidates() -> tuple[str, ...]:
    home = Path.home()

    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        local_appdata = os.environ.get("LOCALAPPDATA")
        common_program_files = os.environ.get("COMMONPROGRAMFILES")
        program_files = os.environ.get("PROGRAMFILES")
        program_data = os.environ.get("ALLUSERSPROFILE")
        candidates = [
            home / "Apprise" / "apprise.conf",
            home / "Apprise" / "apprise.yaml",
        ]
        for env_dir in (
            appdata,
            local_appdata,
            program_data,
            program_files,
            common_program_files,
        ):
            if env_dir:
                base = Path(env_dir) / "Apprise"
                candidates.extend(
                    [
                        base / "apprise.conf",
                        base / "apprise.yaml",
                    ]
                )
        return tuple(str(path) for path in candidates)

    return (
        str(home / ".apprise"),
        str(home / ".apprise.conf"),
        str(home / ".apprise.yml"),
        str(home / ".apprise.yaml"),
        str(home / ".config" / "apprise"),
        str(home / ".config" / "apprise.conf"),
        str(home / ".config" / "apprise.yml"),
        str(home / ".config" / "apprise.yaml"),
        str(home / ".apprise" / "apprise"),
        str(home / ".apprise" / "apprise.conf"),
        str(home / ".apprise" / "apprise.yml"),
        str(home / ".apprise" / "apprise.yaml"),
        str(home / ".config" / "apprise" / "apprise"),
        str(home / ".config" / "apprise" / "apprise.conf"),
        str(home / ".config" / "apprise" / "apprise.yml"),
        str(home / ".config" / "apprise" / "apprise.yaml"),
        "/etc/apprise",
        "/etc/apprise.yml",
        "/etc/apprise.yaml",
        "/etc/apprise/apprise",
        "/etc/apprise/apprise.conf",
        "/etc/apprise/apprise.yml",
        "/etc/apprise/apprise.yaml",
    )


def _discover_default_config() -> str | None:
    for candidate in _default_config_candidates():
        path = Path(candidate).expanduser()
        if path.is_file():
            return str(path)
    return None


def _resolve_notification_source() -> _NotificationSource:
    urls = _split_apprise_urls(os.environ.get(_ENV_APPRISE_URLS))
    explicit_config = (os.environ.get(_ENV_APPRISE_CONFIG) or "").strip() or None

    if explicit_config is not None:
        return _NotificationSource(urls=urls, config=explicit_config)

    discovered_config = _discover_default_config()
    return _NotificationSource(
        urls=urls,
        config=discovered_config,
        discovered_config=discovered_config is not None,
    )


def _load_apprise_module() -> Any | None:
    try:
        import apprise
    except ImportError:
        logger.warning(
            "Apprise is not installed; dropping notification. Run `uv sync` or reinstall the package to enable notifications."
        )
        return None

    return apprise


def _resolve_tag(event: str, source: _NotificationSource) -> str | None:
    event_override = {
        "failure": _ENV_NOTIFY_FAILURE_TAG,
        "trigger": _ENV_NOTIFY_TRIGGER_TAG,
    }.get(event)

    if event_override is not None:
        override_value = (os.environ.get(event_override) or "").strip()
        if override_value:
            return override_value

    generic_value = (os.environ.get(_ENV_NOTIFY_TAG) or "").strip()
    if generic_value:
        return generic_value

    if source.discovered_config:
        return _DEFAULT_DISCOVERY_TAG

    return None


def _build_notifier(source: _NotificationSource) -> Any | None:
    if not source.urls and source.config is None:
        logger.debug("No Apprise notification target configured; notification dropped.")
        return None

    apprise = _load_apprise_module()
    if apprise is None:
        return None

    notifier = apprise.Apprise()
    configured = False

    for url in source.urls:
        configured = bool(notifier.add(url)) or configured

    if source.config is not None:
        config = apprise.AppriseConfig()
        if config.add(source.config):
            configured = bool(notifier.add(config)) or configured
        else:
            logger.warning(
                "Failed to load the Apprise config source; notification dropped."
            )

    if not configured:
        logger.warning("No valid Apprise targets were loaded; notification dropped.")
        return None

    return notifier


def _current_command() -> str:
    if not sys.argv:
        return "rebalance"
    command = Path(sys.argv[0]).name.strip()
    return command or "rebalance"


def _collapse_text(value: str) -> str:
    collapsed = " ".join(value.split())
    if len(collapsed) <= _MAX_ERROR_SUMMARY_LENGTH:
        return collapsed
    return collapsed[: _MAX_ERROR_SUMMARY_LENGTH - 3] + "..."


def _failure_hint(exc: BaseException) -> str:
    if isinstance(exc, FileNotFoundError):
        return "Verify the portfolio path exists and is readable."
    if type(exc).__name__ == "ValidationError":
        return "Fix the portfolio JSON validation errors and rerun the command."
    return "Inspect the command output, fix the issue, and rerun the command."


def _format_failure_message(exc: BaseException, context: str) -> tuple[str, str]:
    command = _current_command()
    title = f"{command} failed ({type(exc).__name__})"
    lines = [f"Command: {command}"]

    if context:
        lines.append(f"Portfolio: {context}")

    summary = _collapse_text(str(exc))
    if summary:
        lines.append(f"Error: {summary}")

    lines.append(f"Action: {_failure_hint(exc)}")
    return title, "\n".join(lines)


def _format_pct(value: Any) -> str:
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return "n/a"


def _format_units(value: Any) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "n/a"

    if abs(numeric - round(numeric)) <= 1e-9:
        return f"{abs(int(round(numeric))):,d}"
    return f"{abs(numeric):,.2f}"


def _trigger_action(trigger: Any) -> str:
    target_pct = float(getattr(trigger, "target_pct", 0.0) or 0.0)
    direction = getattr(trigger, "direction", None)
    if target_pct <= 1e-9 and direction == "above":
        return "EXIT"
    if direction == "below":
        return "BUY"
    return "SELL"


def _trigger_action_counts(triggers: Sequence[Any]) -> tuple[int, int, int]:
    buy_count = sum(1 for trigger in triggers if _trigger_action(trigger) == "BUY")
    sell_count = sum(1 for trigger in triggers if _trigger_action(trigger) == "SELL")
    exit_count = sum(1 for trigger in triggers if _trigger_action(trigger) == "EXIT")
    return buy_count, sell_count, exit_count


def _trigger_sort_key(trigger: Any) -> tuple[float, str]:
    action = _trigger_action(trigger)
    current_pct = float(getattr(trigger, "current_pct", 0.0) or 0.0)
    target_pct = float(getattr(trigger, "target_pct", 0.0) or 0.0)
    if action == "BUY":
        severity = target_pct - current_pct
    elif action == "EXIT":
        severity = current_pct
    else:
        severity = current_pct - target_pct
    ticker = str(
        getattr(trigger, "ticker", None) or getattr(trigger, "name", "unknown")
    )
    return (-severity, ticker)


def _format_trigger_asset(trigger: Any) -> str:
    ticker = getattr(trigger, "ticker", None) or getattr(trigger, "name", "unknown")
    name = (getattr(trigger, "name", None) or "").strip()
    if name and name != ticker:
        return f"{ticker} ({name})"
    return str(ticker)


def _format_trigger_line(trigger: Any) -> str:
    action = _trigger_action(trigger)
    asset = _format_trigger_asset(trigger)
    current_pct = _format_pct(getattr(trigger, "current_pct", None))
    target_pct = _format_pct(getattr(trigger, "target_pct", None))
    lower_band = _format_pct(getattr(trigger, "lower_band", None))
    upper_band = _format_pct(getattr(trigger, "upper_band", None))

    if action == "BUY":
        return (
            f"BUY: {asset} | current {current_pct} | target {target_pct} | "
            f"below {lower_band} floor"
        )
    if action == "EXIT":
        return (
            f"EXIT: {asset} | current {current_pct} | target {target_pct} | "
            "wind down target-zero position"
        )
    return (
        f"SELL: {asset} | current {current_pct} | target {target_pct} | "
        f"above {upper_band} ceiling"
    )


def _append_trigger_section(
    lines: list[str], heading: str, triggers: Sequence[Any]
) -> None:
    if not triggers:
        return

    lines.append(f"{heading}:")
    display_triggers = list(triggers[:_MAX_TRIGGER_LINES_PER_SECTION])
    lines.extend(f"- {_format_trigger_line(trigger)}" for trigger in display_triggers)

    extra_count = len(triggers) - len(display_triggers)
    if extra_count > 0:
        lines.append(f"- +{extra_count} more {heading.lower()}")


def _trade_preview_action(trade: Mapping[str, Any]) -> str:
    delta_units = float(trade.get("delta_units", 0.0) or 0.0)
    if delta_units > 0.0:
        return "BUY"
    if delta_units < 0.0:
        return "SELL"
    return "HOLD"


def _top_trade_previews(
    trade_previews: Sequence[Mapping[str, Any]] | None,
) -> list[Mapping[str, Any]]:
    if not trade_previews:
        return []

    actionable = [
        trade
        for trade in trade_previews
        if abs(float(trade.get("delta_units", 0.0) or 0.0)) > 1e-9
    ]
    actionable.sort(
        key=lambda trade: abs(float(trade.get("amount_common_currency", 0.0) or 0.0)),
        reverse=True,
    )
    return actionable[:_MAX_NOTIFICATION_TRADES]


def _format_trade_preview_asset(trade: Mapping[str, Any]) -> str:
    ticker = str(trade.get("ticker") or trade.get("name") or "unknown")
    name = str(trade.get("name") or "").strip()
    if name and name != ticker:
        return f"{ticker} ({name})"
    return ticker


def _format_trade_preview_line(trade: Mapping[str, Any]) -> str:
    action = _trade_preview_action(trade)
    asset = _format_trade_preview_asset(trade)
    units = _format_units(trade.get("delta_units"))
    amount = abs(float(trade.get("amount_common_currency", 0.0) or 0.0))
    amount_currency = str(trade.get("amount_currency") or "")
    pending_suffix = " | pending" if trade.get("pending") else ""
    return (
        f"{action}: {asset} | {units} units | "
        f"approx {amount:,.0f} {amount_currency}{pending_suffix}"
    )


def _format_leverage_summary(leverage_report: Mapping[str, Any] | None) -> str | None:
    if not leverage_report or not leverage_report.get("configured"):
        return None

    action = str(leverage_report.get("action", "hold") or "hold")
    display_action = {
        "decrease_to_bracket": "DECREASE",
        "opportunistic_zone": "HOLD",
    }.get(action, action.upper())
    current_leverage = leverage_report.get("current_leverage")
    target_leverage = leverage_report.get("target_leverage")
    recommended_debt_delta = float(
        leverage_report.get("recommended_debt_delta", 0.0) or 0.0
    )
    currency = str(leverage_report.get("common_currency") or "")

    parts = [f"Leverage: {display_action}"]
    if current_leverage is not None and target_leverage is not None:
        parts.append(
            f"current {float(current_leverage):.2f}x vs target {float(target_leverage):.2f}x"
        )
    if currency:
        parts.append(
            f"recommended debt delta {recommended_debt_delta:+,.0f} {currency}"
        )
    return " | ".join(parts)


def _format_trigger_message(
    triggers: Sequence[Any],
    *,
    context: str = "",
    portfolio_name: str | None = None,
    trade_previews: Sequence[Mapping[str, Any]] | None = None,
    leverage_report: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    count = len(triggers)
    noun = "asset" if count == 1 else "assets"
    command = _current_command()
    buy_triggers = sorted(
        (trigger for trigger in triggers if _trigger_action(trigger) == "BUY"),
        key=_trigger_sort_key,
    )
    sell_triggers = sorted(
        (trigger for trigger in triggers if _trigger_action(trigger) == "SELL"),
        key=_trigger_sort_key,
    )
    exit_triggers = sorted(
        (trigger for trigger in triggers if _trigger_action(trigger) == "EXIT"),
        key=_trigger_sort_key,
    )
    buy_count, sell_count, exit_count = _trigger_action_counts(triggers)

    summary_parts = []
    if buy_count:
        summary_parts.append(f"BUY {buy_count}")
    if sell_count:
        summary_parts.append(f"SELL {sell_count}")
    if exit_count:
        summary_parts.append(f"EXIT {exit_count}")

    summary = " | ".join(summary_parts)
    title = f"Rebalance trigger: {count} {noun} outside bands"
    if summary:
        title = f"{title} | {summary}"

    lines: list[str] = []
    if portfolio_name:
        lines.append(f"Portfolio: {portfolio_name}")
    lines.append(
        f"Summary: {count} {noun} outside bands" + (f" | {summary}" if summary else "")
    )
    leverage_summary = _format_leverage_summary(leverage_report)
    if leverage_summary is not None:
        lines.append(leverage_summary)

    top_trades = _top_trade_previews(trade_previews)
    if top_trades:
        lines.append("Top planned trades:")
        lines.extend(f"- {_format_trade_preview_line(trade)}" for trade in top_trades)

    if context:
        lines.append(f"Source: {context}")
    lines.append(f"Command: {command}")

    _append_trigger_section(lines, "Buy candidates", buy_triggers)
    _append_trigger_section(lines, "Sell candidates", sell_triggers)
    _append_trigger_section(lines, "Exit candidates", exit_triggers)

    return title, "\n".join(lines)


def _send_notification(event: str, title: str, body: str) -> None:
    source = _resolve_notification_source()
    notifier = _build_notifier(source)
    if notifier is None:
        logger.debug("Dropped {} notification: {}", event, title)
        return

    notify_kwargs: dict[str, Any] = {"title": title, "body": body}
    tag = _resolve_tag(event, source)
    if tag is not None:
        notify_kwargs["tag"] = tag

    try:
        delivered = bool(notifier.notify(**notify_kwargs))
    except Exception:
        logger.opt(exception=True).warning(
            "Notification delivery failed for {} event.", event
        )
        return

    if not delivered:
        logger.warning(
            "Notification delivery returned no successful destinations for {} event.",
            event,
        )


def notify_failure(exc: BaseException, context: str = "") -> None:
    """Send a failure notification for an unrecoverable command error."""
    title, body = _format_failure_message(exc, context)
    _send_notification("failure", title, body)


def notify_rebalance_trigger(
    triggers: list,
    *,
    context: str = "",
    portfolio_name: str | None = None,
    trade_previews: Sequence[Mapping[str, Any]] | None = None,
    leverage_report: Mapping[str, Any] | None = None,
) -> None:
    """Send a notification when one or more assets drift outside their bands."""
    if not triggers:
        logger.debug("notify_rebalance_trigger called with no triggers.")
        return

    title, body = _format_trigger_message(
        triggers,
        context=context,
        portfolio_name=portfolio_name,
        trade_previews=trade_previews,
        leverage_report=leverage_report,
    )
    _send_notification("trigger", title, body)
