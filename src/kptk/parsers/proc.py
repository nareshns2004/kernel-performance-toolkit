"""Pure parsers for /proc text formats.

Every function takes the file's text and returns plain data, so analysis can
run on a recording captured on another machine (see :mod:`kptk.record`).
Field layouts follow Documentation/filesystems/proc.rst and
Documentation/scheduler/sched-stats.rst in the kernel tree.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CPU_TIME_FIELDS = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal", "guest", "guest_nice")


@dataclass
class ProcStat:
    """``/proc/stat``. CPU times are in USER_HZ ticks (almost always 1/100 s)."""

    cpu_total: dict[str, int]
    cpus: dict[int, dict[str, int]]
    ctxt: int = 0
    processes: int = 0
    procs_running: int = 0
    procs_blocked: int = 0
    intr: int = 0


def parse_proc_stat(text: str) -> ProcStat:
    total: dict[str, int] = {}
    cpus: dict[int, dict[str, int]] = {}
    scalars: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        key = parts[0]
        if key.startswith("cpu"):
            times = dict(zip(CPU_TIME_FIELDS, (int(v) for v in parts[1:]), strict=False))
            if key == "cpu":
                total = times
            else:
                cpus[int(key[3:])] = times
        elif key in ("ctxt", "processes", "procs_running", "procs_blocked"):
            scalars[key] = int(parts[1])
        elif key == "intr":
            scalars["intr"] = int(parts[1])
    return ProcStat(total, cpus, **scalars)


def busy_ticks(times: dict[str, int]) -> int:
    """Non-idle ticks. guest/guest_nice are already included in user/nice, so they're excluded here."""
    return sum(v for k, v in times.items() if k not in ("idle", "iowait", "guest", "guest_nice"))


def total_ticks(times: dict[str, int]) -> int:
    return sum(v for k, v in times.items() if k not in ("guest", "guest_nice"))


@dataclass
class CpuSchedStat:
    """One ``cpuN`` line of /proc/schedstat (version 15+).

    ``run_delay_ns`` is the total time tasks spent *runnable but waiting* on
    this CPU's run queue: direct scheduler latency, which no counter in
    /proc/stat shows.
    """

    rq_cpu_time_ns: int
    run_delay_ns: int
    timeslices: int


def parse_schedstat(text: str) -> tuple[int, dict[int, CpuSchedStat]]:
    version = 0
    cpus: dict[int, CpuSchedStat] = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "version":
            version = int(parts[1])
        elif parts[0].startswith("cpu") and len(parts) >= 10:
            # Fields 7-9 (1-based after the cpu label) are rq_cpu_time, run_delay, pcount.
            cpus[int(parts[0][3:])] = CpuSchedStat(int(parts[7]), int(parts[8]), int(parts[9]))
    return version, cpus


@dataclass
class TaskSchedStat:
    """``/proc/<pid>/schedstat``: time on CPU, time waiting on a run queue, timeslices."""

    on_cpu_ns: int
    wait_ns: int
    timeslices: int


def parse_task_schedstat(text: str) -> TaskSchedStat:
    a, b, c = (int(x) for x in text.split()[:3])
    return TaskSchedStat(a, b, c)


@dataclass
class PidStat:
    pid: int
    comm: str
    state: str
    minflt: int
    majflt: int
    utime: int
    stime: int
    num_threads: int
    processor: int
    rss_pages: int


def parse_pid_stat(text: str) -> PidStat:
    """``/proc/<pid>/stat``. ``comm`` may contain spaces and parentheses, so split at the *last* ')'."""
    lpar, rpar = text.index("("), text.rindex(")")
    pid = int(text[:lpar].strip())
    comm = text[lpar + 1 : rpar]
    f = text[rpar + 2 :].split()
    # f[0] is field 3 (state); field n is f[n - 3].
    return PidStat(
        pid=pid,
        comm=comm,
        state=f[0],
        minflt=int(f[7]),
        majflt=int(f[9]),
        utime=int(f[11]),
        stime=int(f[12]),
        num_threads=int(f[17]),
        rss_pages=int(f[21]),
        processor=int(f[36]),
    )


def parse_key_value(text: str, sep: str | None = None) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split(None, 1) if sep is None else line.split(sep, 1)
        if len(parts) == 2:
            out[parts[0].strip().rstrip(":")] = parts[1].strip()
    return out


def parse_pid_status(text: str) -> dict[str, str | int]:
    """``/proc/<pid>/status``. Numeric ``kB`` fields become ints in kB; other values are kept as strings."""
    out: dict[str, str | int] = {}
    for key, value in parse_key_value(text, ":").items():
        token = value.split()[0] if value else ""
        out[key] = int(token) if token.isdigit() else value
    return out


def parse_vmstat(text: str) -> dict[str, int]:
    """``/proc/vmstat``: ``name value`` pairs."""
    out: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("-").isdigit():
            out[parts[0]] = int(parts[1])
    return out


def parse_meminfo(text: str) -> dict[str, int]:
    """``/proc/meminfo`` (and per-node meminfo). Values in kB, or plain counts for HugePages_*."""
    out: dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, rest = line.split(":", 1)
        key = key.split()[-1]  # per-node files are prefixed "Node 0 "
        if not key[:1].isalpha():  # smaps_rollup's address-range header line
            continue
        token = rest.split()[0] if rest.split() else ""
        if token.isdigit():
            out[key] = int(token)
    return out


@dataclass
class Pressure:
    """PSI line: share of wall time in which *some* (or *all*, for full) tasks were stalled."""

    avg10: float
    avg60: float
    avg300: float
    total_us: int


def parse_pressure(text: str) -> dict[str, Pressure]:
    out: dict[str, Pressure] = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        kv = dict(p.split("=", 1) for p in parts[1:])
        out[parts[0]] = Pressure(float(kv["avg10"]), float(kv["avg60"]), float(kv["avg300"]), int(kv["total"]))
    return out


@dataclass
class NumaMaps:
    """Aggregated ``/proc/<pid>/numa_maps``: where a process's pages physically live."""

    pages_by_node: dict[int, int] = field(default_factory=dict)
    huge_pages_by_node: dict[int, int] = field(default_factory=dict)
    anon_pages: int = 0
    policies: dict[str, int] = field(default_factory=dict)

    @property
    def total_pages(self) -> int:
        return sum(self.pages_by_node.values())


def parse_numa_maps(text: str) -> NumaMaps:
    out = NumaMaps()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        policy = parts[1].split(":")[0]
        out.policies[policy] = out.policies.get(policy, 0) + 1
        page_kb = 4
        nodes: dict[int, int] = {}
        for token in parts[2:]:
            if token.startswith("kernelpagesize_kB="):
                page_kb = int(token.split("=")[1])
            elif token.startswith("anon="):
                out.anon_pages += int(token.split("=")[1])
            elif token.startswith("N") and "=" in token and token[1:].split("=")[0].isdigit():
                node_s, count_s = token[1:].split("=")
                nodes[int(node_s)] = int(count_s)
        # Normalise huge pages to 4 KiB-equivalents so nodes are comparable by bytes.
        scale = page_kb // 4
        for n, count in nodes.items():
            out.pages_by_node[n] = out.pages_by_node.get(n, 0) + count * scale
            if page_kb > 4:
                out.huge_pages_by_node[n] = out.huge_pages_by_node.get(n, 0) + count
    return out


def parse_smaps_rollup(text: str) -> dict[str, int]:
    """``/proc/<pid>/smaps_rollup``: kB totals (Rss, Anonymous, AnonHugePages, Swap, ...)."""
    return parse_meminfo(text)
