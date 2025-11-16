"""Command-line interface for running and inspecting the trading app."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from datetime import datetime, timezone
from typing import List, Optional

from .alpaca_client import AlpacaClient
from .config import POLL_INTERVAL_SECONDS, TRADER_QTY
from .engine import run_open_close_loop
from .logging_setup import APP_LOG_PATH, configure_logging, ensure_log_dir
from .sse_events import SSEEventClient
from .universe import DEFAULT_UNIVERSE

LOGGER = logging.getLogger("alpaca_trader.cli")


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    configure_logging(console=True)

    if not hasattr(args, "handler"):
        parser.print_help()
        return 1

    try:
        return args.handler(args)
    except KeyboardInterrupt:  # pragma: no cover - manual interrupt
        LOGGER.info("Interrupted by user; exiting")
        return 130


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Griffin retail trader CLI")
    subparsers = parser.add_subparsers(dest="command")

    trade = subparsers.add_parser("trade", help="Start the trading loop")
    trade.add_argument("--symbols", help="Comma-separated symbol list", default=None)
    trade.add_argument("--qty", type=int, default=TRADER_QTY, help="Order size per symbol")
    trade.add_argument("--direction", choices=["long", "short"], default="long")
    trade.add_argument("--disable-data-stream", action="store_true", help="Disable websocket feed")
    trade.add_argument("--strategy", help="Strategy path module:Class")
    trade.add_argument("--poll-interval", type=int, default=POLL_INTERVAL_SECONDS)
    trade.set_defaults(handler=_handle_trade)

    status = subparsers.add_parser("status", help="Show market and position status")
    status.set_defaults(handler=_handle_status)

    logs = subparsers.add_parser("logs", help="Tail the application log")
    logs.add_argument("--tail", type=int, default=50, help="Number of log lines to show")
    logs.set_defaults(handler=_handle_logs)

    health = subparsers.add_parser("health-check", help="Validate connectivity and configuration")
    health.set_defaults(handler=_handle_health)

    dashboard = subparsers.add_parser("dashboard", help="Start the local dashboard server")
    dashboard.add_argument("--host", default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=8000)
    dashboard.set_defaults(handler=_handle_dashboard)

    events = subparsers.add_parser("events", help="Stream SSE broker events to stdout")
    events.add_argument(
        "event_type",
        help="Event channel, e.g. trades, journal, transfers, account",
    )
    events.add_argument("--since-ulid", help="Resume from this ULID or ID")
    events.add_argument("--until-ulid", help="Stop after reaching this ULID or ID")
    events.add_argument("--since", help="Resume from RFC3339 timestamp or integer id")
    events.add_argument("--until", help="Stop after RFC3339 timestamp or integer id")
    events.add_argument("--max-events", type=int, help="Stop after N events")
    events.set_defaults(handler=_handle_events)

    return parser


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _handle_trade(args: argparse.Namespace) -> int:
    symbols = _parse_symbols(args.symbols) if args.symbols else DEFAULT_UNIVERSE
    LOGGER.info("Launching trade loop for %s symbols", len(symbols))
    run_open_close_loop(
        universe=symbols,
        qty=args.qty,
        strategy_path=args.strategy,
        enable_data_stream=not args.disable_data_stream,
        strategy_kwargs={"direction": args.direction},
        poll_interval=args.poll_interval,
    )
    return 0


def _handle_status(_: argparse.Namespace) -> int:
    client = AlpacaClient()
    clock = client.get_clock()
    positions = client.list_positions()

    now = _fmt_datetime(clock.timestamp)
    next_open = _fmt_datetime(clock.next_open)
    next_close = _fmt_datetime(clock.next_close)

    print(f"Current time: {now}")
    print(f"Market is {'OPEN' if clock.is_open else 'CLOSED'}")
    print(f"Next open:  {next_open}")
    print(f"Next close: {next_close}")
    print(f"Universe:   {', '.join(DEFAULT_UNIVERSE)}")
    print("\nOpen positions:")
    if not positions:
        print("  (none)")
    else:
        for pos in positions:
            print(
                f"  {pos.symbol}: qty={pos.qty} avg_price={pos.avg_entry_price} "
                f"unrealized={pos.unrealized_pl}"
            )
    return 0


def _handle_logs(args: argparse.Namespace) -> int:
    ensure_log_dir()
    path = APP_LOG_PATH
    if not path.exists():
        print("Log file not found; start the app to generate logs")
        return 0
    tail = args.tail
    lines = path.read_text().splitlines()
    for line in lines[-tail:]:
        print(line)
    return 0


def _handle_health(_: argparse.Namespace) -> int:
    try:
        client = AlpacaClient()
        account = client.get_account()
        clock = client.get_clock()
        bar = client.get_latest_bar(DEFAULT_UNIVERSE[0])
    except Exception:
        LOGGER.exception("Health check failed")
        return 1

    print(f"Account status: {getattr(account, 'status', 'unknown')}")
    print(f"Market open: {getattr(clock, 'is_open', False)}")
    print(f"Latest bar {DEFAULT_UNIVERSE[0]}: {bar}")
    return 0


def _handle_dashboard(args: argparse.Namespace) -> int:
    from . import dashboard

    return dashboard.run(host=args.host, port=args.port)


def _handle_events(args: argparse.Namespace) -> int:
    stop = threading.Event()
    client = SSEEventClient()
    count = 0

    def _on_event(payload):
        nonlocal count
        count += 1
        print(json.dumps(payload, indent=2, sort_keys=True))
        if args.max_events and count >= args.max_events:
            stop.set()

    try:
        client.stream_events(
            args.event_type,
            since_ulid=args.since_ulid,
            until_ulid=args.until_ulid,
            since=args.since,
            until=args.until,
            on_event=_on_event,
            stop_event=stop,
        )
    except KeyboardInterrupt:  # pragma: no cover - manual interrupt
        LOGGER.info("SSE stream interrupted by user")
        return 130
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_symbols(raw: str) -> List[str]:
    return [symbol.strip().upper() for symbol in raw.split(",") if symbol.strip()]


def _fmt_datetime(value) -> str:
    if isinstance(value, datetime):
        dt = value
    elif hasattr(value, "to_pydatetime"):
        dt = value.to_pydatetime()
    else:
        return str(value)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")


if __name__ == "__main__":
    sys.exit(main())
