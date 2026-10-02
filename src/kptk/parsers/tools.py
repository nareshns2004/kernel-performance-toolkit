"""Parsers for the text output of ``perf stat``, ``vmstat`` and ``numastat``.

These support the offline workflow from v0.1: analyse output someone pasted
into a ticket. Live collection reads /proc and perf_event_open directly instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NUMBER = re.compile(r"^([0-9][0-9,]*(?:\.[0-9]+)?)([KMG]?)$")


def _number(value: str) -> float:
    match = _NUMBER.match(value.strip())
    if not match:
        raise ValueError(f"invalid numeric value: {value!r}")
    amount = float(match.group(1).replace(",", ""))
    return amount * {"": 1, "K": 1e3, "M": 1e6, "G": 1e9}[match.group(2)]


@dataclass
class PerfStatEvent:
    value: float
    running_pct: float = 100.0  # share of enabled time the counter was scheduled on the PMU


def parse_perf_stat(text: str) -> dict[str, PerfStatEvent]:
    """Parse ``perf stat`` output, CSV (``-x ;`` or ``-x ,``) or human-readable.

    CSV columns: value; unit; event; run-time; running-pct; ... ``perf stat``
    already scales multiplexed counts. The running percentage is kept so callers
    know how much of the value is extrapolation. ``<not supported>`` and
    ``<not counted>`` rows are skipped.
    """
    out: dict[str, PerfStatEvent] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "<not" in line:
            continue
        sep = ";" if ";" in line else ("," if line.count(",") >= 2 and not re.match(r"^\s*[\d,]+\s+\S", line) else None)
        if sep:
            f = [x.strip() for x in line.split(sep)]
            if len(f) >= 3 and f[0] and f[2]:
                try:
                    pct = float(f[4]) if len(f) > 4 and f[4] else 100.0
                    out[f[2]] = PerfStatEvent(_number(f[0]), pct)
                except ValueError:
                    pass
            continue
        f = line.split()
        if len(f) < 2:
            continue
        try:
            value = _number(f[0])
        except ValueError:
            continue
        idx = 2 if len(f) > 2 and f[1] in {"msec", "seconds", "ns"} else 1
        pct = 100.0
        m = re.search(r"\((\d+(?:\.\d+)?)%\)", line)
        if m:
            pct = float(m.group(1))
        out[f[idx]] = PerfStatEvent(value, pct)
    return out


def parse_vmstat_output(text: str) -> dict[str, float]:
    """``vmstat N`` output; returns the last sample row."""
    headers: list[str] | None = None
    latest: dict[str, float] = {}
    for raw in text.splitlines():
        f = raw.split()
        if not f:
            continue
        if f[0] == "procs":
            continue
        if f[0] == "r" and "free" in f:
            headers = f
            continue
        if headers and len(f) >= len(headers) and all(x.lstrip("-").isdigit() for x in f[: len(headers)]):
            latest = {k: float(v) for k, v in zip(headers, f, strict=False)}
    return latest


def parse_numastat_output(text: str) -> dict[str, dict[str, float]]:
    """``numastat`` output as metric -> node -> value (plus ``total``)."""
    result: dict[str, dict[str, float]] = {}
    nodes: list[str] = []
    for raw in text.splitlines():
        f = raw.split()
        if f and all(re.fullmatch(r"node\d+", x.lower()) for x in f):
            nodes = [x.lower() for x in f]
            continue
        if len(f) < 2 or not all(re.fullmatch(r"[0-9.,]+", v) for v in f[1:]):
            continue
        values = [float(v.replace(",", "")) for v in f[1:]]
        if nodes and len(values) == len(nodes):
            result[f[0]] = {**dict(zip(nodes, values, strict=True)), "total": sum(values)}
        elif len(values) >= 2:
            result[f[0]] = {**{f"node{i}": v for i, v in enumerate(values[:-1])}, "total": values[-1]}
    return result
