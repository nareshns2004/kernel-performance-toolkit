from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from parser import parse_numastat, parse_perf_stat, parse_vmstat


def test_parse_perf_stat_csv_and_human_formats():
    data = "1,234; ;cycles; ;\n45; ;cache-misses; ;\n  10.0 msec task-clock\n"
    assert parse_perf_stat(data) == {"cycles": 1234.0, "cache-misses": 45.0, "task-clock": 10.0}


def test_parse_vmstat_uses_last_snapshot():
    data = "procs -----------memory----------\nr  b swpd free\n1 0 0 99\n2 0 0 88\n"
    assert parse_vmstat(data)["free"] == 88.0


def test_parse_numastat():
    result = parse_numastat("numa_hit 100 50 150\nnuma_miss 5 10 15\n")
    assert result["numa_miss"] == {"node0": 5.0, "node1": 10.0, "total": 15.0}


def test_parse_numastat_without_total_column():
    result = parse_numastat("node0 node1\nnuma_hit 100 50\n")
    assert result["numa_hit"]["total"] == 150.0
