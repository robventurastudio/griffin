"""Engine helpers with timezone-safe formatting and a minimal trading loop."""
from __future__ import annotations

import datetime as dt
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from dateutil import parser


LOG = logging.getLogger(__name__)


@dataclass
class OrderRequest:
    symbol: str
    qty: int
    side: str  # "buy" or "sell"


@dataclass
class BarData:
    price: float
    volume: float | None = None


@dataclass
class RiskLimits:
    max_daily_loss_pct: float = 5.0
    per_trade_risk_pct: float = 1.0


class RiskManager:
    def __init__(self, starting_equity: float, limits: RiskLimits) -> None:
        self.starting_equity = starting_equity
        self.limits = limits

    @property
    def max_daily_loss(self) -> float:
        return self.starting_equity * (self.limits.max_daily_loss_pct / 100.0)

    def breached(self, current_equity: float) -> bool:
        return current_equity <= self.starting_equity - self.max_daily_loss


class PositionSizer:
    def __init__(self, limits: RiskLimits, base_qty: int) -> None:
        self.limits = limits
        self.base_qty = base_qty

    def size_for_price(self, equity: float, price: float) -> int:
        if price <= 0:
            return 0
        dollars = equity * (self.limits.per_trade_risk_pct / 100.0)
        sized_qty = max(self.base_qty, int(dollars // price))
        return max(1, sized_qty)


class _PriceHistory:
    """Lightweight rolling price store for intraday calculations."""

    def __init__(self, maxlen: int = 120) -> None:
        self.maxlen = maxlen
        self._prices: dict[str, deque[float]] = {}

    def add_price(self, symbol: str, price: float) -> None:
        if symbol not in self._prices:
            self._prices[symbol] = deque(maxlen=self.maxlen)
        self._prices[symbol].append(price)

    def stats(self, symbol: str) -> tuple[Optional[float], Optional[float], int]:
        prices = self._prices.get(symbol)
        if not prices:
            return None, None, 0

        count = len(prices)
        mean = sum(prices) / count
        variance = sum((p - mean) ** 2 for p in prices) / max(1, count - 1)
        std = variance**0.5
        return mean, std, count


class _RollingVWAP:
    """Rolling VWAP tracker with weighted variance for z-score logic."""

    def __init__(self, maxlen: int = 120) -> None:
        self.maxlen = maxlen
        self._bars: dict[str, deque[tuple[float, float]]] = {}
        self._sum_price_volume: dict[str, float] = {}
        self._sum_volume: dict[str, float] = {}
        self._sum_price2_volume: dict[str, float] = {}

    def add_bar(self, symbol: str, price: float, volume: float) -> None:
        if volume is None or volume <= 0:
            # Fall back to a unit volume so the bar still contributes.
            volume = 1.0

        if symbol not in self._bars:
            self._bars[symbol] = deque(maxlen=self.maxlen)
            self._sum_price_volume[symbol] = 0.0
            self._sum_volume[symbol] = 0.0
            self._sum_price2_volume[symbol] = 0.0

        buf = self._bars[symbol]
        if len(buf) == buf.maxlen:
            old_price, old_vol = buf[0]
            self._sum_price_volume[symbol] -= old_price * old_vol
            self._sum_volume[symbol] -= old_vol
            self._sum_price2_volume[symbol] -= (old_price**2) * old_vol

        buf.append((price, volume))
        self._sum_price_volume[symbol] += price * volume
        self._sum_volume[symbol] += volume
        self._sum_price2_volume[symbol] += (price**2) * volume

    def stats(self, symbol: str) -> tuple[Optional[float], Optional[float], int]:
        buf = self._bars.get(symbol)
        if not buf:
            return None, None, 0

        total_vol = self._sum_volume.get(symbol, 0.0)
        if total_vol <= 0:
            return None, None, len(buf)

        vwap = self._sum_price_volume[symbol] / total_vol
        mean_sq = self._sum_price2_volume[symbol] / total_vol
        variance = max(0.0, mean_sq - vwap**2)
        std = variance**0.5
        return vwap, std, len(buf)


class _BaseTimedStrategy:
    """Shared helpers for strategies that must flatten near the close."""

    def __init__(self, close_buffer_minutes: int) -> None:
        self.close_buffer = dt.timedelta(minutes=close_buffer_minutes)

    def _exit_near_close(
        self, *, now: dt.datetime, next_close: dt.datetime, positions: dict[str, int]
    ) -> list[OrderRequest]:
        if next_close - now > self.close_buffer:
            return []
        return [
            OrderRequest(symbol=symbol, qty=qty, side="sell")
            for symbol, qty in positions.items()
            if qty > 0
        ]


class OpenCloseMarketStrategy(_BaseTimedStrategy):
    """Very simple strategy that buys at open and exits near close."""

    def __init__(
        self,
        symbols: Iterable[str],
        position_sizer: PositionSizer,
        close_buffer_minutes: int = 10,
    ) -> None:
        super().__init__(close_buffer_minutes)
        self.symbols = list(symbols)
        self.position_sizer = position_sizer

    def plan_orders(
        self,
        *,
        now: dt.datetime,
        next_close: dt.datetime,
        positions: dict[str, int],
        prices: dict[str, float],
        equity: float,
        bars: dict[str, BarData] | None = None,
    ) -> list[OrderRequest]:
        close_exits = self._exit_near_close(
            now=now, next_close=next_close, positions=positions
        )
        if close_exits:
            return close_exits

        orders: list[OrderRequest] = []
        for symbol in self.symbols:
            if positions.get(symbol, 0) > 0:
                continue
            price = prices.get(symbol)
            if price is None:
                continue
            qty = self.position_sizer.size_for_price(equity, price)
            if qty > 0:
                orders.append(OrderRequest(symbol=symbol, qty=qty, side="buy"))

        return orders


class VwapReversionStrategy(_BaseTimedStrategy):
    """Mean reversion toward a rolling VWAP-like anchor (price-only fallback)."""

    def __init__(
        self,
        symbols: Iterable[str],
        position_sizer: PositionSizer,
        close_buffer_minutes: int = 10,
        z_threshold: float = 2.0,
        min_history: int = 5,
        history_window: int = 60,
    ) -> None:
        super().__init__(close_buffer_minutes)
        self.symbols = list(symbols)
        self.position_sizer = position_sizer
        self.z_threshold = z_threshold
        self.min_history = min_history
        self.price_history = _PriceHistory(maxlen=history_window)
        self.vwap_history = _RollingVWAP(maxlen=history_window)

    def _should_enter_long(self, price: float, mean: float, std: float) -> bool:
        if std is None or std == 0:
            return False
        return price <= mean - self.z_threshold * std

    def plan_orders(
        self,
        *,
        now: dt.datetime,
        next_close: dt.datetime,
        positions: dict[str, int],
        prices: dict[str, float],
        equity: float,
        bars: dict[str, BarData] | None = None,
    ) -> list[OrderRequest]:
        close_exits = self._exit_near_close(
            now=now, next_close=next_close, positions=positions
        )
        if close_exits:
            return close_exits

        orders: list[OrderRequest] = []

        for symbol in self.symbols:
            bar = (bars or {}).get(symbol)
            price = prices.get(symbol) if bar is None else bar.price
            if price is None:
                continue

            if bar and bar.volume is not None:
                self.vwap_history.add_bar(symbol, price, bar.volume)
                mean, std, count = self.vwap_history.stats(symbol)
            else:
                self.price_history.add_price(symbol, price)
                mean, std, count = self.price_history.stats(symbol)
            if count < self.min_history or mean is None:
                continue

            held_qty = positions.get(symbol, 0)

            # Exit when price snaps back to the anchor.
            if held_qty > 0 and price >= mean:
                orders.append(OrderRequest(symbol=symbol, qty=held_qty, side="sell"))
                continue

            if held_qty == 0 and self._should_enter_long(price, mean, std):
                qty = self.position_sizer.size_for_price(equity, price)
                if qty > 0:
                    orders.append(OrderRequest(symbol=symbol, qty=qty, side="buy"))

        return orders


class OpeningRangeBreakoutStrategy(_BaseTimedStrategy):
    """Opening range breakout with a configurable window and buffer."""

    def __init__(
        self,
        symbols: Iterable[str],
        position_sizer: PositionSizer,
        close_buffer_minutes: int = 10,
        range_minutes: int = 30,
        breakout_buffer: float = 0.001,
    ) -> None:
        super().__init__(close_buffer_minutes)
        self.symbols = list(symbols)
        self.position_sizer = position_sizer
        self.range_minutes = range_minutes
        self.breakout_buffer = breakout_buffer
        self._session_date: dt.date | None = None
        self._range_end: dt.datetime | None = None
        self._range_high: dict[str, float] = {}
        self._range_low: dict[str, float] = {}

    def _reset_session(self, now: dt.datetime) -> None:
        self._session_date = now.date()
        self._range_end = now + dt.timedelta(minutes=self.range_minutes)
        self._range_high = {}
        self._range_low = {}

    def _record_opening_range(self, symbol: str, price: float) -> None:
        high = self._range_high.get(symbol)
        low = self._range_low.get(symbol)
        self._range_high[symbol] = price if high is None else max(high, price)
        self._range_low[symbol] = price if low is None else min(low, price)

    def _opening_range_built(self, now: dt.datetime) -> bool:
        return self._range_end is not None and now > self._range_end

    def plan_orders(
        self,
        *,
        now: dt.datetime,
        next_close: dt.datetime,
        positions: dict[str, int],
        prices: dict[str, float],
        equity: float,
        bars: dict[str, BarData] | None = None,
    ) -> list[OrderRequest]:
        close_exits = self._exit_near_close(
            now=now, next_close=next_close, positions=positions
        )
        if close_exits:
            return close_exits

        if self._session_date != now.date() or self._range_end is None:
            self._reset_session(now)

        building_range = not self._opening_range_built(now)
        if building_range:
            for symbol in self.symbols:
                price = prices.get(symbol)
                if price is not None:
                    self._record_opening_range(symbol, price)
            return []

        orders: list[OrderRequest] = []
        for symbol in self.symbols:
            price = prices.get(symbol)
            if price is None:
                continue

            range_high = self._range_high.get(symbol)
            range_low = self._range_low.get(symbol)
            if range_high is None or range_low is None:
                continue

            held_qty = positions.get(symbol, 0)

            if held_qty > 0 and price <= range_low:
                orders.append(OrderRequest(symbol=symbol, qty=held_qty, side="sell"))
                continue

            breakout_price = range_high * (1 + self.breakout_buffer)
            if held_qty == 0 and price >= breakout_price:
                qty = self.position_sizer.size_for_price(equity, price)
                if qty > 0:
                    orders.append(OrderRequest(symbol=symbol, qty=qty, side="buy"))

        return orders


class EmaPullbackStrategy(_BaseTimedStrategy):
    """Ride an intraday trend using a dual-EMA bias and pullback entries."""

    def __init__(
        self,
        symbols: Iterable[str],
        position_sizer: PositionSizer,
        close_buffer_minutes: int = 10,
        fast_span: int = 12,
        slow_span: int = 26,
        pullback_buffer: float = 0.001,
    ) -> None:
        super().__init__(close_buffer_minutes)
        self.symbols = list(symbols)
        self.position_sizer = position_sizer
        self.fast_span = max(2, fast_span)
        self.slow_span = max(3, slow_span)
        self.pullback_buffer = pullback_buffer
        self._ema_fast: dict[str, float] = {}
        self._ema_slow: dict[str, float] = {}

    @staticmethod
    def _update_ema(prev: Optional[float], price: float, span: int) -> float:
        alpha = 2 / (span + 1)
        if prev is None:
            return price
        return alpha * price + (1 - alpha) * prev

    def _record_price(self, symbol: str, price: float) -> None:
        self._ema_fast[symbol] = self._update_ema(
            self._ema_fast.get(symbol), price, self.fast_span
        )
        self._ema_slow[symbol] = self._update_ema(
            self._ema_slow.get(symbol), price, self.slow_span
        )

    def plan_orders(
        self,
        *,
        now: dt.datetime,
        next_close: dt.datetime,
        positions: dict[str, int],
        prices: dict[str, float],
        equity: float,
        bars: dict[str, BarData] | None = None,
    ) -> list[OrderRequest]:
        close_exits = self._exit_near_close(
            now=now, next_close=next_close, positions=positions
        )
        if close_exits:
            return close_exits

        orders: list[OrderRequest] = []

        for symbol in self.symbols:
            price = prices.get(symbol)
            if price is None:
                continue

            self._record_price(symbol, price)
            ema_fast = self._ema_fast.get(symbol)
            ema_slow = self._ema_slow.get(symbol)
            if ema_fast is None or ema_slow is None:
                continue

            held_qty = positions.get(symbol, 0)

            # Exit if the trend fails.
            if held_qty > 0 and price < ema_slow:
                orders.append(OrderRequest(symbol=symbol, qty=held_qty, side="sell"))
                continue

            bias_long = ema_fast > ema_slow and price >= ema_slow
            pulled_back = price <= ema_fast * (1 + self.pullback_buffer)
            if held_qty == 0 and bias_long and pulled_back:
                qty = self.position_sizer.size_for_price(equity, price)
                if qty > 0:
                    orders.append(OrderRequest(symbol=symbol, qty=qty, side="buy"))

        return orders


# Time helpers -------------------------------------------------------------

def _ensure_timezone(dt_obj: dt.datetime) -> dt.datetime:
    """Ensure the datetime is timezone-aware in UTC."""

    if dt_obj.tzinfo is None:
        return dt_obj.replace(tzinfo=dt.timezone.utc)
    return dt_obj.astimezone(dt.timezone.utc)


def _ensure_datetime(value: Any) -> dt.datetime:
    """Convert value to a timezone-aware UTC datetime.

    Handles standard ``datetime`` instances, ISO8601 strings, and pandas
    ``Timestamp`` objects without triggering the pandas ``tz_convert`` bug
    encountered when ``astimezone`` was called with no arguments.
    """

    if value is None:
        raise ValueError("Cannot format a missing datetime value")

    if isinstance(value, str):
        value = parser.isoparse(value)

    # pandas.Timestamp implements ``to_pydatetime``; convert early to avoid
    # invoking its ``astimezone`` override which raised during the dry run.
    to_py_dt = getattr(value, "to_pydatetime", None)
    if callable(to_py_dt):
        value = to_py_dt()

    if not isinstance(value, dt.datetime):
        raise TypeError(f"Unsupported datetime type: {type(value)}")

    return _ensure_timezone(value)


def fmt_datetime(value: Any) -> str:
    """Format datetime-like values safely for logs."""

    normalized = _ensure_datetime(value)
    return normalized.strftime("%Y-%m-%d %H:%M:%S %Z")


def _get_clock_field(clock: Any, field: str) -> Any:
    if clock is None:
        return None
    if isinstance(clock, dict):
        return clock.get(field)
    return getattr(clock, field, None)


def log_clock(clock: Optional[dict]) -> str:
    """Render a stable clock status message for Alpaca `Clock` payloads."""

    if not clock:
        return "(clock unavailable)"

    timestamp = (
        _get_clock_field(clock, "timestamp")
        or _get_clock_field(clock, "next_open")
        or _get_clock_field(clock, "next_close")
    )
    is_open = _get_clock_field(clock, "is_open")
    next_open = _get_clock_field(clock, "next_open")
    next_close = _get_clock_field(clock, "next_close")

    parts = [f"now={fmt_datetime(timestamp)}"]
    if next_open:
        parts.append(f"next_open={fmt_datetime(next_open)}")
    if next_close:
        parts.append(f"next_close={fmt_datetime(next_close)}")

    return f"Market clock ({'open' if is_open else 'closed'}): " + " | ".join(parts)


# Trading loop -------------------------------------------------------------

def _positions_as_qty_map(positions: Iterable[Any]) -> dict[str, int]:
    qtys: dict[str, int] = {}
    for pos in positions:
        qtys[pos.symbol] = int(getattr(pos, "qty", 0))
    return qtys


def _latest_prices(client: Any, symbols: Iterable[str]) -> dict[str, float]:
    prices: dict[str, float] = {}
    for symbol in symbols:
        try:
            bar = client.latest_bar(symbol)
            price = getattr(bar, "c", None) or getattr(bar, "close", None)
            if price is not None:
                prices[symbol] = float(price)
        except Exception:
            LOG.exception("Failed to fetch latest bar for %s", symbol)
    return prices


def _latest_bars(client: Any, symbols: Iterable[str]) -> dict[str, BarData]:
    bars: dict[str, BarData] = {}
    for symbol in symbols:
        try:
            bar = client.latest_bar(symbol)
            price = getattr(bar, "c", None) or getattr(bar, "close", None)
            volume = getattr(bar, "v", None) or getattr(bar, "volume", None)
            if price is not None:
                bars[symbol] = BarData(price=float(price), volume=None if volume is None else float(volume))
        except Exception:
            LOG.exception("Failed to fetch latest bar for %s", symbol)
    return bars


def run_open_close_loop(
    *,
    poll_seconds: int | None = None,
    universe=None,
    qty=None,
    direction=None,
    strategy=None,
    data=None,
    risk_limits=None,
    client=None,
    **_: Any,
) -> None:
    """Continuously log Alpaca market clock status with safe timezone handling."""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    from alpaca_trader.alpaca_client import AlpacaClient  # imported lazily

    poll_seconds = poll_seconds or 30
    client = client or AlpacaClient()
    LOG.info("Starting open/close loop (poll=%ss)", poll_seconds)

    try:
        while True:
            try:
                clock = client.get_clock()
                LOG.info(log_clock(clock))
            except Exception:
                LOG.exception("Failed to fetch or format market clock")

            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        LOG.info("Open/close loop interrupted by user; exiting cleanly")


def _build_strategy(
    name: str,
    symbols: list[str],
    sizer: PositionSizer,
    close_buffer_minutes: int,
    strategy_config: Optional[dict[str, Any]] = None,
):
    config = strategy_config or {}
    if name == "orb":
        return OpeningRangeBreakoutStrategy(
            symbols,
            sizer,
            close_buffer_minutes=close_buffer_minutes,
            range_minutes=int(config.get("range_minutes", 30)),
            breakout_buffer=float(config.get("breakout_buffer", 0.001)),
        )
    if name == "open-close":
        return OpenCloseMarketStrategy(symbols, sizer, close_buffer_minutes)
    if name == "vwap-reversion":
        return VwapReversionStrategy(
            symbols,
            sizer,
            close_buffer_minutes=close_buffer_minutes,
            z_threshold=float(config.get("z_threshold", 2.0)),
            min_history=int(config.get("min_history", 5)),
            history_window=int(config.get("history_window", 60)),
        )
    if name == "ema-pullback":
        return EmaPullbackStrategy(
            symbols,
            sizer,
            close_buffer_minutes=close_buffer_minutes,
            fast_span=int(config.get("fast_span", 12)),
            slow_span=int(config.get("slow_span", 26)),
            pullback_buffer=float(config.get("pullback_buffer", 0.001)),
        )
    raise ValueError(
        "Unknown strategy '{name}'. Supported: orb, open-close, vwap-reversion, ema-pullback".format(
            name=name
        )
    )


def run_trading_session(
    *,
    symbols: list[str],
    base_qty: int,
    per_trade_risk_pct: float,
    max_daily_loss_pct: float,
    close_buffer_minutes: int,
    poll_seconds: int,
    client=None,
    strategy_name: str = "open-close",
    strategy_config: Optional[dict[str, Any]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    max_cycles: int | None = None,
) -> None:
    """Run a simple trading loop with pluggable strategies and mode logging."""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    from alpaca_trader.alpaca_client import AlpacaClient  # imported lazily

    client = client or AlpacaClient()
    base_url = getattr(client, "base_url", "(unknown)")
    trading_mode = getattr(client, "trading_mode", "(unknown)")
    LOG.info("Alpaca endpoint: %s (%s trading)", base_url, trading_mode)
    LOG.info("Strategy: %s", strategy_name)
    account = client.get_account()
    starting_equity = float(getattr(account, "equity", 0.0))
    risk_limits = RiskLimits(
        max_daily_loss_pct=max_daily_loss_pct, per_trade_risk_pct=per_trade_risk_pct
    )
    risk = RiskManager(starting_equity, risk_limits)
    sizer = PositionSizer(risk_limits, base_qty)
    strategy = _build_strategy(
        strategy_name, symbols, sizer, close_buffer_minutes, strategy_config
    )

    LOG.info(
        "Trading session: symbols=%s base_qty=%s risk_per_trade=%.2f%% loss_limit=%.2f%%",
        ",".join(symbols),
        base_qty,
        per_trade_risk_pct,
        max_daily_loss_pct,
    )

    cycles = 0
    while True:
        if max_cycles is not None and cycles >= max_cycles:
            LOG.info("Reached max cycles; exiting trading loop")
            return
        cycles += 1

        clock = client.get_clock()
        LOG.info(log_clock(clock))
        now = _ensure_datetime(_get_clock_field(clock, "timestamp"))
        next_open = _ensure_datetime(_get_clock_field(clock, "next_open"))
        next_close = _ensure_datetime(_get_clock_field(clock, "next_close"))
        is_open = bool(_get_clock_field(clock, "is_open"))

        if not is_open:
            wait_seconds = max(5, (next_open - now).total_seconds())
            LOG.info("Market closed. Sleeping until next check (%.0fs)", wait_seconds)
            sleep_fn(min(wait_seconds, poll_seconds))
            continue

        account = client.get_account()
        equity = float(getattr(account, "equity", 0.0))
        if risk.breached(equity):
            LOG.warning(
                "Daily loss limit reached (equity=%.2f, start=%.2f). Closing positions.",
                equity,
                starting_equity,
            )
            try:
                client.close_all_positions()
            except Exception:
                LOG.exception("Failed to close all positions during risk stop")
            return

        positions = _positions_as_qty_map(client.list_positions())
        bars = _latest_bars(client, symbols)
        prices = {sym: data.price for sym, data in bars.items()}

        orders = strategy.plan_orders(
            now=now,
            next_close=next_close,
            positions=positions,
            prices=prices,
            equity=equity,
            bars=bars,
        )

        for order in orders:
            try:
                LOG.info("Submitting %s %s x%s", order.side, order.symbol, order.qty)
                client.submit_order(order.symbol, order.qty, order.side)
            except Exception:
                LOG.exception("Failed to submit order for %s", order.symbol)

        sleep_fn(poll_seconds)
