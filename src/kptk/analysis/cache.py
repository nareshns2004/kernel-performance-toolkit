"""Cache, branch and TLB efficiency from hardware counters.

Normalising by instructions (MPKI, misses per kilo-instruction) makes
numbers comparable across runs of different length, and connects directly
to cost: at ~80 ns per DRAM miss and 3 GHz, an LLC MPKI of 10 costs about 2.4
cycles per instruction if nothing overlaps.

The classification is a coarse "top-down lite". Real Top-down Microarchitecture
Analysis needs model-specific events (topdown-* slots); generic perf events
can only point in a direction. Findings say so.
"""

from __future__ import annotations

from ..topology import Topology
from .findings import DomainResult, Severity, Thresholds


def _ratio(a: float | None, b: float | None) -> float | None:
    return a / b if a is not None and b else None


def analyze_cache(events: dict[str, dict[str, float]] | None, topo: Topology, th: Thresholds, anon_kib: int | None) -> DomainResult:
    r = DomainResult()
    m = r.metrics
    m["caches"] = [{"level": c.level, "type": c.type, "size_kib": c.size_bytes // 1024, "shared_by_cpus": len(c.shared_cpus)} for c in topo.caches]
    if not events:
        m["available"] = False
        r.add(
            "cache.no_counters",
            "cache",
            Severity.INFO,
            "No hardware counters in this recording; cache and TLB efficiency can't be measured",
            {},
            "Use `kptk profile -- <cmd>` with perf_event_paranoid <= 2 (or CAP_PERFMON), or pass `--perf-stat` with saved `perf stat -x;` output.",
        )
        return r
    m["available"] = True

    def v(name: str) -> float | None:
        e = events.get(name)
        return e["value"] if e else None

    instr = v("instructions")
    kinstr = instr / 1000 if instr else None
    m["ipc"] = _ratio(instr, v("cycles"))
    m["llc_miss_ratio"] = _ratio(v("cache-misses"), v("cache-references"))
    m["llc_mpki"] = _ratio(v("cache-misses"), kinstr)
    m["branch_miss_ratio"] = _ratio(v("branch-misses"), v("branches"))
    m["branch_mpki"] = _ratio(v("branch-misses"), kinstr)
    m["l1d_miss_ratio"] = _ratio(v("L1-dcache-load-misses"), v("L1-dcache-loads"))
    m["dtlb_miss_ratio"] = _ratio(v("dTLB-load-misses"), v("dTLB-loads"))
    m["dtlb_mpki"] = _ratio(v("dTLB-load-misses"), kinstr)
    m["backend_stall_frac"] = _ratio(v("stalled-cycles-backend"), v("cycles"))
    m["frontend_stall_frac"] = _ratio(v("stalled-cycles-frontend"), v("cycles"))
    running = [e.get("running_frac", 1.0) for e in events.values() if e.get("running_frac") is not None]
    m["min_running_frac"] = min(running) if running else None

    if m["min_running_frac"] is not None and m["min_running_frac"] < th.multiplex_running_warn:
        r.add(
            "cache.multiplexed",
            "cache",
            Severity.INFO,
            f"Counters were multiplexed (some counted only {m['min_running_frac']:.0%} of the time); values are extrapolated",
            {"min_running_frac": round(m["min_running_frac"], 3)},
            "Request fewer events per run, or group related events, for exact ratios. Treat cross-event ratios as estimates.",
            "With more events than PMU counters the kernel time-slices them and scales by enabled/running time.",
        )

    ipc, mpki = m["ipc"], m["llc_mpki"]
    memory_bound = (ipc is not None and ipc < th.ipc_low and mpki is not None and mpki > th.llc_mpki_warn) or (m["backend_stall_frac"] or 0) > 0.4
    if memory_bound:
        llc = topo.llc
        ws = f"; anonymous working set {anon_kib // 1024} MiB vs {llc.size_bytes >> 20} MiB L{llc.level}" if anon_kib and llc else ""
        r.add(
            "cache.memory_bound",
            "cache",
            Severity.WARNING,
            f"Likely memory-bound: IPC {ipc:.2f}, LLC {mpki:.1f} misses per 1k instructions{ws}"
            if ipc is not None and mpki is not None
            else "Likely memory-bound (high backend stalls)",
            {k: round(m[k], 4) for k in ("ipc", "llc_mpki", "llc_miss_ratio", "backend_stall_frac") if m.get(k) is not None},
            "Improve locality: tile or block loops to fit the LLC, use struct-of-arrays for scanned fields, prefer contiguous containers over pointer-chasing, and prefetch for irregular access.",
            "Low IPC together with many LLC misses means the core is waiting on DRAM. Top-down analysis (perf stat --topdown) can confirm it on supported CPUs.",
        )
    if m["branch_miss_ratio"] is not None and m["branch_miss_ratio"] > th.branch_miss_warn:
        r.add(
            "cache.bad_speculation",
            "cache",
            Severity.INFO,
            f"Branch misprediction rate {m['branch_miss_ratio']:.1%} ({m['branch_mpki'] or 0:.1f} per 1k instructions)",
            {"branch_miss_ratio": round(m["branch_miss_ratio"], 4)},
            "Make data-dependent branches predictable (sort, partition), use branchless selects, or use PGO so the compiler lays out hot paths.",
            "Each mispredict flushes the pipeline (~15-20 cycles on modern cores).",
        )
    if m["dtlb_miss_ratio"] is not None and m["dtlb_miss_ratio"] > th.dtlb_miss_warn:
        r.add(
            "cache.tlb_pressure",
            "cache",
            Severity.WARNING,
            f"dTLB miss rate {m['dtlb_miss_ratio']:.1%} ({m['dtlb_mpki'] or 0:.2f} per 1k instructions): page walks are significant",
            {"dtlb_miss_ratio": round(m["dtlb_miss_ratio"], 4)},
            "Back the hot heap with huge pages (madvise(MADV_HUGEPAGE), THP, or hugetlbfs). A 2 MiB page covers 512x the reach of a 4 KiB TLB entry. See the hugepages findings.",
            "With a few thousand TLB entries x 4 KiB, reach is only a few MiB; larger random-access working sets miss constantly.",
        )
    if ipc is not None and ipc > 2.0 and not memory_bound:
        r.add(
            "cache.efficient",
            "cache",
            Severity.INFO,
            f"IPC {ipc:.2f}: the core pipeline is well utilised",
            {"ipc": round(ipc, 3)},
            "Gains now come from doing less work (algorithms, vectorisation), not from memory layout.",
        )
    return r
