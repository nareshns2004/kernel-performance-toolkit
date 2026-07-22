# Kernel Performance Toolkit

A small, dependency-free toolkit for turning Linux `perf stat`, `vmstat`, and
`numastat` output into a single JSON report. It works in two modes:

- **Offline analysis** parses saved command output. This is portable and needs
  no special permissions.
- **Live collection** runs a command under `perf stat` and captures a `vmstat`
  and `numastat` snapshot when those tools are available.

## Quick start

Generate a report from the bundled fixtures:

```bash
make sample-report
python3 -m json.tool samples/report.json
```

Run the test suite:

```bash
python3 -m pytest -q
```

Build the small workload fixtures (a C compiler is required):

```bash
make benchmarks
```

## Usage

Parse existing outputs:

```bash
python3 src/perf_runner.py \
  --perf-file samples/perf_output.txt \
  --vmstat-file samples/vmstat.txt \
  --numastat-file samples/numastat.txt \
  --output report.json --markdown report.md
```

Collect metrics while a program runs:

```bash
python3 src/perf_runner.py --command ./my-workload --size 1024
```

For live collection, install the Linux `perf` tools and `numastat` (usually
provided by the `numactl` package). Some systems restrict performance counters;
adjust `kernel.perf_event_paranoid` or use an administrator-approved setup when
appropriate. No `sudo` is used by the toolkit itself.

## Report contents

The JSON report preserves the raw perf, vmstat, and NUMA inputs and derives:

- cache references, misses, miss rate, and instructions per miss;
- minor/major fault totals and major-fault percentage;
- context switches, CPU migrations, and switches per task-clock millisecond;
- local versus remote NUMA allocation percentage.

Missing or unsupported counters are represented as zero-derived metrics, so a
report can still be compared across hosts with different capabilities.

## Layout

- `src/perf_runner.py` — command-line entry point.
- `src/parser.py` — parsers for human-readable and CSV perf output.
- `src/report.py` — report construction plus JSON/Markdown output.
- `samples/` — known-good offline inputs and a generated example report.
- `scripts/` — convenience collection and huge-page setup scripts.

`scripts/setup_hugepages.sh` changes a kernel setting and intentionally requires
root. Run it only when huge pages are part of your workload configuration.
