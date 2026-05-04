"""Failure notification hook for rebalance.

:func:`notify_failure` is called when a run fails unrecoverably.  Right now
it only logs the error; wire up a real notification channel here when needed.

## How to add a notification channel

Pick one of the approaches below and implement it inside ``notify_failure``.

### Option A — Email via SMTP (stdlib)

    import smtplib, ssl
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["Subject"] = f"rebalance run failed: {type(exc).__name__}"
    msg["From"] = os.environ["NOTIFY_FROM"]
    msg["To"] = os.environ["NOTIFY_TO"]
    msg.set_content(f"{context}\\n\\n{exc}")

    with smtplib.SMTP_SSL(os.environ["SMTP_HOST"], 465,
                           context=ssl.create_default_context()) as s:
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        s.send_message(msg)

### Option B — Apprise (meta-library: Slack, Teams, Gotify, ntfy, …)

    import apprise  # pip install apprise

    ap = apprise.Apprise()
    ap.add(os.environ["APPRISE_URL"])   # e.g. "slack://token/channel"
    ap.notify(title="rebalance failed", body=str(exc))

### Option C — ntfy.sh (simple HTTP push)

    import httpx

    httpx.post(
        f"https://ntfy.sh/{os.environ['NTFY_TOPIC']}",
        content=f"rebalance failed: {exc}",
        headers={"Title": "rebalance run failed", "Priority": "high"},
    )

### Option D — Healthchecks.io (cron-job style, ping on success / silence = alert)

    import httpx

    # Call this on SUCCESS (not failure) to signal the cron is alive:
    #   httpx.get(os.environ["HC_PING_URL"])
    # Call this on FAILURE to signal a failed run:
    httpx.get(os.environ["HC_PING_URL"] + "/fail")
"""

from loguru import logger


def notify_failure(exc: BaseException, context: str = "") -> None:
    """Called when a rebalance run fails unrecoverably.

    Currently a no-op beyond logging; see module docstring for how to wire up
    a real notification channel.

    Args:
        exc: The exception that caused the failure.
        context: Optional extra context string (e.g. portfolio filename).
    """
    logger.debug(
        "notify_failure called (no notifier configured): {} — {}",
        type(exc).__name__,
        context or "no context",
    )
