import json

import pytest

from kptk.analysis.engine import analyze
from kptk.analysis.findings import Thresholds
from kptk.compare import compare, permutation_p
from kptk.synth import scenario

EXPECTED = {
    "healthy": set(),
    "numa_misplaced": {
        "numa.process_misplaced",
        "numa.remote_alloc",
        "numa.access_locality",
        "numa.node_exhausted",
        "cache.memory_bound",
        "cache.tlb_pressure",
        "hp.sync_compaction",
        "hp.thp_fallback",
        "hp.compaction_stalls",
        "hp.low_coverage",
    },
    "cpu_saturated": {"cpu.saturation", "cpu.process_runq_wait", "cpu.preemption"},
    "memory_pressure": {
        "mem.swapping",
        "mem.pressure",
        "mem.direct_reclaim",
        "mem.low_available",
        "mem.major_faults",
        "mem.minor_fault_storm",
        "numa.node_exhausted",
    },
}


@pytest.mark.parametrize("name", list(EXPECTED))
def test_scenario_findings_are_exact(name: str) -> None:
    report = analyze(scenario(name))
    actionable = {f["id"] for f in report["findings"] if f["severity"] != "info"}
    assert actionable == EXPECTED[name]
    json.dumps(report, default=str)


def test_metrics_recover_synthetic_ground_truth() -> None:
    m = analyze(scenario("cpu_saturated"))["metrics"]
    assert m["cpu"]["utilization"] == pytest.approx(0.98, abs=0.01)
    assert m["cpu"]["psi_cpu_some"] == pytest.approx(0.85, abs=0.01)
    assert m["cpu"]["runq_waiting_tasks_avg"] == pytest.approx(24, rel=0.01)
    assert m["cpu"]["process_runq_wait_share"] == pytest.approx(0.45, abs=0.01)
    assert m["cpu"]["process_cpus_used"] == pytest.approx(30, rel=0.01)
    n = analyze(scenario("numa_misplaced"))["metrics"]
    assert n["numa"]["remote_alloc_frac"] == pytest.approx(0.35, abs=0.01)
    assert n["numa"]["hint_fault_local_frac"] == pytest.approx(0.25, abs=0.01)
    # Exact ground truth: 0.5 * (|0.95 - 0.15| + |0.05 - 0.85|). Leader-only residency hit 0.85 by sampling noise.
    assert n["numa"]["process_placement_mismatch"] == pytest.approx(0.80, abs=0.01)
    assert n["hugepages"]["thp_fallback_frac"] == pytest.approx(40 / 90, abs=0.01)
    assert n["cache"]["ipc"] == pytest.approx(0.6) and n["cache"]["llc_mpki"] == pytest.approx(17.92, abs=0.01)


def test_numa_residency_follows_workers_not_idle_main_thread() -> None:
    # A2: the main thread sleeps on node 1, all workers run on node 0 where the heap lives.
    # Correct answer: well placed. Following /proc/<pid>/stat (the leader) blames node 1.
    spec = {"cpu_node_residency": {0: 1.0}, "mem_by_node": {0: 0.9, 1: 0.1}, "main_thread_node": 1}
    report = analyze(scenario("healthy", **spec))
    cpu, numa = report["metrics"]["cpu"], report["metrics"]["numa"]
    assert cpu["process_residency_source"] == "threads"
    assert cpu["process_node_residency"] == {0: 1.0}
    assert numa["process_placement_mismatch"] == pytest.approx(0.1, abs=0.01)
    assert "numa.process_misplaced" not in {f["id"] for f in report["findings"]}

    legacy = analyze(scenario("healthy", sampled_threads=0, **spec))
    assert legacy["metrics"]["cpu"]["process_residency_source"] == "main_thread"
    assert legacy["metrics"]["cpu"]["process_node_residency"] == {1: 1.0}
    assert "numa.process_misplaced" in {f["id"] for f in legacy["findings"]}
    assert any("main thread only" in m for m in legacy["data_quality"]["missing"])


def test_thread_cap_is_reported() -> None:
    rec = scenario("healthy")
    rec.meta["max_threads"] = 4
    assert any("only the 4 lowest tids" in m for m in analyze(rec)["data_quality"]["missing"])


def test_spread_process_with_spread_memory_is_not_misplaced() -> None:
    # 50/50 threads and 50/50 memory: ~50% remote under uniform access, but not a placement bug.
    m = analyze(scenario("healthy"))["metrics"]["numa"]
    assert m["process_remote_access_frac_est"] == pytest.approx(0.5, abs=0.1)
    assert m["process_placement_mismatch"] < 0.1


def test_thresholds_override(tmp_path) -> None:
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"cpu_psi_some_warn": 0.9, "runq_wait_share_warn": 0.9, "involuntary_switch_rate_warn": 1e9}))
    report = analyze(scenario("cpu_saturated"), thresholds=Thresholds.load(p))
    assert not [f for f in report["findings"] if f["domain"] == "cpu" and f["severity"] != "info"]
    p.write_text(json.dumps({"bogus": 1}))
    with pytest.raises(ValueError):
        Thresholds.load(p)


def test_missing_schedstat_is_reported_not_fatal() -> None:
    rec = scenario("healthy")
    for s in rec.samples:
        s.files.pop("/proc/schedstat")
    report = analyze(rec)
    assert report["metrics"]["cpu"]["runq_waiting_tasks_avg"] is None
    assert any("SCHEDSTATS" in x for x in report["data_quality"]["missing"])


def test_needs_two_samples() -> None:
    rec = scenario("healthy")
    rec.samples = rec.samples[:1]
    with pytest.raises(ValueError):
        analyze(rec)


def test_permutation_test() -> None:
    assert permutation_p([1.0, 1.1, 0.9, 1.0], [2.0, 2.1, 1.9, 2.0]) < 0.05
    assert permutation_p([1.0, 2.0, 1.5], [1.1, 1.9, 1.4]) > 0.5


def test_compare_flags_regression_with_significance() -> None:
    before = [analyze(scenario("healthy", proc_runq_wait_share=x)) for x in (0.02, 0.021, 0.019, 0.02)]
    after = [analyze(scenario("healthy", proc_runq_wait_share=x)) for x in (0.08, 0.081, 0.079, 0.08)]
    changes = {c.metric: c for c in compare(before, after)}
    c = changes["cpu.process_runq_wait_share"]
    assert c.verdict == "regressed" and c.p_value is not None and c.p_value < 0.05
    assert changes["cpu.utilization"].verdict == "no significant change"


def test_compare_higher_is_better() -> None:
    before = [analyze(scenario("numa_misplaced"))]
    after = [analyze(scenario("numa_misplaced", hint_local_frac=0.9))]
    c = {c.metric: c for c in compare(before, after)}["numa.hint_fault_local_frac"]
    assert c.verdict.startswith("improved") and c.p_value is None
