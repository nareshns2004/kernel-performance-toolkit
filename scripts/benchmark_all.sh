#!/usr/bin/env bash
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
make -C "$root" sample-report
echo "Wrote $root/samples/report.json"
