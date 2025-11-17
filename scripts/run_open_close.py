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


def _ensure_repo_root() -> Path:
    """Add the repository root to ``sys.path`` if it's missing.

    When running this script directly (``python scripts/run_open_close.py``),
    Python sets ``sys.path[0]`` to the ``scripts`` folder, so the project root
    is not importable. We walk up the filesystem from this file until we find
    a directory containing ``alpaca_trader``.
    """

    script_path = Path(__file__).resolve()
    for candidate in script_path.parents:
        if (candidate / "alpaca_trader").is_dir():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            return candidate

    raise ImportError(
        "Could not locate repository root containing 'alpaca_trader'. "
        "Run the script from within the project checkout."
    )


# Ensure repository root is importable when running the script directly.
ROOT = _ensure_repo_root()

from alpaca_trader.engine import run_open_close_loop


if __name__ == "__main__":
    run_open_close_loop()
