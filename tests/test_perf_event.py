import ctypes
import errno
import os

import pytest

from kptk import perf_event
from kptk.perf_event import (
    FLAG_DISABLED,
    FLAG_ENABLE_ON_EXEC,
    FLAG_EXCLUDE_KERNEL,
    FLAG_INHERIT,
    FORMAT_TOTAL_TIME_ENABLED,
    FORMAT_TOTAL_TIME_RUNNING,
    PERF_ATTR_SIZE_VER5,
    Counters,
    PerfEventAttr,
    PerfUnavailable,
    event_config,
    make_attr,
    readings_to_dict,
    scale,
)


def test_attr_layout_matches_uapi_header() -> None:
    assert ctypes.sizeof(PerfEventAttr) == PERF_ATTR_SIZE_VER5 == 112
    offsets = {name: getattr(PerfEventAttr, name).offset for name, _ in PerfEventAttr._fields_}
    assert offsets["config"] == 8
    assert offsets["read_format"] == 32
    assert offsets["flags"] == 40
    assert offsets["config1"] == 56
    assert offsets["sample_regs_intr"] == 96
    assert offsets["sample_max_stack"] == 108


def test_event_encoding() -> None:
    assert event_config("instructions") == (0, 1)
    assert event_config("major-faults") == (1, 6)
    # LLC (2) | read (0 << 8) | miss (1 << 16)
    assert event_config("LLC-load-misses") == (3, 2 | (1 << 16))
    assert event_config("dTLB-loads") == (3, 3)
    with pytest.raises(ValueError):
        event_config("not-an-event")


def test_make_attr_flags() -> None:
    a = make_attr("cycles", inherit=True, enable_on_exec=True)
    assert a.flags & FLAG_DISABLED and a.flags & FLAG_INHERIT and a.flags & FLAG_ENABLE_ON_EXEC
    assert a.read_format == FORMAT_TOTAL_TIME_ENABLED | FORMAT_TOTAL_TIME_RUNNING
    assert a.size == 112


def test_multiplex_scaling() -> None:
    assert scale(1000, enabled=100, running=25) == (4000.0, 0.25)
    assert scale(1000, 100, 0) == (0.0, 0.0)


def test_live_counters_or_clean_failure() -> None:
    try:
        with Counters(["task-clock", "instructions"], inherit=False) as c:
            c.enable()
            sum(range(1000))
            c.disable()
            readings = c.read()
    except PerfUnavailable as exc:
        assert "perf_event_open failed" in str(exc)
        pytest.skip(f"perf_event_open not permitted here: {exc}")
    assert readings and readings[0].value > 0


class _FakeKernel:
    """Stands in for perf_event_open: refuses kernel-mode counting like perf_event_paranoid=2 does for a non-root user."""

    def __init__(self, allow_user: bool = True) -> None:
        self.allow_user = allow_user
        self.calls: list[int] = []

    def __call__(self, attr: PerfEventAttr, pid: int, cpu: int, group_fd: int, flags: int) -> int:
        self.calls.append(attr.flags)
        if not attr.flags & FLAG_EXCLUDE_KERNEL or not self.allow_user:
            raise PerfUnavailable("perf_event_open failed: Permission denied", errno.EACCES)
        return os.open(os.devnull, os.O_RDONLY)


def test_paranoid2_falls_back_to_user_only(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeKernel()
    monkeypatch.setattr(perf_event, "_syscall", fake)
    c = Counters(["cycles", "instructions", "task-clock"], pid=1234)
    try:
        assert c.scope == "user" and c.user_only
        assert [name for name, _ in c.fds] == ["cycles", "instructions", "task-clock"]
        assert not c.skipped
        assert c.fallback_reason and "Permission denied" in c.fallback_reason
        # First attempt was user+kernel, then every event was reopened user-only.
        assert not fake.calls[0] & FLAG_EXCLUDE_KERNEL
        assert all(f & FLAG_EXCLUDE_KERNEL for f in fake.calls[1:]) and len(fake.calls) == 4
    finally:
        c.close()
    assert readings_to_dict([], {}, c.scope)["counted_scope"] == "user"


def test_no_permission_at_all_still_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(perf_event, "_syscall", _FakeKernel(allow_user=False))
    with pytest.raises(PerfUnavailable) as info:
        Counters(["cycles"], pid=1234)
    assert info.value.errno == errno.EACCES and info.value.permission


def test_unsupported_event_is_skipped_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    def syscall(attr: PerfEventAttr, *_: int) -> int:
        if attr.config == 1:  # instructions
            raise PerfUnavailable("perf_event_open failed: No such file or directory", errno.ENOENT)
        return os.open(os.devnull, os.O_RDONLY)

    monkeypatch.setattr(perf_event, "_syscall", syscall)
    with Counters(["cycles", "instructions"], pid=1234) as c:
        assert c.scope == "user+kernel"
        assert list(c.skipped) == ["instructions"] and [n for n, _ in c.fds] == ["cycles"]


def test_user_only_scope_surfaces_in_report() -> None:
    from kptk.analysis.engine import analyze
    from kptk.synth import scenario

    rec = scenario("healthy")
    assert rec.counters is not None
    rec.counters["counted_scope"] = "user"
    report = analyze(rec)
    assert report["metrics"]["cache"]["counted_scope"] == "user"
    assert any(f["id"] == "cache.user_only" for f in report["findings"])
