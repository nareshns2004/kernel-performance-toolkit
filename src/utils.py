"""Shared command helpers."""

from __future__ import annotations

import subprocess


def run(command: list[str], timeout: int = 60) -> str:
    """Run a command and return stdout, raising a helpful exception on failure."""
    try:
        result = subprocess.run(command, check=True, text=True, capture_output=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise RuntimeError(f"command not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip()
        raise RuntimeError(f"{' '.join(command)} failed: {detail}") from exc
    # ``perf stat`` writes measurements to stderr; returning both streams also
    # preserves tools which use stderr for informational output.
    return result.stdout + result.stderr
