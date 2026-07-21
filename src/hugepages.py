"""Transparent huge-page state reader."""

from pathlib import Path


def transparent_hugepages_enabled(path: str = "/sys/kernel/mm/transparent_hugepage/enabled") -> str | None:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    for option in text.split():
        if option.startswith("[") and option.endswith("]"):
            return option[1:-1]
    return None
