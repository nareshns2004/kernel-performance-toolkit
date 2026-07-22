#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "Usage: $0 COMMAND [ARG ...]" >&2
  exit 2
fi

exec python3 "$(dirname "$0")/../src/perf_runner.py" --command "$@"
