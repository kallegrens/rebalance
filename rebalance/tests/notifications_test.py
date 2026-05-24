from types import SimpleNamespace
from unittest.mock import Mock, call

from rebalance import notifications


def _clear_notification_env(monkeypatch):
    for env_name in (
        "REBALANCE_APPRISE_URLS",
        "REBALANCE_APPRISE_CONFIG",
        "REBALANCE_NOTIFY_TAG",
        "REBALANCE_NOTIFY_FAILURE_TAG",
        "REBALANCE_NOTIFY_TRIGGER_TAG",
    ):
        monkeypatch.delenv(env_name, raising=False)


def _fake_apprise_module():
    notifier = Mock()
    notifier.add.return_value = True
    notifier.notify.return_value = True

    config = Mock()
    config.add.return_value = True

    module = SimpleNamespace(
        Apprise=Mock(return_value=notifier),
        AppriseConfig=Mock(return_value=config),
    )
    return module, notifier, config


def test_notify_failure_uses_inline_apprise_urls(monkeypatch):
    _clear_notification_env(monkeypatch)
    module, notifier, _ = _fake_apprise_module()

    monkeypatch.setenv(
        "REBALANCE_APPRISE_URLS",
        "ntfy://rebalance-topic\nmailtos://example.com?user=user@example.com&pass=app-password",
    )
    monkeypatch.setattr(notifications, "_load_apprise_module", lambda: module)
    monkeypatch.setattr(notifications.sys, "argv", ["rebalance-monitor"])

    notifications.notify_failure(RuntimeError("boom"), context="portfolios/p.json")

    assert notifier.add.call_args_list == [
        call("ntfy://rebalance-topic"),
        call("mailtos://example.com?user=user@example.com&pass=app-password"),
    ]
    notify_kwargs = notifier.notify.call_args.kwargs
    assert notify_kwargs["title"] == "rebalance-monitor failed (RuntimeError)"
    assert notify_kwargs["body_format"] == "text"
    assert "Portfolio: portfolios/p.json" in notify_kwargs["body"]
    assert "Error: boom" in notify_kwargs["body"]
    assert "tag" not in notify_kwargs


def test_notify_trigger_uses_default_tag_for_discovered_config(monkeypatch, tmp_path):
    _clear_notification_env(monkeypatch)
    module, notifier, config = _fake_apprise_module()
    config_path = tmp_path / "apprise.conf"
    config_path.write_text("rebalance=ntfy://rebalance-topic\n")

    monkeypatch.setattr(
        notifications,
        "_default_config_candidates",
        lambda: (str(config_path),),
    )
    monkeypatch.setattr(notifications, "_load_apprise_module", lambda: module)
    monkeypatch.setattr(notifications.sys, "argv", ["rebalance-monitor"])

    trigger = SimpleNamespace(
        ticker="AAA",
        name="Asset A",
        current_pct=70.0,
        target_pct=50.0,
        direction="above",
        lower_band=45.0,
        upper_band=55.0,
    )

    notifications.notify_rebalance_trigger(
        [trigger], context="portfolios/p.json", portfolio_name="allweather_zino"
    )

    config.add.assert_called_once_with(str(config_path))
    notify_kwargs = notifier.notify.call_args.kwargs
    assert notify_kwargs["tag"] == "rebalance"
    assert notify_kwargs["body_format"] == "text"
    assert notify_kwargs["title"] == "Rebalance trigger: 1 asset outside bands | SELL 1"
    assert "Portfolio: allweather_zino" in notify_kwargs["body"]
    assert "Source: portfolios/p.json" in notify_kwargs["body"]
    assert "Summary: 1 asset outside bands | SELL 1" in notify_kwargs["body"]
    assert (
        "SELL: AAA (Asset A) | current 70.00% | target 50.00% | above 55.00% ceiling"
        in notify_kwargs["body"]
    )


def test_notify_failure_uses_event_specific_tag_override(monkeypatch, tmp_path):
    _clear_notification_env(monkeypatch)
    module, notifier, config = _fake_apprise_module()
    config_path = tmp_path / "apprise.conf"
    config_path.write_text("rebalance=ntfy://rebalance-topic\n")

    monkeypatch.setenv("REBALANCE_APPRISE_CONFIG", str(config_path))
    monkeypatch.setenv("REBALANCE_NOTIFY_FAILURE_TAG", "rebalance-failure")
    monkeypatch.setattr(notifications, "_load_apprise_module", lambda: module)
    monkeypatch.setattr(notifications.sys, "argv", ["rebalance"])

    notifications.notify_failure(ValueError("bad weights"), context="p.json")

    config.add.assert_called_once_with(str(config_path))
    assert notifier.notify.call_args.kwargs["tag"] == "rebalance-failure"
    assert notifier.notify.call_args.kwargs["body_format"] == "text"


def test_notify_failure_swallows_delivery_errors(monkeypatch):
    _clear_notification_env(monkeypatch)
    module, notifier, _ = _fake_apprise_module()
    mocked_logger = Mock()
    notifier.notify.side_effect = RuntimeError("transport down")

    monkeypatch.setenv("REBALANCE_APPRISE_URLS", "ntfy://rebalance-topic")
    monkeypatch.setattr(notifications, "_load_apprise_module", lambda: module)
    monkeypatch.setattr(notifications, "logger", mocked_logger)
    monkeypatch.setattr(notifications.sys, "argv", ["rebalance-monitor"])

    notifications.notify_failure(RuntimeError("boom"), context="p.json")

    mocked_logger.opt.assert_called_once_with(exception=True)
    mocked_logger.opt.return_value.warning.assert_called_once()


def test_format_trigger_message_groups_actions_and_truncates_per_section():
    buy_triggers = [
        SimpleNamespace(
            ticker=f"BUY{i}",
            name=f"Buy Asset {i}",
            current_pct=0.0,
            target_pct=5.0 + i,
            direction="below",
            lower_band=4.0 + i,
            upper_band=6.0 + i,
        )
        for i in range(9)
    ]
    sell_trigger = SimpleNamespace(
        ticker="SELL1",
        name="Sell Asset",
        current_pct=8.0,
        target_pct=4.0,
        direction="above",
        lower_band=3.0,
        upper_band=5.0,
    )
    exit_trigger = SimpleNamespace(
        ticker="EXIT1",
        name="Exit Asset",
        current_pct=2.5,
        target_pct=0.0,
        direction="above",
        lower_band=0.0,
        upper_band=0.0,
    )

    title, body = notifications._format_trigger_message(
        [*buy_triggers, sell_trigger, exit_trigger],
        context="/config/portfolio.json",
        portfolio_name="allweather_zino",
    )

    assert (
        title == "Rebalance trigger: 11 assets outside bands | BUY 9 | SELL 1 | EXIT 1"
    )
    assert "Portfolio: allweather_zino" in body
    assert "Source: /config/portfolio.json" in body
    assert "Summary: 11 assets outside bands | BUY 9 | SELL 1 | EXIT 1" in body
    assert "Buy candidates:" in body
    assert "Sell candidates:" in body
    assert "Exit candidates:" in body
    assert (
        "- BUY: BUY8 (Buy Asset 8) | current 0.00% | target 13.00% | below 12.00% floor"
        in body
    )
    assert "- +1 more buy candidates" in body
    assert (
        "- SELL: SELL1 (Sell Asset) | current 8.00% | target 4.00% | above 5.00% ceiling"
        in body
    )
    assert (
        "- EXIT: EXIT1 (Exit Asset) | current 2.50% | target 0.00% | wind down target-zero position"
        in body
    )


def test_format_trigger_message_includes_top_trades_and_leverage_summary():
    trigger = SimpleNamespace(
        ticker="AAA",
        name="Asset A",
        current_pct=70.0,
        target_pct=50.0,
        direction="above",
        lower_band=45.0,
        upper_band=55.0,
    )
    trade_previews = [
        {
            "ticker": "BBB",
            "name": "Asset B",
            "delta_units": 25,
            "amount_common_currency": 25_000.0,
            "amount_currency": "SEK",
            "pending": False,
        },
        {
            "ticker": "CCC",
            "name": "Asset C",
            "delta_units": -12.5,
            "amount_common_currency": -18_750.0,
            "amount_currency": "SEK",
            "pending": True,
        },
        {
            "ticker": "DDD",
            "name": "Asset D",
            "delta_units": 4,
            "amount_common_currency": 9_000.0,
            "amount_currency": "SEK",
            "pending": False,
        },
        {
            "ticker": "EEE",
            "name": "Asset E",
            "delta_units": 2,
            "amount_common_currency": 1_000.0,
            "amount_currency": "SEK",
            "pending": False,
        },
    ]
    leverage_report = {
        "configured": True,
        "action": "hold",
        "current_leverage": 1.38,
        "target_leverage": 1.37,
        "recommended_debt_delta": 0.0,
        "common_currency": "SEK",
    }

    _, body = notifications._format_trigger_message(
        [trigger],
        context="/config/portfolio.json",
        portfolio_name="allweather_zino",
        trade_previews=trade_previews,
        leverage_report=leverage_report,
    )

    assert (
        "Leverage: HOLD | current 1.38x vs target 1.37x | recommended debt delta +0 SEK"
        in body
    )
    assert "Top planned trades:" in body
    assert "- BUY: BBB (Asset B) | 25 units | approx 25,000 SEK" in body
    assert "- SELL: CCC (Asset C) | 12.50 units | approx 18,750 SEK | pending" in body
    assert "- BUY: DDD (Asset D) | 4 units | approx 9,000 SEK" in body
    assert "EEE (Asset E)" not in body
