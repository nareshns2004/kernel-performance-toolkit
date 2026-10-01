"""``kptk`` command-line interface (argparse, standard library only)."""

from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path
from typing import Any

from . import __version__


def _write_outputs(report: dict[str, Any], args: argparse.Namespace) -> None:
    from .report import render

    if getattr(args, "json", None):
        Path(args.json).write_text(json.dumps(report, indent=2, default=str))
    text = render(report)
    if getattr(args, "md", None):
        Path(args.md).write_text(text)
        print(f"report written to {args.md}", file=sys.stderr)
    else:
        print(text)


def _gate(report: dict[str, Any], fail_on: str | None) -> int:
    """Exit status for CI: 3 if any finding is at or above ``fail_on``."""
    if not fail_on:
        return 0
    order = {"info": 0, "warning": 1, "critical": 2}
    worst = max((order[f["severity"]] for f in report["findings"]), default=-1)
    return 3 if worst >= order[fail_on] else 0


def cmd_inspect(args: argparse.Namespace) -> int:
    from .analysis.engine import audit_config
    from .analysis.hugepages import hugetlb_pools
    from .parsers.sysfs import parse_bracket_choice
    from .record import Recorder
    from .topology import from_static

    static = Recorder().capture_static()
    topo = from_static(static)
    audit = audit_config(static, topo)
    thp = "/sys/kernel/mm/transparent_hugepage/"
    doc: dict[str, Any] = {
        "topology": topo.summary(),
        "config": audit.metrics,
        "thp": {"enabled": parse_bracket_choice(static.get(thp + "enabled", "")), "defrag": parse_bracket_choice(static.get(thp + "defrag", ""))},
        "hugetlb_pools": hugetlb_pools(static),
        "findings": [f.to_dict() for f in audit.findings],
    }
    if args.json:
        print(json.dumps(doc, indent=2, default=str))
        return 0
    t = doc["topology"]
    print(f"CPUs {t['cpus']} | cores {t['physical_cores']} | sockets {t['packages']} | SMT {'on' if t['smt'] else 'off'} | NUMA nodes {t['numa_nodes']}")
    for node, cpus in t["node_cpus"].items():
        print(f"  node{node}: cpus {cpus}  distances {t['distances'].get(node, '-')}")
    for c in t["caches"]:
        print(f"  L{c['level']} {c['type']:<12} {c['size_kib']:>7} KiB  x{c['instances']}  (shared by {c['shared_by_cpus']} CPUs)")
    print(f"THP enabled={doc['thp']['enabled']} defrag={doc['thp']['defrag']} | hugetlb pools {doc['hugetlb_pools'] or 'none'}")
    print(f"governors {t['governors'] or '-'} | " + " | ".join(f"{k}={v}" for k, v in doc["config"].items() if k != "governors"))
    for f in doc["findings"]:
        print(f"[{f['severity']}] {f['title']}\n    -> {f['recommendation']}")
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    from .record import Recorder

    rec_ = Recorder(interval_s=args.interval, pid=args.pid, slow_every=args.slow_every)
    signal.signal(signal.SIGTERM, lambda *_: rec_.stop_event.set())
    print(f"recording every {args.interval}s for {args.duration}s" + (f" (pid {args.pid})" if args.pid else "") + f" -> {args.output}", file=sys.stderr)
    try:
        rec = rec_.run(duration_s=args.duration)
    except KeyboardInterrupt:
        return 130
    rec.save(args.output)
    print(f"{len(rec.samples)} samples, mean cost {1e3 * sum(s.cost_s for s in rec.samples) / len(rec.samples):.2f} ms/sample", file=sys.stderr)
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    from .analysis.engine import analyze, events_from_perf_stat
    from .analysis.findings import Thresholds
    from .parsers.tools import parse_perf_stat
    from .record import Recording

    rec = Recording.load(args.recording)
    events = events_from_perf_stat(parse_perf_stat(Path(args.perf_stat).read_text())) if args.perf_stat else None
    report = analyze(rec, events, Thresholds.load(args.thresholds))
    _write_outputs(report, args)
    return _gate(report, args.fail_on)


def cmd_profile(args: argparse.Namespace) -> int:
    from .analysis.engine import analyze
    from .analysis.findings import Thresholds
    from .profile import profile

    cmd = args.command[1:] if args.command and args.command[0] == "--" else args.command
    if not cmd:
        print("usage: kptk profile [options] -- <command> [args...]", file=sys.stderr)
        return 2
    rec, rc = profile(cmd, args.interval, args.events.split(",") if args.events else None)
    if args.output:
        rec.save(args.output)
    if rec.counters and rec.counters.get("error"):
        print(f"note: hardware counters unavailable: {rec.counters['error']}", file=sys.stderr)
    if len(rec.samples) < 2:
        print("command finished before two samples were taken; lower --interval", file=sys.stderr)
        return rc or 1
    report = analyze(rec, None, Thresholds.load(args.thresholds))
    _write_outputs(report, args)
    return _gate(report, args.fail_on) or rc


def cmd_parse(args: argparse.Namespace) -> int:
    """v0.1 workflow: analyse saved perf stat / vmstat / numastat text."""
    from .analysis.cache import analyze_cache
    from .analysis.engine import events_from_perf_stat
    from .analysis.findings import Thresholds
    from .parsers.tools import parse_numastat_output, parse_perf_stat, parse_vmstat_output
    from .topology import Topology

    perf = parse_perf_stat(Path(args.perf_file).read_text())
    events = events_from_perf_stat(perf)
    cache = analyze_cache(events, Topology(), Thresholds(), None)
    doc: dict[str, Any] = {"perf": {k: v.value for k, v in perf.items()}, "cache": cache.metrics, "findings": [f.to_dict() for f in cache.findings]}
    sw = {k: perf[k].value for k in ("task-clock", "context-switches", "cpu-migrations", "minor-faults", "major-faults", "page-faults") if k in perf}
    if sw:
        doc["software"] = sw
    if args.vmstat_file:
        doc["vmstat"] = parse_vmstat_output(Path(args.vmstat_file).read_text())
    if args.numastat_file:
        ns = parse_numastat_output(Path(args.numastat_file).read_text())
        doc["numastat"] = ns
        local, other = ns.get("local_node", {}).get("total"), ns.get("other_node", {}).get("total")
        if local is not None and other is not None and local + other:
            doc["remote_alloc_frac"] = other / (local + other)
        hit, miss = ns.get("numa_hit", {}).get("total"), ns.get("numa_miss", {}).get("total")
        if hit is not None and miss is not None and hit + miss:
            doc["policy_miss_frac"] = miss / (hit + miss)
    text = json.dumps(doc, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(text + "\n")
    else:
        print(text)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    from .compare import compare, to_markdown

    before = [json.loads(Path(p).read_text()) for p in args.before]
    after = [json.loads(Path(p).read_text()) for p in args.after]
    changes = compare(before, after, args.min_change, all_metrics=args.all)
    print(to_markdown(changes, len(before), len(after)))
    return 3 if args.fail_on_regression and any(c.verdict.startswith("regressed") for c in changes) else 0


def cmd_demo(args: argparse.Namespace) -> int:
    from .analysis.engine import analyze
    from .synth import SCENARIOS, scenario

    if args.list:
        for name, (desc, _) in SCENARIOS.items():
            print(f"{name:<16} {desc}")
        return 0
    rec = scenario(args.scenario)
    if args.save:
        rec.save(args.save)
    report = analyze(rec)
    _write_outputs(report, args)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kptk", description="Linux kernel performance toolkit: scheduling, memory, NUMA, cache, page faults, huge pages.")
    p.add_argument("--version", action="version", version=f"kptk {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def outputs(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--json", help="write the full report as JSON")
        sp.add_argument("--md", help="write the Markdown report here (default: stdout)")
        sp.add_argument("--thresholds", help="JSON file overriding analysis thresholds")
        sp.add_argument("--fail-on", choices=["info", "warning", "critical"], help="exit 3 if a finding at this severity or above exists (for CI)")

    sp = sub.add_parser("inspect", help="topology, cache hierarchy, THP/hugetlb and kernel config audit")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_inspect)

    sp = sub.add_parser("record", help="record raw /proc + /sys state to a JSONL file")
    sp.add_argument("-d", "--duration", type=float, default=10.0)
    sp.add_argument("-i", "--interval", type=float, default=1.0)
    sp.add_argument("-p", "--pid", type=int, help="also record this process (stat, status, schedstat, numa_maps, smaps_rollup)")
    sp.add_argument("--slow-every", type=int, default=5, help="read page-table-walking files every N samples")
    sp.add_argument("-o", "--output", default="recording.jsonl.gz")
    sp.set_defaults(func=cmd_record)

    sp = sub.add_parser("analyze", help="analyse a recording")
    sp.add_argument("recording")
    sp.add_argument("--perf-stat", help="saved `perf stat -x;` output to use as hardware counters")
    outputs(sp)
    sp.set_defaults(func=cmd_analyze)

    sp = sub.add_parser("profile", help="run a command under counters + recording, then analyse")
    sp.add_argument("-i", "--interval", type=float, default=0.5)
    sp.add_argument("-e", "--events", help="comma-separated perf events (default: a cache/TLB/branch set)")
    sp.add_argument("-o", "--output", help="also save the recording")
    outputs(sp)
    sp.add_argument("command", nargs=argparse.REMAINDER)
    sp.set_defaults(func=cmd_profile)

    sp = sub.add_parser("parse", help="analyse saved perf stat / vmstat / numastat text (v0.1 workflow)")
    sp.add_argument("--perf-file", required=True)
    sp.add_argument("--vmstat-file")
    sp.add_argument("--numastat-file")
    sp.add_argument("-o", "--output")
    sp.set_defaults(func=cmd_parse)

    sp = sub.add_parser("demo", help="analyse a synthetic two-socket server scenario (no privileges needed)")
    sp.add_argument("scenario", nargs="?", default="numa_misplaced")
    sp.add_argument("--list", action="store_true")
    sp.add_argument("--save", help="save the synthetic recording")
    sp.add_argument("--json")
    sp.add_argument("--md")
    sp.set_defaults(func=cmd_demo)

    sp = sub.add_parser("compare", help="A/B compare reports with a permutation test")
    sp.add_argument("--before", nargs="+", required=True)
    sp.add_argument("--after", nargs="+", required=True)
    sp.add_argument("--min-change", type=float, default=0.05)
    sp.add_argument("--all", action="store_true", help="compare every numeric metric, not just the key ones")
    sp.add_argument("--fail-on-regression", action="store_true")
    sp.set_defaults(func=cmd_compare)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
