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
        current_pct=70.0,
        target_pct=50.0,
        direction="above",
        lower_band=45.0,
        upper_band=55.0,
    )

    notifications.notify_rebalance_trigger([trigger])

    config.add.assert_called_once_with(str(config_path))
    notify_kwargs = notifier.notify.call_args.kwargs
    assert notify_kwargs["tag"] == "rebalance"
    assert "AAA: 70.00% vs target 50.00%" in notify_kwargs["body"]


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
