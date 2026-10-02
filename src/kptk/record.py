"""Capture raw /proc and /sys text over time; analyse later, anywhere.

Design: the recorder never parses. It copies file contents verbatim into a
JSONL recording, which keeps per-sample overhead to a few hundred
microseconds of read() syscalls. It also means a recording taken on a
production host can be analysed on a laptop, re-analysed after a parser bug
fix, or attached to a ticket. Parsing and analysis are pure functions over
the recording (see :mod:`kptk.analysis`).

Expensive files (``numa_maps`` and ``smaps_rollup`` walk the target's page
tables under its mmap lock) are read only every ``slow_every`` samples, so
the recorder doesn't perturb the process it's watching.
"""

from __future__ import annotations

import fnmatch
import glob
import gzip
import json
import os
import platform
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from . import __version__

STATIC_PATTERNS = (
    "/proc/version",
    "/proc/sys/kernel/numa_balancing",
    "/proc/sys/kernel/perf_event_paranoid",
    "/proc/sys/vm/swappiness",
    "/proc/sys/vm/zone_reclaim_mode",
    "/proc/sys/vm/overcommit_memory",
    "/proc/sys/vm/min_free_kbytes",
    "/sys/kernel/mm/transparent_hugepage/enabled",
    "/sys/kernel/mm/transparent_hugepage/defrag",
    "/sys/kernel/mm/transparent_hugepage/khugepaged/defrag",
    "/sys/kernel/mm/transparent_hugepage/khugepaged/max_ptes_none",
    "/sys/kernel/mm/transparent_hugepage/khugepaged/pages_to_scan",
    "/sys/kernel/mm/transparent_hugepage/khugepaged/scan_sleep_millisecs",
    "/sys/devices/system/cpu/online",
    "/sys/devices/system/node/online",
    "/sys/devices/system/cpu/cpu[0-9]*/topology/physical_package_id",
    "/sys/devices/system/cpu/cpu[0-9]*/topology/core_id",
    "/sys/devices/system/cpu/cpu[0-9]*/topology/thread_siblings_list",
    "/sys/devices/system/cpu/cpu[0-9]*/cache/index[0-9]*/level",
    "/sys/devices/system/cpu/cpu[0-9]*/cache/index[0-9]*/type",
    "/sys/devices/system/cpu/cpu[0-9]*/cache/index[0-9]*/size",
    "/sys/devices/system/cpu/cpu[0-9]*/cache/index[0-9]*/shared_cpu_list",
    "/sys/devices/system/cpu/cpu[0-9]*/cache/index[0-9]*/coherency_line_size",
    "/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_governor",
    "/sys/devices/system/node/node[0-9]*/cpulist",
    "/sys/devices/system/node/node[0-9]*/distance",
    "/sys/kernel/mm/hugepages/hugepages-*/nr_hugepages",
    "/sys/kernel/mm/hugepages/hugepages-*/free_hugepages",
    "/sys/kernel/mm/hugepages/hugepages-*/resv_hugepages",
    "/sys/kernel/mm/hugepages/hugepages-*/surplus_hugepages",
)

DYNAMIC_PATTERNS = (
    "/proc/stat",
    "/proc/schedstat",
    "/proc/vmstat",
    "/proc/meminfo",
    "/proc/loadavg",
    "/proc/pressure/cpu",
    "/proc/pressure/memory",
    "/proc/pressure/io",
    "/sys/devices/system/node/node[0-9]*/numastat",
    "/sys/devices/system/node/node[0-9]*/meminfo",
)

PID_FAST = ("stat", "status", "schedstat")
PID_SLOW = ("numa_maps", "smaps_rollup")


class Source:
    """Where file contents come from: the live system, or a dict (tests, replays)."""

    def read(self, path: str) -> str | None:
        raise NotImplementedError

    def glob(self, pattern: str) -> list[str]:
        raise NotImplementedError


class LiveSource(Source):
    def __init__(self, root: str = "/") -> None:
        self.root = root.rstrip("/")

    def read(self, path: str) -> str | None:
        try:
            with open(self.root + path, encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:  # vanished pid, permission denied, attribute unsupported on this kernel
            return None

    def glob(self, pattern: str) -> list[str]:
        n = len(self.root)
        return sorted(p[n:] for p in glob.glob(self.root + pattern))


class DictSource(Source):
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    def read(self, path: str) -> str | None:
        return self.files.get(path)

    def glob(self, pattern: str) -> list[str]:
        return sorted(p for p in self.files if fnmatch.fnmatchcase(p, pattern))


def expand(source: Source, patterns: Iterable[str]) -> list[str]:
    out: list[str] = []
    for pat in patterns:
        out.extend(source.glob(pat) if any(c in pat for c in "*?[") else [pat])
    return out


@dataclass
class Sample:
    ts: float
    files: dict[str, str]
    cost_s: float = 0.0


@dataclass
class Recording:
    meta: dict[str, Any]
    static: dict[str, str]
    samples: list[Sample] = field(default_factory=list)
    counters: dict[str, Any] | None = None

    @property
    def pid(self) -> int | None:
        pid = self.meta.get("pid")
        return int(pid) if pid else None

    @property
    def duration_s(self) -> float:
        return self.samples[-1].ts - self.samples[0].ts if len(self.samples) > 1 else 0.0

    def latest(self, path: str) -> str | None:
        """Most recent sample that contains ``path`` (slow files aren't in every sample)."""
        for s in reversed(self.samples):
            if path in s.files:
                return s.files[path]
        return None

    def save(self, path: str | Path) -> None:
        with _open(path, "w") as fh:
            fh.write(json.dumps({"type": "meta", **self.meta, "static": self.static}) + "\n")
            for s in self.samples:
                fh.write(json.dumps({"type": "sample", "ts": s.ts, "cost_s": s.cost_s, "files": s.files}, separators=(",", ":")) + "\n")
            if self.counters is not None:
                fh.write(json.dumps({"type": "counters", **self.counters}) + "\n")

    @classmethod
    def load(cls, path: str | Path) -> Recording:
        meta: dict[str, Any] = {}
        static: dict[str, str] = {}
        samples: list[Sample] = []
        counters = None
        with _open(path, "r") as fh:
            for line in fh:
                if not line.strip():
                    continue
                d = json.loads(line)
                kind = d.pop("type")
                if kind == "meta":
                    static = d.pop("static", {})
                    meta = d
                elif kind == "sample":
                    samples.append(Sample(d["ts"], d["files"], d.get("cost_s", 0.0)))
                elif kind == "counters":
                    counters = d
        return cls(meta, static, samples, counters)


def _open(path: str | Path, mode: str) -> IO[str]:
    p = Path(path)
    if p.suffix == ".gz":
        return gzip.open(p, mode + "t", encoding="utf-8")  # type: ignore[return-value]
    return p.open(mode, encoding="utf-8")


class Recorder:
    """Deadline-scheduled sampler (no drift; overruns are skipped and counted, not burst)."""

    def __init__(
        self,
        source: Source | None = None,
        interval_s: float = 1.0,
        pid: int | None = None,
        slow_every: int = 5,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.source = source or LiveSource()
        self.interval_s = interval_s
        self.pid = pid
        self.slow_every = max(1, slow_every)
        self.clock = clock
        self.stop_event = threading.Event()
        self.overruns = 0
        self._dynamic = expand(self.source, DYNAMIC_PATTERNS)

    def capture_static(self) -> dict[str, str]:
        out = {}
        for p in expand(self.source, STATIC_PATTERNS):
            text = self.source.read(p)
            if text is not None:
                out[p] = text
        return out

    def sample(self, index: int) -> Sample:
        t0 = time.perf_counter()
        ts = self.clock()
        files: dict[str, str] = {}
        paths = list(self._dynamic)
        if self.pid:
            paths += [f"/proc/{self.pid}/{n}" for n in PID_FAST]
            if index % self.slow_every == 0:
                paths += [f"/proc/{self.pid}/{n}" for n in PID_SLOW]
        for p in paths:
            text = self.source.read(p)
            if text is not None:
                files[p] = text
        return Sample(ts, files, time.perf_counter() - t0)

    def meta(self, command: list[str] | None = None) -> dict[str, Any]:
        return {
            "tool": f"kptk {__version__}",
            "host": platform.node(),
            "kernel": platform.release(),
            "arch": platform.machine(),
            "started": self.clock(),
            "interval_s": self.interval_s,
            "pid": self.pid,
            "command": command,
            "user_hz": os.sysconf("SC_CLK_TCK"),
            "page_size": os.sysconf("SC_PAGE_SIZE"),
        }

    def run(
        self,
        duration_s: float | None = None,
        max_samples: int | None = None,
        until: Callable[[], bool] | None = None,
        command: list[str] | None = None,
    ) -> Recording:
        rec = Recording(self.meta(command), self.capture_static())
        start = deadline = time.monotonic()
        i = 0
        while not self.stop_event.is_set():
            rec.samples.append(self.sample(i))
            i += 1
            if max_samples is not None and i >= max_samples:
                break
            if until is not None and until():
                rec.samples.append(self.sample(0))  # closing sample, including slow files if still readable
                break
            if duration_s is not None and time.monotonic() - start >= duration_s:
                break
            deadline += self.interval_s
            now = time.monotonic()
            if now > deadline:
                missed = int((now - deadline) // self.interval_s) + 1
                self.overruns += missed
                deadline += missed * self.interval_s
            self.stop_event.wait(max(0.0, deadline - time.monotonic()))
        rec.meta["overruns"] = self.overruns
        return rec
