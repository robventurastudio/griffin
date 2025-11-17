"""Opening Range Breakout strategy with basic risk hooks."""

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
    eastern_open = time(hour=9, minute=30)
    eastern = ZoneInfo("America/New_York")
    open_dt = datetime.combine(now.astimezone(eastern).date(), eastern_open, tzinfo=eastern)
    return open_dt.astimezone(timezone.utc)


@dataclass
class OpeningRangeBreakout:
    """Simple ORB strategy using 5m bars and optional ATR sizing."""

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
        if self.direction not in {"long", "short", "both"}:
            raise ValueError("direction must be 'long', 'short', or 'both'")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def bootstrap_state(self) -> None:
        LOGGER.info("ORB bootstrap: warming state for %s symbols", len(self.universe))
        for symbol in self.universe:
            self.state[symbol] = {
                "or_high": 0.0,
                "or_low": 0.0,
                "triggered": False,
                "entry": 0.0,
                "stop": 0.0,
                "target": 0.0,
                "side": "",
                "active": False,
                "done": False,
                "qty": 0,
            }

    # ------------------------------------------------------------------
    # Hooks used by the engine loop
    # ------------------------------------------------------------------
    def should_open(self, clock) -> bool:
        # ORB reacts via on_tick after the opening range, so we only open after range set
        return False

    def should_close(self, clock) -> bool:
        return bool((not clock.is_open))

    def on_tick(self, clock, symbol_state: Dict[str, Dict[str, float]]) -> None:
        if not clock.is_open:
            return

        now = clock.timestamp if hasattr(clock, "timestamp") else datetime.utcnow().replace(tzinfo=timezone.utc)
        open_dt = _session_open(now)
        range_end = open_dt + timedelta(minutes=self.range_minutes)
        for symbol in self.universe:
            self._maybe_seed_range(symbol, start=open_dt, end=range_end, now=now)
            if now < range_end:
                continue
            self._maybe_trade(symbol, symbol_state.get(symbol))
            self._maybe_exit(symbol, symbol_state.get(symbol))

    def on_bar(self, symbol: str, bar: Dict[str, float], session) -> list:
        """Backtest hook returning actions for the provided bar."""

        state = self.state.setdefault(symbol, {})
        state.setdefault("or_high", 0.0)
        state.setdefault("or_low", 0.0)
        state.setdefault("or_avg_vol", 0.0)
        state.setdefault("triggered", False)
        state.setdefault("active", False)
        state.setdefault("done", False)

        now = bar.get("timestamp")
        open_dt = _session_open(now if isinstance(now, datetime) else datetime.utcnow().replace(tzinfo=timezone.utc))
        range_end = open_dt + timedelta(minutes=self.range_minutes)

        if now and now < range_end:
            state["or_high"] = max(state.get("or_high", 0.0), bar["high"])
            state["or_low"] = min(state.get("or_low", bar["low"]), bar["low"])
            vols = state.get("vols", [])
            vols.append(bar.get("volume", 0.0))
            state["vols"] = vols
            if vols:
                state["or_avg_vol"] = mean(vols)
            return []

        return self._bar_actions(symbol, bar, state)

    def exit_positions(self) -> None:
        LOGGER.info("ORB flattening positions before close")
        for symbol in self.universe:
            self.client.close_position(symbol)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _maybe_seed_range(self, symbol: str, start: datetime, end: datetime, now: datetime) -> None:
        if now < start:
            return
        if now >= end and self.state[symbol]["or_high"] and self.state[symbol]["or_low"]:
            return

        try:
            bars = self.client.get_bars(symbol, "5Min", start=start, end=min(now, end))
        except Exception:  # pragma: no cover - network path
            LOGGER.exception("Unable to seed OR range for %s", symbol)
            return

        highs = [float(bar.h) for bar in bars]
        lows = [float(bar.l) for bar in bars]
        vols = [float(getattr(bar, "v", 0.0)) for bar in bars]
        if not highs or not lows:
            return

        self.state[symbol]["or_high"] = max(highs)
        self.state[symbol]["or_low"] = min(lows)
        self.state[symbol]["or_avg_vol"] = mean(vols) if vols else 0.0
        if vols:
            self.state[symbol]["last_vol"] = vols[-1]
        LOGGER.debug(
            "Seeded OR range for %s: high=%.2f low=%.2f avg_vol=%.1f",
            symbol,
            self.state[symbol]["or_high"],
            self.state[symbol]["or_low"],
            self.state[symbol].get("or_avg_vol", 0.0),
        )

    def _maybe_trade(self, symbol: str, state: Optional[Dict[str, float]]) -> None:
        if not state or state.get("done"):
            return

        price = self._latest_price(symbol)
        if price is None:
            return

        or_high = state.get("or_high", 0.0)
        or_low = state.get("or_low", 0.0)
        avg_vol = state.get("or_avg_vol", 0.0)
        if not or_high or not or_low:
            return

        last_vol = state.get("last_vol") or avg_vol
        long_ok = price > or_high and last_vol >= avg_vol * self.volume_factor and self.direction in {"long", "both"}
        short_ok = price < or_low and last_vol >= avg_vol * self.volume_factor and self.direction in {"short", "both"}

        if not (long_ok or short_ok):
            return

        side = "buy" if long_ok else "sell"
        range_size = max(or_high - or_low, 0.01)
        stop = or_low if long_ok else or_high
        target = price + (range_size * self.target_multiple) if long_ok else price - (range_size * self.target_multiple)
        qty = self._size(symbol, price, atr=range_size)
        if qty <= 0:
            LOGGER.info("ORB risk rules blocked %s trade for %s", side, symbol)
            return
        if self.risk_manager and not self.risk_manager.validate_order(symbol=symbol, side=side, price=price, qty=qty):
            return

        order = self.client.submit_market_order(symbol=symbol, qty=qty, side=side)
        LOGGER.info(
            "ORB %s %s qty=%s entry=%.2f stop=%.2f target=%.2f", symbol, side, qty, price, stop, target
        )
        state.update(
            {
                "triggered": True,
                "active": True,
                "entry": price,
                "stop": stop,
                "target": target,
                "order_id": getattr(order, "id", ""),
                "side": side,
                "qty": qty,
            }
        )

    def _size(self, symbol: str, price: float, atr: Optional[float]) -> int:
        if self.risk_manager:
            qty = self.risk_manager.size_order(symbol=symbol, price=price, atr=atr)
            if qty:
                return qty
        return self.qty

    def _bar_actions(self, symbol: str, bar: Dict[str, float], state: Dict[str, float]) -> list:
        price = bar["close"]
        if state.get("done"):
            return []
        if state.get("active"):
            return self._bar_exit_actions(symbol, price, state)
        if state.get("triggered"):
            return []
        or_high = state.get("or_high", 0.0)
        or_low = state.get("or_low", 0.0)
        avg_vol = state.get("or_avg_vol", 0.0)
        vol = bar.get("volume", avg_vol)
        long_ok = price > or_high and vol >= avg_vol * self.volume_factor and self.direction in {"long", "both"}
        short_ok = price < or_low and vol >= avg_vol * self.volume_factor and self.direction in {"short", "both"}
        if not (long_ok or short_ok):
            return []
        side = "buy" if long_ok else "sell"
        range_size = max(or_high - or_low, 0.01)
        qty = self._size(symbol, price, atr=range_size)
        if qty <= 0:
            return []
        state.update(
            {
                "triggered": True,
                "active": True,
                "entry": price,
                "side": side,
                "stop": or_low if long_ok else or_high,
                "target": price + (range_size * self.target_multiple) if long_ok else price - (range_size * self.target_multiple),
                "qty": qty,
            }
        )
        return [{"side": side, "qty": qty}]

    def _bar_exit_actions(self, symbol: str, price: float, state: Dict[str, float]) -> list:
        side = state.get("side", "")
        stop = state.get("stop")
        target = state.get("target")
        if not side or stop is None or target is None:
            return []
        hit_stop = (side == "buy" and price <= stop) or (side == "sell" and price >= stop)
        hit_target = (side == "buy" and price >= target) or (side == "sell" and price <= target)
        if not (hit_stop or hit_target):
            return []
        state.update({"active": False, "done": True})
        exit_side = "sell" if side == "buy" else "buy"
        return [{"side": exit_side, "qty": int(state.get("qty", 0) or 0) or 1}]

    def _latest_price(self, symbol: str) -> Optional[float]:
        try:
            trade = self.client.get_latest_trade(symbol)
            price = getattr(trade, "price", None)
            return float(price) if price is not None else None
        except Exception:  # pragma: no cover - network
            LOGGER.exception("Failed to fetch latest trade for %s", symbol)
            return None

    def _maybe_exit(self, symbol: str, state: Optional[Dict[str, float]]) -> None:
        if not state or not state.get("active"):
            return
        price = state.get("last_price") or self._latest_price(symbol)
        if price is None:
            return
        stop = state.get("stop")
        target = state.get("target")
        side = state.get("side")
        if stop is None or target is None or not side:
            return
        hit_stop = (side == "buy" and price <= stop) or (side == "sell" and price >= stop)
        hit_target = (side == "buy" and price >= target) or (side == "sell" and price <= target)
        if not (hit_stop or hit_target):
            return
        exit_side = "sell" if side == "buy" else "buy"
        LOGGER.info(
            "ORB exit %s via %s at %.2f (stop=%.2f target=%.2f)",
            symbol,
            "stop" if hit_stop else "target",
            price,
            stop,
            target,
        )
        exit_qty = int(state.get("qty", 0) or self.qty)
        self.client.submit_market_order(symbol=symbol, qty=exit_qty, side=exit_side)
        self.state[symbol].update({"active": False, "done": True})
