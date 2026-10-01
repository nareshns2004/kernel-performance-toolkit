"""kptk: Linux kernel performance toolkit.

Records raw /proc and /sys state with negligible overhead, reads hardware
counters through perf_event_open, and turns both into findings about CPU
scheduling, memory, NUMA locality, cache efficiency, page faults and huge pages.
Zero runtime dependencies: everything is Python standard library.
"""

__version__ = "0.2.0"
