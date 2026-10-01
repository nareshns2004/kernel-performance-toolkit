import ctypes

import pytest

from kptk.perf_event import (
    FLAG_DISABLED,
    FLAG_ENABLE_ON_EXEC,
    FLAG_INHERIT,
    FORMAT_TOTAL_TIME_ENABLED,
    FORMAT_TOTAL_TIME_RUNNING,
    PERF_ATTR_SIZE_VER5,
    Counters,
    PerfEventAttr,
    PerfUnavailable,
    event_config,
    make_attr,
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
