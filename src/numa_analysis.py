"""NUMA balance calculations."""

from __future__ import annotations


def numa_metrics(numastat: dict[str, dict[str, float]]) -> dict[str, float]:
    local = numastat.get("numa_hit", {}).get("total", 0.0)
    remote = numastat.get("numa_miss", {}).get("total", 0.0)
    total = local + remote
    return {"numa_local_allocations": local, "numa_remote_allocations": remote,
            "numa_remote_percent": 100 * remote / total if total else 0.0}
