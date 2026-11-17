"""Noise-aware A/B comparison of reports.

A single before/after pair can't tell a 3% regression from run-to-run noise.
Given several reports per side, each metric gets a two-sided permutation
test on the difference of means. It's exact for small n, distribution-free, and
needs only the standard library. With one report per side, only the relative
delta is shown and no significance is claimed.

Two things keep the verdicts honest:

* **Power floor.** An exact permutation test with n runs per side can't produce a
  p-value below ``2 / C(2n, n)``: 0.33 at n=2, 0.10 at n=3, 0.029 at n=4, 0.008 at
  n=5. Below the floor a "no change" is really "can't tell", and the verdict says
  ``inconclusive``.
* **Multiple comparisons.** Testing ~23 metrics at alpha=0.05 expects about one false
  positive by chance. Metrics named as ``primary`` (declared *before* looking at the
  data: the thing the change is meant to move) are tested at alpha. All other metrics
  are exploratory and judged on Benjamini-Hochberg adjusted p-values (q), which bound
  the false discovery rate. A metric whose values are identical in every run can't be
  rejected under any relabelling, so it's left out of the family (a filter that is
  permutation-invariant, so it doesn't bias the test).
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


def min_achievable_p(n_a: int, n_b: int, rounds: int = 10_000) -> float:
    """Smallest p-value :func:`permutation_p` can return for these sample sizes.

    Exact enumeration: the most extreme split is one of C(n_a+n_b, n_a); with equal
    sizes its mirror image is equally extreme, so the floor is 2/C. Monte Carlo can't go
    below 1/(rounds+1).
    """
    splits = math.comb(n_a + n_b, n_a)
    if splits <= rounds:
        return (2 if n_a == n_b else 1) / splits
    return 1 / (rounds + 1)


def benjamini_hochberg(pvalues: dict[str, float]) -> dict[str, float]:
    """BH step-up adjusted p-values (q-values): q_(i) = min_{j>=i} p_(j) * m / j, capped at 1."""
    m = len(pvalues)
    ranked = sorted(pvalues.items(), key=lambda kv: kv[1])
    out: dict[str, float] = {}
    running = 1.0
    for rank in range(m, 0, -1):
        name, p = ranked[rank - 1]
        running = min(running, p * m / rank)
        out[name] = running
    return out


@dataclass
class Change:
    metric: str
    before: float
    after: float
    rel_change: float | None
    p_value: float | None
    verdict: str
    q_value: float | None = None
    primary: bool = False


def compare(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    min_rel: float = 0.05,
    alpha: float = 0.05,
    all_metrics: bool = False,
    primary: tuple[str, ...] | list[str] = (),
    fdr: bool = True,
) -> list[Change]:
    fa = [flatten(r) for r in before]
    fb = [flatten(r) for r in after]
    names = sorted(set.intersection(*(set(x) for x in fa + fb)))
    if not all_metrics:
        names = [n for n in names if n in KEY_METRICS or n in primary]
    tested = len(fa) >= 2 and len(fb) >= 2
    floor = min_achievable_p(len(fa), len(fb)) if tested else None

    rows: dict[str, tuple[float, float, float | None, float | None]] = {}
    family: dict[str, float] = {}  # exploratory metrics that enter the BH correction
    for name in names:
        xa, xb = [x[name] for x in fa], [x[name] for x in fb]
        ma, mb = statistics.fmean(xa), statistics.fmean(xb)
        rel = (mb - ma) / abs(ma) if ma else None
        p = permutation_p(xa, xb) if tested else None
        rows[name] = (ma, mb, rel, p)
        if p is not None and name not in primary and len(set(xa + xb)) > 1:
            family[name] = p
    qvalues = benjamini_hochberg(family) if fdr and family else {}

    out = []
    for name, (ma, mb, rel, p) in rows.items():
        is_primary = name in primary
        q = qvalues.get(name)
        if p is None:
            stat, best = None, None
        elif is_primary or not fdr:
            stat, best = p, floor
        else:
            stat = q if q is not None else 1.0
            best = (floor or 1.0) * len(family)  # BH can't push the smallest p below floor * m
        big = rel is not None and abs(rel) >= min_rel
        if p is None:
            verdict = "unchanged" if rel is not None and not big else "changed (n=1, untested)"
            if big and rel is not None:
                verdict = ("improved" if (rel > 0) == (name in HIGHER_IS_BETTER) else "regressed") + " (n=1, untested)"
        elif big and stat is not None and stat < alpha:
            verdict = "improved" if (rel is not None and (rel > 0) == (name in HIGHER_IS_BETTER)) else "regressed"
        elif big and best is not None and best >= alpha:
            verdict = "inconclusive (too few runs)"
        else:
            verdict = "no significant change"
        out.append(Change(name, ma, mb, rel, p, verdict, q, is_primary))
    return out


def runs_needed(alpha: float = 0.05, family_size: int = 1) -> int:
    """Smallest equal n per side whose exact-permutation floor, times the family size, is below alpha."""
    n = 2
    while min_achievable_p(n, n) * family_size >= alpha:
        n += 1
    return n


def to_markdown(changes: list[Change], n_before: int, n_after: int, alpha: float = 0.05) -> str:
    lines = [f"# Comparison ({n_before} run(s) before, {n_after} after)", ""]
    tested = n_before >= 2 and n_after >= 2
    if tested:
        floor = min_achievable_p(n_before, n_after)
        family = sum(1 for c in changes if c.q_value is not None)
        lines.append(
            f"Minimum achievable p with {n_before} vs {n_after} runs: **{floor:.3g}**"
            + (f"; for exploratory metrics after Benjamini-Hochberg across {family}: **{min(1.0, floor * family):.3g}**" if family else "")
            + f" (alpha = {alpha})."
        )
        lines.append("")
    lines += ["| metric | before | after | change | p-value | q (BH) | verdict |", "|---|---:|---:|---:|---:|---:|---|"]
    order = {"regressed": 0, "improved": 1, "inconclusive": 2}
    for c in sorted(changes, key=lambda c: (not c.primary, order.get(c.verdict.split()[0], 3), c.metric)):
        rel = f"{c.rel_change:+.1%}" if c.rel_change is not None else "-"
        p = f"{c.p_value:.3f}" if c.p_value is not None else "-"
        q = "primary" if c.primary and c.p_value is not None else (f"{c.q_value:.3f}" if c.q_value is not None else "-")
        name = f"**{c.metric}**" if c.primary else c.metric
        lines.append(f"| {name} | {c.before:.4g} | {c.after:.4g} | {rel} | {p} | {q} | {c.verdict} |")
    family = sum(1 for c in changes if c.q_value is not None)
    # n=4 reaches p < 0.05 only if every "after" run beats every "before" run; 5 leaves margin.
    need_primary = max(5, runs_needed(alpha))
    need_fdr = runs_needed(alpha, max(1, family or len(KEY_METRICS)))
    if not tested:
        lines += ["", f"_With one run per side, changes can't be separated from noise. Record ≥ {need_primary} runs per side for p-values._"]
    elif min(n_before, n_after) < max(need_primary, need_fdr):
        lines += [
            "",
            f"_Power: ≥ {need_primary} runs per side can reach p < {alpha} for a pre-declared `--primary` metric; "
            f"exploratory metrics need ≥ {need_fdr} per side to survive the multiple-comparison correction._",
        ]
    return "\n".join(lines) + "\n"
