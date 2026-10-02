"""Parsers for /sys formats: CPU lists, THP modes, cache sizes, NUMA distances."""

from __future__ import annotations


def parse_cpulist(text: str) -> list[int]:
    """``0-3,8-11`` -> [0, 1, 2, 3, 8, 9, 10, 11]."""
    out: list[int] = []
    for part in text.strip().split(","):
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-")
            out.extend(range(int(lo), int(hi) + 1))
        else:
            out.append(int(part))
    return out


def parse_bracket_choice(text: str) -> str | None:
    """THP-style mode files: ``always [madvise] never`` -> ``madvise``."""
    for token in text.split():
        if token.startswith("[") and token.endswith("]"):
            return token[1:-1]
    return None


def parse_size(text: str) -> int:
    """Cache size strings: ``48K`` / ``2048K`` / ``32M`` -> bytes."""
    t = text.strip().upper()
    mult = {"K": 1 << 10, "M": 1 << 20, "G": 1 << 30}.get(t[-1:], 1)
    return int(t.rstrip("KMG")) * mult


def parse_distance(text: str) -> list[int]:
    """``/sys/devices/system/node/nodeN/distance``: SLIT distances (10 = local)."""
    return [int(x) for x in text.split()]
