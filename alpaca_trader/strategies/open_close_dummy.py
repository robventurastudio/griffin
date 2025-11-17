"""Backwards-compatible wrapper around the open/close market strategy."""
from __future__ import annotations

from alpaca_trader.engine import OpenCloseMarketStrategy


class OpenCloseStrategy(OpenCloseMarketStrategy):
    """Alias for the open/close market strategy.

    This keeps old dotted-path references working while sharing the same
    implementation used by the CLI trading loop.
    """

    pass
