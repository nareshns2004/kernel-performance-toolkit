from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from cache_analysis import cache_metrics
from numa_analysis import numa_metrics
from page_faults import page_fault_metrics


def test_cache_miss_rate():
    assert cache_metrics({"cache-references": 200, "cache-misses": 10})["cache_miss_rate_percent"] == 5


def test_faults_and_numa_metrics_handle_counts():
    assert page_fault_metrics({"minor-faults": 90, "major-faults": 10})["major_fault_percent"] == 10
    assert numa_metrics({"numa_hit": {"total": 80}, "numa_miss": {"total": 20}})["numa_remote_percent"] == 20
