FROM ghcr.io/astral-sh/uv:python3.12-trixie-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    UV_NO_CACHE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/rebalance/.venv

WORKDIR /src

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY rebalance ./rebalance

RUN uv sync --locked --no-dev --no-editable


FROM ghcr.io/astral-sh/uv:python3.12-trixie-slim AS runtime

ENV HOME=/home/appuser \
    PATH="/opt/rebalance/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /config /opt/rebalance \
    && chown appuser:appuser /config

WORKDIR /home/appuser

COPY --from=builder /opt/rebalance/.venv /opt/rebalance/.venv

USER appuser

ENTRYPOINT ["rebalance-monitor"]
CMD ["/config/portfolio.json"]
