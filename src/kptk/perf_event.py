"""Hardware and software counters through the ``perf_event_open(2)`` syscall, via ctypes.

No ``perf`` binary or kernel headers are needed: the attr struct, constants and
ioctls are defined here from ``include/uapi/linux/perf_event.h``.

Multiplexing: a CPU has a handful of programmable PMU counters (often 4-8 per
hyperthread). When more events are requested, the kernel time-slices them, and
each counter reports ``time_enabled`` and ``time_running``. The estimated full count is
``value * enabled / running``, an extrapolation that assumes the workload is
uniform over time. Grouped events are always scheduled together, so *ratios within a
group* (IPC, miss rate) stay exact even under multiplexing. That's why
:class:`CounterGroup` keeps leader-relative ratios in one group when it can.

Access is governed by ``/proc/sys/kernel/perf_event_paranoid`` (and
CAP_PERFMON). On Ubuntu the default of 4 blocks unprivileged use entirely, so every
entry point here degrades to a clear :class:`PerfUnavailable` error instead of crashing.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import platform
import struct
from dataclasses import dataclass

# perf_type_id
TYPE_HARDWARE, TYPE_SOFTWARE, TYPE_HW_CACHE = 0, 1, 3
# perf_hw_id
HW = {
    "cycles": 0,
    "instructions": 1,
    "cache-references": 2,
    "cache-misses": 3,
    "branches": 4,
    "branch-misses": 5,
    "stalled-cycles-frontend": 7,
    "stalled-cycles-backend": 8,
    "ref-cycles": 9,
}
# perf_sw_ids
SW = {
    "cpu-clock": 0,
    "task-clock": 1,
    "page-faults": 2,
    "context-switches": 3,
    "cpu-migrations": 4,
    "minor-faults": 5,
    "major-faults": 6,
}
# HW cache: id | (op << 8) | (result << 16)
CACHE_ID = {"L1-dcache": 0, "L1-icache": 1, "LLC": 2, "dTLB": 3, "iTLB": 4, "branch": 5, "node": 6}
# perf's naming: "<cache>-loads" counts accesses, "<cache>-load-misses" counts misses.
CACHE_SUFFIX = {
    "loads": (0, 0),
    "load-misses": (0, 1),
    "stores": (1, 0),
    "store-misses": (1, 1),
    "prefetches": (2, 0),
    "prefetch-misses": (2, 1),
}

FORMAT_TOTAL_TIME_ENABLED, FORMAT_TOTAL_TIME_RUNNING, FORMAT_ID, FORMAT_GROUP = 1, 2, 4, 8
FLAG_DISABLED, FLAG_INHERIT, FLAG_EXCLUDE_KERNEL, FLAG_EXCLUDE_HV, FLAG_ENABLE_ON_EXEC = 1 << 0, 1 << 1, 1 << 5, 1 << 6, 1 << 12
IOC_ENABLE, IOC_DISABLE, IOC_RESET, IOC_FLAG_GROUP = 0x2400, 0x2401, 0x2403, 1

SYSCALL_NR = {"x86_64": 298, "aarch64": 241, "riscv64": 241, "ppc64le": 319, "s390x": 331, "i686": 336}
PERF_ATTR_SIZE_VER5 = 112


class PerfEventAttr(ctypes.Structure):
    """``struct perf_event_attr`` up to PERF_ATTR_SIZE_VER5 (112 bytes, Linux 4.1+).

    Newer kernels accept older sizes, so declaring the minimum we need keeps
    this working everywhere.
    """

    _fields_ = [
        ("type", ctypes.c_uint32),
        ("size", ctypes.c_uint32),
        ("config", ctypes.c_uint64),
        ("sample_period", ctypes.c_uint64),
        ("sample_type", ctypes.c_uint64),
        ("read_format", ctypes.c_uint64),
        ("flags", ctypes.c_uint64),
        ("wakeup_events", ctypes.c_uint32),
        ("bp_type", ctypes.c_uint32),
        ("config1", ctypes.c_uint64),
        ("config2", ctypes.c_uint64),
        ("branch_sample_type", ctypes.c_uint64),
        ("sample_regs_user", ctypes.c_uint64),
        ("sample_stack_user", ctypes.c_uint32),
        ("clockid", ctypes.c_int32),
        ("sample_regs_intr", ctypes.c_uint64),
        ("aux_watermark", ctypes.c_uint32),
        ("sample_max_stack", ctypes.c_uint16),
        ("reserved_2", ctypes.c_uint16),
    ]


class PerfUnavailable(RuntimeError):
    pass


def event_config(name: str) -> tuple[int, int]:
    """Map a perf-style event name to ``(type, config)``."""
    if name in HW:
        return TYPE_HARDWARE, HW[name]
    if name in SW:
        return TYPE_SOFTWARE, SW[name]
    for cache, cid in CACHE_ID.items():
        if name.startswith(cache + "-") and name[len(cache) + 1 :] in CACHE_SUFFIX:
            op, result = CACHE_SUFFIX[name[len(cache) + 1 :]]
            return TYPE_HW_CACHE, cid | (op << 8) | (result << 16)
    raise ValueError(f"unknown event {name!r}")


def make_attr(
    name: str, *, disabled: bool = True, inherit: bool = False, enable_on_exec: bool = False, user_only: bool = False, group: bool = False
) -> PerfEventAttr:
    etype, config = event_config(name)
    attr = PerfEventAttr()
    attr.type, attr.size, attr.config = etype, ctypes.sizeof(PerfEventAttr), config
    attr.read_format = FORMAT_TOTAL_TIME_ENABLED | FORMAT_TOTAL_TIME_RUNNING | (FORMAT_GROUP | FORMAT_ID if group else 0)
    flags = FLAG_EXCLUDE_HV
    flags |= FLAG_DISABLED if disabled else 0
    flags |= FLAG_INHERIT if inherit else 0
    flags |= FLAG_ENABLE_ON_EXEC if enable_on_exec else 0
    flags |= FLAG_EXCLUDE_KERNEL if user_only else 0
    attr.flags = flags
    return attr


def scale(value: int, enabled: int, running: int) -> tuple[float, float]:
    """Multiplexing-corrected count and the fraction of time actually counted."""
    if running == 0:
        return 0.0, 0.0
    return value * enabled / running, running / enabled if enabled else 0.0


_libc: ctypes.CDLL | None = None


def _syscall(attr: PerfEventAttr, pid: int, cpu: int, group_fd: int, flags: int) -> int:
    global _libc
    nr = SYSCALL_NR.get(platform.machine())
    if nr is None:
        raise PerfUnavailable(f"perf_event_open syscall number unknown for {platform.machine()}")
    if _libc is None:
        _libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    fd = int(_libc.syscall(nr, ctypes.byref(attr), pid, cpu, group_fd, ctypes.c_ulong(flags)))
    if fd < 0:
        err = ctypes.get_errno()
        hint = ""
        if err in (errno.EACCES, errno.EPERM):
            hint = f" (perf_event_paranoid={_paranoid()}; need <= 2 for own processes, or CAP_PERFMON)"
        elif err == errno.ENOENT:
            hint = " (event not supported by this CPU/PMU, common in VMs)"
        raise PerfUnavailable(f"perf_event_open failed: {os.strerror(err)}{hint}")
    return fd


def _ioctl(fd: int, request: int, arg: int = 0) -> None:
    assert _libc is not None
    if _libc.ioctl(fd, request, arg) < 0:
        raise OSError(ctypes.get_errno(), "perf ioctl failed")


def _paranoid() -> str:
    try:
        with open("/proc/sys/kernel/perf_event_paranoid") as fh:
            return fh.read().strip()
    except OSError:
        return "?"


@dataclass
class Reading:
    name: str
    raw: int
    enabled_ns: int
    running_ns: int

    @property
    def value(self) -> float:
        return scale(self.raw, self.enabled_ns, self.running_ns)[0]

    @property
    def running_frac(self) -> float:
        return scale(self.raw, self.enabled_ns, self.running_ns)[1]


class Counters:
    """Independent (ungrouped) counters on a task, optionally inherited by its children.

    ``inherit`` is needed to count all threads and children of a command, and
    the kernel doesn't support PERF_FORMAT_GROUP reads on inherited events, so
    these are separate fds. Each is multiplex-scaled independently.
    """

    def __init__(self, events: list[str], pid: int = 0, cpu: int = -1, inherit: bool = True, enable_on_exec: bool = False) -> None:
        self.events = events
        self.fds: list[tuple[str, int]] = []
        self.skipped: dict[str, str] = {}
        for name in events:
            attr = make_attr(name, disabled=True, inherit=inherit, enable_on_exec=enable_on_exec)
            try:
                self.fds.append((name, _syscall(attr, pid, cpu, -1, 0)))
            except PerfUnavailable as exc:
                if not self.fds and "paranoid" in str(exc):
                    raise  # no permission at all: fail fast with the hint
                self.skipped[name] = str(exc)

    def enable(self) -> None:
        for _, fd in self.fds:
            _ioctl(fd, IOC_ENABLE)

    def disable(self) -> None:
        for _, fd in self.fds:
            _ioctl(fd, IOC_DISABLE)

    def read(self) -> list[Reading]:
        out = []
        for name, fd in self.fds:
            value, enabled, running = struct.unpack("QQQ", os.read(fd, 24))
            out.append(Reading(name, value, enabled, running))
        return out

    def close(self) -> None:
        for _, fd in self.fds:
            os.close(fd)
        self.fds = []

    def __enter__(self) -> Counters:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def readings_to_dict(readings: list[Reading], skipped: dict[str, str] | None = None) -> dict[str, object]:
    return {
        "events": {r.name: {"value": r.value, "raw": r.raw, "running_frac": round(r.running_frac, 4)} for r in readings},
        "skipped": skipped or {},
    }


DEFAULT_EVENTS = [
    "cycles",
    "instructions",
    "cache-references",
    "cache-misses",
    "branches",
    "branch-misses",
    "L1-dcache-loads",
    "L1-dcache-load-misses",
    "dTLB-loads",
    "dTLB-load-misses",
    "task-clock",
    "context-switches",
    "cpu-migrations",
    "minor-faults",
    "major-faults",
]
