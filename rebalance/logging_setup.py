"""Logging configuration for rebalance.

Call :func:`setup_logging` once at startup (in ``main()``).  All modules
should use ``from loguru import logger`` directly — no per-module setup needed.

Stdlib ``logging`` (used by third-party libraries like tenacity/requests) is
intercepted and forwarded to loguru so everything appears in one stream.
"""

import logging
import os
import sys

from loguru import logger


class _InterceptHandler(logging.Handler):
    """Forward stdlib log records to loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        # Resolve the loguru level name from the stdlib level
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Walk up the call stack to find the real caller (skip loguru internals)
        frame, depth = logging.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back  # type: ignore[assignment]
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def setup_logging() -> None:
    """Configure loguru for the application.

    Reads ``LOG_LEVEL`` from the environment (default ``INFO``).
    Removes the loguru default sink and adds a plain-text stdout sink
    suitable for ``kubectl logs``.

    Intercepts stdlib ``logging`` so third-party libraries (tenacity,
    requests, yfinance, urllib3) are routed through loguru too.
    """
    level = os.environ.get("LOG_LEVEL", "INFO").upper()

    logger.remove()
    logger.add(
        sys.stdout,
        level=level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        colorize=None,  # auto: colors when stdout is a TTY, plain in K8s/pipes
        backtrace=True,
        diagnose=False,  # set True locally for full variable dump on exceptions
    )

    # Route all stdlib logging through loguru
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
