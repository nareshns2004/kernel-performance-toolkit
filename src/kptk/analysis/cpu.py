"""CPU utilisation and scheduler latency.

Utilisation (/proc/stat) answers "how busy"; it can't distinguish a CPU
that's 100% busy with an empty run queue from one with ten tasks queued
behind it. Saturation is visible in three other places:

* ``/proc/schedstat`` ``run_delay``: total ns tasks spent runnable but
  waiting, per CPU (needs CONFIG_SCHEDSTATS, and ``kernel.sched_schedstats=1`` on
  some kernels).
* ``/proc/<pid>/schedstat``: the same, for one task.
* PSI ``/proc/pressure/cpu``: share of wall time at least one task waited.

For a single process, *involuntary* context switches (preempted while still
runnable) point at CPU contention, and *voluntary* switches (blocked on
I/O, a futex or sleep) point at blocking.
"""

from __future__ import annotations

from collections import Counter

from ..parsers.proc import busy_ticks, parse_pid_stat, parse_pid_status, parse_pressure, parse_proc_stat, parse_schedstat, parse_task_schedstat, total_ticks
from ..topology import Topology
from .findings import DomainResult, Severity, Thresholds
from .window import Window


def analyze_cpu(w: Window, topo: Topology, th: Thresholds) -> DomainResult:
    r = DomainResult()
    m = r.metrics
    stat = w.pair("/proc/stat", parse_proc_stat)
    if stat:
        a, b, dt = stat
        tot = {k: b.cpu_total[k] - a.cpu_total.get(k, 0) for k in b.cpu_total}
        total = total_ticks(tot) or 1
        m["utilization"] = busy_ticks(tot) / total
        for k in ("user", "system", "iowait", "steal", "softirq", "irq"):
            m[f"{k}_frac"] = tot.get(k, 0) / total
        per_cpu = {}
        for cpu, times in b.cpus.items():
            d = {k: times[k] - a.cpus.get(cpu, {}).get(k, 0) for k in times}
            per_cpu[cpu] = busy_ticks(d) / (total_ticks(d) or 1)
        m["per_cpu_utilization"] = {c: round(u, 3) for c, u in sorted(per_cpu.items())}
        m["context_switches_per_s"] = (b.ctxt - a.ctxt) / dt
        running = [s.procs_running for _, s in w.series("/proc/stat", parse_proc_stat)]
        m["procs_running_mean"] = sum(running) / len(running)
        m["procs_running_per_cpu"] = m["procs_running_mean"] / max(1, len(topo.cpus))
        if per_cpu:
            hot = max(per_cpu.values())
            spread = hot - min(per_cpu.values())
            m["per_cpu_spread"] = spread
            if hot > 0.9 and spread > th.cpu_imbalance_warn:
                busiest = sorted(per_cpu, key=lambda c: -per_cpu[c])[:4]
                r.add(
                    "cpu.imbalance",
                    "cpu",
                    Severity.WARNING,
                    f"CPU load is imbalanced: CPU {busiest[0]} at {hot:.0%} while the least busy CPU is at {hot - spread:.0%}",
                    {"busiest_cpus": busiest, "spread": round(spread, 2)},
                    "Check affinity masks (taskset/cgroup cpusets), IRQ affinity (/proc/interrupts, irqbalance) and single-threaded bottlenecks.",
                    "A pinned task or an IRQ storm can saturate one CPU while the scheduler can't move the work.",
                )
        if m["steal_frac"] > th.steal_warn:
            r.add(
                "cpu.steal",
                "cpu",
                Severity.WARNING,
                f"Hypervisor steal time is {m['steal_frac']:.1%} of CPU time",
                {"steal_frac": round(m["steal_frac"], 4)},
                "The host is oversubscribed: move to dedicated or larger instances, or ask the platform team about vCPU overcommit.",
                "Steal is time the vCPU was runnable but the hypervisor ran someone else.",
            )
        if m["iowait_frac"] > th.iowait_warn:
            r.add(
                "cpu.iowait",
                "cpu",
                Severity.INFO,
                f"iowait is {m['iowait_frac']:.1%}: CPUs idle while I/O is outstanding",
                {"iowait_frac": round(m["iowait_frac"], 4)},
                "iowait is idle time, not busy time. Look at I/O pressure (PSI io) and the device queue rather than at CPU.",
            )

    sched = w.pair("/proc/schedstat", lambda t: parse_schedstat(t)[1])
    if sched:
        a2, b2, dt = sched
        delay = sum(b2[c].run_delay_ns - a2[c].run_delay_ns for c in b2 if c in a2)
        slices = sum(b2[c].timeslices - a2[c].timeslices for c in b2 if c in a2)
        # ns of waiting per ns of wall time = average number of tasks waiting in run queues
        m["runq_waiting_tasks_avg"] = delay / 1e9 / dt
        m["runq_wait_per_timeslice_ms"] = delay / slices / 1e6 if slices else 0.0
        per_cpu_wait = {c: (b2[c].run_delay_ns - a2[c].run_delay_ns) / 1e9 / dt for c in b2 if c in a2}
        m["runq_waiting_tasks_per_cpu"] = {c: round(v, 3) for c, v in sorted(per_cpu_wait.items())}
    else:
        m["runq_waiting_tasks_avg"] = None

    psi = w.pair("/proc/pressure/cpu", parse_pressure)
    if psi:
        a3, b3, dt = psi
        m["psi_cpu_some"] = (b3["some"].total_us - a3["some"].total_us) / 1e6 / dt
        if m["psi_cpu_some"] > th.cpu_psi_some_warn:
            r.add(
                "cpu.saturation",
                "cpu",
                Severity.WARNING if m["psi_cpu_some"] < 0.5 else Severity.CRITICAL,
                f"CPU saturation: some task was waiting for a CPU {m['psi_cpu_some']:.0%} of the time",
                {
                    "psi_cpu_some": round(m["psi_cpu_some"], 3),
                    "runq_waiting_tasks_avg": round(m.get("runq_waiting_tasks_avg") or 0, 2),
                    "runq_wait_per_timeslice_ms": round(m.get("runq_wait_per_timeslice_ms") or 0, 3),
                },
                "Reduce runnable threads (pool sizes), spread load across more CPUs, or isolate latency-critical threads (cpusets / isolcpus).",
                "PSI counts wall time with at least one runnable task not running; it is saturation, not utilisation.",
            )

    _process(w, topo, th, r)
    return r


def _process(w: Window, topo: Topology, th: Thresholds, r: DomainResult) -> None:
    pid = w.rec.pid
    if not pid:
        return
    m = r.metrics
    ps = w.pair(f"/proc/{pid}/stat", parse_pid_stat)
    if ps:
        a, b, dt = ps
        m["process_comm"] = b.comm
        m["process_threads"] = b.num_threads
        m["process_cpus_used"] = (b.utime + b.stime - a.utime - a.stime) / w.user_hz / dt
        m["process_system_frac"] = (b.stime - a.stime) / max(1, b.utime + b.stime - a.utime - a.stime)
        cpus_seen = Counter(s.processor for _, s in w.series(f"/proc/{pid}/stat", parse_pid_stat))
        nodes_seen = Counter(topo.cpu_node.get(c, 0) for c in cpus_seen.elements())
        m["process_cpus_seen"] = sorted(cpus_seen)
        m["process_node_residency"] = {n: round(c / sum(nodes_seen.values()), 3) for n, c in sorted(nodes_seen.items())}
    st = w.pair(f"/proc/{pid}/schedstat", parse_task_schedstat)
    if st:
        a2, b2, _ = st
        on, wait, slices = b2.on_cpu_ns - a2.on_cpu_ns, b2.wait_ns - a2.wait_ns, b2.timeslices - a2.timeslices
        share = wait / (wait + on) if wait + on else 0.0
        m["process_runq_wait_share"] = share
        m["process_runq_wait_per_slice_ms"] = wait / slices / 1e6 if slices else 0.0
        if share > th.runq_wait_share_warn:
            r.add(
                "cpu.process_runq_wait",
                "cpu",
                Severity.WARNING if share < 0.3 else Severity.CRITICAL,
                f"The process spent {share:.0%} of its runnable time waiting in a run queue",
                {"wait_share": round(share, 3), "avg_wait_per_slice_ms": round(m["process_runq_wait_per_slice_ms"], 3)},
                "Give the process more CPUs or fewer competing threads; for latency-critical work, pin it to isolated cores.",
                "Scheduler latency adds directly to request latency: the work was ready, but no CPU was free.",
            )
    status = w.pair(f"/proc/{pid}/status", parse_pid_status)
    if status:
        a3, b3, dt = status
        vol = (int(b3.get("voluntary_ctxt_switches", 0)) - int(a3.get("voluntary_ctxt_switches", 0))) / dt
        invol = (int(b3.get("nonvoluntary_ctxt_switches", 0)) - int(a3.get("nonvoluntary_ctxt_switches", 0))) / dt
        m["process_voluntary_switches_per_s"] = vol
        m["process_involuntary_switches_per_s"] = invol
        m["process_cpus_allowed"] = b3.get("Cpus_allowed_list")
        if invol > th.involuntary_switch_rate_warn and invol > vol:
            r.add(
                "cpu.preemption",
                "cpu",
                Severity.WARNING,
                f"{invol:,.0f} involuntary context switches/s: the process is being preempted",
                {"involuntary_per_s": round(invol), "voluntary_per_s": round(vol)},
                "Too many runnable threads per CPU. Size thread pools to the CPU count, or isolate the hot threads.",
                "Involuntary switches happen when a runnable task is descheduled because its slice expired or a higher-priority task woke.",
            )
        elif vol > th.voluntary_switch_rate_warn:
            r.add(
                "cpu.blocking",
                "cpu",
                Severity.INFO,
                f"{vol:,.0f} voluntary context switches/s: threads block and wake frequently",
                {"voluntary_per_s": round(vol), "involuntary_per_s": round(invol)},
                "Look for lock contention (futex), small synchronous I/O or chatty producer/consumer queues; batch work or spin briefly before blocking.",
                "Each block/wake pair costs a few microseconds of scheduler and cache-refill overhead.",
            )
