"""Run every domain analyzer over a recording and assemble the report."""

from __future__ import annotations

import time
from typing import Any

from ..parsers.proc import busy_ticks, parse_pressure, parse_proc_stat, parse_schedstat, parse_vmstat, total_ticks
from ..parsers.tools import PerfStatEvent
from ..record import Recording
from ..topology import Topology, from_static
from .cache import analyze_cache
from .cpu import analyze_cpu
from .findings import DomainResult, Finding, Severity, Thresholds
from .hugepages import analyze_hugepages
from .memory import analyze_memory
from .numa import analyze_numa
from .window import Window


def events_from_perf_stat(parsed: dict[str, PerfStatEvent]) -> dict[str, dict[str, float]]:
    return {k: {"value": e.value, "running_frac": e.running_pct / 100} for k, e in parsed.items()}


def audit_config(static: dict[str, str], topo: Topology) -> DomainResult:
    """Static configuration checks that need no workload."""
    r = DomainResult()
    m = r.metrics

    def val(path: str) -> str | None:
        v = static.get(path)
        return v.strip() if v is not None else None

    m["swappiness"] = val("/proc/sys/vm/swappiness")
    m["zone_reclaim_mode"] = val("/proc/sys/vm/zone_reclaim_mode")
    m["numa_balancing"] = val("/proc/sys/kernel/numa_balancing")
    m["perf_event_paranoid"] = val("/proc/sys/kernel/perf_event_paranoid")
    m["governors"] = sorted(set(topo.governors.values()))
    if m["zone_reclaim_mode"] not in (None, "0") and len(topo.nodes) > 1:
        r.add(
            "config.zone_reclaim",
            "config",
            Severity.WARNING,
            f"vm.zone_reclaim_mode={m['zone_reclaim_mode']}: the kernel reclaims local page cache instead of allocating remotely",
            {"zone_reclaim_mode": m["zone_reclaim_mode"]},
            "Good for strictly partitioned HPC workloads; usually bad for file servers and databases (page cache thrash). Default is 0.",
        )
    if "powersave" in m["governors"]:
        r.add(
            "config.governor",
            "config",
            Severity.INFO,
            "CPU frequency governor 'powersave' is active",
            {"governors": m["governors"]},
            "On intel_pstate/amd-pstate 'powersave' is dynamic and often fine; for latency-critical services compare against 'performance' and check C-state exit latency.",
        )
    try:
        paranoid = int(m["perf_event_paranoid"] or 0)
    except ValueError:
        paranoid = 0
    if paranoid > 2:
        r.add(
            "config.perf_paranoid",
            "config",
            Severity.INFO,
            f"kernel.perf_event_paranoid={paranoid}: unprivileged hardware counters are disabled",
            {"perf_event_paranoid": paranoid},
            "For profiling sessions grant CAP_PERFMON to the profiler, or temporarily set perf_event_paranoid=2 (own processes, user+kernel).",
        )
    return r


def use_summary(metrics: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Brendan Gregg's USE method: Utilisation, Saturation, Errors, per resource."""
    cpu, mem, numa, hp = metrics.get("cpu", {}), metrics.get("memory", {}), metrics.get("numa", {}), metrics.get("hugepages", {})
    avail = mem.get("mem_available_min_frac")
    return [
        {
            "resource": "CPU",
            "utilization": cpu.get("utilization"),
            "saturation": {"psi_some": cpu.get("psi_cpu_some"), "runq_waiting_tasks": cpu.get("runq_waiting_tasks_avg")},
            "errors": {"steal_frac": cpu.get("steal_frac")},
        },
        {
            "resource": "Memory capacity",
            "utilization": (1 - avail) if avail is not None else None,
            "saturation": {
                "psi_some": mem.get("psi_memory_some"),
                "direct_reclaim_per_s": mem.get("direct_reclaim_stalls_per_s"),
                "swap_pages_per_s": mem.get("swap_pages_per_s"),
            },
            "errors": {"oom_kills": mem.get("oom_kills")},
        },
        {
            "resource": "NUMA interconnect",
            "utilization": numa.get("remote_alloc_frac"),
            "saturation": {"pages_migrated_per_s": numa.get("pages_migrated_per_s")},
            "errors": {"policy_miss_frac": numa.get("policy_miss_frac")},
        },
        {
            "resource": "Huge pages",
            "utilization": hp.get("process_thp_coverage"),
            "saturation": {"compact_stall_per_s": hp.get("compact_stall_per_s")},
            "errors": {"thp_fallback_frac": hp.get("thp_fallback_frac")},
        },
    ]


def timeseries(rec: Recording) -> dict[str, list[float | None]]:
    """Per-interval rates for the report's sparklines."""
    out: dict[str, list[float | None]] = {"t": [], "cpu_util": [], "runq_waiting": [], "faults_per_s": [], "major_faults_per_s": [], "psi_mem_some": []}
    for a, b in zip(rec.samples, rec.samples[1:], strict=False):
        dt = b.ts - a.ts
        if dt <= 0:
            continue
        out["t"].append(round(b.ts - rec.samples[0].ts, 3))
        util = rq = faults = majf = psim = None
        if "/proc/stat" in a.files and "/proc/stat" in b.files:
            sa, sb = parse_proc_stat(a.files["/proc/stat"]), parse_proc_stat(b.files["/proc/stat"])
            d = {k: sb.cpu_total[k] - sa.cpu_total.get(k, 0) for k in sb.cpu_total}
            util = busy_ticks(d) / (total_ticks(d) or 1)
        if "/proc/schedstat" in a.files and "/proc/schedstat" in b.files:
            qa, qb = parse_schedstat(a.files["/proc/schedstat"])[1], parse_schedstat(b.files["/proc/schedstat"])[1]
            rq = sum(qb[c].run_delay_ns - qa[c].run_delay_ns for c in qb if c in qa) / 1e9 / dt
        if "/proc/vmstat" in a.files and "/proc/vmstat" in b.files:
            va, vb = parse_vmstat(a.files["/proc/vmstat"]), parse_vmstat(b.files["/proc/vmstat"])
            faults = (vb.get("pgfault", 0) - va.get("pgfault", 0)) / dt
            majf = (vb.get("pgmajfault", 0) - va.get("pgmajfault", 0)) / dt
        if "/proc/pressure/memory" in a.files and "/proc/pressure/memory" in b.files:
            pa, pb = parse_pressure(a.files["/proc/pressure/memory"]), parse_pressure(b.files["/proc/pressure/memory"])
            psim = (pb["some"].total_us - pa["some"].total_us) / 1e6 / dt
        for k, v in (("cpu_util", util), ("runq_waiting", rq), ("faults_per_s", faults), ("major_faults_per_s", majf), ("psi_mem_some", psim)):
            out[k].append(None if v is None else round(v, 4))
    return out


def data_quality(rec: Recording) -> dict[str, Any]:
    pid = rec.pid
    costs = [s.cost_s for s in rec.samples]
    dq: dict[str, Any] = {
        "samples": len(rec.samples),
        "duration_s": round(rec.duration_s, 3),
        "mean_sample_cost_ms": round(1e3 * sum(costs) / len(costs), 3) if costs else None,
        "overruns": rec.meta.get("overruns", 0),
        "missing": [],
    }
    first = rec.samples[0].files if rec.samples else {}
    for path, why in (
        ("/proc/schedstat", "CONFIG_SCHEDSTATS off: no run-queue latency"),
        ("/proc/pressure/cpu", "PSI unavailable (CONFIG_PSI or psi=0): no saturation stall times"),
    ):
        if path not in first:
            dq["missing"].append(why)
    if pid:
        alive = [s for s in rec.samples if f"/proc/{pid}/stat" in s.files]
        dq["process_samples"] = len(alive)
        if len(alive) < len(rec.samples):
            dq["missing"].append(f"pid {pid} exited or became unreadable after {len(alive)} samples")
        if not any(f"/proc/{pid}/numa_maps" in s.files for s in rec.samples):
            dq["missing"].append("numa_maps unreadable (permissions or short run): no per-process placement")
    return dq


def analyze(rec: Recording, events: dict[str, dict[str, float]] | None = None, thresholds: Thresholds | None = None) -> dict[str, Any]:
    th = thresholds or Thresholds()
    w = Window(rec)
    topo = from_static(rec.static)
    events = events or (rec.counters or {}).get("events")

    cpu = analyze_cpu(w, topo, th)
    mem = analyze_memory(w, th)
    numa = analyze_numa(w, topo, th, cpu.metrics.get("process_node_residency"))
    cache = analyze_cache(events, topo, th, mem.metrics.get("process_anon_kib"))
    hp = analyze_hugepages(w, th, cache.metrics.get("dtlb_miss_ratio"))
    cfg = audit_config(rec.static, topo)

    domains: dict[str, DomainResult] = {"cpu": cpu, "memory": mem, "numa": numa, "cache": cache, "hugepages": hp, "config": cfg}
    findings: list[Finding] = [f for d in domains.values() for f in d.findings]
    findings.sort(key=lambda f: (-int(f.severity), f.domain))
    metrics = {name: d.metrics for name, d in domains.items()}
    return {
        "generated_at": time.time(),
        "meta": rec.meta,
        "topology": topo.summary(),
        "metrics": metrics,
        "use": use_summary(metrics),
        "findings": [f.to_dict() for f in findings],
        "counters": events,
        "timeseries": timeseries(rec),
        "data_quality": data_quality(rec),
    }
