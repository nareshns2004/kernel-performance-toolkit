"""Markdown rendering of an analysis report."""

from __future__ import annotations

from typing import Any

BLOCKS = "▁▂▃▄▅▆▇█"
ICON = {"critical": "🔴", "warning": "🟠", "info": "🔵"}


def sparkline(values: list[float | None], width: int = 60) -> str:
    vals = [v for v in values if v is not None]
    if not vals:
        return ""
    step = max(1, len(values) // width)
    binned: list[float | None] = []
    for i in range(0, len(values), step):
        chunk = [v for v in values[i : i + step] if v is not None]
        binned.append(max(chunk) if chunk else None)
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    return "".join(" " if v is None else BLOCKS[min(7, int((v - lo) / span * 8))] for v in binned)


def _fmt(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        if abs(v) >= 1000:
            return f"{v:,.0f}"
        return f"{v:.3g}"
    if isinstance(v, dict):
        return ", ".join(f"{k}: {_fmt(x)}" for k, x in v.items() if x is not None) or "-"
    return str(v)


def render(report: dict[str, Any]) -> str:
    meta, topo, dq = report["meta"], report["topology"], report["data_quality"]
    caches = ", ".join(f"L{c['level']}{c['type'][0] if c['type'] != 'Unified' else ''} {c['size_kib']} KiB" for c in topo["caches"])
    out = [
        "# Kernel performance report",
        "",
        f"**Host** {meta.get('host')} · kernel {meta.get('kernel')} · {topo['cpus']} CPUs ({topo['physical_cores']} cores, "
        f"{topo['packages']} socket(s), SMT {'on' if topo['smt'] else 'off'}) · {topo['numa_nodes']} NUMA node(s) · {caches}",
        "",
        f"**Window** {dq['duration_s']} s, {dq['samples']} samples, recorder cost {dq['mean_sample_cost_ms']} ms/sample"
        + (f" · **target** pid {meta['pid']} ({report['metrics']['cpu'].get('process_comm', '?')})" if meta.get("pid") else "")
        + (f" · `{' '.join(meta['command'])}`" if meta.get("command") else ""),
        "",
    ]
    findings = report["findings"]
    out += ["## Findings", ""]
    if not any(f["severity"] != "info" for f in findings):
        out += ["No warnings: nothing crossed a threshold in this window.", ""]
    for f in findings:
        out.append(f"### {ICON[f['severity']]} {f['title']}")
        out.append("")
        if f["evidence"]:
            out.append("Evidence: " + ", ".join(f"`{k}={{{_fmt(v)}}}`" if isinstance(v, dict) else f"`{k}={_fmt(v)}`" for k, v in f["evidence"].items()))
            out.append("")
        if f.get("why"):
            out.append(f"_Why it matters:_ {f['why']}")
            out.append("")
        out.append(f"**Action:** {f['recommendation']}")
        out.append("")

    out += ["## USE summary", "", "| resource | utilisation | saturation | errors |", "|---|---:|---|---|"]
    for row in report["use"]:
        out.append(f"| {row['resource']} | {_fmt(row['utilization'])} | {_fmt(row['saturation'])} | {_fmt(row['errors'])} |")
    out.append("")

    ts = report.get("timeseries", {})
    if ts.get("t"):
        out += ["## Over time", "", "```"]
        for key, label in (
            ("cpu_util", "cpu util     "),
            ("runq_waiting", "runq waiting "),
            ("faults_per_s", "faults/s     "),
            ("major_faults_per_s", "major flt/s  "),
            ("psi_mem_some", "mem pressure "),
        ):
            vals = ts.get(key, [])
            if any(v is not None for v in vals):
                present = [v for v in vals if v is not None]
                out.append(f"{label} {sparkline(vals)}  max {_fmt(max(present))}")
        out += ["```", ""]

    out += ["## Metrics", ""]
    for domain, metrics in report["metrics"].items():
        rows = [(k, v) for k, v in metrics.items() if not isinstance(v, (dict, list)) and v is not None]
        if not rows:
            continue
        out += [f"<details><summary>{domain}</summary>", "", "| metric | value |", "|---|---:|"]
        out += [f"| {k} | {_fmt(v)} |" for k, v in rows]
        out += ["", "</details>", ""]
    if dq["missing"]:
        out += ["## Data quality", ""] + [f"- {x}" for x in dq["missing"]] + [""]
    return "\n".join(out)
