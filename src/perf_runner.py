#!/usr/bin/env python3
"""Collect or parse Linux performance data and emit a JSON report."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from parser import parse_numastat, parse_perf_stat, parse_vmstat
from report import build_report, markdown_summary, write_json
from utils import run

DEFAULT_EVENTS = "cycles,instructions,cache-references,cache-misses,context-switches,cpu-migrations,page-faults,minor-faults,major-faults,task-clock"


def collect(command: list[str], events: str) -> tuple[dict[str, float], dict[str, float], dict[str, dict[str, float]]]:
    perf_output = run(["perf", "stat", "-x", ";", "-e", events, "--", *command])
    return parse_perf_stat(perf_output), parse_vmstat(run(["vmstat", "1", "2"])), parse_numastat(run(["numastat"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--perf-file", type=Path, help="parse saved perf stat output")
    source.add_argument("--command", nargs=argparse.REMAINDER, help="run a command under perf stat; use after --command")
    parser.add_argument("--vmstat-file", type=Path, help="optional saved vmstat output")
    parser.add_argument("--numastat-file", type=Path, help="optional saved numastat output")
    parser.add_argument("--events", default=DEFAULT_EVENTS, help="comma-separated perf events")
    parser.add_argument("--output", type=Path, default=Path("report.json"), help="JSON output path")
    parser.add_argument("--markdown", type=Path, help="optional Markdown summary path")
    args = parser.parse_args(argv)

    try:
        if args.perf_file:
            perf = parse_perf_stat(args.perf_file.read_text(encoding="utf-8"))
            vmstat = parse_vmstat(args.vmstat_file.read_text(encoding="utf-8")) if args.vmstat_file else {}
            numa = parse_numastat(args.numastat_file.read_text(encoding="utf-8")) if args.numastat_file else {}
        else:
            if not args.command:
                parser.error("--command requires a command, for example: --command sleep 1")
            perf, vmstat, numa = collect(args.command, args.events)
        report = build_report(perf, vmstat, numa)
        write_json(report, args.output)
        if args.markdown:
            args.markdown.write_text(markdown_summary(report), encoding="utf-8")
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
