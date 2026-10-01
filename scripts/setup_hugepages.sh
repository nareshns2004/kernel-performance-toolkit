#!/usr/bin/env bash
# Reserve hugetlb pages, optionally on one NUMA node, and show the result.
#   sudo scripts/setup_hugepages.sh 1024          # 1024 x 2 MiB, system-wide
#   sudo scripts/setup_hugepages.sh 512 1         # 512 x 2 MiB on node 1
# Reserving at runtime can fall short on a fragmented system; for guaranteed
# pools use the kernel command line (hugepages=N, or hugepagesz=1G hugepages=N).
set -euo pipefail
pages=${1:-128}
node=${2:-}
if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root to configure huge pages." >&2
  exit 1
fi
if [ -n "$node" ]; then
  target=/sys/devices/system/node/node${node}/hugepages/hugepages-2048kB/nr_hugepages
else
  target=/proc/sys/vm/nr_hugepages
fi
echo "$pages" > "$target"
got=$(cat "$target")
echo "requested $pages, got $got (2 MiB pages) via $target"
[ "$got" -lt "$pages" ] && echo "warning: memory too fragmented for the full reservation; try after boot or compact first" >&2
grep -E '^HugePages_|^Hugepagesize' /proc/meminfo
