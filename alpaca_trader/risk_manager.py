"""Lightweight risk controls for position sizing and exposure gating."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from math import floor
from typing import Dict, Optional

LOGGER = logging.getLogger(__name__)


@dataclass
class RiskLimits:
    """Configuration container for basic retail-friendly risk rules."""

    max_position_dollars: float = 2_000.0
    daily_loss_limit_pct: float = 0.05
    fixed_stop_pct: float = 0.01
    atr_multiple: float = 1.5
    risk_buffer_pct: float = 0.5


class RiskManager:
    """Evaluate orders against simple sizing and loss controls."""

    def __init__(self, client, limits: Optional[RiskLimits] = None) -> None:
        self.client = client
        self.limits = limits or RiskLimits()
        account = self.client.get_account()
        self._start_equity = float(getattr(account, "equity", 0.0) or 0.0)
        self._lockouts: Dict[str, datetime] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def daily_loss_exceeded(self) -> bool:
        """Return True if equity drawdown breaches the daily limit."""

        account = self.client.get_account()
        current_equity = float(getattr(account, "equity", 0.0) or 0.0)
        threshold = self._start_equity * (1 - self.limits.daily_loss_limit_pct)
        if current_equity <= threshold:
            LOGGER.error(
                "Daily loss lockout triggered: start=%.2f current=%.2f threshold=%.2f",
                self._start_equity,
                current_equity,
                threshold,
            )
            return True
        return False

    def size_order(self, *, symbol: str, price: float, atr: Optional[float] = None) -> int:
        """Return the suggested maximum quantity for ``symbol`` at ``price``.

        The size respects both a dollar exposure cap and a per-trade risk
        budget derived from either ATR or a fixed percentage stop.
        """

        if price <= 0:
            LOGGER.warning("Invalid price %.4f for %s; rejecting order", price, symbol)
            return 0

        cap_qty = floor(self.limits.max_position_dollars / price)
        risk_per_share = (
            (atr * self.limits.atr_multiple) if atr else price * self.limits.fixed_stop_pct
        )
        if risk_per_share <= 0:
            return cap_qty

        risk_budget = self.limits.max_position_dollars * self.limits.risk_buffer_pct
        risk_qty = floor(risk_budget / risk_per_share)
        qty = max(0, min(cap_qty, risk_qty))
        LOGGER.debug(
            "Sizing %s at price=%.2f cap_qty=%s risk_qty=%s atr=%s -> qty=%s",
            symbol,
            price,
            cap_qty,
            risk_qty,
            atr,
            qty,
        )
        return qty

    def validate_order(self, *, symbol: str, side: str, price: float, qty: int) -> bool:
        """Return True if the proposed order is within exposure and loss limits."""

        if self.daily_loss_exceeded():
            LOGGER.error("Order for %s blocked: daily loss limit reached", symbol)
            return False

        exposure = price * qty
        if exposure > self.limits.max_position_dollars:
            LOGGER.warning(
                "Order for %s exceeds per-symbol cap (exposure %.2f > %.2f)",
                symbol,
                exposure,
                self.limits.max_position_dollars,
            )
            return False

        current_qty = self._current_position_qty(symbol)
        if current_qty and side.lower() == "buy" and current_qty * price >= self.limits.max_position_dollars:
            LOGGER.warning("Order for %s blocked: position already at cap", symbol)
            return False

        return True

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _current_position_qty(self, symbol: str) -> int:
        try:
            positions = self.client.list_positions()
        except Exception:  # pragma: no cover - network interaction
            LOGGER.exception("Unable to fetch positions for risk check")
            return 0
        for pos in positions:
            if getattr(pos, "symbol", "").upper() == symbol.upper():
                try:
                    return int(float(getattr(pos, "qty", 0)))
                except (TypeError, ValueError):
                    return 0
        return 0
