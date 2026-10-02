# Linux performance: the kernel mechanics behind each finding

Background for each domain the toolkit analyses: what the kernel does, how it shows up in the
counters, and which microbenchmark in `benchmarks/c/` reproduces it.

## Method: USE first, then drill down

For every resource, check **U**tilisation, **S**aturation and **E**rrors (Brendan Gregg's USE
method). Utilisation alone misleads: a CPU at 70% average can have a run queue ten tasks deep
in bursts. `kptk` reports a USE row per resource, then domain findings.

## CPU scheduling (CFS / EEVDF)

* Every runnable task waits on a per-CPU run queue. The time between becoming runnable and
  actually running is **scheduler latency**, counted per CPU in `/proc/schedstat` (`run_delay`)
  and per task in `/proc/<pid>/schedstat`. It's invisible in `top`.
* **Voluntary** switches (blocking) vs **involuntary** switches (preemption) tell lock and I/O
  waits apart from CPU oversubscription.
* **Migrations** across cores cost cache warmth; across sockets they also cost memory locality.
  The load balancer trades these against idle CPUs.
* Mitigations: right-size thread pools, `taskset`/cpusets, `isolcpus`/`nohz_full` for dedicated
  cores, IRQ affinity, SCHED_FIFO for the few threads that truly need it.
* Reproduce: `wakeup_latency` (block/wake latency percentiles, pinned to the same core, the same
  socket or across sockets).

## Memory and page faults

* Anonymous memory is allocated lazily: `mmap`/`malloc` reserve address space, and the first
  touch of each page takes a **minor fault** that zero-fills it. File-backed pages not in the
  page cache take **major faults**.
* Allocators return memory to the OS (`munmap`, `MADV_DONTNEED`), so steady-state churn can
  keep faulting. Mitigations are buffer reuse, `MAP_POPULATE`, and huge pages (one fault per 2 MiB).
* Under pressure, `kswapd` reclaims in the background. When it falls behind, allocating tasks
  enter **direct reclaim** (`allocstall_*`), and PSI `memory full` shows lost throughput.
* Reproduce: `page_fault_cost` (base vs `MAP_POPULATE` vs THP vs pre-faulted).

## NUMA

* Each socket (or sub-NUMA cluster) owns memory. Remote access costs ~1.3-2x local latency
  and consumes interconnect (UPI/Infinity Fabric) bandwidth.
* Default policy is **first touch**: a page lands on the node of the CPU that first writes it.
  The classic bug is a single thread initialising a big buffer that many threads on other
  nodes then use.
* AutoNUMA (`kernel.numa_balancing`) samples accesses with hinting faults and migrates pages
  or tasks. It helps long-running unpinned workloads and hurts when threads bounce around.
* Policies: `numactl --cpunodebind --membind`, `--interleave` for shared read-mostly data,
  cpuset `mems`, `mbind`/`set_mempolicy` in code, and `migratepages` after the fact.
* Reproduce: `numactl --cpunodebind=0 --membind=1 ./mem_latency` vs `--membind=0`.

## Caches

* Typical server hierarchy: L1d 32-48 KiB and L2 1-2 MiB per core, L3 tens of MiB per socket,
  then DRAM. Latency roughly 1 ns, 4 ns, 15-40 ns, then 80-120 ns.
* Dependent loads (pointer chasing) expose full latency. Independent streams let the
  prefetcher and memory-level parallelism hide it.
* **False sharing**: independent variables on one 64-byte line ping-pong between cores through
  coherence (MESI) traffic.
* Reproduce: `mem_latency` (latency cliffs per level) and `false_sharing` (packed vs padded).

## TLB and huge pages

* The TLB caches virtual→physical translations. With a few thousand entries of 4 KiB, reach is
  only a few MiB. Beyond that, every miss walks a 4-level (or 5-level) page table.
* A 2 MiB huge page covers 512x more per entry. Two ways to get them:
  * **THP**: automatic. `enabled=always|madvise|never` decides who gets them, and
    `defrag` decides whether a fault may *stall* to compact memory (`always`) or defers to
    khugepaged (`defer`, `defer+madvise`). `always/always` is the classic latency-spike setting.
  * **hugetlbfs**: an explicit reserved pool, predictable, but the memory is set aside even when unused.
* Fragmentation makes huge-page allocation fail (`thp_fault_fallback`), and compaction to fix
  it costs CPU and latency (`compact_stall`).
* Reproduce: `mem_latency` vs `mem_latency --huge` (TLB-reach cliff moves out).

## Hardware counters and their limits

* `perf_event_open` programs the PMU. A core has only a few programmable counters. Beyond
  that the kernel **multiplexes** and scales by `time_enabled / time_running`. Ratios are exact
  only within an event group.
* Generic events (`cache-misses`) map to model-specific events, so compare values only on the
  same CPU model.
* Access is gated by `kernel.perf_event_paranoid` (Ubuntu defaults to 4 = no unprivileged
  access) or `CAP_PERFMON`.
