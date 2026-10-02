"""Finding model and tunable thresholds."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from enum import IntEnum
from pathlib import Path
from typing import Any


class Severity(IntEnum):
    INFO = 0
    WARNING = 1
    CRITICAL = 2


@dataclass
class Finding:
    id: str
    domain: str
    severity: Severity
    title: str
    evidence: dict[str, Any]
    recommendation: str
    why: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.name.lower()
        return d


@dataclass
class Thresholds:
    """Defaults are conservative starting points. Override per fleet with ``--thresholds file.json``."""

    # CPU / scheduler
    cpu_psi_some_warn: float = 0.10  # share of time some task waited for CPU
    runq_wait_share_warn: float = 0.10  # process time runnable-but-waiting / (that + on-CPU)
    runq_wait_per_slice_ms_warn: float = 1.0
    involuntary_switch_rate_warn: float = 1000.0  # per second, for the target process
    voluntary_switch_rate_warn: float = 20000.0
    steal_warn: float = 0.05
    iowait_warn: float = 0.10
    cpu_imbalance_warn: float = 0.50  # max - min per-CPU utilisation while some CPU is > 90% busy
    # Memory
    major_fault_rate_warn: float = 10.0  # per second
    minor_fault_rate_warn: float = 200_000.0
    direct_reclaim_rate_warn: float = 1.0  # allocstall per second
    swap_rate_warn: float = 10.0  # pages per second in+out
    mem_psi_full_warn: float = 0.01
    mem_available_frac_warn: float = 0.05
    # NUMA
    remote_alloc_warn: float = 0.10
    numa_placement_mismatch_warn: float = 0.30  # total variation distance between CPU-node and memory-node distributions
    numa_hint_local_warn: float = 0.70
    node_free_frac_warn: float = 0.03
    numa_migrate_rate_warn: float = 10_000.0  # pages per second
    # Cache / TLB
    ipc_low: float = 1.0
    llc_mpki_warn: float = 5.0
    branch_miss_warn: float = 0.02
    dtlb_miss_warn: float = 0.01
    multiplex_running_warn: float = 0.95
    # Huge pages
    thp_fallback_warn: float = 0.20
    thp_coverage_low: float = 0.20
    thp_min_anon_kib: int = 1 << 20  # only judge THP coverage for processes with >= 1 GiB anonymous memory
    compact_stall_rate_warn: float = 1.0
    hugetlb_waste_kib_warn: int = 1 << 20

    @classmethod
    def load(cls, path: str | Path | None) -> Thresholds:
        if not path:
            return cls()
        values = json.loads(Path(path).read_text())
        known = {f.name for f in fields(cls)}
        unknown = set(values) - known
        if unknown:
            raise ValueError(f"unknown thresholds: {sorted(unknown)}")
        return cls(**values)


@dataclass
class DomainResult:
    metrics: dict[str, Any] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    def add(self, *args: Any, **kwargs: Any) -> None:
        self.findings.append(Finding(*args, **kwargs))
