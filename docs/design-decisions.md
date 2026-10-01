# Design decisions

## ADR-1: Capture raw text, parse later
**Decision.** The recorder copies /proc and /sys files verbatim; all parsing happens at analysis time.
**Why.** It keeps per-sample overhead minimal, makes recordings portable (capture on prod,
analyse on a laptop), lets old recordings benefit from parser fixes, and turns analysis into
a pure, testable function. **Cost**: recordings are larger than parsed numbers (~1 KB/s gzipped
for system-wide sampling), which is acceptable for minutes-long captures.

## ADR-2: Zero runtime dependencies
**Why.** The tool runs on production hosts where installing packages is a change-management
event. Python 3.10+ stdlib covers file I/O, ctypes (for `perf_event_open`), JSON, gzip and
statistics. YAML configs from v0.1 became JSON for the same reason.

## ADR-3: `perf_event_open` via ctypes instead of shelling out to `perf`
**Why.** `perf` isn't installed on many hosts, its version must match the kernel, and parsing
its output is brittle. The syscall ABI is stable: the 112-byte `PERF_ATTR_SIZE_VER5` struct is
accepted by every kernel since 4.1. **Trade-off**: no sampling, call graphs or symbolisation.
This is a counting tool. Sampling profilers (perf record, eBPF) complement it.

## ADR-4: Attach counters before exec (`enable_on_exec`), not after spawn
**Why.** Attaching after the child starts misses its startup and races with short commands.
The fork → block on pipe → attach → release → exec handshake (the one `perf stat` uses)
counts exactly the target from its first instruction. `subprocess.Popen` can't express it,
because it waits for exec to complete before returning, which deadlocks the handshake.
Hence raw `os.fork` / `os.execvp`.

## ADR-5: Ungrouped counters with `inherit` for commands
**Why.** Counting all threads and children needs `inherit=1`, and the kernel doesn't support
`PERF_FORMAT_GROUP` reads of inherited events. Each counter is therefore read separately and
scaled by its own `time_enabled/time_running`. The report flags multiplexing so that
cross-counter ratios (IPC, miss rates) are read as estimates when it occurred.

## ADR-6: Saturation over utilisation
**Why.** Utilisation is the most-reported and least-diagnostic metric. Findings trigger on
saturation signals (run-queue delay, PSI, direct reclaim, compaction stalls) that directly
represent waiting. Utilisation is kept as context in the USE table.

## ADR-7: Correct NUMA semantics, and mismatch instead of "remote %"
**Why.** `numa_miss` is a policy counter, not a locality counter. Locality comes from
`local_node`/`other_node` (allocation) and AutoNUMA hinting faults (access). For a process, a
naive "remote %" under uniform access flags any process spread across nodes at ~50%, even a
perfectly interleaved one. The finding triggers on the **mismatch** (total variation distance)
between where threads ran and where memory lives. A unit test pins that distinction.

## ADR-8: Rate-limit page-table-walking reads
**Why.** `numa_maps` and `smaps_rollup` walk the target's page tables while holding its mmap
lock. For a 100 GB process that takes milliseconds, and it contends with the target's own page
faults. They're read every N samples (`--slow-every`), and the latest value is used.

## ADR-9: Synthetic recordings as the test oracle
**Why.** Most findings need hardware a CI runner doesn't have (two sockets, a PMU, memory
pressure). `synth.py` generates internally consistent /proc text for a two-socket server
under named scenarios. Tests assert that the *exact* set of findings fires, and that metrics
recover the injected ground truth. Real recordings in `samples/` keep the parsers honest
against live kernel output.

## ADR-10: Noise-aware comparison
**Why.** Performance changes of a few percent are often within run-to-run noise. `compare`
uses a permutation test (exact for small n, distribution-free, stdlib-only) and requires both
significance and a minimum effect size. With n=1 per side it explicitly refuses to call a
result significant.
