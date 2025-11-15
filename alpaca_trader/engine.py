"""Trading engine that wires together the strategy and Alpaca client."""

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
from .strategies.open_close_dummy import OpenCloseStrategy
from .universe import DEFAULT_UNIVERSE

LOGGER = logging.getLogger(__name__)


@contextmanager
def _graceful_shutdown():
    """Allow ``Ctrl+C`` to terminate the loop without a scary traceback."""

    shutdown = {"value": False}

    def _handler(signum, frame):  # pragma: no cover - signal handling
        LOGGER.info("Received signal %s, shutting down", signum)
        shutdown["value"] = True

    original = signal.signal(signal.SIGINT, _handler)
    try:
        yield shutdown
    finally:  # pragma: no branch - restoring handler is deterministic
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
) -> None:
    """Continuously monitor the Alpaca clock and trade the configured strategy."""

    trading_client = client or AlpacaClient()
    strategy_cls = _resolve_strategy(strategy_path)
    strategy_options = dict(strategy_kwargs or {})
    strategy = strategy_cls(
        trading_client,
        universe=list(universe or DEFAULT_UNIVERSE),
        qty=qty or TRADER_QTY,
        **strategy_options,
    )
    strategy.bootstrap_state()

    feed = _maybe_start_data_stream(strategy, enable_data_stream, data_feed)

    interval = poll_interval or POLL_INTERVAL_SECONDS
    LOGGER.info(
        "Running %s with %s symbols (qty=%s, poll=%ss, data=%s)",
        strategy.__class__.__name__,
        len(strategy.universe),
        strategy.qty,
        interval,
        "on" if feed else "off",
    )

    with _graceful_shutdown() as shutdown:
        while not shutdown["value"]:
            try:
                clock = trading_client.get_clock()
            except Exception:  # pragma: no cover - network dependent
                LOGGER.exception("Failed to fetch market clock; retrying")
                time.sleep(interval)
                continue

            _log_clock_countdown(clock)
            _log_data_snapshot(feed)

            if strategy.should_open(clock):
                trading_client.cancel_all_orders()
                strategy.enter_positions()
            elif strategy.should_close(clock):
                strategy.exit_positions()

            time.sleep(interval)

    if feed:
        feed.stop()

    LOGGER.info("Trading loop exited cleanly")


def _resolve_strategy(strategy_path: Optional[str]) -> Type:
    if not strategy_path:
        return OpenCloseStrategy

    module_path, sep, class_name = strategy_path.partition(":")
    if not sep:
        raise ValueError(
            "Strategy path must be in the format 'module.submodule:ClassName'"
        )
    module = importlib.import_module(module_path)
    try:
        return getattr(module, class_name)
    except AttributeError as exc:  # pragma: no cover - import error path
        raise ImportError(
            f"Unable to load strategy '{class_name}' from '{module_path}'"
        ) from exc


def _maybe_start_data_stream(strategy, enable_stream: bool, data_feed: str):
    if not enable_stream:
        return None

    try:
        feed = LiveTickerFeed(symbols=strategy.universe, data_feed=data_feed)
    except Exception:  # pragma: no cover - import errors
        LOGGER.exception("Failed to initialise live ticker feed; continuing without it")
        return None

    try:
        feed.start()
    except Exception:  # pragma: no cover - network errors
        LOGGER.exception("Unable to start live ticker feed; continuing without it")
        return None

    return feed


def _log_clock_countdown(clock) -> None:
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
    if not feed:
        return

    raw = feed.snapshot(limit=5)
    if not raw:
        return
    LOGGER.info("Live ticker snapshot: %s", feed.format_snapshot(limit=5))


def _ensure_datetime(value) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime()
    raise TypeError(f"Unsupported clock timestamp type: {type(value)!r}")


def _fmt_datetime(value: datetime) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _format_delta(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
