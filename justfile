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

container-build tag='rebalance:test':
    @if ! command -v podman >/dev/null 2>&1; then \
        echo 'podman is not installed.' >&2; \
        exit 1; \
    fi
    @if ! command -v newuidmap >/dev/null 2>&1 || ! command -v newgidmap >/dev/null 2>&1; then \
        echo 'rootless Podman requires newuidmap and newgidmap from the uidmap/shadow-utils package.' >&2; \
        echo 'Install them first, for example: sudo apt-get install uidmap' >&2; \
        exit 1; \
    fi
    podman build -f Containerfile -t {{ tag }} .

check: lock-check fmt lint typecheck test

lock-check:
    uv lock --check

lint:
    prek run --all-files

fmt:
    uv run ruff format rebalance/

typecheck:
    uv run ty check

run portfolio:
    uv run rebalance {{ portfolio }} --verbose
