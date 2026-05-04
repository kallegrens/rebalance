default: test

test:
    uv run pytest rebalance/tests/ -v

coverage:
    uv run pytest rebalance/tests/ --cov=rebalance --cov-report=html

lint:
    uv run ruff check rebalance/

fmt:
    uv run ruff format rebalance/

typecheck:
    uv run ty check

hooks:
    prek run --all-files

run portfolio:
    uv run rebalance {{portfolio}} --verbose
