"""Trading engine with enhanced error handling and state management."""

from __future__ import annotations

import importlib
import logging
import signal
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Type

from .alpaca_client import AlpacaClient
from .config import POLL_INTERVAL_SECONDS, TRADER_QTY
from .data_stream import LiveTickerFeed
from .logging_setup import append_pnl_log
from .risk_manager import RiskLimits, RiskManager
from .strategies.open_close_dummy import OpenCloseStrategy
from .universe import DEFAULT_UNIVERSE

LOGGER = logging.getLogger(__name__)


@contextmanager
def _graceful_shutdown():
    """Allow Ctrl+C to terminate the loop cleanly."""
    shutdown = {"value": False}

    def _handler(signum, frame):
        LOGGER.info("Received signal %s, shutting down gracefully", signum)
        shutdown["value"] = True

    original = signal.signal(signal.SIGINT, _handler)
    try:
        yield shutdown
    finally:
        signal.signal(signal.SIGINT, original)


def run_open_close_loop(
    *,
    universe: Optional[Iterable[str]] = None,
    qty: Optional[int] = None,
    poll_interval: Optional[int] = None,
    client: Optional[AlpacaClient] = None,
    strategy_path: Optional[str] = None,
    enable_data_stream: bool = True,
    data_feed: str = "iex",
    strategy_kwargs: Optional[dict] = None,
    risk_limits: Optional[RiskLimits] = None,
    risk_manager: Optional[RiskManager] = None,
) -> None:
    """Continuously monitor Alpaca clock and execute strategy."""
    
    # Initialize components
    trading_client = client or AlpacaClient()
    rm = risk_manager or RiskManager(trading_client, limits=risk_limits)
    strategy_cls = _resolve_strategy(strategy_path)
    strategy_options = dict(strategy_kwargs or {})
    
    strategy = strategy_cls(
        trading_client,
        universe=list(universe or DEFAULT_UNIVERSE),
        qty=qty or TRADER_QTY,
        risk_manager=rm,
        **strategy_options,
    )
    
    # Bootstrap strategy state
    try:
        strategy.bootstrap_state()
    except Exception:
        LOGGER.exception("Failed to bootstrap strategy state")
        raise

    # Start data stream if enabled
    feed = _maybe_start_data_stream(strategy, enable_data_stream, data_feed)

    interval = poll_interval or POLL_INTERVAL_SECONDS
    LOGGER.info(
        "Starting %s with %s symbols (qty=%s, poll=%ss, data=%s)",
        strategy.__class__.__name__,
        len(strategy.universe),
        strategy.qty,
        interval,
        "on" if feed else "off",
    )

    # Backoff parameters
    failure_sleep = 5
    max_backoff = max(interval, 60)
    consecutive_failures = 0
    last_pnl_date = None
    last_cache_invalidation = datetime.utcnow()

    with _graceful_shutdown() as shutdown:
        while not shutdown["value"]:
            try:
                # Fetch market clock
                clock = trading_client.get_clock()
                _log_clock_countdown(clock)
                
                # Log data stream snapshot
                _log_data_snapshot(feed)
                
                # Record daily PnL
                last_pnl_date = _maybe_record_pnl(trading_client, last_pnl_date)
                
                # Invalidate risk manager position cache periodically
                now = datetime.utcnow()
                if (now - last_cache_invalidation).seconds > 60:
                    rm.invalidate_cache()
                    last_cache_invalidation = now

                # Check risk lockout
                if rm.daily_loss_exceeded():
                    LOGGER.warning(
                        "Risk lockout active; pausing submissions until recovery"
                    )
                    time.sleep(interval)
                    continue

                # Build current symbol state
                symbol_state = _build_symbol_state(
                    trading_client, strategy.universe, feed
                )

                # Let strategy process tick (for continuous monitoring)
                if hasattr(strategy, "on_tick"):
                    try:
                        strategy.on_tick(clock, symbol_state)
                    except Exception:
                        LOGGER.exception("Error in strategy on_tick")

                # Check for open signal
                if strategy.should_open(clock):
                    LOGGER.info("=== OPENING SIGNAL ===")
                    try:
                        trading_client.cancel_all_orders()
                        strategy.enter_positions()
                        # Invalidate cache after position changes
                        rm.invalidate_cache()
                    except Exception:
                        LOGGER.exception("Failed to enter positions")
                
                # Check for close signal
                elif strategy.should_close(clock):
                    LOGGER.info("=== CLOSING SIGNAL ===")
                    try:
                        strategy.exit_positions()
                        # Invalidate cache after position changes
                        rm.invalidate_cache()
                    except Exception:
                        LOGGER.exception("Failed to exit positions")

                # Reset failure counter on success
                consecutive_failures = 0
                failure_sleep = 5
                
                time.sleep(interval)
                
            except KeyboardInterrupt:
                LOGGER.info("Keyboard interrupt received")
                break
                
            except Exception:
                consecutive_failures += 1
                LOGGER.exception(
                    "Engine loop failure #%d; backing off for %ss",
                    consecutive_failures,
                    failure_sleep,
                )
                
                # Exponential backoff with max
                time.sleep(failure_sleep)
                failure_sleep = min(failure_sleep * 2, max_backoff)
                
                # Exit after too many consecutive failures
                if consecutive_failures >= 10:
                    LOGGER.error(
                        "Too many consecutive failures (%d); exiting",
                        consecutive_failures,
                    )
                    break

    # Cleanup
    if feed:
        LOGGER.info("Stopping data stream")
        feed.stop()

    LOGGER.info("Trading loop exited cleanly")


def _resolve_strategy(strategy_path: Optional[str]) -> Type:
    """Load strategy class from module path."""
    if not strategy_path:
        return OpenCloseStrategy

    module_path, sep, class_name = strategy_path.partition(":")
    if not sep:
        raise ValueError(
            "Strategy path must be in format 'module.submodule:ClassName'"
        )
    
    try:
        module = importlib.import_module(module_path)
        return getattr(module, class_name)
    except (ImportError, AttributeError) as exc:
        raise ImportError(
            f"Unable to load strategy '{class_name}' from '{module_path}'"
        ) from exc


def _maybe_start_data_stream(
    strategy, enable_stream: bool, data_feed: str
) -> Optional[LiveTickerFeed]:
    """Initialize and start the live data stream."""
    if not enable_stream:
        LOGGER.info("Data stream disabled")
        return None

    try:
        feed = LiveTickerFeed(symbols=strategy.universe, data_feed=data_feed)
        feed.start()
        LOGGER.info("Data stream started successfully")
        return feed
    except Exception:
        LOGGER.exception("Failed to start live ticker feed; continuing without it")
        return None


def _log_clock_countdown(clock) -> None:
    """Log market status and countdown to next event."""
    now = _ensure_datetime(clock.timestamp)
    next_open = _ensure_datetime(clock.next_open)
    next_close = _ensure_datetime(clock.next_close)

    if clock.is_open:
        event = "close"
        target = next_close
    else:
        event = "open"
        target = next_open

    delta = max(target - now, timedelta())
    LOGGER.info(
        "Market %s | now=%s | next %s in %s (at %s)",
        "OPEN" if clock.is_open else "CLOSED",
        _fmt_datetime(now),
        event,
        _format_delta(delta),
        _fmt_datetime(target),
    )


def _log_data_snapshot(feed: Optional[LiveTickerFeed]) -> None:
    """Log recent ticker data if stream is active."""
    if not feed:
        return

    snapshot = feed.snapshot(limit=5)
    if snapshot:
        LOGGER.info("Live tickers: %s", feed.format_snapshot(limit=5))


def _ensure_datetime(value) -> datetime:
    """Convert various timestamp formats to timezone-aware datetime."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if hasattr(value, "to_pydatetime"):
        dt = value.to_pydatetime()
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    raise TypeError(f"Unsupported clock timestamp type: {type(value)!r}")


def _fmt_datetime(value: datetime) -> str:
    """Format datetime for logging."""
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _format_delta(delta: timedelta) -> str:
    """Format timedelta as HH:MM:SS."""
    seconds = int(delta.total_seconds())
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _maybe_record_pnl(client: AlpacaClient, last_date) -> datetime.date:
    """Record daily PnL snapshot once per day."""
    try:
        now = datetime.utcnow().date()
        if last_date == now:
            return last_date
        
        account = client.get_account()
        equity = float(getattr(account, "equity", 0.0) or 0.0)
        cash = float(getattr(account, "cash", 0.0) or 0.0)
        portfolio_value = float(getattr(account, "portfolio_value", equity))
        
        append_pnl_log(
            date=str(now),
            equity=equity,
            cash=cash,
            portfolio_value=portfolio_value,
        )
        
        LOGGER.info(
            "Recorded daily PnL snapshot (equity=%.2f, cash=%.2f, pnl=%.2f)",
            equity,
            cash,
            portfolio_value - equity,
        )
        return now
        
    except Exception:
        LOGGER.exception("Failed to record PnL snapshot")
        return last_date


def _build_symbol_state(
    client: AlpacaClient, universe: Iterable[str], feed: Optional[LiveTickerFeed]
) -> dict:
    """Build current state dictionary for all symbols."""
    state = {}
    positions = {}
    
    # Fetch positions once
    try:
        for pos in client.list_positions():
            symbol = pos.symbol.upper()
            positions[symbol] = {
                "qty": float(getattr(pos, "qty", 0) or 0),
                "entry_price": float(getattr(pos, "avg_entry_price", 0.0) or 0.0),
                "unrealized_pl": float(getattr(pos, "unrealized_pl", 0.0) or 0.0),
            }
    except Exception:
        LOGGER.exception("Unable to fetch positions for symbol state")

    # Get prices from stream
    prices = feed.snapshot() if feed else {}
    
    # Build state for each symbol
    for symbol in universe:
        last = prices.get(symbol, {})
        state[symbol] = {
            "last_price": last.get("price"),
            "last_size": last.get("size"),
            "last_timestamp": last.get("timestamp"),
        }
        
        # Add position info if exists
        if symbol in positions:
            state[symbol].update(positions[symbol])
    
    return state
