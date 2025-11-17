"""Lightweight command-line interface for the trading session helper."""
from __future__ import annotations

import argparse
import logging
from typing import Sequence

from alpaca_trader.engine import run_trading_session
from alpaca_trader.universe import DEFAULT_UNIVERSE

LOG = logging.getLogger(__name__)


def _parse_symbols(value: str | None) -> list[str]:
    if not value:
        return list(DEFAULT_UNIVERSE)
    return [symbol.strip().upper() for symbol in value.split(",") if symbol.strip()]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Alpaca trading CLI")
    sub = parser.add_subparsers(dest="command")

    trade = sub.add_parser("trade", help="Run the open/close trading loop")
    trade.add_argument("--symbols", help="Comma-separated symbols", default=None)
    trade.add_argument("--qty", type=int, default=1, help="Base quantity per order")
    trade.add_argument(
        "--per-trade-risk-pct",
        type=float,
        default=1.0,
        help="Percent of equity to allocate per symbol (sized against latest price)",
    )
    trade.add_argument(
        "--max-daily-loss-pct",
        type=float,
        default=5.0,
        help="Stop trading once equity drops by this percent",
    )
    trade.add_argument(
        "--close-buffer-min",
        type=int,
        default=10,
        help="Begin exiting positions this many minutes before market close",
    )
    trade.add_argument(
        "--poll-seconds",
        type=int,
        default=30,
        help="Polling interval for clock/account refresh",
    )
    trade.set_defaults(handler=_handle_trade)

    return parser


def _handle_trade(args: argparse.Namespace) -> int:
    symbols = _parse_symbols(args.symbols)
    LOG.info("Starting trade loop for symbols: %s", ",".join(symbols))

    run_trading_session(
        symbols=symbols,
        base_qty=args.qty,
        per_trade_risk_pct=args.per_trade_risk_pct,
        max_daily_loss_pct=args.max_daily_loss_pct,
        close_buffer_minutes=args.close_buffer_min,
        poll_seconds=args.poll_seconds,
    )

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 1
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
