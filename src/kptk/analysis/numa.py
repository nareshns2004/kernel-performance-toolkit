"""NUMA locality.

The per-node ``numastat`` counters are often misread:

* ``numa_hit`` / ``numa_miss``: the allocation landed on the node the *memory
  policy* preferred, or didn't (the preferred node was full). Policy success,
  not locality.
* ``local_node`` / ``other_node``: the allocation landed on the node *where the
  allocating task was running*, or on another one. This is allocation
  locality.

Neither says where memory is *accessed* from later, after the scheduler moves
threads. Two better sources are:

* AutoNUMA hinting faults (``numa_hint_faults`` / ``numa_hint_faults_local`` in
  /proc/vmstat): sampled real accesses, when ``kernel.numa_balancing=1``.
* ``/proc/<pid>/numa_maps`` (where the pages live) compared with where the
  process's threads actually ran (``/proc/<pid>/stat`` processor field). Combined
  with the SLIT distance matrix, that gives a relative memory-access cost.
"""

from __future__ import annotations

import re

from ..parsers.proc import parse_meminfo, parse_numa_maps, parse_vmstat
from ..topology import Topology
from .findings import DomainResult, Severity, Thresholds
from .window import Window, delta_dict

_NODE_FILE = re.compile(r"/sys/devices/system/node/node(\d+)/(numastat|meminfo)$")


def _numastat(text: str) -> dict[str, int]:
    return {k: int(v) for k, v in (line.split() for line in text.splitlines() if line.strip())}


def analyze_numa(w: Window, topo: Topology, th: Thresholds, process_node_residency: dict[int, float] | None) -> DomainResult:
    r = DomainResult()
    m = r.metrics
    nodes = topo.nodes
    m["nodes"] = len(nodes)
    m["distances"] = topo.distances
    if len(nodes) < 2:
        m["applicable"] = False
        r.add(
            "numa.single_node",
            "numa",
            Severity.INFO,
            "Single NUMA node: locality analysis doesn't apply",
            {"nodes": len(nodes)},
            "On multi-socket or sub-NUMA-clustered servers, record there to get locality findings.",
        )
        return r
    m["applicable"] = True

    per_node: dict[int, dict[str, int]] = {}
    for n in nodes:
        pair = w.pair(f"/sys/devices/system/node/node{n}/numastat", _numastat)
        if pair:
            per_node[n] = delta_dict(pair[0], pair[1])
    if per_node:
        local = sum(d.get("local_node", 0) for d in per_node.values())
        other = sum(d.get("other_node", 0) for d in per_node.values())
        hit = sum(d.get("numa_hit", 0) for d in per_node.values())
        miss = sum(d.get("numa_miss", 0) for d in per_node.values())
        m["remote_alloc_frac"] = other / (local + other) if local + other else None
        m["policy_miss_frac"] = miss / (hit + miss) if hit + miss else None
        m["per_node_alloc"] = {
            n: {"local": d.get("local_node", 0), "other": d.get("other_node", 0), "miss": d.get("numa_miss", 0)} for n, d in per_node.items()
        }
        if m["remote_alloc_frac"] and m["remote_alloc_frac"] > th.remote_alloc_warn:
            r.add(
                "numa.remote_alloc",
                "numa",
                Severity.WARNING,
                f"{m['remote_alloc_frac']:.0%} of page allocations landed on a node other than the allocating CPU's",
                {"remote_alloc_frac": round(m["remote_alloc_frac"], 3), "policy_miss_frac": round(m["policy_miss_frac"] or 0, 3)},
                "Bind CPU and memory together (numactl --cpunodebind=N --membind=N, or cpuset.mems) and check for interleave or preferred policies set by the runtime.",
                "Remote DRAM costs roughly 1.3-2x local latency and shares inter-socket link bandwidth.",
            )

    vm = w.pair("/proc/vmstat", parse_vmstat)
    balancing = (w.rec.static.get("/proc/sys/kernel/numa_balancing") or "").strip()
    m["numa_balancing"] = balancing or None
    if vm:
        d = delta_dict(vm[0], vm[1])
        hints, local_hints = d.get("numa_hint_faults", 0), d.get("numa_hint_faults_local", 0)
        m["hint_fault_local_frac"] = local_hints / hints if hints else None
        m["pages_migrated_per_s"] = d.get("numa_pages_migrated", 0) / vm[2]
        if hints and local_hints / hints < th.numa_hint_local_warn:
            r.add(
                "numa.access_locality",
                "numa",
                Severity.WARNING,
                f"Only {local_hints / hints:.0%} of sampled memory accesses were node-local (AutoNUMA hinting faults)",
                {"hint_faults": hints, "local": local_hints},
                "Threads and their data are on different nodes. Partition data per node, or pin threads to the node holding their memory.",
            )
        if m["pages_migrated_per_s"] > th.numa_migrate_rate_warn:
            r.add(
                "numa.migration_storm",
                "numa",
                Severity.WARNING,
                f"AutoNUMA is migrating {m['pages_migrated_per_s']:,.0f} pages/s",
                {"pages_migrated_per_s": round(m["pages_migrated_per_s"])},
                "Threads are bouncing between nodes and dragging memory with them. Pin the workload, or disable numa_balancing for it.",
                "Each migration copies 4 KiB (or 2 MiB) and causes TLB shootdowns; it only pays off if the thread then stays put.",
            )
    if balancing == "0" and m.get("remote_alloc_frac") and m["remote_alloc_frac"] > th.remote_alloc_warn:
        r.add(
            "numa.balancing_off",
            "numa",
            Severity.INFO,
            "kernel.numa_balancing is off while remote allocations are high",
            {},
            "Either pin explicitly or enable AutoNUMA (sysctl kernel.numa_balancing=1) and measure.",
        )

    # Per-node free memory: one full node silently pushes allocations remote.
    for n in nodes:
        info = w.latest(f"/sys/devices/system/node/node{n}/meminfo", parse_meminfo)
        if info and info.get("MemTotal"):
            frac = info.get("MemFree", 0) / info["MemTotal"]
            m.setdefault("node_free_frac", {})[n] = round(frac, 4)
            if frac < th.node_free_frac_warn:
                others = [x for x in nodes if x != n]
                r.add(
                    "numa.node_exhausted",
                    "numa",
                    Severity.WARNING,
                    f"Node {n} is nearly full ({frac:.1%} free): new allocations will spill to node(s) {others} or trigger reclaim",
                    {"node": n, "free_frac": round(frac, 4), "zone_reclaim_mode": (w.rec.static.get("/proc/sys/vm/zone_reclaim_mode") or "").strip()},
                    "Rebalance placement or interleave large shared buffers. With zone_reclaim_mode=0 (default) the kernel goes remote rather than reclaim locally.",
                )

    _process_placement(w, topo, th, r, process_node_residency)
    return r


def _process_placement(w: Window, topo: Topology, th: Thresholds, r: DomainResult, residency: dict[int, float] | None) -> None:
    pid = w.rec.pid
    if not pid:
        return
    maps = w.latest(f"/proc/{pid}/numa_maps", parse_numa_maps)
    if not maps or not maps.total_pages:
        return
    m = r.metrics
    mem_frac = {n: c / maps.total_pages for n, c in sorted(maps.pages_by_node.items())}
    m["process_memory_by_node"] = {n: round(f, 3) for n, f in mem_frac.items()}
    m["process_memory_policies"] = maps.policies
    if not residency:
        return
    # Expected distance a memory access travels, given where threads ran and where pages live; 10 = all local.
    # Under a uniform-access assumption (every thread touches all of the heap):
    cost = sum(cf * mf * topo.distance(cn, mn) for cn, cf in residency.items() for mn, mf in mem_frac.items())
    remote = sum(cf * mf for cn, cf in residency.items() for mn, mf in mem_frac.items() if cn != mn)
    m["process_expected_distance"] = round(cost, 2)
    m["process_remote_access_frac_est"] = round(remote, 3)
    # A process spread over both nodes with memory spread the same way is fine
    # (each thread can work on local data); 50% "remote" under uniform access is
    # just the interleave baseline. What signals misplacement is a *mismatch*
    # between where threads run and where memory lives: total variation distance.
    nodes = set(residency) | set(mem_frac)
    mismatch = 0.5 * sum(abs(residency.get(n, 0.0) - mem_frac.get(n, 0.0)) for n in nodes)
    m["process_placement_mismatch"] = round(mismatch, 3)
    if mismatch > th.numa_placement_mismatch_warn:
        main_cpu_node = max(residency, key=lambda k: residency[k])
        main_mem_node = max(mem_frac, key=lambda k: mem_frac[k])
        r.add(
            "numa.process_misplaced",
            "numa",
            Severity.WARNING if mismatch < 0.6 else Severity.CRITICAL,
            f"About {remote:.0%} of the process's memory accesses are likely remote: threads ran mostly on node {main_cpu_node}, memory sits mostly on node {main_mem_node}",
            {
                "cpu_node_residency": residency,
                "memory_by_node": m["process_memory_by_node"],
                "placement_mismatch": m["process_placement_mismatch"],
                "expected_slit_distance": m["process_expected_distance"],
            },
            f"Start it with numactl --cpunodebind={main_mem_node} --membind={main_mem_node}, or migratepages {pid} {main_mem_node} {main_cpu_node}.",
            "Typical cause: memory was first-touched by an initialisation thread on one node, then worker threads were scheduled on another.",
        )
