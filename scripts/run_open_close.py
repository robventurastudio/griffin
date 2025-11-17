"""Helper script to run the open/close clock loop without CLI args.

Running a module from the ``scripts`` directory means Python sets
``sys.path[0]`` to the scripts folder, so the project root is not on the
import path by default. We explicitly add the repository root so the
``alpaca_trader`` package can be imported when this script is executed
directly (e.g., ``python scripts/run_open_close.py``).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure repository root is importable when running the script directly.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alpaca_trader.engine import run_open_close_loop


if __name__ == "__main__":
    run_open_close_loop()
