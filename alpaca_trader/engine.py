"""Engine helpers with timezone-safe formatting and a minimal trading loop."""
from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Optional

from dateutil import parser


LOG = logging.getLogger(__name__)


@dataclass
class OrderRequest:
    symbol: str
    qty: int
    side: str  # "buy" or "sell"


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


class OpenCloseMarketStrategy:
    """Very simple strategy that buys at open and exits near close."""

    def __init__(
        self,
        symbols: Iterable[str],
        position_sizer: PositionSizer,
        close_buffer_minutes: int = 10,
    ) -> None:
        self.symbols = list(symbols)
        self.position_sizer = position_sizer
        self.close_buffer = dt.timedelta(minutes=close_buffer_minutes)

    def plan_orders(
        self,
        *,
        now: dt.datetime,
        next_close: dt.datetime,
        positions: Dict[str, int],
        prices: Dict[str, float],
        equity: float,
    ) -> list[OrderRequest]:
        orders: list[OrderRequest] = []

        # If we are approaching the close window, exit any positions.
        if next_close - now <= self.close_buffer:
            for symbol, qty in positions.items():
                if qty > 0:
                    orders.append(OrderRequest(symbol=symbol, qty=qty, side="sell"))
            return orders

        # Otherwise open positions we don't already hold.
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


# Time helpers -------------------------------------------------------------

def _ensure_timezone(dt_obj: dt.datetime) -> dt.datetime:
    if dt_obj.tzinfo is None:
        return dt_obj.replace(tzinfo=dt.timezone.utc)
    return dt_obj.astimezone(dt.timezone.utc)


def _ensure_datetime(value: Any) -> dt.datetime:
    if value is None:
        raise ValueError("Cannot format a missing datetime value")

    if isinstance(value, str):
        value = parser.isoparse(value)

    to_py_dt = getattr(value, "to_pydatetime", None)
    if callable(to_py_dt):
        value = to_py_dt()

    if not isinstance(value, dt.datetime):
        raise TypeError(f"Unsupported datetime type: {type(value)}")

    return _ensure_timezone(value)


def fmt_datetime(value: Any) -> str:
    normalized = _ensure_datetime(value)
    return normalized.strftime("%Y-%m-%d %H:%M:%S %Z")


def _get_clock_field(clock: Any, field: str) -> Any:
    if clock is None:
        return None
    if isinstance(clock, dict):
        return clock.get(field)
    return getattr(clock, field, None)


def log_clock(clock: Optional[dict]) -> str:
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


def run_trading_session(
    *,
    symbols: list[str],
    base_qty: int,
    per_trade_risk_pct: float,
    max_daily_loss_pct: float,
    close_buffer_minutes: int,
    poll_seconds: int,
    client=None,
    sleep_fn: Callable[[float], None] = time.sleep,
    max_cycles: int | None = None,
) -> None:
    """Run a simple open/close trading loop with paper orders."""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    from alpaca_trader.alpaca_client import AlpacaClient  # imported lazily

    client = client or AlpacaClient()
    account = client.get_account()
    starting_equity = float(getattr(account, "equity", 0.0))
    risk_limits = RiskLimits(
        max_daily_loss_pct=max_daily_loss_pct, per_trade_risk_pct=per_trade_risk_pct
    )
    risk = RiskManager(starting_equity, risk_limits)
    sizer = PositionSizer(risk_limits, base_qty)
    strategy = OpenCloseMarketStrategy(symbols, sizer, close_buffer_minutes)

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
        now = _ensure_datetime(getattr(clock, "timestamp", None))
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
        prices = _latest_prices(client, symbols)

        orders = strategy.plan_orders(
            now=now,
            next_close=next_close,
            positions=positions,
            prices=prices,
            equity=equity,
        )

        for order in orders:
            try:
                LOG.info("Submitting %s %s x%s", order.side, order.symbol, order.qty)
                client.submit_order(order.symbol, order.qty, order.side)
            except Exception:
                LOG.exception("Failed to submit order for %s", order.symbol)

        sleep_fn(poll_seconds)
