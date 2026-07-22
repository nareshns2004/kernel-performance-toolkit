"""Report creation and serialization."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def build_report(perf: dict[str, float], vmstat: dict[str, float] | None = None,
                 numa: dict[str, dict[str, float]] | None = None) -> dict[str, Any]:
    from cache_analysis import cache_metrics
    from numa_analysis import numa_metrics
    from page_faults import page_fault_metrics
    from scheduler_latency import scheduler_metrics

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "perf": perf,
        "metrics": {**cache_metrics(perf), **page_fault_metrics(perf), **scheduler_metrics(perf)},
    }
    if vmstat:
        report["vmstat"] = vmstat
    if numa:
        report["numastat"] = numa
        report["metrics"].update(numa_metrics(numa))
    return report


def write_json(report: dict[str, Any], output: str | Path) -> None:
    Path(output).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def markdown_summary(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = ["# Kernel Performance Report", "", "| Metric | Value |", "| --- | ---: |"]
    for name, value in sorted(metrics.items()):
        lines.append(f"| {name.replace('_', ' ')} | {value:.2f} |")
    return "\n".join(lines) + "\n"
