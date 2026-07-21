"""Parsers for Linux performance command output."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable


_NUMBER = re.compile(r"^([0-9][0-9,]*(?:\.[0-9]+)?)([KMG]?)$")


def _number(value: str) -> float:
    """Convert perf-style numbers (including commas and K/M/G suffixes)."""
    match = _NUMBER.match(value.strip())
    if not match:
        raise ValueError(f"invalid numeric value: {value!r}")
    amount = float(match.group(1).replace(",", ""))
    return amount * {"": 1, "K": 1_000, "M": 1_000_000, "G": 1_000_000_000}[match.group(2)]


def parse_perf_stat(text: str) -> dict[str, float]:
    """Parse ``perf stat`` CSV or human-readable output into event counters.

    Unsupported, unavailable counters (``<not supported>``) are deliberately
    ignored so reports remain useful on restricted hosts.
    """
    metrics: dict[str, float] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "<not" in line:
            continue
        if ";" in line:
            fields = [field.strip() for field in line.split(";")]
            if len(fields) >= 3 and fields[0] and fields[2]:
                try:
                    metrics[fields[2]] = _number(fields[0])
                except ValueError:
                    pass
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        try:
            value = _number(fields[0])
        except ValueError:
            continue
        # perf may print a unit between the count and event name.
        event_index = 2 if len(fields) > 2 and fields[1] in {"msec", "seconds", "ns"} else 1
        if event_index < len(fields):
            metrics[fields[event_index]] = value
    return metrics


def parse_vmstat(text: str) -> dict[str, float]:
    """Parse one or more ``vmstat`` snapshots, retaining the last sample."""
    headers: list[str] | None = None
    latest: dict[str, float] = {}
    for raw_line in text.splitlines():
        fields = raw_line.split()
        if not fields:
            continue
        if fields[0] == "procs":
            headers = None
            continue
        if fields[0] == "r" and "free" in fields:
            headers = fields
            continue
        if headers and len(fields) >= len(headers) and all(item.lstrip("-").isdigit() for item in fields[:len(headers)]):
            latest = {key: float(value) for key, value in zip(headers, fields)}
    return latest


def parse_numastat(text: str) -> dict[str, dict[str, float]]:
    """Parse ``numastat`` output as metric -> node -> value."""
    result: dict[str, dict[str, float]] = {}
    nodes: list[str] = []
    for raw_line in text.splitlines():
        fields = raw_line.split()
        if fields and all(re.fullmatch(r"node\d+", field.lower()) for field in fields):
            nodes = [field.lower() for field in fields]
            continue
        if len(fields) < 2 or fields[0].lower() in {"node", "per-node"}:
            continue
        metric = fields[0]
        values = fields[1:]
        if not all(re.fullmatch(r"[0-9.,]+", value) for value in values):
            continue
        numeric_values = [float(value.replace(",", "")) for value in values]
        if nodes and len(numeric_values) == len(nodes):
            result[metric] = dict(zip(nodes, numeric_values))
            result[metric]["total"] = sum(numeric_values)
        elif len(numeric_values) >= 2:  # output with a trailing Total column
            result[metric] = {f"node{i}": value for i, value in enumerate(numeric_values[:-1])}
            result[metric]["total"] = numeric_values[-1]
    return result


def read_and_parse(path: str | Path, parser: object) -> dict:
    return parser(Path(path).read_text(encoding="utf-8"))
