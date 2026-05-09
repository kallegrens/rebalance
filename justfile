default: test

test:
    uv run pytest rebalance/tests/ -v

test-fast:
    uv run pytest rebalance/tests/ -v -m 'not integration'

coverage:
    uv run pytest rebalance/tests/ --cov=rebalance --cov-report=html

test-cov:
    uv run pytest rebalance/tests/ -v --cov=rebalance --cov-report=xml --cov-report=term-missing

build:
    uv build

lint:
    prek run --all-files

fmt:
    uv run ruff format rebalance/

typecheck:
    uv run ty check

run portfolio:
    uv run rebalance {{ portfolio }} --verbose
