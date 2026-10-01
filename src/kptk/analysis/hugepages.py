"""Transparent Huge Pages and hugetlbfs: configuration, effectiveness and cost.

Huge pages trade TLB reach for allocation cost:

* **Benefit**: one 2 MiB TLB entry covers 512 base pages, so random-access workloads
  with big heaps spend far fewer cycles in page walks.
* **Costs**: obtaining contiguous 2 MiB physical blocks may need *compaction*. With
  ``defrag=always`` that happens synchronously in the page-fault path, causing
  latency spikes; with ``defer`` khugepaged does it in the background. Huge pages
  can also bloat RSS (sparse use of a 2 MiB region) and must be split under pressure.

The useful questions are: is the workload actually getting huge pages
(coverage), are allocations failing (fallback), and is anyone paying for
compaction (``compact_stall``)?
"""

from __future__ import annotations

import re

from ..parsers.proc import parse_meminfo, parse_smaps_rollup, parse_vmstat
from ..parsers.sysfs import parse_bracket_choice
from .findings import DomainResult, Severity, Thresholds
from .window import Window, delta_dict

_POOL = re.compile(r"/sys/kernel/mm/hugepages/hugepages-(\d+)kB/(\w+)$")
THP = "/sys/kernel/mm/transparent_hugepage/"


def hugetlb_pools(static: dict[str, str]) -> dict[int, dict[str, int]]:
    pools: dict[int, dict[str, int]] = {}
    for path, text in static.items():
        if m := _POOL.match(path):
            pools.setdefault(int(m.group(1)), {})[m.group(2)] = int(text.strip() or 0)
    return pools


def analyze_hugepages(w: Window, th: Thresholds, dtlb_miss_ratio: float | None) -> DomainResult:
    r = DomainResult()
    m = r.metrics
    s = w.rec.static
    enabled = parse_bracket_choice(s.get(THP + "enabled", ""))
    defrag = parse_bracket_choice(s.get(THP + "defrag", ""))
    m["thp_enabled"], m["thp_defrag"] = enabled, defrag
    m["khugepaged_max_ptes_none"] = (s.get(THP + "khugepaged/max_ptes_none") or "").strip() or None

    if enabled == "always" and defrag == "always":
        r.add(
            "hp.sync_compaction",
            "hugepages",
            Severity.WARNING,
            "THP enabled=always with defrag=always: page faults may stall on synchronous compaction",
            {"enabled": enabled, "defrag": defrag},
            "Use defrag=defer+madvise: background compaction for everyone, synchronous only for regions that asked with MADV_HUGEPAGE.",
            "Under fragmentation every anonymous fault can enter direct compaction, a classic source of multi-ms latency spikes in databases and JVMs.",
        )

    vm = w.pair("/proc/vmstat", parse_vmstat)
    if vm:
        d = delta_dict(vm[0], vm[1])
        dt = vm[2]
        alloc, fallback = d.get("thp_fault_alloc", 0), d.get("thp_fault_fallback", 0)
        m["thp_fault_alloc_per_s"] = alloc / dt
        m["thp_fallback_frac"] = fallback / (alloc + fallback) if alloc + fallback else None
        m["thp_collapse_per_s"] = d.get("thp_collapse_alloc", 0) / dt
        m["thp_split_per_s"] = d.get("thp_split_page", 0) / dt
        m["compact_stall_per_s"] = d.get("compact_stall", 0) / dt
        m["compact_fail_frac"] = d.get("compact_fail", 0) / d["compact_stall"] if d.get("compact_stall") else None
        if m["thp_fallback_frac"] is not None and m["thp_fallback_frac"] > th.thp_fallback_warn and alloc + fallback >= 10:
            r.add(
                "hp.thp_fallback",
                "hugepages",
                Severity.WARNING,
                f"{m['thp_fallback_frac']:.0%} of huge-page faults fell back to 4 KiB pages",
                {"thp_fault_alloc": alloc, "thp_fault_fallback": fallback},
                "Physical memory is fragmented. Reserve hugetlbfs pages at boot for critical workloads, raise vm.min_free_kbytes, or trigger compaction off-peak (echo 1 > /proc/sys/vm/compact_memory).",
            )
        if m["compact_stall_per_s"] > th.compact_stall_rate_warn:
            r.add(
                "hp.compaction_stalls",
                "hugepages",
                Severity.WARNING,
                f"{m['compact_stall_per_s']:.1f} direct compaction stalls/s",
                {"compact_stall_per_s": round(m["compact_stall_per_s"], 2), "compact_fail_frac": m["compact_fail_frac"]},
                "Tasks are compacting memory synchronously to get contiguous pages. Prefer THP defrag=defer+madvise and keep free-memory headroom.",
            )

    meminfo = w.latest("/proc/meminfo", parse_meminfo)
    pools = hugetlb_pools(s)
    m["hugetlb_pools"] = pools
    for size_kib, p in pools.items():
        nr, free, resv = p.get("nr_hugepages", 0), p.get("free_hugepages", 0), p.get("resv_hugepages", 0)
        idle_kib = max(0, free - resv) * size_kib
        if nr and idle_kib >= th.hugetlb_waste_kib_warn:
            r.add(
                "hp.hugetlb_idle",
                "hugepages",
                Severity.INFO,
                f"{idle_kib >> 20} GiB of {size_kib // 1024} MiB hugetlb pages are reserved in the pool but unused",
                {"page_size_kib": size_kib, "nr": nr, "free": free, "reserved": resv},
                "hugetlb pages are unavailable to the rest of the system. Shrink vm.nr_hugepages, or confirm the consumer (DPDK, a DB) starts later.",
            )
    if meminfo:
        m["anon_huge_kib_system"] = meminfo.get("AnonHugePages", 0)

    pid = w.rec.pid
    if pid:
        smaps = w.latest(f"/proc/{pid}/smaps_rollup", parse_smaps_rollup)
        if smaps:
            anon = smaps.get("Anonymous", 0)
            huge = smaps.get("AnonHugePages", 0)
            m["process_anon_kib"] = anon
            m["process_thp_coverage"] = huge / anon if anon else None
            m["process_hugetlb_kib"] = smaps.get("Private_Hugetlb", 0) + smaps.get("Shared_Hugetlb", 0)
            coverage = m["process_thp_coverage"] or 0.0
            if anon >= th.thp_min_anon_kib and coverage < th.thp_coverage_low and not m["process_hugetlb_kib"]:
                tlb = f" and dTLB miss rate is {dtlb_miss_ratio:.1%}" if dtlb_miss_ratio is not None else ""
                sev = Severity.WARNING if (dtlb_miss_ratio or 0) > th.dtlb_miss_warn else Severity.INFO
                hint = (
                    "madvise(MADV_HUGEPAGE) on the hot heap regions (THP is in madvise mode)"
                    if enabled == "madvise"
                    else "enabling THP (or hugetlbfs for predictable latency)"
                )
                r.add(
                    "hp.low_coverage",
                    "hugepages",
                    sev,
                    f"Only {coverage:.0%} of the process's {anon >> 10} MiB anonymous memory is backed by huge pages{tlb}",
                    {"anon_kib": anon, "anon_huge_kib": huge, "thp_enabled": enabled},
                    f"Try {hint}. For jemalloc/tcmalloc there are allocator options to request huge pages. Measure: the benefit depends on random-access TLB pressure.",
                )
    return r
