#!/usr/bin/env bash
set -euo pipefail

pages=${1:-128}
if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root to configure huge pages." >&2
  exit 1
fi
echo "$pages" > /proc/sys/vm/nr_hugepages
echo "Configured $pages huge pages."
