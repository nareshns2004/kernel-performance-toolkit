# Kernel performance report

**Host** notebook · kernel 6.8.0-138-generic · 8 CPUs (4 cores, 1 socket(s), SMT on) · 1 NUMA node(s) · L1D 48 KiB, L1I 32 KiB, L2 1280 KiB, L3 8192 KiB

**Window** 1.201 s, 8 samples, recorder cost 1.379 ms/sample · **target** pid 2579584 (python3) · `python3 -c import time; x=[bytearray(1<<20) for _ in range(150)]; time.sleep(1)`

## Findings

No warnings: nothing crossed a threshold in this window.

### 🔵 No hardware counters in this recording; cache and TLB efficiency can't be measured

**Action:** Use `kptk profile -- <cmd>` with perf_event_paranoid <= 2 (or CAP_PERFMON), or pass `--perf-stat` with saved `perf stat -x;` output.

### 🔵 CPU frequency governor 'powersave' is active

Evidence: `governors=['powersave']`

**Action:** On intel_pstate/amd-pstate 'powersave' is dynamic and often fine; for latency-critical services compare against 'performance' and check C-state exit latency.

### 🔵 kernel.perf_event_paranoid=4: unprivileged hardware counters are disabled

Evidence: `perf_event_paranoid=4`

**Action:** For profiling sessions grant CAP_PERFMON to the profiler, or temporarily set perf_event_paranoid=2 (own processes, user+kernel).

### 🔵 Single NUMA node: locality analysis doesn't apply

Evidence: `nodes=1`

**Action:** On multi-socket or sub-NUMA-clustered servers, record there to get locality findings.

## USE summary

| resource | utilisation | saturation | errors |
|---|---:|---|---|
| CPU | 0.0833 | psi_some: 0.00788, runq_waiting_tasks: 0.0478 | steal_frac: 0 |
| Memory capacity | 0.853 | psi_some: 0, direct_reclaim_per_s: 0, swap_pages_per_s: 0 | oom_kills: 0 |
| NUMA interconnect | - | - | - |
| Huge pages | 0 | compact_stall_per_s: 0 | - |

## Over time

```
cpu util      ▆▃▄▄▄█▁  max 0.155
runq waiting  ▇▁▁▁▂█▁  max 0.13
faults/s      █▁▁▁▁▁▁  max 200,929
major flt/s   ▁▁▁▁▁▁▁  max 0
mem pressure  ▁▁▁▁▁▁▁  max 0
```

## Metrics

<details><summary>cpu</summary>

| metric | value |
|---|---:|
| utilization | 0.0833 |
| user_frac | 0.0556 |
| system_frac | 0.0267 |
| iowait_frac | 0.00321 |
| steal_frac | 0 |
| softirq_frac | 0.00107 |
| irq_frac | 0 |
| context_switches_per_s | 11,538 |
| procs_running_mean | 1.25 |
| procs_running_per_cpu | 0.156 |
| per_cpu_spread | 0.093 |
| runq_waiting_tasks_avg | 0.0478 |
| runq_wait_per_timeslice_ms | 0.00806 |
| psi_cpu_some | 0.00788 |
| process_comm | python3 |
| process_threads | 1 |
| process_cpus_used | 0.0666 |
| process_system_frac | 0.75 |
| process_runq_wait_share | 0.00171 |
| process_runq_wait_per_slice_ms | 0.0184 |
| process_voluntary_switches_per_s | 1.67 |
| process_involuntary_switches_per_s | 5 |
| process_cpus_allowed | 0-7 |

</details>

<details><summary>memory</summary>

| metric | value |
|---|---:|
| faults_per_s | 43,415 |
| major_faults_per_s | 0 |
| swap_pages_per_s | 0 |
| direct_reclaim_stalls_per_s | 0 |
| pgscan_direct_per_s | 0 |
| pgscan_kswapd_per_s | 0 |
| oom_kills | 0 |
| workingset_refault_per_s | 35 |
| mem_total_kib | 16110848 |
| mem_available_min_frac | 0.147 |
| psi_memory_some | 0 |
| psi_memory_full | 0 |
| process_minor_faults_per_s | 32,501 |
| process_major_faults_per_s | 0 |
| process_rss_kib | 0 |
| process_anon_kib | 158500 |
| process_swap_kib | 0 |

</details>

<details><summary>numa</summary>

| metric | value |
|---|---:|
| nodes | 1 |
| applicable | False |

</details>

<details><summary>cache</summary>

| metric | value |
|---|---:|
| available | False |

</details>

<details><summary>hugepages</summary>

| metric | value |
|---|---:|
| thp_enabled | madvise |
| thp_defrag | madvise |
| khugepaged_max_ptes_none | 511 |
| thp_fault_alloc_per_s | 0 |
| thp_collapse_per_s | 0 |
| thp_split_per_s | 0 |
| compact_stall_per_s | 0 |
| anon_huge_kib_system | 0 |
| process_anon_kib | 158500 |
| process_thp_coverage | 0 |
| process_hugetlb_kib | 0 |

</details>

<details><summary>config</summary>

| metric | value |
|---|---:|
| swappiness | 60 |
| zone_reclaim_mode | 0 |
| numa_balancing | 0 |
| perf_event_paranoid | 4 |

</details>
