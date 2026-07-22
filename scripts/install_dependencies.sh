#!/usr/bin/env bash
set -euo pipefail

command -v perf >/dev/null || echo "Install linux-tools for live perf collection."
command -v numastat >/dev/null || echo "Install numactl for NUMA collection."
echo "No Python runtime dependencies are required; install pytest to run tests."
