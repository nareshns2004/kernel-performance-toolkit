"""Noise-aware A/B comparison of reports.

A single before/after pair can't tell a 3% regression from run-to-run noise.
Given several reports per side, each metric gets a two-sided permutation
test on the difference of means. It's exact for small n, distribution-free, and
needs only the standard library. With one report per side, only the relative
delta is shown and no significance is claimed.
"""

from __future__ import annotations

import itertools
import math
import random
import statistics
from dataclasses import dataclass
from typing import Any

# Metrics where a higher value is better; everything else in the curated list is "lower is better".
HIGHER_IS_BETTER = {"cache.ipc", "hugepages.process_thp_coverage", "numa.hint_fault_local_frac"}
KEY_METRICS = (
    "cpu.utilization",
    "cpu.psi_cpu_some",
    "cpu.runq_waiting_tasks_avg",
    "cpu.runq_wait_per_timeslice_ms",
    "cpu.process_runq_wait_share",
    "cpu.process_involuntary_switches_per_s",
    "cpu.process_voluntary_switches_per_s",
    "memory.faults_per_s",
    "memory.major_faults_per_s",
    "memory.process_minor_faults_per_s",
    "memory.process_major_faults_per_s",
    "memory.direct_reclaim_stalls_per_s",
    "memory.psi_memory_some",
    "numa.remote_alloc_frac",
    "numa.process_remote_access_frac_est",
    "numa.hint_fault_local_frac",
    "cache.ipc",
    "cache.llc_mpki",
    "cache.branch_miss_ratio",
    "cache.dtlb_miss_ratio",
    "hugepages.process_thp_coverage",
    "hugepages.thp_fallback_frac",
    "hugepages.compact_stall_per_s",
)


def flatten(report: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for domain, metrics in report.get("metrics", {}).items():
        for k, v in metrics.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
                out[f"{domain}.{k}"] = float(v)
    return out


def permutation_p(a: list[float], b: list[float], rounds: int = 10_000, seed: int = 0) -> float:
    """Two-sided p-value for difference in means. Exact enumeration when small, Monte Carlo otherwise."""
    observed = abs(statistics.fmean(b) - statistics.fmean(a))
    pooled = a + b
    n = len(a)
    if math.comb(len(pooled), n) <= rounds:
        splits: Any = (set(c) for c in itertools.combinations(range(len(pooled)), n))
        total = extreme = 0
        for idx in splits:
            xa = [pooled[i] for i in idx]
            xb = [pooled[i] for i in range(len(pooled)) if i not in idx]
            total += 1
            extreme += abs(statistics.fmean(xb) - statistics.fmean(xa)) >= observed - 1e-12
        return extreme / total
    rng = random.Random(seed)
    extreme = 0
    for _ in range(rounds):
        rng.shuffle(pooled)
        extreme += abs(statistics.fmean(pooled[n:]) - statistics.fmean(pooled[:n])) >= observed - 1e-12
    return (extreme + 1) / (rounds + 1)


@dataclass
class Change:
    metric: str
    before: float
    after: float
    rel_change: float | None
    p_value: float | None
    verdict: str


def compare(before: list[dict[str, Any]], after: list[dict[str, Any]], min_rel: float = 0.05, alpha: float = 0.05, all_metrics: bool = False) -> list[Change]:
    fa = [flatten(r) for r in before]
    fb = [flatten(r) for r in after]
    names = sorted(set.intersection(*(set(x) for x in fa + fb)))
    if not all_metrics:
        names = [n for n in names if n in KEY_METRICS]
    out = []
    for name in names:
        xa, xb = [x[name] for x in fa], [x[name] for x in fb]
        ma, mb = statistics.fmean(xa), statistics.fmean(xb)
        rel = (mb - ma) / abs(ma) if ma else None
        p = permutation_p(xa, xb) if len(xa) >= 2 and len(xb) >= 2 else None
        significant = (p is None or p < alpha) and rel is not None and abs(rel) >= min_rel
        if not significant or rel is None:
            verdict = "no significant change" if p is not None else ("unchanged" if rel is not None and abs(rel) < min_rel else "changed (n=1, untested)")
        else:
            better = (rel > 0) == (name in HIGHER_IS_BETTER)
            verdict = "improved" if better else "regressed"
            if p is None:
                verdict += " (n=1, untested)"
        out.append(Change(name, ma, mb, rel, p, verdict))
    return out


def to_markdown(changes: list[Change], n_before: int, n_after: int) -> str:
    lines = [
        f"# Comparison ({n_before} run(s) before, {n_after} after)",
        "",
        "| metric | before | after | change | p-value | verdict |",
        "|---|---:|---:|---:|---:|---|",
    ]
    order = {"regressed": 0, "improved": 1}
    for c in sorted(changes, key=lambda c: (order.get(c.verdict.split()[0], 2), c.metric)):
        rel = f"{c.rel_change:+.1%}" if c.rel_change is not None else "-"
        p = f"{c.p_value:.3f}" if c.p_value is not None else "-"
        lines.append(f"| {c.metric} | {c.before:.4g} | {c.after:.4g} | {rel} | {p} | {c.verdict} |")
    if n_before < 2 or n_after < 2:
        lines += ["", "_With one run per side, changes can't be separated from noise. Record ≥ 3 runs per side for p-values._"]
    return "\n".join(lines) + "\n"
