import json
import sys

from kptk.cli import main
from kptk.synth import scenario


def test_demo_and_list(capsys) -> None:
    assert main(["demo", "--list"]) == 0
    assert "numa_misplaced" in capsys.readouterr().out
    assert main(["demo", "cpu_saturated"]) == 0
    assert "CPU saturation" in capsys.readouterr().out


def test_analyze_recording_with_gate(tmp_path, capsys) -> None:
    path = tmp_path / "rec.jsonl"
    scenario("memory_pressure").save(path)
    out = tmp_path / "r.json"
    assert main(["analyze", str(path), "--json", str(out), "--md", str(tmp_path / "r.md"), "--fail-on", "critical"]) == 3
    assert json.loads(out.read_text())["findings"][0]["severity"] == "critical"
    assert main(["analyze", str(path), "--md", str(tmp_path / "r2.md")]) == 0


def test_analyze_with_perf_stat_file(tmp_path, capsys) -> None:
    path = tmp_path / "rec.jsonl"
    scenario("healthy", counters=None).save(path)
    perf = tmp_path / "perf.txt"
    perf.write_text("1000;;cycles;1;100.00;;\n400;;instructions;1;100.00;;\n100;;cache-references;1;100.00;;\n30;;cache-misses;1;100.00;;\n")
    assert main(["analyze", str(path), "--perf-stat", str(perf)]) == 0
    assert "memory-bound" in capsys.readouterr().out


def test_inspect_json(capsys) -> None:
    assert main(["inspect", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["topology"]["cpus"] >= 1


def test_parse_legacy_samples(tmp_path) -> None:
    out = tmp_path / "o.json"
    assert (
        main(
            [
                "parse",
                "--perf-file",
                "samples/perf_output.txt",
                "--vmstat-file",
                "samples/vmstat.txt",
                "--numastat-file",
                "samples/numastat.txt",
                "-o",
                str(out),
            ]
        )
        == 0
    )
    doc = json.loads(out.read_text())
    assert doc["cache"]["ipc"] == 2.5
    assert doc["policy_miss_frac"] > 0


def test_compare_cli(tmp_path, capsys) -> None:
    from kptk.analysis.engine import analyze

    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(analyze(scenario("healthy")), default=str))
    b.write_text(json.dumps(analyze(scenario("cpu_saturated")), default=str))
    assert main(["compare", "--before", str(a), "--after", str(b), "--fail-on-regression"]) == 3
    assert "n=1, untested" in capsys.readouterr().out


def test_profile_short_command(tmp_path, capsys) -> None:
    rec = tmp_path / "p.jsonl"
    rc = main(["profile", "-i", "0.05", "-o", str(rec), "--md", str(tmp_path / "p.md"), "--", sys.executable, "-c", "import time; time.sleep(0.3)"])
    assert rc == 0
    assert (tmp_path / "p.md").read_text().startswith("# Kernel performance report")
