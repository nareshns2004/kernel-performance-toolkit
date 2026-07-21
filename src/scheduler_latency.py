"""Scheduler-related perf calculations."""

from __future__ import annotations


def scheduler_metrics(perf: dict[str, float]) -> dict[str, float]:
    context_switches = perf.get("context-switches", 0.0)
    migrations = perf.get("cpu-migrations", 0.0)
    task_clock = perf.get("task-clock", 0.0)
    return {"context_switches": context_switches, "cpu_migrations": migrations,
            "context_switches_per_ms": context_switches / task_clock if task_clock else 0.0}
