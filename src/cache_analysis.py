"""Cache-derived metrics."""

from __future__ import annotations


def cache_metrics(perf: dict[str, float]) -> dict[str, float]:
    references = perf.get("cache-references", 0.0)
    misses = perf.get("cache-misses", 0.0)
    instructions = perf.get("instructions", 0.0)
    return {
        "cache_references": references,
        "cache_misses": misses,
        "cache_miss_rate_percent": (100 * misses / references) if references else 0.0,
        "instructions_per_cache_miss": (instructions / misses) if misses else 0.0,
    }
