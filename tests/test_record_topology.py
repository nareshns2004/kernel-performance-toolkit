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
