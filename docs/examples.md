# Workflows

## 1. "Is this host healthy?" (30 seconds, unprivileged)

```bash
kptk inspect                       # topology, caches, THP/hugetlb, risky sysctls
kptk record -d 30 -i 1 -o host.jsonl.gz
kptk analyze host.jsonl.gz
```

## 2. Profile one command end to end

```bash
kptk profile -i 0.25 -o run.jsonl.gz -- ./my_service --bench
```

Hardware counters attach at `exec`, so the profiler itself isn't counted. If
`perf_event_paranoid` blocks them, the report says so and still covers scheduling,
memory, NUMA and huge pages from /proc.

No counter access, but someone can run `perf stat` for you? Pass the output in:

```bash
perf stat -x';' -e cycles,instructions,cache-references,cache-misses,dTLB-loads,dTLB-load-misses -- ./my_service 2> perf.txt
kptk analyze run.jsonl.gz --perf-stat perf.txt
```

## 3. Watch a running service

```bash
kptk record -p $(pidof postgres | cut -d' ' -f1) -d 120 -i 1 -o pg.jsonl.gz
kptk analyze pg.jsonl.gz --md pg.md --json pg.json
```

## 4. Prove a tuning change (A/B with significance)

```bash
for i in 1 2 3 4 5; do kptk profile -i 0.25 --json before_$i.json --md /dev/null -- ./bench; done
# apply change: numactl binding, THP mode, thread-pool size, ...
for i in 1 2 3 4 5; do kptk profile -i 0.25 --json after_$i.json  --md /dev/null -- ./bench; done
kptk compare --before before_*.json --after after_*.json --fail-on-regression
```

Each metric gets a two-sided permutation test. "regressed" requires both p < 0.05 and a
change of at least 5% (`--min-change`).

## 5. CI performance gate

```bash
kptk profile --fail-on warning --md perf-report.md -- ./integration_bench
# exit status 3 if any warning/critical finding: fail the pipeline, attach perf-report.md
```

## 6. Case study (synthetic): the misplaced database

`kptk demo numa_misplaced` (report: [`samples/numa_misplaced_report.md`](../samples/numa_misplaced_report.md)):

1. **Critical NUMA misplacement**: threads ran ~100% on node 0, while 85% of the heap sits on
   node 1 (placement mismatch 0.85, expected SLIT distance 19.4 vs 10 local).
2. That explains **memory-bound** counters: IPC 0.60 and LLC MPKI 17.9.
3. Node 1 is **nearly full**, so new allocations go remote too (35% remote allocations; AutoNUMA
   sees only 25% local accesses).
4. **THP always/always** with a 44% fallback rate and 12 compaction stalls/s: the heap is only
   4% huge-page backed, while dTLB misses are 3%.
5. And 4 GiB of **hugetlb pool** is reserved but unused: memory that could have relieved node 1.

The fix order follows from the evidence: bind the database to node 1 (or migrate its pages
to node 0), switch THP defrag to `defer+madvise`, then either give the 4 GiB hugetlb pool to
the DB (as huge-page shared buffers) or release it.

## 7. Reproducing phenomena with the microbenchmarks

```bash
make bench-build
./build/mem_latency > lat_4k.csv; ./build/mem_latency --huge > lat_2m.csv    # cache + TLB cliffs
numactl --cpunodebind=0 --membind=1 ./build/mem_latency > lat_remote.csv     # NUMA penalty
./build/page_fault_cost 1024                                                 # fault cost by mode
./build/wakeup_latency 200000 0 1; ./build/wakeup_latency 200000 0 <other-socket-cpu>
./build/false_sharing 8
```

These are deliberately heavy. Run them on an otherwise idle machine, and never in CI.
