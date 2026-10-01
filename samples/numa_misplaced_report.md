# Kernel performance report

**Host** synthetic-2s · kernel 6.8.0-synthetic · 32 CPUs (16 cores, 2 socket(s), SMT on) · 2 NUMA node(s) · L1D 48 KiB, L1I 32 KiB, L2 2048 KiB, L3 32768 KiB

**Window** 10.0 s, 11 samples, recorder cost 0.4 ms/sample · **target** pid 4242 (pgdb)

## Findings

### 🔴 About 85% of the process's memory accesses are likely remote: threads ran mostly on node 0, memory sits mostly on node 1

Evidence: `cpu_node_residency={0: 1}`, `memory_by_node={0: 0.15, 1: 0.85}`, `placement_mismatch=0.85`, `expected_slit_distance=19.4`

_Why it matters:_ Typical cause: memory was first-touched by an initialisation thread on one node, then worker threads were scheduled on another.

**Action:** Start it with numactl --cpunodebind=1 --membind=1, or migratepages 4242 1 0.

### 🟠 Likely memory-bound: IPC 0.60, LLC 17.9 misses per 1k instructions; anonymous working set 49152 MiB vs 32 MiB L3

Evidence: `ipc=0.6`, `llc_mpki=17.9`, `llc_miss_ratio=0.478`

_Why it matters:_ Low IPC together with many LLC misses means the core is waiting on DRAM. Top-down analysis (perf stat --topdown) can confirm it on supported CPUs.

**Action:** Improve locality: tile or block loops to fit the LLC, use struct-of-arrays for scanned fields, prefer contiguous containers over pointer-chasing, and prefetch for irregular access.

### 🟠 dTLB miss rate 3.0% (10.00 per 1k instructions): page walks are significant

Evidence: `dtlb_miss_ratio=0.03`

_Why it matters:_ With a few thousand TLB entries x 4 KiB, reach is only a few MiB; larger random-access working sets miss constantly.

**Action:** Back the hot heap with huge pages (madvise(MADV_HUGEPAGE), THP, or hugetlbfs). A 2 MiB page covers 512x the reach of a 4 KiB TLB entry. See the hugepages findings.

### 🟠 THP enabled=always with defrag=always: page faults may stall on synchronous compaction

Evidence: `enabled=always`, `defrag=always`

_Why it matters:_ Under fragmentation every anonymous fault can enter direct compaction, a classic source of multi-ms latency spikes in databases and JVMs.

**Action:** Use defrag=defer+madvise: background compaction for everyone, synchronous only for regions that asked with MADV_HUGEPAGE.

### 🟠 44% of huge-page faults fell back to 4 KiB pages

Evidence: `thp_fault_alloc=500`, `thp_fault_fallback=400`

**Action:** Physical memory is fragmented. Reserve hugetlbfs pages at boot for critical workloads, raise vm.min_free_kbytes, or trigger compaction off-peak (echo 1 > /proc/sys/vm/compact_memory).

### 🟠 12.0 direct compaction stalls/s

Evidence: `compact_stall_per_s=12`, `compact_fail_frac=0.5`

**Action:** Tasks are compacting memory synchronously to get contiguous pages. Prefer THP defrag=defer+madvise and keep free-memory headroom.

### 🟠 Only 4% of the process's 49152 MiB anonymous memory is backed by huge pages and dTLB miss rate is 3.0%

Evidence: `anon_kib=50331648`, `anon_huge_kib=2013265`, `thp_enabled=always`

**Action:** Try enabling THP (or hugetlbfs for predictable latency). For jemalloc/tcmalloc there are allocator options to request huge pages. Measure: the benefit depends on random-access TLB pressure.

### 🟠 35% of page allocations landed on a node other than the allocating CPU's

Evidence: `remote_alloc_frac=0.35`, `policy_miss_frac=0`

_Why it matters:_ Remote DRAM costs roughly 1.3-2x local latency and shares inter-socket link bandwidth.

**Action:** Bind CPU and memory together (numactl --cpunodebind=N --membind=N, or cpuset.mems) and check for interleave or preferred policies set by the runtime.

### 🟠 Only 25% of sampled memory accesses were node-local (AutoNUMA hinting faults)

Evidence: `hint_faults=400000`, `local=100000`

**Action:** Threads and their data are on different nodes. Partition data per node, or pin threads to the node holding their memory.

### 🟠 Node 1 is nearly full (2.0% free): new allocations will spill to node(s) [0] or trigger reclaim

Evidence: `node=1`, `free_frac=0.02`, `zone_reclaim_mode=0`

**Action:** Rebalance placement or interleave large shared buffers. With zone_reclaim_mode=0 (default) the kernel goes remote rather than reclaim locally.

### 🔵 4 GiB of 2 MiB hugetlb pages are reserved in the pool but unused

Evidence: `page_size_kib=2048`, `nr=2048`, `free=2048`, `reserved=0`

**Action:** hugetlb pages are unavailable to the rest of the system. Shrink vm.nr_hugepages, or confirm the consumer (DPDK, a DB) starts later.

## USE summary

| resource | utilisation | saturation | errors |
|---|---:|---|---|
| CPU | 0.4 | psi_some: 0.01, runq_waiting_tasks: 0.05 | steal_frac: 0 |
| Memory capacity | 0.742 | psi_some: 0, direct_reclaim_per_s: 0, swap_pages_per_s: 0 | oom_kills: 0 |
| NUMA interconnect | 0.35 | pages_migrated_per_s: 0 | policy_miss_frac: 0 |
| Huge pages | 0.04 | compact_stall_per_s: 12 | thp_fallback_frac: 0.444 |

## Over time

```
cpu util      ▁▁▁▁▁▁▁▁▁▁  max 0.4
runq waiting  ▁▁▁▁▁▁▁▁▁▁  max 0.05
faults/s      ▁▁▁▁▁▁▁▁▁▁  max 20,000
major flt/s   ▁▁▁▁▁▁▁▁▁▁  max 0
mem pressure  ▁▁▁▁▁▁▁▁▁▁  max 0
```

## Metrics

<details><summary>cpu</summary>

| metric | value |
|---|---:|
| utilization | 0.4 |
| user_frac | 0.32 |
| system_frac | 0.08 |
| iowait_frac | 0 |
| steal_frac | 0 |
| softirq_frac | 0 |
| irq_frac | 0 |
| context_switches_per_s | 50,000 |
| procs_running_mean | 13 |
| procs_running_per_cpu | 0.406 |
| per_cpu_spread | 0 |
| runq_waiting_tasks_avg | 0.05 |
| runq_wait_per_timeslice_ms | 0.00156 |
| psi_cpu_some | 0.01 |
| process_comm | pgdb |
| process_threads | 32 |
| process_cpus_used | 4 |
| process_system_frac | 0.15 |
| process_runq_wait_share | 0.02 |
| process_runq_wait_per_slice_ms | 0.0163 |
| process_voluntary_switches_per_s | 2,000 |
| process_involuntary_switches_per_s | 100 |
| process_cpus_allowed | 0-31 |

</details>

<details><summary>memory</summary>

| metric | value |
|---|---:|
| faults_per_s | 20,000 |
| major_faults_per_s | 0 |
| swap_pages_per_s | 0 |
| direct_reclaim_stalls_per_s | 0 |
| pgscan_direct_per_s | 0 |
| pgscan_kswapd_per_s | 0 |
| oom_kills | 0 |
| workingset_refault_per_s | 0 |
| mem_total_kib | 134217728 |
| mem_available_min_frac | 0.258 |
| psi_memory_some | 0 |
| psi_memory_full | 0 |
| process_minor_faults_per_s | 2,000 |
| process_major_faults_per_s | 0 |
| process_rss_kib | 50331648 |
| process_anon_kib | 50331648 |
| process_swap_kib | 0 |

</details>

<details><summary>numa</summary>

| metric | value |
|---|---:|
| nodes | 2 |
| applicable | True |
| remote_alloc_frac | 0.35 |
| policy_miss_frac | 0 |
| numa_balancing | 1 |
| hint_fault_local_frac | 0.25 |
| pages_migrated_per_s | 0 |
| process_expected_distance | 19.4 |
| process_remote_access_frac_est | 0.85 |
| process_placement_mismatch | 0.85 |

</details>

<details><summary>cache</summary>

| metric | value |
|---|---:|
| available | True |
| ipc | 0.6 |
| llc_miss_ratio | 0.478 |
| llc_mpki | 17.9 |
| branch_miss_ratio | 0.005 |
| branch_mpki | 0.833 |
| dtlb_miss_ratio | 0.03 |
| dtlb_mpki | 10 |
| min_running_frac | 1 |

</details>

<details><summary>hugepages</summary>

| metric | value |
|---|---:|
| thp_enabled | always |
| thp_defrag | always |
| khugepaged_max_ptes_none | 511 |
| thp_fault_alloc_per_s | 50 |
| thp_fallback_frac | 0.444 |
| thp_collapse_per_s | 0 |
| thp_split_per_s | 0 |
| compact_stall_per_s | 12 |
| compact_fail_frac | 0.5 |
| anon_huge_kib_system | 0 |
| process_anon_kib | 50331648 |
| process_thp_coverage | 0.04 |
| process_hugetlb_kib | 0 |

</details>

<details><summary>config</summary>

| metric | value |
|---|---:|
| swappiness | 60 |
| zone_reclaim_mode | 0 |
| numa_balancing | 1 |
| perf_event_paranoid | 2 |

</details>
