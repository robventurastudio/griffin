"""Enhanced risk controls with proper equity-based sizing and position caching."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import floor
from typing import Dict, Optional

LOGGER = logging.getLogger(__name__)


@dataclass
class RiskLimits:
    """Configuration container for retail-friendly risk rules."""

    max_position_dollars: float = 2_000.0
    daily_loss_limit_pct: float = 0.05
    fixed_stop_pct: float = 0.01
    atr_multiple: float = 1.5
    risk_per_trade_pct: float = 0.01  # Risk 1% of equity per trade
    max_portfolio_exposure_pct: float = 0.95  # Use max 95% of equity


class RiskManager:
    """Evaluate orders against equity-based sizing and loss controls."""

    def __init__(self, client, limits: Optional[RiskLimits] = None) -> None:
        self.client = client
        self.limits = limits or RiskLimits()
        
        # Initialize starting equity
        account = self._get_account()
        self._start_equity = float(getattr(account, "equity", 0.0) or 0.0)
        self._start_date = datetime.utcnow().date()
        
        # Position cache to avoid repeated API calls
        self._position_cache: Dict[str, Dict] = {}
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl = timedelta(seconds=30)
        
        LOGGER.info(
            "Risk manager initialized: start_equity=%.2f daily_loss_limit=%.1f%%",
            self._start_equity,
            self.limits.daily_loss_limit_pct * 100,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def daily_loss_exceeded(self) -> bool:
        """Return True if equity drawdown breaches the daily limit."""
        # Reset on new day
        today = datetime.utcnow().date()
        if today != self._start_date:
            LOGGER.info("New trading day - resetting risk limits")
            account = self._get_account()
            self._start_equity = float(getattr(account, "equity", 0.0) or 0.0)
            self._start_date = today
            return False
        
        account = self._get_account()
        current_equity = float(getattr(account, "equity", 0.0) or 0.0)
        threshold = self._start_equity * (1 - self.limits.daily_loss_limit_pct)
        
        if current_equity <= threshold:
            drawdown = (self._start_equity - current_equity) / self._start_equity
            LOGGER.error(
                "Daily loss lockout triggered: start=%.2f current=%.2f drawdown=%.2f%% threshold=%.2f",
                self._start_equity,
                current_equity,
                drawdown * 100,
                threshold,
            )
            return True
        
        return False

    def size_order(
        self, *, symbol: str, price: float, atr: Optional[float] = None
    ) -> int:
        """Calculate position size based on account equity and risk parameters.
        
        Returns suggested quantity respecting:
        - Per-position dollar cap
        - Portfolio exposure limits
        - Risk-per-trade budget (1% of equity by default)
        """
        if price <= 0:
            LOGGER.warning("Invalid price %.4f for %s; rejecting order", price, symbol)
            return 0

        account = self._get_account()
        equity = float(getattr(account, "equity", 0.0) or 0.0)
        
        if equity <= 0:
            LOGGER.error("Account equity is %.2f; cannot size orders", equity)
            return 0

        # 1. Per-position dollar cap
        cap_qty = floor(self.limits.max_position_dollars / price)

        # 2. Risk-based sizing using ATR or fixed stop
        risk_per_share = (
            (atr * self.limits.atr_multiple)
            if atr 
            else price * self.limits.fixed_stop_pct
        )
        
        if risk_per_share <= 0:
            LOGGER.warning("Risk per share is %.4f for %s; using cap only", risk_per_share, symbol)
            return cap_qty
        
        # Risk budget is % of equity
        risk_budget = equity * self.limits.risk_per_trade_pct
        risk_qty = floor(risk_budget / risk_per_share)

        # 3. Portfolio exposure cap
        cash = float(getattr(account, "cash", equity))
        current_exposure = equity - cash
        max_exposure = equity * self.limits.max_portfolio_exposure_pct
        available_exposure = max(0, max_exposure - current_exposure)
        exposure_qty = floor(available_exposure / price) if available_exposure > 0 else 0

        # Take the minimum of all constraints
        qty = max(0, min(cap_qty, risk_qty, exposure_qty))
        
        LOGGER.debug(
            "Sizing %s at price=%.2f: equity=%.0f cap_qty=%d risk_qty=%d exposure_qty=%d -> qty=%d (atr=%s)",
            symbol,
            price,
            equity,
            cap_qty,
            risk_qty,
            exposure_qty,
            qty,
            f"{atr:.4f}" if atr else "N/A",
        )
        
        return qty

    def validate_order(
        self, *, symbol: str, side: str, price: float, qty: int
    ) -> bool:
        """Validate proposed order against all risk limits."""
        # Check daily loss limit
        if self.daily_loss_exceeded():
            LOGGER.error("Order for %s blocked: daily loss limit reached", symbol)
            return False

        # Check exposure limit
        exposure = price * qty
        if exposure > self.limits.max_position_dollars:
            LOGGER.warning(
                "Order for %s exceeds per-symbol cap (exposure %.2f > %.2f)",
                symbol,
                exposure,
                self.limits.max_position_dollars,
            )
            return False

        # Check if we already have a position at cap
        current_qty = self._current_position_qty(symbol)
        if current_qty > 0:
            current_exposure = current_qty * price
            if side.lower() == "buy" and current_exposure >= self.limits.max_position_dollars:
                LOGGER.warning(
                    "Order for %s blocked: position already at cap (exposure=%.2f)",
                    symbol,
                    current_exposure,
                )
                return False

        # Check portfolio exposure limit
        account = self._get_account()
        equity = float(getattr(account, "equity", 0.0) or 0.0)
        cash = float(getattr(account, "cash", equity))
        current_exposure = equity - cash
        max_exposure = equity * self.limits.max_portfolio_exposure_pct
        
        if current_exposure + exposure > max_exposure:
            LOGGER.warning(
                "Order for %s blocked: would exceed portfolio exposure limit (current=%.2f new=%.2f max=%.2f)",
                symbol,
                current_exposure,
                exposure,
                max_exposure,
            )
            return False

        return True

    def invalidate_cache(self) -> None:
        """Force refresh of position cache on next access."""
        self._cache_timestamp = None
        self._position_cache.clear()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _get_account(self):
        """Fetch account with error handling."""
        try:
            return self.client.get_account()
        except Exception:
            LOGGER.exception("Failed to fetch account information")
            # Return dummy account to avoid crashes
            class DummyAccount:
                equity = 0.0
                cash = 0.0
            return DummyAccount()

    def _current_position_qty(self, symbol: str) -> int:
        """Get current position quantity with caching."""
        # Check cache validity
        now = datetime.utcnow()
        if (
            self._cache_timestamp is None
            or (now - self._cache_timestamp) > self._cache_ttl
        ):
            self._refresh_position_cache()

        position = self._position_cache.get(symbol.upper())
        if position:
            return position.get("qty", 0)
        return 0

    def _refresh_position_cache(self) -> None:
        """Fetch all positions and update cache."""
        try:
            positions = self.client.list_positions()
            self._position_cache = {}
            
            for pos in positions:
                symbol = getattr(pos, "symbol", "").upper()
                try:
                    qty = int(float(getattr(pos, "qty", 0)))
                    entry_price = float(getattr(pos, "avg_entry_price", 0.0) or 0.0)
                    self._position_cache[symbol] = {
                        "qty": qty,
                        "entry_price": entry_price,
                    }
                except (TypeError, ValueError) as e:
                    LOGGER.warning("Error parsing position for %s: %s", symbol, e)
            
            self._cache_timestamp = datetime.utcnow()
            LOGGER.debug("Position cache refreshed: %d positions", len(self._position_cache))
            
        except Exception:
            LOGGER.exception("Unable to refresh position cache")
            # Keep stale cache rather than clearing it
