"""CPU / cache / NUMA topology reconstructed from captured sysfs files."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .parsers.sysfs import parse_cpulist, parse_distance, parse_size

_CPU = re.compile(r"/sys/devices/system/cpu/cpu(\d+)/")
_NODE = re.compile(r"/sys/devices/system/node/node(\d+)/")


@dataclass(frozen=True)
class Cache:
    level: int
    type: str
    size_bytes: int
    shared_cpus: tuple[int, ...]
    line_bytes: int = 64


@dataclass
class Topology:
    cpus: list[int] = field(default_factory=list)
    cpu_node: dict[int, int] = field(default_factory=dict)
    cpu_package: dict[int, int] = field(default_factory=dict)
    cpu_core: dict[int, int] = field(default_factory=dict)
    smt_siblings: dict[int, list[int]] = field(default_factory=dict)
    node_cpus: dict[int, list[int]] = field(default_factory=dict)
    distances: dict[int, list[int]] = field(default_factory=dict)
    caches: list[Cache] = field(default_factory=list)
    governors: dict[int, str] = field(default_factory=dict)

    @property
    def nodes(self) -> list[int]:
        return sorted(self.node_cpus) or [0]

    @property
    def packages(self) -> int:
        return len(set(self.cpu_package.values())) or 1

    @property
    def physical_cores(self) -> int:
        return len(set(zip(self.cpu_package.values(), self.cpu_core.values(), strict=False))) or len(self.cpus)

    @property
    def smt(self) -> bool:
        return any(len(s) > 1 for s in self.smt_siblings.values())

    def distance(self, a: int, b: int) -> int:
        row = self.distances.get(a)
        return row[b] if row and b < len(row) else (10 if a == b else 20)

    def cache(self, level: int, kind: str = "Unified") -> Cache | None:
        for c in self.caches:
            if c.level == level and (c.type == kind or (kind == "Data" and c.type == "Data")):
                return c
        return None

    @property
    def llc(self) -> Cache | None:
        return max(self.caches, key=lambda c: c.level, default=None)

    def summary(self) -> dict[str, object]:
        return {
            "cpus": len(self.cpus),
            "packages": self.packages,
            "physical_cores": self.physical_cores,
            "smt": self.smt,
            "numa_nodes": len(self.nodes),
            "node_cpus": {n: _fmt_cpus(c) for n, c in self.node_cpus.items()},
            "distances": self.distances,
            "caches": [
                {"level": c.level, "type": c.type, "size_kib": c.size_bytes // 1024, "shared_by_cpus": len(c.shared_cpus), "instances": self._instances(c)}
                for c in self.caches
            ],
            "governors": sorted(set(self.governors.values())),
        }

    def _instances(self, c: Cache) -> int:
        return max(1, len(self.cpus) // max(1, len(c.shared_cpus)))


def _fmt_cpus(cpus: list[int]) -> str:
    if not cpus:
        return ""
    out, start, prev = [], cpus[0], cpus[0]
    for c in [*cpus[1:], None]:
        if c is not None and c == prev + 1:
            prev = c
            continue
        out.append(f"{start}-{prev}" if prev != start else str(start))
        if c is not None:
            start = prev = c
    return ",".join(out)


def from_static(files: dict[str, str]) -> Topology:
    topo = Topology()
    online = files.get("/sys/devices/system/cpu/online")
    cpus: set[int] = set(parse_cpulist(online)) if online else set()
    cache_parts: dict[tuple[int, str], dict[str, str]] = {}
    for path, text in files.items():
        if m := _CPU.match(path):
            cpu = int(m.group(1))
            cpus.add(cpu)
            value = text.strip()
            if path.endswith("/topology/physical_package_id"):
                topo.cpu_package[cpu] = int(value)
            elif path.endswith("/topology/core_id"):
                topo.cpu_core[cpu] = int(value)
            elif path.endswith("/topology/thread_siblings_list"):
                topo.smt_siblings[cpu] = parse_cpulist(value)
            elif path.endswith("/cpufreq/scaling_governor"):
                topo.governors[cpu] = value
            elif "/cache/index" in path:
                idx = path.split("/cache/")[1].split("/")[0]
                cache_parts.setdefault((cpu, idx), {})[path.rsplit("/", 1)[1]] = value
        elif m := _NODE.match(path):
            node = int(m.group(1))
            if path.endswith("/cpulist"):
                topo.node_cpus[node] = parse_cpulist(text)
                for c in topo.node_cpus[node]:
                    topo.cpu_node[c] = node
            elif path.endswith("/distance"):
                topo.distances[node] = parse_distance(text)
    topo.cpus = sorted(cpus)
    for c in topo.cpus:
        topo.cpu_node.setdefault(c, 0)
    if not topo.node_cpus:
        topo.node_cpus = {0: topo.cpus}
    seen: set[tuple[int, str, tuple[int, ...]]] = set()
    for parts in cache_parts.values():
        if not {"level", "type", "size"} <= parts.keys():
            continue
        shared = tuple(parse_cpulist(parts.get("shared_cpu_list", "")))
        key = (int(parts["level"]), parts["type"], shared)
        if key in seen:
            continue
        seen.add(key)
        topo.caches.append(Cache(key[0], key[1], parse_size(parts["size"]), shared, int(parts.get("coherency_line_size", "64") or 64)))
    # Keep one representative per (level, type); sizes are per instance.
    unique: dict[tuple[int, str], Cache] = {}
    for cache in sorted(topo.caches, key=lambda x: (x.level, x.type, -len(x.shared_cpus))):
        unique.setdefault((cache.level, cache.type), cache)
    topo.caches = sorted(unique.values(), key=lambda c: (c.level, c.type))
    return topo
