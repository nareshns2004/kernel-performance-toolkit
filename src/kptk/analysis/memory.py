"""Memory pressure and page faults.

* **Minor fault**: the page is in memory (or just needs zeroing), so only a page-table
  update is needed. Costs ~0.1-1 µs, but at millions per second it shows up as
  system time. Typical sources are first-touch of fresh allocations, fork/COW, and mmap churn.
* **Major fault**: the page had to be read from storage (page cache miss, swap-in).
  Costs ~10 µs (NVMe) to ~10 ms (HDD), and stalls the thread.
* **Direct reclaim** (``allocstall_*``, ``pgscan_direct``): an allocating task had
  to free memory itself because kswapd fell behind. It turns allocation into a
  latency spike.
"""

from __future__ import annotations

from ..parsers.proc import parse_meminfo, parse_pid_stat, parse_pressure, parse_smaps_rollup, parse_vmstat
from .findings import DomainResult, Severity, Thresholds
from .window import Window, delta_dict, rate


def analyze_memory(w: Window, th: Thresholds) -> DomainResult:
    r = DomainResult()
    m = r.metrics
    vm = w.pair("/proc/vmstat", parse_vmstat)
    if vm:
        a, b, dt = vm
        d = delta_dict(a, b)
        allocstall = [k for k in d if k.startswith("allocstall")]
        m["faults_per_s"] = rate(d, dt, "pgfault")
        m["major_faults_per_s"] = rate(d, dt, "pgmajfault")
        m["swap_pages_per_s"] = rate(d, dt, "pswpin", "pswpout")
        m["direct_reclaim_stalls_per_s"] = rate(d, dt, *allocstall)
        m["pgscan_direct_per_s"] = rate(d, dt, "pgscan_direct")
        m["pgscan_kswapd_per_s"] = rate(d, dt, "pgscan_kswapd")
        m["oom_kills"] = d.get("oom_kill", 0)
        m["workingset_refault_per_s"] = rate(d, dt, "workingset_refault_file", "workingset_refault_anon")
        if m["direct_reclaim_stalls_per_s"] > th.direct_reclaim_rate_warn:
            r.add(
                "mem.direct_reclaim",
                "memory",
                Severity.WARNING,
                f"Direct reclaim: {m['direct_reclaim_stalls_per_s']:.1f} allocation stalls/s",
                {"allocstall_per_s": round(m["direct_reclaim_stalls_per_s"], 2), "pgscan_direct_per_s": round(m["pgscan_direct_per_s"])},
                "Raise vm.min_free_kbytes / watermark_scale_factor so kswapd starts earlier, or reduce memory pressure (cgroup limits, page cache footprint).",
                "In direct reclaim the allocating thread scans and frees pages itself, which adds ms-scale tail latency to malloc and page faults.",
            )
        if m["swap_pages_per_s"] > th.swap_rate_warn:
            r.add(
                "mem.swapping",
                "memory",
                Severity.CRITICAL,
                f"Swapping {m['swap_pages_per_s']:.0f} pages/s",
                {"pswpin_out_per_s": round(m["swap_pages_per_s"])},
                "The working set doesn't fit. Reduce memory use, add memory, or set memory.high on low-priority cgroups.",
            )
        if m["oom_kills"]:
            r.add(
                "mem.oom",
                "memory",
                Severity.CRITICAL,
                f"{m['oom_kills']} OOM kill(s) during the window",
                {"oom_kills": m["oom_kills"]},
                "Check dmesg for the victim and the cgroup limit that was hit.",
            )

    mi = w.series("/proc/meminfo", parse_meminfo)
    if mi:
        total = mi[-1][1].get("MemTotal", 0)
        avail_min = min(x.get("MemAvailable", total) for _, x in mi)
        m["mem_total_kib"] = total
        m["mem_available_min_frac"] = avail_min / total if total else None
        if total and avail_min / total < th.mem_available_frac_warn:
            r.add(
                "mem.low_available",
                "memory",
                Severity.WARNING,
                f"MemAvailable dropped to {avail_min / total:.1%} of RAM",
                {"mem_available_min_kib": avail_min},
                "Expect reclaim and refaults. Find the largest consumers (smaps_rollup, cgroup memory.stat).",
            )

    psi = w.pair("/proc/pressure/memory", parse_pressure)
    if psi:
        a2, b2, dt = psi
        m["psi_memory_some"] = (b2["some"].total_us - a2["some"].total_us) / 1e6 / dt
        m["psi_memory_full"] = (b2["full"].total_us - a2["full"].total_us) / 1e6 / dt
        if m["psi_memory_full"] > th.mem_psi_full_warn:
            r.add(
                "mem.pressure",
                "memory",
                Severity.CRITICAL if m["psi_memory_full"] > 0.1 else Severity.WARNING,
                f"Memory stalls: all non-idle tasks were blocked on memory {m['psi_memory_full']:.1%} of the time",
                {"psi_memory_some": round(m["psi_memory_some"], 4), "psi_memory_full": round(m["psi_memory_full"], 4)},
                "This is lost throughput, not just latency. Reduce working set or memory limits pressure before tuning anything else.",
                "PSI 'full' means no task could make progress because of reclaim, refaults or swap-in.",
            )

    pid = w.rec.pid
    if pid:
        ps = w.pair(f"/proc/{pid}/stat", parse_pid_stat)
        if ps:
            a3, b3, dt = ps
            m["process_minor_faults_per_s"] = (b3.minflt - a3.minflt) / dt
            m["process_major_faults_per_s"] = (b3.majflt - a3.majflt) / dt
            m["process_rss_kib"] = b3.rss_pages * w.page_kib
            if m["process_major_faults_per_s"] > th.major_fault_rate_warn:
                r.add(
                    "mem.major_faults",
                    "memory",
                    Severity.WARNING,
                    f"{m['process_major_faults_per_s']:.0f} major page faults/s in the process",
                    {"major_faults_per_s": round(m["process_major_faults_per_s"], 1)},
                    "Each major fault blocks on storage. Prefetch with madvise(MADV_WILLNEED) or readahead, mlock hot regions, or check whether this is swap-in.",
                )
            if m["process_minor_faults_per_s"] > th.minor_fault_rate_warn:
                r.add(
                    "mem.minor_fault_storm",
                    "memory",
                    Severity.WARNING,
                    f"{m['process_minor_faults_per_s']:,.0f} minor faults/s: heavy first-touch or mmap churn",
                    {"minor_faults_per_s": round(m["process_minor_faults_per_s"])},
                    "Reuse buffers instead of freeing and reallocating (the allocator may return memory to the OS), pre-fault with MAP_POPULATE, or use huge pages to cut fault count by 512x.",
                    "Allocators like glibc malloc release large free blocks with munmap/madvise, so the next allocation faults again.",
                )
        smaps = w.latest(f"/proc/{pid}/smaps_rollup", parse_smaps_rollup)
        if smaps:
            m["process_anon_kib"] = smaps.get("Anonymous", 0)
            m["process_swap_kib"] = smaps.get("Swap", 0)
    return r
