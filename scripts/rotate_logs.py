"""Utility to delete logs older than ``--days`` (default: 7)."""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

LOG_DIR = Path("logs")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prune old log files")
    parser.add_argument("--days", type=int, default=7, help="Remove logs older than N days")
    args = parser.parse_args()

    if not LOG_DIR.exists():
        print("No logs directory found")
        return 0

    cutoff = time.time() - args.days * 86400
    removed = 0
    for path in LOG_DIR.iterdir():
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink()
            removed += 1
    print(f"Removed {removed} old log files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
