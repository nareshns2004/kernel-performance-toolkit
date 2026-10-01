"""Synthetic recordings of a two-socket server, for tests and ``kptk demo``.

Generates internally consistent /proc and /sys *text* (the same formats the
recorder captures), so the full parse -> analyse path is exercised. This lets
every finding be tested deterministically and demonstrated on a single-node
laptop. Real recordings in ``samples/`` show what live data looks like.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .record import Recording, Sample

USER_HZ = 100


@dataclass
class Spec:
    sockets: int = 2
    cores_per_socket: int = 8
    smt: int = 2
    duration_s: int = 10
    interval_s: float = 1.0
    mem_per_node_gib: int = 64
    # system
    cpu_util: float = 0.40
    steal: float = 0.0
    runq_waiting_tasks: float = 0.05
    psi_cpu_some: float = 0.01
    psi_mem_some: float = 0.0
    psi_mem_full: float = 0.0
    faults_per_s: float = 20_000
    major_faults_per_s: float = 0.0
    allocstall_per_s: float = 0.0
    swap_per_s: float = 0.0
    node_free_frac: tuple[float, ...] = (0.5, 0.5)
    allocs_per_s: float = 50_000
    remote_alloc_frac: float = 0.02
    numa_balancing: str = "1"
    hint_faults_per_s: float = 0.0
    hint_local_frac: float = 0.95
    migrated_per_s: float = 0.0
    zone_reclaim_mode: str = "0"
    # huge pages
    thp_enabled: str = "madvise"
    thp_defrag: str = "madvise"
    thp_alloc_per_s: float = 10
    thp_fallback_per_s: float = 0
    compact_stall_per_s: float = 0
    hugetlb_2m_nr: int = 0
    hugetlb_2m_free: int = 0
    # target process
    pid: int | None = 4242
    comm: str = "kvstore"
    threads: int = 32
    proc_cpus: float = 4.0
    proc_runq_wait_share: float = 0.02
    proc_vol_cs_per_s: float = 2_000
    proc_invol_cs_per_s: float = 100
    proc_minflt_per_s: float = 2_000
    proc_majflt_per_s: float = 0.0
    proc_anon_gib: float = 8.0
    proc_thp_coverage: float = 0.6
    cpu_node_residency: dict[int, float] = field(default_factory=lambda: {0: 0.5, 1: 0.5})
    mem_by_node: dict[int, float] = field(default_factory=lambda: {0: 0.5, 1: 0.5})
    # hardware counters (None = not collected)
    counters: dict[str, float] | None = None

    @property
    def ncpu(self) -> int:
        return self.sockets * self.cores_per_socket * self.smt


SCENARIOS: dict[str, tuple[str, Spec]] = {
    "healthy": (
        "Two-socket server, balanced and unsaturated",
        Spec(
            counters={
                "cycles": 3e10,
                "instructions": 6.6e10,
                "cache-references": 4e8,
                "cache-misses": 2e7,
                "branches": 1.2e10,
                "branch-misses": 6e7,
                "dTLB-loads": 2e10,
                "dTLB-load-misses": 4e7,
            }
        ),
    ),
    "numa_misplaced": (
        "Database whose heap was first-touched on node 1, while its threads run on node 0; THP misconfigured",
        Spec(
            comm="pgdb",
            cpu_node_residency={0: 0.95, 1: 0.05},
            mem_by_node={0: 0.15, 1: 0.85},
            remote_alloc_frac=0.35,
            hint_faults_per_s=40_000,
            hint_local_frac=0.25,
            node_free_frac=(0.45, 0.02),
            proc_anon_gib=48,
            proc_thp_coverage=0.04,
            thp_enabled="always",
            thp_defrag="always",
            thp_alloc_per_s=50,
            thp_fallback_per_s=40,
            compact_stall_per_s=12,
            hugetlb_2m_nr=2048,
            hugetlb_2m_free=2048,
            counters={
                "cycles": 4e10,
                "instructions": 2.4e10,
                "cache-references": 9e8,
                "cache-misses": 4.3e8,
                "branches": 4e9,
                "branch-misses": 2e7,
                "dTLB-loads": 8e9,
                "dTLB-load-misses": 2.4e8,
            },
        ),
    ),
    "cpu_saturated": (
        "Thread-pool oversubscription: 256 busy threads on 32 CPUs",
        Spec(
            comm="api-server",
            threads=256,
            cpu_util=0.98,
            runq_waiting_tasks=24,
            psi_cpu_some=0.85,
            proc_cpus=30,
            proc_runq_wait_share=0.45,
            proc_invol_cs_per_s=9_000,
            proc_vol_cs_per_s=3_000,
            counters={"cycles": 9e10, "instructions": 1.1e11, "cache-references": 1.5e9, "cache-misses": 1.2e8, "branches": 2.2e10, "branch-misses": 6.6e8},
        ),
    ),
    "memory_pressure": (
        "Working set exceeds RAM: direct reclaim, swapping and major faults",
        Spec(
            comm="batch-etl",
            psi_mem_some=0.35,
            psi_mem_full=0.12,
            allocstall_per_s=40,
            swap_per_s=3_000,
            major_faults_per_s=900,
            proc_majflt_per_s=850,
            proc_minflt_per_s=400_000,
            node_free_frac=(0.01, 0.015),
        ),
    ),
}


def _cpu_line(label: str, busy: int, idle: int, steal: int = 0) -> str:
    user, system = int(busy * 0.8), busy - int(busy * 0.8)
    return f"{label} {user} 0 {system} {idle} 0 0 0 {steal} 0 0"


def _proc_stat(s: Spec, t: float) -> str:
    total = int(t * USER_HZ)
    lines = []
    all_busy = all_idle = all_steal = 0
    per = []
    for c in range(s.ncpu):
        busy = int(total * s.cpu_util)
        steal = int(total * s.steal)
        idle = total - busy - steal
        per.append(_cpu_line(f"cpu{c}", busy, idle, steal))
        all_busy, all_idle, all_steal = all_busy + busy, all_idle + idle, all_steal + steal
    lines.append(_cpu_line("cpu", all_busy, all_idle, all_steal))
    lines += per
    running = max(1, round(s.cpu_util * s.ncpu + s.runq_waiting_tasks))
    lines += ["intr 1000 0", f"ctxt {int(t * 50_000)}", "btime 1700000000", f"processes {int(t * 20)}", f"procs_running {running}", "procs_blocked 0"]
    return "\n".join(lines) + "\n"


def _schedstat(s: Spec, t: float) -> str:
    out = ["version 15", f"timestamp {int(t * 250)}"]
    per_cpu_delay = s.runq_waiting_tasks / s.ncpu
    for c in range(s.ncpu):
        out.append(f"cpu{c} 0 0 0 0 0 0 {int(t * s.cpu_util * 1e9)} {int(t * per_cpu_delay * 1e9)} {int(t * 1000)}")
    return "\n".join(out) + "\n"


def _vmstat(s: Spec, t: float) -> str:
    v = {
        "pgfault": s.faults_per_s * t,
        "pgmajfault": s.major_faults_per_s * t,
        "pswpin": s.swap_per_s * t / 2,
        "pswpout": s.swap_per_s * t / 2,
        "allocstall_normal": s.allocstall_per_s * t,
        "allocstall_movable": 0,
        "pgscan_direct": s.allocstall_per_s * t * 32,
        "pgscan_kswapd": 0,
        "oom_kill": 0,
        "numa_hint_faults": s.hint_faults_per_s * t,
        "numa_hint_faults_local": s.hint_faults_per_s * t * s.hint_local_frac,
        "numa_pages_migrated": s.migrated_per_s * t,
        "thp_fault_alloc": s.thp_alloc_per_s * t,
        "thp_fault_fallback": s.thp_fallback_per_s * t,
        "thp_collapse_alloc": 0,
        "thp_split_page": 0,
        "compact_stall": s.compact_stall_per_s * t,
        "compact_fail": s.compact_stall_per_s * t * 0.5,
    }
    return "".join(f"{k} {int(1_000_000 + x)}\n" for k, x in v.items())


def _meminfo(s: Spec, prefix: str = "", total_gib: float | None = None, free_frac: float | None = None) -> str:
    total = int((total_gib or s.mem_per_node_gib * s.sockets) * (1 << 20))
    free = int(total * (free_frac if free_frac is not None else sum(s.node_free_frac) / len(s.node_free_frac)))
    rows = {
        "MemTotal": total,
        "MemFree": free,
        "MemAvailable": int(free * 1.1),
        "AnonHugePages": 0,
        "HugePages_Total": s.hugetlb_2m_nr,
        "HugePages_Free": s.hugetlb_2m_free,
        "Hugepagesize": 2048,
    }
    return "".join(f"{prefix}{k}: {v} kB\n" for k, v in rows.items())


def _psi(some: float, full: float | None, t: float) -> str:
    line = f"some avg10={some * 100:.2f} avg60={some * 100:.2f} avg300={some * 100:.2f} total={int(t * some * 1e6)}\n"
    if full is not None:
        line += f"full avg10={full * 100:.2f} avg60={full * 100:.2f} avg300={full * 100:.2f} total={int(t * full * 1e6)}\n"
    return line


def _numastat(s: Spec, t: float, node: int) -> str:
    allocs = s.allocs_per_s * t / s.sockets
    other = allocs * s.remote_alloc_frac
    return f"numa_hit {int(allocs)}\nnuma_miss 0\nnuma_foreign 0\ninterleave_hit 0\nlocal_node {int(allocs - other)}\nother_node {int(other)}\n"


def _pid_files(s: Spec, t: float, i: int) -> dict[str, str]:
    assert s.pid
    cpu_time = int(s.proc_cpus * t * USER_HZ)
    # Deterministic CPU choice per sample following the residency weights.
    nodes = sorted(s.cpu_node_residency)
    acc, pick = 0.0, nodes[-1]
    frac = (i * 0.618) % 1.0
    for n in nodes:
        acc += s.cpu_node_residency[n]
        if frac < acc:
            pick = n
            break
    # CPU ids are numbered core-major (cpu = core + k * cores), so node n owns cores n*cores_per_socket...
    processor = pick * s.cores_per_socket + (i % s.cores_per_socket)
    rss_pages = int(s.proc_anon_gib * (1 << 18))
    stat_fields = [
        "S",
        "1",
        "4242",
        "4242",
        "0",
        "-1",
        "4194560",
        str(int(s.proc_minflt_per_s * t)),
        "0",
        str(int(s.proc_majflt_per_s * t)),
        "0",
        str(int(cpu_time * 0.85)),
        str(int(cpu_time * 0.15)),
        "0",
        "0",
        "20",
        "0",
        str(s.threads),
        "0",
        "100",
        "1000000",
        str(rss_pages),
    ]
    stat_fields += ["18446744073709551615"] + ["0"] * 12 + ["17", str(processor)] + ["0"] * 13  # fields 25-52; exit_signal=38, processor=39
    on = s.proc_cpus * t * 1e9
    wait = on * s.proc_runq_wait_share / (1 - s.proc_runq_wait_share)
    anon_kib = int(s.proc_anon_gib * (1 << 20))
    pages = anon_kib // 4
    huge = int(anon_kib * s.proc_thp_coverage)
    numa_lines = []
    for n, f in sorted(s.mem_by_node.items()):
        numa_lines.append(f"7f{n:02x}00000000 default anon={int(pages * f)} dirty={int(pages * f)} active=0 N{n}={int(pages * f)} kernelpagesize_kB=4")
    return {
        f"/proc/{s.pid}/stat": f"{s.pid} ({s.comm}) " + " ".join(stat_fields) + "\n",
        f"/proc/{s.pid}/status": (
            f"Name:\t{s.comm}\nThreads:\t{s.threads}\nVmRSS:\t{anon_kib} kB\nCpus_allowed_list:\t0-{s.ncpu - 1}\n"
            f"voluntary_ctxt_switches:\t{int(s.proc_vol_cs_per_s * t)}\nnonvoluntary_ctxt_switches:\t{int(s.proc_invol_cs_per_s * t)}\n"
        ),
        f"/proc/{s.pid}/schedstat": f"{int(on)} {int(wait)} {int(t * 5000)}\n",
        f"/proc/{s.pid}/smaps_rollup": f"00400000-7fff00000000 ---p 00000000 00:00 0 [rollup]\nRss: {anon_kib} kB\nAnonymous: {anon_kib} kB\nAnonHugePages: {huge} kB\nSwap: 0 kB\nPrivate_Hugetlb: 0 kB\nShared_Hugetlb: 0 kB\n",
        f"/proc/{s.pid}/numa_maps": "\n".join(numa_lines) + "\n",
    }


def static_files(s: Spec) -> dict[str, str]:
    files = {
        "/proc/version": "Linux version 6.8.0-synthetic\n",
        "/proc/sys/kernel/numa_balancing": s.numa_balancing + "\n",
        "/proc/sys/kernel/perf_event_paranoid": "2\n",
        "/proc/sys/vm/swappiness": "60\n",
        "/proc/sys/vm/zone_reclaim_mode": s.zone_reclaim_mode + "\n",
        "/sys/kernel/mm/transparent_hugepage/enabled": " ".join(f"[{m}]" if m == s.thp_enabled else m for m in ("always", "madvise", "never")) + "\n",
        "/sys/kernel/mm/transparent_hugepage/defrag": " ".join(
            f"[{m}]" if m == s.thp_defrag else m for m in ("always", "defer", "defer+madvise", "madvise", "never")
        )
        + "\n",
        "/sys/kernel/mm/transparent_hugepage/khugepaged/max_ptes_none": "511\n",
        "/sys/devices/system/cpu/online": f"0-{s.ncpu - 1}\n",
        "/sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages": f"{s.hugetlb_2m_nr}\n",
        "/sys/kernel/mm/hugepages/hugepages-2048kB/free_hugepages": f"{s.hugetlb_2m_free}\n",
        "/sys/kernel/mm/hugepages/hugepages-2048kB/resv_hugepages": "0\n",
        "/sys/kernel/mm/hugepages/hugepages-2048kB/surplus_hugepages": "0\n",
    }
    cores = s.sockets * s.cores_per_socket
    per_node = s.ncpu // s.sockets
    for c in range(s.ncpu):
        core = c % cores
        sock = core // s.cores_per_socket
        siblings = ",".join(str(core + k * cores) for k in range(s.smt))
        base = f"/sys/devices/system/cpu/cpu{c}"
        files[f"{base}/topology/physical_package_id"] = f"{sock}\n"
        files[f"{base}/topology/core_id"] = f"{core % s.cores_per_socket}\n"
        files[f"{base}/topology/thread_siblings_list"] = siblings + "\n"
        files[f"{base}/cpufreq/scaling_governor"] = "performance\n"
        sock_cpus = ",".join(str(x) for x in range(s.ncpu) if (x % cores) // s.cores_per_socket == sock)
        for idx, (lvl, typ, size, shared) in enumerate(
            ((1, "Data", "48K", siblings), (1, "Instruction", "32K", siblings), (2, "Unified", "2048K", siblings), (3, "Unified", "32768K", sock_cpus))
        ):
            cb = f"{base}/cache/index{idx}"
            files.update(
                {
                    f"{cb}/level": f"{lvl}\n",
                    f"{cb}/type": f"{typ}\n",
                    f"{cb}/size": f"{size}\n",
                    f"{cb}/shared_cpu_list": f"{shared}\n",
                    f"{cb}/coherency_line_size": "64\n",
                }
            )
    for n in range(s.sockets):
        cpus = [x for x in range(s.ncpu) if (x % cores) // s.cores_per_socket == n]
        files[f"/sys/devices/system/node/node{n}/cpulist"] = ",".join(map(str, cpus)) + "\n"
        files[f"/sys/devices/system/node/node{n}/distance"] = " ".join("10" if m == n else "21" for m in range(s.sockets)) + "\n"
    assert per_node > 0
    return files


def recording(spec: Spec) -> Recording:
    n = int(spec.duration_s / spec.interval_s) + 1
    samples = []
    for i in range(n):
        t = 1000.0 + i * spec.interval_s  # counters have history before the window
        files = {
            "/proc/stat": _proc_stat(spec, t),
            "/proc/schedstat": _schedstat(spec, t),
            "/proc/vmstat": _vmstat(spec, t),
            "/proc/meminfo": _meminfo(spec),
            "/proc/pressure/cpu": _psi(spec.psi_cpu_some, None, t),
            "/proc/pressure/memory": _psi(spec.psi_mem_some, spec.psi_mem_full, t),
        }
        for node in range(spec.sockets):
            files[f"/sys/devices/system/node/node{node}/numastat"] = _numastat(spec, t, node)
            files[f"/sys/devices/system/node/node{node}/meminfo"] = _meminfo(spec, f"Node {node} ", spec.mem_per_node_gib, spec.node_free_frac[node])
        if spec.pid:
            files.update(_pid_files(spec, t, i))
        samples.append(Sample(1_700_000_000 + i * spec.interval_s, files, 0.0004))
    meta = {
        "tool": "kptk synth",
        "host": "synthetic-2s",
        "kernel": "6.8.0-synthetic",
        "arch": "x86_64",
        "interval_s": spec.interval_s,
        "pid": spec.pid,
        "command": None,
        "user_hz": USER_HZ,
        "page_size": 4096,
        "overruns": 0,
    }
    counters = {"events": {k: {"value": v, "raw": int(v), "running_frac": 1.0} for k, v in spec.counters.items()}, "skipped": {}} if spec.counters else None
    return Recording(meta, static_files(spec), samples, counters)


def scenario(name: str, **overrides: object) -> Recording:
    if name not in SCENARIOS:
        raise ValueError(f"unknown scenario {name!r}; choose from {sorted(SCENARIOS)}")
    return recording(replace(SCENARIOS[name][1], **overrides))  # type: ignore[arg-type]
