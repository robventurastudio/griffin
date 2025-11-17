"""Opening Range Breakout strategy with proper exit monitoring and risk hooks."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from statistics import mean
from typing import Dict, Optional, Sequence

from ..risk_manager import RiskManager
from ..universe import DEFAULT_UNIVERSE

LOGGER = logging.getLogger(__name__)


def _session_open(now: datetime) -> datetime:
    """Get market open time in UTC, handling timezone-naive inputs."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    
    eastern = ZoneInfo("America/New_York")
    eastern_open = time(hour=9, minute=30)
    open_dt = datetime.combine(now.astimezone(eastern).date(), eastern_open, tzinfo=eastern)
    return open_dt.astimezone(timezone.utc)


@dataclass
class OpeningRangeBreakout:
    """ORB strategy with exit monitoring, proper state management, and volume tracking."""

    client: "AlpacaClient"
    universe: Sequence[str] = field(default_factory=lambda: list(DEFAULT_UNIVERSE))
    qty: int = 1
    range_minutes: int = 30
    target_multiple: float = 1.5
    volume_factor: float = 1.2
    direction: str = "both"
    risk_manager: Optional[RiskManager] = None

    def __post_init__(self) -> None:
        self.universe = [symbol.upper() for symbol in self.universe]
        self.state: Dict[str, Dict[str, float]] = {}
        self._last_session_date: Optional[str] = None
        if self.direction not in {"long", "short", "both"}:
            raise ValueError("direction must be 'long', 'short', or 'both'")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def bootstrap_state(self) -> None:
        """Initialize state and sync with existing positions."""
        LOGGER.info("ORB bootstrap: warming state for %s symbols", len(self.universe))
        
        # Check for existing positions
        try:
            positions = self.client.list_positions()
            for pos in positions:
                symbol = pos.symbol.upper()
                if symbol in self.universe:
                    LOGGER.info("Found existing position in %s: qty=%s", symbol, pos.qty)
                    # Mark as triggered to avoid double-entry
                    self.state[symbol] = {
                        "or_high": 0.0,
                        "or_low": 0.0,
                        "triggered": True,
                        "entry": float(getattr(pos, "avg_entry_price", 0.0) or 0.0),
                        "stop": 0.0,
                        "target": 0.0,
                        "side": "buy" if float(pos.qty) > 0 else "sell",
                    }
        except Exception:
            LOGGER.exception("Failed to check existing positions during bootstrap")
        
        # Initialize state for all symbols
        for symbol in self.universe:
            if symbol not in self.state:
                self.state[symbol] = {
                    "or_high": 0.0,
                    "or_low": 0.0,
                    "or_avg_vol": 0.0,
                    "triggered": False,
                    "entry": 0.0,
                    "stop": 0.0,
                    "target": 0.0,
                    "side": "",
                }

    # ------------------------------------------------------------------
    # Hooks used by the engine loop
    # ------------------------------------------------------------------
    def should_open(self, clock) -> bool:
        """ORB doesn't use the standard open signal - it trades via on_tick."""
        return False

    def should_close(self, clock) -> bool:
        """Close all positions when market closes."""
        return bool(not clock.is_open)

    def on_tick(self, clock, symbol_state: Dict[str, Dict[str, float]]) -> None:
        """Main tick handler - seeds ranges, checks entries, monitors exits."""
        if not clock.is_open:
            return

        now = clock.timestamp if hasattr(clock, "timestamp") else datetime.utcnow().replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        
        # Reset state on new trading day
        self._maybe_reset_daily_state(now)
        
        open_dt = _session_open(now)
        range_end = open_dt + timedelta(minutes=self.range_minutes)
        
        for symbol in self.universe:
            # Seed the opening range
            self._maybe_seed_range(symbol, start=open_dt, end=range_end, now=now)
            
            # Update current volume from stream
            if symbol in symbol_state:
                last_size = symbol_state[symbol].get("last_size")
                if last_size:
                    self.state[symbol]["current_vol"] = float(last_size)
            
            # Check for exits on existing positions
            if self.state[symbol].get("triggered"):
                self._check_exits(symbol, symbol_state.get(symbol, {}))
            
            # Check for new entries after range is set
            elif now >= range_end:
                self._maybe_trade(symbol, symbol_state.get(symbol, {}))

    def on_bar(self, symbol: str, bar: Dict[str, float], session) -> list:
        """Backtest hook returning actions for the provided bar."""
        state = self.state.setdefault(symbol, {})
        state.setdefault("or_high", 0.0)
        state.setdefault("or_low", 0.0)
        state.setdefault("or_avg_vol", 0.0)
        state.setdefault("triggered", False)

        now = bar.get("timestamp")
        if not isinstance(now, datetime):
            now = datetime.utcnow().replace(tzinfo=timezone.utc)
        
        open_dt = _session_open(now)
        range_end = open_dt + timedelta(minutes=self.range_minutes)

        # Build opening range
        if now < range_end:
            state["or_high"] = max(state.get("or_high", 0.0), bar["high"])
            state["or_low"] = min(state.get("or_low", bar["low"]), bar["low"])
            vols = state.get("vols", [])
            vols.append(bar.get("volume", 0.0))
            state["vols"] = vols
            if vols:
                state["or_avg_vol"] = mean(vols)
            return []

        # Check exits first
        if state.get("triggered"):
            exit_actions = self._bar_check_exits(symbol, bar, state)
            if exit_actions:
                return exit_actions
            return []

        # Check for new entry
        return self._bar_actions(symbol, bar, state)

    def exit_positions(self) -> None:
        """Flatten all positions before market close."""
        LOGGER.info("ORB flattening positions before close")
        for symbol in self.universe:
            try:
                self.client.close_position(symbol)
                # Reset triggered state
                if symbol in self.state:
                    self.state[symbol]["triggered"] = False
                    self.state[symbol]["entry"] = 0.0
                    self.state[symbol]["stop"] = 0.0
                    self.state[symbol]["target"] = 0.0
            except Exception:
                LOGGER.exception("Failed to close position for %s", symbol)

    # ------------------------------------------------------------------
    # Exit monitoring
    # ------------------------------------------------------------------
    def _check_exits(self, symbol: str, state: Dict[str, float]) -> None:
        """Monitor stops and targets for open positions."""
        if not self.state[symbol].get("triggered"):
            return
        
        current_price = state.get("last_price")
        if not current_price:
            current_price = self._latest_price(symbol)
        if not current_price:
            return
        
        current_price = float(current_price)
        entry = self.state[symbol].get("entry", 0.0)
        stop = self.state[symbol].get("stop", 0.0)
        target = self.state[symbol].get("target", 0.0)
        side = self.state[symbol].get("side", "")
        
        if not entry or not stop or not target:
            return
        
        # Check stop loss
        stop_hit = False
        if side == "buy" and current_price <= stop:
            stop_hit = True
            LOGGER.warning("ORB STOP hit for %s (long): entry=%.2f stop=%.2f current=%.2f", 
                         symbol, entry, stop, current_price)
        elif side == "sell" and current_price >= stop:
            stop_hit = True
            LOGGER.warning("ORB STOP hit for %s (short): entry=%.2f stop=%.2f current=%.2f", 
                         symbol, entry, stop, current_price)
        
        # Check target
        target_hit = False
        if side == "buy" and current_price >= target:
            target_hit = True
            LOGGER.info("ORB TARGET hit for %s (long): entry=%.2f target=%.2f current=%.2f", 
                       symbol, entry, target, current_price)
        elif side == "sell" and current_price <= target:
            target_hit = True
            LOGGER.info("ORB TARGET hit for %s (short): entry=%.2f target=%.2f current=%.2f", 
                       symbol, entry, target, current_price)
        
        if stop_hit or target_hit:
            try:
                self.client.close_position(symbol)
                self.state[symbol].update({
                    "triggered": False,
                    "entry": 0.0,
                    "stop": 0.0,
                    "target": 0.0,
                    "side": "",
                })
                LOGGER.info("Successfully closed position in %s", symbol)
            except Exception:
                LOGGER.exception("Failed to close position for %s", symbol)

    def _bar_check_exits(self, symbol: str, bar: Dict[str, float], state: Dict) -> list:
        """Check exits during backtesting."""
        current_price = bar["close"]
        entry = state.get("entry", 0.0)
        stop = state.get("stop", 0.0)
        target = state.get("target", 0.0)
        side = state.get("side", "")
        
        if not entry or not stop or not target:
            return []
        
        # Check stop
        if side == "buy" and bar["low"] <= stop:
            state.update({"triggered": False, "entry": 0.0})
            return [{"side": "sell", "qty": state.get("position_qty", 1)}]
        elif side == "sell" and bar["high"] >= stop:
            state.update({"triggered": False, "entry": 0.0})
            return [{"side": "buy", "qty": state.get("position_qty", 1)}]
        
        # Check target
        if side == "buy" and bar["high"] >= target:
            state.update({"triggered": False, "entry": 0.0})
            return [{"side": "sell", "qty": state.get("position_qty", 1)}]
        elif side == "sell" and bar["low"] <= target:
            state.update({"triggered": False, "entry": 0.0})
            return [{"side": "buy", "qty": state.get("position_qty", 1)}]
        
        return []

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------
    def _maybe_reset_daily_state(self, now: datetime) -> None:
        """Reset opening range state on new trading day."""
        session_date = now.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        
        if self._last_session_date == session_date:
            return
        
        if self._last_session_date is not None:
            LOGGER.info("New trading day %s - resetting ORB state", session_date)
            for symbol in self.universe:
                # Keep triggered status but reset range
                triggered = self.state[symbol].get("triggered", False)
                self.state[symbol].update({
                    "or_high": 0.0,
                    "or_low": 0.0,
                    "or_avg_vol": 0.0,
                })
                # Reset triggered if we don't have an active position
                if triggered:
                    has_position = self._has_position(symbol)
                    if not has_position:
                        self.state[symbol]["triggered"] = False
        
        self._last_session_date = session_date

    def _has_position(self, symbol: str) -> bool:
        """Check if we currently have a position in symbol."""
        try:
            positions = self.client.list_positions()
            return any(pos.symbol.upper() == symbol for pos in positions)
        except Exception:
            LOGGER.exception("Failed to check position for %s", symbol)
            return False

    # ------------------------------------------------------------------
    # Range seeding
    # ------------------------------------------------------------------
    def _maybe_seed_range(self, symbol: str, start: datetime, end: datetime, now: datetime) -> None:
        """Fetch opening range bars and calculate high/low/avg volume."""
        if now < start:
            return
        
        # Only seed once
        if self.state[symbol]["or_high"] and self.state[symbol]["or_low"]:
            return
        
        if now < end:
            # Still building range, wait
            return

        try:
            bars = self.client.get_bars(symbol, "5Min", start=start, end=end, limit=10)
        except Exception:
            LOGGER.exception("Unable to seed OR range for %s", symbol)
            return

        highs = [float(bar.h) for bar in bars]
        lows = [float(bar.l) for bar in bars]
        vols = [float(getattr(bar, "v", 0.0)) for bar in bars]
        
        if not highs or not lows:
            LOGGER.warning("No bars found for %s opening range", symbol)
            return

        self.state[symbol]["or_high"] = max(highs)
        self.state[symbol]["or_low"] = min(lows)
        self.state[symbol]["or_avg_vol"] = mean(vols) if vols else 0.0
        
        LOGGER.info(
            "Seeded OR range for %s: high=%.2f low=%.2f avg_vol=%.0f",
            symbol,
            self.state[symbol]["or_high"],
            self.state[symbol]["or_low"],
            self.state[symbol].get("or_avg_vol", 0.0),
        )

    # ------------------------------------------------------------------
    # Entry logic
    # ------------------------------------------------------------------
    def _maybe_trade(self, symbol: str, state: Dict[str, float]) -> None:
        """Check for breakout entry with volume confirmation."""
        if self.state[symbol].get("triggered"):
            return

        price = state.get("last_price")
        if not price:
            price = self._latest_price(symbol)
        if price is None:
            return
        price = float(price)

        or_high = self.state[symbol].get("or_high", 0.0)
        or_low = self.state[symbol].get("or_low", 0.0)
        avg_vol = self.state[symbol].get("or_avg_vol", 0.0)
        
        if not or_high or not or_low:
            return

        # Get current volume from state or stream
        current_vol = self.state[symbol].get("current_vol", avg_vol)
        
        # Check breakout conditions
        long_ok = (
            price > or_high 
            and current_vol >= avg_vol * self.volume_factor 
            and self.direction in {"long", "both"}
        )
        short_ok = (
            price < or_low 
            and current_vol >= avg_vol * self.volume_factor 
            and self.direction in {"short", "both"}
        )

        if not (long_ok or short_ok):
            return

        side = "buy" if long_ok else "sell"
        range_size = max(or_high - or_low, 0.01)
        stop = or_low if long_ok else or_high
        target = (
            price + (range_size * self.target_multiple) 
            if long_ok 
            else price - (range_size * self.target_multiple)
        )
        
        qty = self._size(symbol, price, atr=range_size)
        if qty <= 0:
            LOGGER.info("ORB risk rules blocked %s trade for %s", side, symbol)
            return
        
        if self.risk_manager and not self.risk_manager.validate_order(
            symbol=symbol, side=side, price=price, qty=qty
        ):
            LOGGER.warning("Risk manager rejected %s order for %s", side, symbol)
            return

        try:
            order = self.client.submit_market_order(symbol=symbol, qty=qty, side=side)
            LOGGER.info(
                "ORB ENTRY: %s %s qty=%s entry=%.2f stop=%.2f target=%.2f range=%.2f",
                symbol, side.upper(), qty, price, stop, target, range_size
            )
            self.state[symbol].update({
                "triggered": True,
                "entry": price,
                "stop": stop,
                "target": target,
                "side": side,
                "order_id": getattr(order, "id", ""),
                "position_qty": qty,
            })
        except Exception:
            LOGGER.exception("Failed to submit ORB order for %s", symbol)

    def _size(self, symbol: str, price: float, atr: Optional[float]) -> int:
        """Calculate position size using risk manager or default."""
        if self.risk_manager:
            qty = self.risk_manager.size_order(symbol=symbol, price=price, atr=atr)
            if qty:
                return qty
        return self.qty

    def _bar_actions(self, symbol: str, bar: Dict[str, float], state: Dict[str, float]) -> list:
        """Generate entry actions during backtesting."""
        if state.get("triggered"):
            return []
        
        price = bar["close"]
        or_high = state.get("or_high", 0.0)
        or_low = state.get("or_low", 0.0)
        avg_vol = state.get("or_avg_vol", 0.0)
        vol = bar.get("volume", avg_vol)
        
        long_ok = (
            price > or_high 
            and vol >= avg_vol * self.volume_factor 
            and self.direction in {"long", "both"}
        )
        short_ok = (
            price < or_low 
            and vol >= avg_vol * self.volume_factor 
            and self.direction in {"short", "both"}
        )
        
        if not (long_ok or short_ok):
            return []
        
        side = "buy" if long_ok else "sell"
        range_size = max(or_high - or_low, 0.01)
        stop = or_low if long_ok else or_high
        target = (
            price + (range_size * self.target_multiple) 
            if long_ok 
            else price - (range_size * self.target_multiple)
        )
        
        qty = self._size(symbol, price, atr=range_size)
        if qty <= 0:
            return []
        
        state.update({
            "triggered": True,
            "entry": price,
            "stop": stop,
            "target": target,
            "side": side,
            "position_qty": qty,
        })
        return [{"side": side, "qty": qty}]

    def _latest_price(self, symbol: str) -> Optional[float]:
        """Fetch latest trade price from Alpaca."""
        try:
            trade = self.client.get_latest_trade(symbol)
            price = getattr(trade, "price", None)
            return float(price) if price is not None else None
        except Exception:
            LOGGER.debug("Failed to fetch latest trade for %s", symbol)
            return None
