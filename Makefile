.PHONY: test sample-report benchmarks clean

CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra -std=c11
BUILD := build
BENCHMARKS := cache_benchmark cpu_stress memory_stress numa_benchmark page_fault_generator

test:
	python3 -m pytest -q

sample-report:
	python3 src/perf_runner.py --perf-file samples/perf_output.txt --vmstat-file samples/vmstat.txt --numastat-file samples/numastat.txt --output samples/report.json

benchmarks: $(BENCHMARKS:%=$(BUILD)/%)

$(BUILD):
	mkdir -p $@

$(BUILD)/%: benchmarks/%.c | $(BUILD)
	$(CC) $(CFLAGS) $< -o $@

clean:
	rm -rf $(BUILD)
