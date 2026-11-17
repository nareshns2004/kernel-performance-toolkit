from kptk.record import DictSource, Recorder, Recording, expand
from kptk.synth import Spec, scenario, static_files
from kptk.topology import from_static


def test_dict_source_glob_and_slow_files() -> None:
    files = {
        "/proc/stat": "cpu 1 0 0 1 0 0 0 0 0 0\n",
        "/sys/devices/system/node/node0/numastat": "local_node 1\n",
        "/sys/devices/system/node/node1/numastat": "local_node 2\n",
        "/proc/7/stat": "x",
        "/proc/7/numa_maps": "y",
    }
    src = DictSource(files)
    assert expand(src, ["/sys/devices/system/node/node[0-9]*/numastat"]) == [
        "/sys/devices/system/node/node0/numastat",
        "/sys/devices/system/node/node1/numastat",
    ]
    rec = Recorder(src, interval_s=0.001, pid=7, slow_every=3).run(max_samples=4)
    has_slow = ["/proc/7/numa_maps" in s.files for s in rec.samples]
    assert has_slow == [True, False, False, True]
    assert all("/proc/7/stat" in s.files for s in rec.samples)
    assert rec.latest("/proc/7/numa_maps") == "y"


def test_recording_round_trip_gz(tmp_path) -> None:
    rec = scenario("healthy")
    path = tmp_path / "r.jsonl.gz"
    rec.save(path)
    back = Recording.load(path)
    assert back.meta["pid"] == 4242
    assert back.static == rec.static
    assert [s.files for s in back.samples] == [s.files for s in rec.samples]
    assert back.counters == rec.counters


def test_live_recording_is_cheap_and_complete() -> None:
    rec = Recorder(interval_s=0.05).run(max_samples=3)
    assert len(rec.samples) == 3
    assert "/proc/stat" in rec.samples[0].files and "/proc/vmstat" in rec.samples[0].files
    assert "/sys/devices/system/cpu/online" in rec.static
    assert max(s.cost_s for s in rec.samples) < 0.5


def test_topology_from_synthetic_two_socket() -> None:
    topo = from_static(static_files(Spec()))
    assert len(topo.cpus) == 32 and topo.nodes == [0, 1] and topo.packages == 2
    assert topo.physical_cores == 16 and topo.smt
    assert topo.distance(0, 1) == 21 and topo.distance(1, 1) == 10
    assert topo.llc is not None and topo.llc.level == 3 and topo.llc.size_bytes == 32 << 20
    assert len(topo.llc.shared_cpus) == 16
    assert topo.summary()["node_cpus"][0] == "0-7,16-23"


def test_topology_live_smoke() -> None:
    topo = from_static(Recorder().capture_static())
    assert topo.cpus and topo.nodes


def test_recorder_reads_thread_stats_capped_by_numeric_tid() -> None:
    files = {"/proc/7/stat": "x"} | {f"/proc/7/task/{tid}/stat": str(tid) for tid in (7, 9, 10, 100)}
    rec = Recorder(DictSource(files), interval_s=0.001, pid=7, max_threads=3).run(max_samples=2)
    tasks = sorted(p for p in rec.samples[0].files if "/task/" in p)
    # numeric order, not lexicographic ("100" < "7"): keeps 7, 9, 10
    assert tasks == ["/proc/7/task/10/stat", "/proc/7/task/7/stat", "/proc/7/task/9/stat"]
    assert rec.meta["max_threads"] == 3
    rec0 = Recorder(DictSource(files), interval_s=0.001, pid=7, max_threads=0).run(max_samples=1)
    assert not [p for p in rec0.samples[0].files if "/task/" in p]


def test_live_thread_stats_for_own_process() -> None:
    import os
    import threading

    stop = threading.Event()
    worker = threading.Thread(target=stop.wait)
    worker.start()
    try:
        rec = Recorder(interval_s=0.01, pid=os.getpid()).run(max_samples=2)
    finally:
        stop.set()
        worker.join()
    tasks = [p for p in rec.samples[0].files if p.startswith(f"/proc/{os.getpid()}/task/")]
    assert len(tasks) >= 2


def test_ntp_step_does_not_corrupt_rates(tmp_path) -> None:
    """A5: wall clock steps back an hour mid-recording; rates must use the monotonic clock."""
    from kptk.analysis.window import Window

    ctxt = iter(range(0, 10_000, 1_000))
    # meta's "started", then 4 samples; wall time steps back 3600 s after sample 2
    wall = iter([1_700_000_000.0, 1_700_000_000.0, 1_700_000_001.0, 1_699_996_402.0, 1_699_996_403.0])
    mono = iter([50.0, 51.0, 52.0, 53.0])

    class Source(DictSource):
        def read(self, path: str) -> str | None:
            return f"cpu 1 0 0 1 0 0 0 0 0 0\nctxt {next(ctxt)}\n" if path == "/proc/stat" else None

    rec = Recorder(Source({"/proc/stat": ""}), interval_s=0.001, clock=lambda: next(wall), monotonic=lambda: next(mono)).run(max_samples=4)
    w = Window(rec)
    assert w.dt == 3.0 and rec.duration_s == 3.0
    assert [s.ts for s in rec.samples][2] < rec.samples[0].ts  # the step really happened in wall time
    path = tmp_path / "r.jsonl"
    rec.save(path)
    back = Recording.load(path)
    assert [s.mono for s in back.samples] == [50.0, 51.0, 52.0, 53.0] and Window(back).dt == 3.0


def test_old_recordings_without_mono_fall_back_to_wall_time(tmp_path) -> None:
    import json

    path = tmp_path / "old.jsonl"
    rows = [{"type": "meta", "static": {}}] + [{"type": "sample", "ts": 100.0 + i, "files": {}} for i in range(3)]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    rec = Recording.load(path)
    assert rec.samples[0].mono is None and rec.duration_s == 2.0
