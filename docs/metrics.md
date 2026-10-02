# Metric reference

Each metric: where it comes from, how it's computed over the window `[first, last]` sample,
and how to read it. `Δx` is the change in a cumulative counter, `Δt` the elapsed time.

## CPU and scheduler

| Metric | Source | Formula | Reading |
|---|---|---|---|
| `utilization` | `/proc/stat` cpu line | Δbusy / Δtotal (busy excludes idle, iowait, and guest, which is already in user) | How busy, not how *contended* |
| `steal_frac` | `/proc/stat` | Δsteal / Δtotal | vCPU runnable but the hypervisor ran someone else |
| `iowait_frac` | `/proc/stat` | Δiowait / Δtotal | Idle with I/O outstanding. It's idle time, not CPU work |
| `runq_waiting_tasks_avg` | `/proc/schedstat` field 8 (`run_delay`) | ΣΔrun_delay_ns / 1e9 / Δt | Average number of tasks runnable but waiting: direct **saturation** |
| `runq_wait_per_timeslice_ms` | `/proc/schedstat` fields 8, 9 | ΣΔrun_delay / ΣΔpcount | Mean scheduler latency per time slice |
| `psi_cpu_some` | `/proc/pressure/cpu` `some total=` | Δtotal_us / 1e6 / Δt | Share of wall time at least one task waited for CPU |
| `process_runq_wait_share` | `/proc/<pid>/schedstat` | Δwait / (Δwait + Δon_cpu) | Share of the process's runnable time spent waiting: its scheduler latency tax |
| `process_involuntary_switches_per_s` | `/proc/<pid>/status` `nonvoluntary_ctxt_switches` | Δ / Δt | Preempted while runnable, so **CPU contention** |
| `process_voluntary_switches_per_s` | `/proc/<pid>/status` `voluntary_ctxt_switches` | Δ / Δt | Blocked (I/O, futex, sleep), so **blocking/lock contention** |
| `process_node_residency` | `/proc/<pid>/stat` field 39 (`processor`) per sample | histogram of CPU → node | Where the threads actually ran |

## Memory and page faults

| Metric | Source | Reading |
|---|---|---|
| `faults_per_s`, `major_faults_per_s` | `/proc/vmstat` `pgfault`, `pgmajfault` | Minor faults cost ~0.1-1 µs; major faults block on storage (~10 µs NVMe to ~10 ms HDD) |
| `process_minor/major_faults_per_s` | `/proc/<pid>/stat` fields 10, 12 | Per-process fault rates |
| `direct_reclaim_stalls_per_s` | `/proc/vmstat` `allocstall_*` | An allocating task had to reclaim memory itself: latency spikes |
| `pgscan_direct_per_s` vs `pgscan_kswapd_per_s` | `/proc/vmstat` | Reclaim by allocators vs by the background daemon |
| `swap_pages_per_s` | `/proc/vmstat` `pswpin + pswpout` | Any sustained value means the working set doesn't fit |
| `psi_memory_some` / `psi_memory_full` | `/proc/pressure/memory` | *full* = no task could progress because of memory: lost throughput |
| `mem_available_min_frac` | `/proc/meminfo` `MemAvailable / MemTotal` | Minimum over the window |

## NUMA

| Metric | Source | Reading |
|---|---|---|
| `remote_alloc_frac` | node `numastat` `other_node / (local_node + other_node)` | Allocations placed on a node other than the allocating CPU's. **Locality** |
| `policy_miss_frac` | node `numastat` `numa_miss / (numa_hit + numa_miss)` | Allocation-*policy* misses (preferred node full). **Not** locality |
| `hint_fault_local_frac` | `/proc/vmstat` `numa_hint_faults_local / numa_hint_faults` | AutoNUMA's sampled *access* locality (needs `numa_balancing=1`) |
| `pages_migrated_per_s` | `/proc/vmstat` `numa_pages_migrated` | AutoNUMA migration cost |
| `process_memory_by_node` | `/proc/<pid>/numa_maps` `N<n>=` | Where the process's pages live (huge pages normalised to 4 KiB units) |
| `process_placement_mismatch` | residency vs memory_by_node | Total variation distance ½Σ\|cpu_n − mem_n\|: 0 = matched, 1 = fully apart |
| `process_remote_access_frac_est` | same | Σ cpu_c · mem_m over c ≠ m: remote share *if* every thread touches all memory uniformly |
| `process_expected_distance` | + SLIT `distance` | Σ cpu_c · mem_m · dist(c, m): 10 = all local |
| `node_free_frac` | node `meminfo` | A full node pushes allocations remote (with `zone_reclaim_mode=0`) |

## Cache, branch, TLB (hardware counters)

| Metric | Formula | Reading |
|---|---|---|
| `ipc` | instructions / cycles | < 1 on a modern core usually means stalls; > 2 is pipeline-efficient |
| `llc_mpki` | cache-misses / (instructions / 1000) | ~80 ns per DRAM miss: 10 MPKI ≈ 2.4 cycles/instr of exposure at 3 GHz |
| `llc_miss_ratio` | cache-misses / cache-references | Generic events; mapping is CPU-specific (LLC on most Intel) |
| `branch_miss_ratio`, `branch_mpki` | branch-misses / branches | ~15-20 cycles per mispredict |
| `dtlb_miss_ratio`, `dtlb_mpki` | dTLB-load-misses / dTLB-loads | Page-walk pressure, which huge pages reduce |
| `min_running_frac` | time_running / time_enabled | < 1 means counters were multiplexed and values are extrapolated |

## Huge pages

| Metric | Source | Reading |
|---|---|---|
| `thp_enabled`, `thp_defrag` | `/sys/kernel/mm/transparent_hugepage/*` | `always`+`always` = synchronous compaction in the fault path |
| `thp_fallback_frac` | vmstat `thp_fault_fallback / (alloc + fallback)` | Huge-page faults that got 4 KiB pages: fragmentation |
| `compact_stall_per_s` | vmstat `compact_stall` | Tasks that compacted memory synchronously |
| `process_thp_coverage` | smaps_rollup `AnonHugePages / Anonymous` | Share of the heap backed by THP |
| `hugetlb_pools` | `/sys/kernel/mm/hugepages/hugepages-*/` | `free - resv` pages are reserved but unused memory |
