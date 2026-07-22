from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from report import build_report, markdown_summary, write_json


def test_report_can_be_serialized(tmp_path):
    report = build_report({"cache-references": 100, "cache-misses": 5})
    output = tmp_path / "report.json"
    write_json(report, output)
    assert '"cache_miss_rate_percent": 5.0' in output.read_text()
    assert "Kernel Performance Report" in markdown_summary(report)
