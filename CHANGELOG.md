# Changelog

## 0.2.0: analysis toolkit

Rebuilt from the 0.1 text-to-JSON scaffold into a packaged toolkit (`kptk`).

### Added
- **Recorder** that captures raw /proc and /sys text into portable JSONL recordings, with
  deadline scheduling, per-sample cost tracking, and rate-limited page-table-walking reads.
- **perf_event_open via ctypes**: hardware/software/cache events, multiplexing correction,
  and the fork → attach → exec (`enable_on_exec`) handshake for `kptk profile`.
- **Analyzers** for CPU scheduling (schedstat run-queue delay, PSI, voluntary/involuntary
  switches, steal, imbalance), memory (faults, direct reclaim, swap, PSI), NUMA (allocation vs
  access locality, numa_maps placement vs thread residency, SLIT distance, node exhaustion),
  cache/TLB (IPC, MPKI, multiplexing), huge pages (THP mode, fallback, compaction, coverage,
  idle hugetlb), and a config audit.
- Findings engine with severities, evidence and recommendations; USE summary; sparklines;
  data-quality report; tunable thresholds (JSON); CI gate (`--fail-on`).
- `kptk compare`: multi-run A/B with a permutation test.
- Topology reconstruction (SMT, sockets, NUMA nodes, cache hierarchy).
- Synthetic two-socket recordings (`kptk demo`) and a test suite asserting exact findings per scenario.
- C microbenchmarks: pointer-chase latency (4K vs THP, NUMA), page-fault cost, wakeup latency, false sharing.
- Docs: architecture, metric reference, kernel mechanics, workflows, design decisions, interview guide. CI workflow.

### Changed
- NUMA "remote" no longer uses `numa_miss` (a policy counter); locality uses
  `local_node`/`other_node` and hinting faults.
- "Scheduler latency" now measures run-queue delay rather than context-switch counts.
- `perf stat` parsing keeps the multiplexing percentage. The v0.1 workflow is now `kptk parse`.
- Code is an installable package (`src/kptk`) instead of flat scripts with `sys.path` edits;
  `parser.py` no longer shadows a standard-library module name.
- Configs are JSON (stdlib-only). LICENSE was empty and is now Apache-2.0.

### Removed
- Empty placeholder images and docs; trivial C programs replaced by the microbenchmarks above.

## 0.1.0
- Initial scaffold.
