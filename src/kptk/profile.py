"""Profile a command: hardware counters plus /proc recording for its whole lifetime.

The counters must cover the command from its first instruction, without counting the
profiler. That's the classic ``perf stat`` sequence:

1. fork; the child blocks reading a pipe *before* exec,
2. the parent opens counters on the child pid with ``disabled=1, enable_on_exec=1, inherit=1``,
3. the parent writes to the pipe; the child execs, and the kernel enables the counters at exec.

``subprocess.Popen`` can't do this: it blocks until the child has exec'd
(it waits on an internal status pipe), but here the child is deliberately
held *before* exec until the parent has attached the counters, so Popen
deadlocks. Plain ``os.fork`` / ``os.execvp`` avoids that.

Exit detection uses ``waitid(WNOWAIT)`` so the child stays a zombie until the
recorder takes its closing sample: /proc/<pid>/stat of a zombie still holds the
final fault and CPU-time totals.
"""

from __future__ import annotations

import os

from .perf_event import DEFAULT_EVENTS, Counters, PerfUnavailable, readings_to_dict
from .record import Recorder, Recording


def _exited(pid: int) -> bool:
    try:
        return os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
    except ChildProcessError:
        return True


def profile(cmd: list[str], interval_s: float = 0.5, events: list[str] | None = None, slow_every: int = 4) -> tuple[Recording, int]:
    rfd, wfd = os.pipe()
    pid = os.fork()
    if pid == 0:  # child: wait for the go signal, then become the command
        try:
            os.close(wfd)
            os.read(rfd, 1)
            os.close(rfd)
            os.execvp(cmd[0], cmd)
        except BaseException:
            os._exit(127)
    os.close(rfd)
    counters: Counters | None = None
    error = None
    try:
        counters = Counters(events or DEFAULT_EVENTS, pid=pid, inherit=True, enable_on_exec=True)
    except PerfUnavailable as exc:
        error = str(exc)
    os.write(wfd, b"g")
    os.close(wfd)

    rec = Recorder(interval_s=interval_s, pid=pid, slow_every=slow_every).run(until=lambda: _exited(pid), command=cmd)
    _, status = os.waitpid(pid, 0)
    returncode = os.waitstatus_to_exitcode(status)
    if counters is not None:
        rec.counters = readings_to_dict(counters.read(), counters.skipped)
        counters.close()
    else:
        rec.counters = {"events": None, "error": error}
    return rec, returncode
