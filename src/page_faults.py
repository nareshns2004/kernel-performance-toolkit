"""Page-fault metrics."""

from __future__ import annotations


def page_fault_metrics(perf: dict[str, float]) -> dict[str, float]:
    minor = perf.get("minor-faults", perf.get("page-faults", 0.0))
    major = perf.get("major-faults", 0.0)
    total = minor + major
    return {"minor_faults": minor, "major_faults": major, "total_faults": total,
            "major_fault_percent": 100 * major / total if total else 0.0}
