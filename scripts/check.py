"""Run deterministic unit and local-service integration checks; no model calls."""

from pathlib import Path
import subprocess
import sys


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    return subprocess.call(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests/unit", "-v"],
        cwd=root,
    )


if __name__ == "__main__":
    raise SystemExit(main())
