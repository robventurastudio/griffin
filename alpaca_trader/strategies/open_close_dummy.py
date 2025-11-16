"""Simplistic opening/closing strategy for experimentation."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from ..universe import DEFAULT_UNIVERSE

LOGGER = logging.getLogger(__name__)


@dataclass
class OpenCloseStrategy:
    """Buy the universe at the opening bell and sell at the close.

    The goal of this project is to provide structure around experimentation, so
    the strategy intentionally leans towards clarity instead of cleverness.  All
    interesting bits (order submission, portfolio syncing, etc.) are exposed as
    separate methods so that they can be swapped out later without touching the
    engine wiring.
    """

    client: "AlpacaClient"
    universe: Sequence[str] = field(default_factory=lambda: list(DEFAULT_UNIVERSE))
    qty: int = 1
    positions_open: bool = False
    direction: str = "long"
    risk_manager: Optional["RiskManager"] = None

    def __post_init__(self) -> None:
        self.universe = [symbol.upper() for symbol in self.universe]
        if self.direction not in {"long", "short"}:
            raise ValueError("direction must be either 'long' or 'short'")

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------
    def bootstrap_state(self) -> None:
        """Inspect the account and update ``positions_open`` accordingly."""

        LOGGER.info("Bootstrapping account state from Alpaca")
        self.positions_open = self.client.has_position_for(self.universe)
        if self.positions_open:
            LOGGER.info(
                "Existing positions detected for %s symbols; will hold until close",
                len(self.universe),
            )

    def should_open(self, clock) -> bool:
        """Return ``True`` when the market is open and we are flat."""

        return bool(clock.is_open and not self.positions_open)

    def should_close(self, clock) -> bool:
        """Return ``True`` when the market is closed and we hold positions."""

        return bool((not clock.is_open) and self.positions_open)

    # ------------------------------------------------------------------
    # Execution helpers
    # ------------------------------------------------------------------
    def enter_positions(self) -> None:
        """Submit market orders for the entire universe."""

        side = "buy" if self.direction == "long" else "sell"
        LOGGER.info(
            "Entering %s positions for %s symbols", self.direction.upper(), len(self.universe)
        )
        self._submit_bulk_orders(self.universe, side=side)
        self.positions_open = True

    def exit_positions(self) -> None:
        """Close any open positions for the tracked universe."""

        LOGGER.info("Exiting positions for %s symbols", len(self.universe))
        for symbol in self.universe:
            self.client.close_position(symbol)
        self.positions_open = False

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _submit_bulk_orders(self, symbols: Iterable[str], *, side: str) -> None:
        for symbol in symbols:
            price = None
            try:
                trade = self.client.get_latest_trade(symbol)
                price = getattr(trade, "price", None)
            except Exception:  # pragma: no cover
                LOGGER.debug("Unable to fetch latest trade for %s", symbol)

            qty = self.qty
            if self.risk_manager and price:
                qty = self.risk_manager.size_order(symbol=symbol, price=float(price)) or 0
                if qty and not self.risk_manager.validate_order(symbol=symbol, side=side, price=float(price), qty=qty):
                    qty = 0
            if qty <= 0:
                LOGGER.info("Risk rules skipped %s order for %s", side, symbol)
                continue
            self.client.submit_market_order(symbol=symbol, qty=qty, side=side)
