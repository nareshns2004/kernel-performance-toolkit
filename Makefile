PYTHON ?= python3
CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra -std=c11 -pthread
BUILD := build
BENCH := mem_latency page_fault_cost wakeup_latency false_sharing
export PYTHONPATH := src

.PHONY: install dev test lint typecheck fmt check demo samples bench-build clean

install:
	$(PYTHON) -m pip install -e .

dev:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest -q

lint:
	ruff check src tests
	ruff format --check src tests

typecheck:
	mypy

fmt:
	ruff check --fix src tests
	ruff format src tests

check: lint typecheck test

demo:
	$(PYTHON) -m kptk demo numa_misplaced

samples:
	$(PYTHON) -m kptk demo numa_misplaced --save samples/numa_misplaced.jsonl.gz --md samples/numa_misplaced_report.md --json samples/numa_misplaced_report.json
	$(PYTHON) -m kptk parse --perf-file samples/perf_output.txt --vmstat-file samples/vmstat.txt --numastat-file samples/numastat.txt -o samples/parse_report.json

# Microbenchmarks are compiled here but never run by `make check`: they are
# deliberately CPU/memory heavy. Run them by hand on the machine under study.
bench-build: $(BENCH:%=$(BUILD)/%)

$(BUILD):
	mkdir -p $@

$(BUILD)/%: benchmarks/c/%.c | $(BUILD)
	$(CC) $(CFLAGS) $< -o $@

clean:
	rm -rf $(BUILD) .pytest_cache .mypy_cache .ruff_cache src/*.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
