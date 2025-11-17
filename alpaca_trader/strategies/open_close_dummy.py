"""Backwards-compatible wrapper around the open/close market strategy."""
from __future__ import annotations

from alpaca_trader.engine import OpenCloseMarketStrategy


class OpenCloseStrategy(OpenCloseMarketStrategy):
    """Alias for the open/close market strategy.

    This keeps old dotted-path references working while sharing the same
    implementation used by the CLI trading loop.
    """

    pass
"""Opening/closing strategy with robust position state management."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from ..universe import DEFAULT_UNIVERSE

LOGGER = logging.getLogger(__name__)


@dataclass
class OpenCloseStrategy:
    """Buy/sell universe at open, exit at close with proper state tracking.

    This strategy now properly verifies position state after order submission
    to handle partial fills and API failures gracefully.
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
        """Inspect the account and update positions_open accordingly."""
        LOGGER.info("Bootstrapping account state from Alpaca")
        self.positions_open = self.client.has_position_for(self.universe)
        
        if self.positions_open:
            LOGGER.info(
                "Existing positions detected for universe; will hold until close"
            )
            self._log_current_positions()

    def should_open(self, clock) -> bool:
        """Return True when market is open and we are flat."""
        return bool(clock.is_open and not self.positions_open)

    def should_close(self, clock) -> bool:
        """Return True when market is closed and we hold positions."""
        return bool((not clock.is_open) and self.positions_open)

    # ------------------------------------------------------------------
    # Execution helpers
    # ------------------------------------------------------------------
    def enter_positions(self) -> None:
        """Submit market orders for the entire universe and verify fills."""
        side = "buy" if self.direction == "long" else "sell"
        LOGGER.info(
            "Entering %s positions for %s symbols", self.direction.upper(), len(self.universe)
        )
        
        self._submit_bulk_orders(self.universe, side=side)
        
        # Verify actual positions after submission
        # This handles partial fills and API failures gracefully
        self.positions_open = self.client.has_position_for(self.universe)
        
        if self.positions_open:
            LOGGER.info("Position entry confirmed - now holding positions")
            self._log_current_positions()
        else:
            LOGGER.warning(
                "No positions detected after entry attempt - may need manual review"
            )

    def exit_positions(self) -> None:
        """Close any open positions for the tracked universe."""
        LOGGER.info("Exiting positions for %s symbols", len(self.universe))
        
        failed_symbols = []
        for symbol in self.universe:
            try:
                self.client.close_position(symbol)
            except Exception:
                LOGGER.exception("Failed to close position for %s", symbol)
                failed_symbols.append(symbol)
        
        if failed_symbols:
            LOGGER.error(
                "Failed to close positions for: %s - manual intervention may be required",
                ", ".join(failed_symbols)
            )
        
        # Verify positions are actually closed
        self.positions_open = self.client.has_position_for(self.universe)
        
        if self.positions_open:
            LOGGER.warning(
                "Some positions remain open after exit attempt - verifying status"
            )
            self._log_current_positions()
        else:
            LOGGER.info("All positions successfully closed")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _submit_bulk_orders(self, symbols: Iterable[str], *, side: str) -> None:
        """Submit orders for all symbols with risk checks."""
        submitted = 0
        skipped = 0
        failed = 0
        
        for symbol in symbols:
            try:
                price = self._get_price(symbol)
                if not price:
                    LOGGER.warning("No price available for %s, skipping", symbol)
                    skipped += 1
                    continue
                
                qty = self._calculate_quantity(symbol, price, side)
                if qty <= 0:
                    LOGGER.info("Risk rules skipped %s order for %s", side, symbol)
                    skipped += 1
                    continue
                
                self.client.submit_market_order(symbol=symbol, qty=qty, side=side)
                submitted += 1
                
            except Exception:
                LOGGER.exception("Failed to submit order for %s", symbol)
                failed += 1
        
        LOGGER.info(
            "Order submission complete: submitted=%d skipped=%d failed=%d",
            submitted, skipped, failed
        )

    def _calculate_quantity(self, symbol: str, price: float, side: str) -> int:
        """Determine order quantity using risk manager if available."""
        qty = self.qty
        
        if self.risk_manager:
            # Use risk manager for position sizing
            sized_qty = self.risk_manager.size_order(symbol=symbol, price=price)
            if sized_qty > 0:
                qty = sized_qty
            else:
                return 0
            
            # Validate order against risk rules
            if not self.risk_manager.validate_order(
                symbol=symbol, side=side, price=price, qty=qty
            ):
                LOGGER.warning("Risk manager rejected order for %s", symbol)
                return 0
        
        return qty

    def _get_price(self, symbol: str) -> Optional[float]:
        """Fetch current price for symbol."""
        try:
            trade = self.client.get_latest_trade(symbol)
            price = getattr(trade, "price", None)
            return float(price) if price is not None else None
        except Exception:
            LOGGER.debug("Unable to fetch latest trade for %s", symbol)
            return None

    def _log_current_positions(self) -> None:
        """Log current position details for monitoring."""
        try:
            positions = self.client.list_positions()
            universe_positions = [
                p for p in positions if p.symbol.upper() in self.universe
            ]
            
            if not universe_positions:
                LOGGER.info("No positions in tracked universe")
                return
            
            for pos in universe_positions:
                LOGGER.info(
                    "Position: %s qty=%s avg_price=%s unrealized_pl=%s",
                    pos.symbol,
                    getattr(pos, "qty", "N/A"),
                    getattr(pos, "avg_entry_price", "N/A"),
                    getattr(pos, "unrealized_pl", "N/A"),
                )
        except Exception:
            LOGGER.exception("Failed to fetch position details")
