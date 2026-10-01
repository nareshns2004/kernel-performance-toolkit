import pytest

from kptk.parsers.proc import (
    busy_ticks,
    parse_meminfo,
    parse_numa_maps,
    parse_pid_stat,
    parse_pid_status,
    parse_pressure,
    parse_proc_stat,
    parse_schedstat,
    parse_smaps_rollup,
    parse_task_schedstat,
    parse_vmstat,
    total_ticks,
)
from kptk.parsers.sysfs import parse_bracket_choice, parse_cpulist, parse_distance, parse_size
from kptk.parsers.tools import parse_numastat_output, parse_perf_stat, parse_vmstat_output

PROC_STAT = """cpu  100 5 50 800 20 0 5 10 7 0
cpu0 50 2 25 400 10 0 3 5 7 0
cpu1 50 3 25 400 10 0 2 5 0 0
intr 12345 0 0
ctxt 99999
btime 1700000000
processes 4242
procs_running 3
procs_blocked 1
"""


def test_proc_stat() -> None:
    s = parse_proc_stat(PROC_STAT)
    assert s.ctxt == 99999 and s.procs_running == 3 and s.procs_blocked == 1 and s.intr == 12345
    assert set(s.cpus) == {0, 1}
    # guest time is already inside user, so it must not be double counted
    assert busy_ticks(s.cpu_total) == 100 + 5 + 50 + 0 + 5 + 10
    assert total_ticks(s.cpu_total) == 990


def test_schedstat_v15() -> None:
    text = "version 15\ntimestamp 4708\ncpu0 0 0 0 0 0 0 1000 250 40\ndomain0 ff 1 2 3\ncpu1 0 0 0 0 0 0 2000 750 60\n"
    version, cpus = parse_schedstat(text)
    assert version == 15
    assert cpus[1].run_delay_ns == 750 and cpus[0].timeslices == 40


def test_task_schedstat() -> None:
    t = parse_task_schedstat("30170206 1127 64\n")
    assert (t.on_cpu_ns, t.wait_ns, t.timeslices) == (30170206, 1127, 64)


def test_pid_stat_handles_comm_with_spaces_and_parens() -> None:
    rest = (
        ["S", "1", "1", "1", "0", "-1", "0", "111", "0", "22", "0", "300", "40", "0", "0", "20", "0", "7", "0", "100", "1000", "555"]
        + ["0"] * 13
        + ["17", "5"]
        + ["0"] * 13
    )
    p = parse_pid_stat("1234 (my (weird) proc) " + " ".join(rest))
    assert p.comm == "my (weird) proc"
    assert (p.minflt, p.majflt, p.utime, p.stime, p.num_threads, p.rss_pages, p.processor) == (111, 22, 300, 40, 7, 555, 5)


def test_pid_status() -> None:
    s = parse_pid_status("Name:\tredis\nVmRSS:\t  1024 kB\nvoluntary_ctxt_switches:\t10\nCpus_allowed_list:\t0-3\n")
    assert s["VmRSS"] == 1024 and s["voluntary_ctxt_switches"] == 10 and s["Cpus_allowed_list"] == "0-3"


def test_vmstat_and_meminfo() -> None:
    assert parse_vmstat("pgfault 10\nnr_free_pages 5\nbogus\n") == {"pgfault": 10, "nr_free_pages": 5}
    mi = parse_meminfo("MemTotal:  100 kB\nHugePages_Total:   4\n")
    assert mi == {"MemTotal": 100, "HugePages_Total": 4}
    assert parse_meminfo("Node 1 MemFree:   42 kB\n") == {"MemFree": 42}


def test_smaps_rollup_skips_range_header() -> None:
    s = parse_smaps_rollup("00400000-7ffd00000000 ---p 00000000 00:00 0  [rollup]\nRss: 10 kB\nAnonHugePages: 4096 kB\n")
    assert s == {"Rss": 10, "AnonHugePages": 4096}


def test_pressure() -> None:
    p = parse_pressure("some avg10=1.50 avg60=0.50 avg300=0.10 total=1000\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=5\n")
    assert p["some"].avg10 == 1.5 and p["full"].total_us == 5


def test_numa_maps_normalises_huge_pages() -> None:
    text = (
        "7f0000000000 default anon=100 dirty=100 N0=60 N1=40 kernelpagesize_kB=4\n"
        "7f1000000000 bind:1 anon=2 dirty=2 N1=2 kernelpagesize_kB=2048\n"
        "7f2000000000 interleave:0-1 file=/lib/x.so mapped=3 N0=3 kernelpagesize_kB=4\n"
    )
    m = parse_numa_maps(text)
    assert m.pages_by_node == {0: 63, 1: 40 + 2 * 512}
    assert m.huge_pages_by_node == {1: 2}
    assert m.policies == {"default": 1, "bind": 1, "interleave": 1}
    assert m.anon_pages == 102


def test_sysfs_helpers() -> None:
    assert parse_cpulist("0-3,8,10-11\n") == [0, 1, 2, 3, 8, 10, 11]
    assert parse_cpulist("") == []
    assert parse_bracket_choice("always [madvise] never\n") == "madvise"
    assert parse_bracket_choice("none here") is None
    assert parse_size("48K") == 48 * 1024 and parse_size("32M") == 32 << 20
    assert parse_distance("10 21\n") == [10, 21]


def test_perf_stat_csv_keeps_running_pct() -> None:
    text = "1,234;;cycles;5000;50.00;;\n45;;cache-misses;5000;100.00;;\n<not supported>;;LLC-loads;0;0.00;;\n"
    out = parse_perf_stat(text)
    assert out["cycles"].value == 1234 and out["cycles"].running_pct == 50.0
    assert "LLC-loads" not in out


def test_perf_stat_human_format() -> None:
    text = "  10.00 msec task-clock\n  1,000,000      cycles          (66.67%)\n   2.5G instructions\n"
    out = parse_perf_stat(text)
    assert out["task-clock"].value == 10.0
    assert out["cycles"].running_pct == pytest.approx(66.67)
    assert out["instructions"].value == 2.5e9


def test_vmstat_command_output() -> None:
    text = "procs ---memory---\n r  b swpd free\n 1  0 0 99\n 2  0 0 88\n"
    assert parse_vmstat_output(text)["free"] == 88.0


def test_numastat_command_output() -> None:
    assert parse_numastat_output("numa_hit 100 50 150\n")["numa_hit"] == {"node0": 100.0, "node1": 50.0, "total": 150.0}
    assert parse_numastat_output("node0 node1\nother_node 1 2\n")["other_node"]["total"] == 3.0
