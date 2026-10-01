"""Delta helpers over a recording: first/last sample, per-file deltas, per-sample rates."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from ..record import Recording, Sample

T = TypeVar("T")


class Window:
    """The analysis window: from the first to the last sample of a recording."""

    def __init__(self, rec: Recording) -> None:
        if len(rec.samples) < 2:
            raise ValueError("a recording needs at least 2 samples to compute rates")
        self.rec = rec
        self.first, self.last = rec.samples[0], rec.samples[-1]
        self.dt = self.last.ts - self.first.ts
        self.user_hz = int(rec.meta.get("user_hz") or 100)
        self.page_kib = int(rec.meta.get("page_size") or 4096) // 1024

    def pair(self, path: str, parse: Callable[[str], T]) -> tuple[T, T, float] | None:
        """Parsed values at the first and last sample containing ``path``, plus the elapsed time between them."""
        present = [s for s in self.rec.samples if path in s.files]
        if len(present) < 2:
            return None
        a, b = present[0], present[-1]
        return parse(a.files[path]), parse(b.files[path]), b.ts - a.ts

    def series(self, path: str, parse: Callable[[str], T]) -> list[tuple[Sample, T]]:
        return [(s, parse(s.files[path])) for s in self.rec.samples if path in s.files]

    def latest(self, path: str, parse: Callable[[str], T]) -> T | None:
        text = self.rec.latest(path)
        return parse(text) if text is not None else None

    def has(self, path: str) -> bool:
        return any(path in s.files for s in self.rec.samples)


def delta_dict(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    return {k: b[k] - a[k] for k in b if k in a}


def rate(d: dict[str, int], dt: float, *keys: str) -> float:
    return sum(d.get(k, 0) for k in keys) / dt if dt > 0 else 0.0


def ratio(num: float, den: float) -> float | None:
    return num / den if den else None
