#!/usr/bin/env bash
# Build and run the C microbenchmarks. CPU/memory heavy: run on an otherwise idle
# machine, never in CI. Results go to build/results/.
set -euo pipefail
cd "$(dirname "$0")/.."
make bench-build
mkdir -p build/results
./build/mem_latency            | tee build/results/mem_latency_4k.csv
./build/mem_latency --huge     | tee build/results/mem_latency_thp.csv
./build/page_fault_cost 512    | tee build/results/page_fault_cost.csv
./build/wakeup_latency 200000  | tee build/results/wakeup_latency.txt
./build/false_sharing 4        | tee build/results/false_sharing.txt
