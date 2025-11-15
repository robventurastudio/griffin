"""CLI entrypoint for orchestrating the trading loop."""

from __future__ import annotations

import argparse
import logging
import os
from typing import Iterable

from alpaca_trader.engine import run_open_close_loop

DEFAULT_STRATEGY = "alpaca_trader.strategies.open_close_dummy:OpenCloseStrategy"


def _configure_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def _parse_symbols(raw: str | None) -> Iterable[str] | None:
    if not raw:
        return None
    return [symbol.strip().upper() for symbol in raw.split(",") if symbol.strip()]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Alpaca trading loop")
    parser.add_argument(
        "--symbols",
        help="Comma separated list of tickers to trade (overrides the default universe)",
    )
    parser.add_argument("--qty", type=int, help="Number of shares per trade")
    parser.add_argument(
        "--poll-interval",
        type=int,
        help="Seconds between Alpaca clock polls",
    )
    parser.add_argument(
        "--strategy",
        default=DEFAULT_STRATEGY,
        help="Dotted path to the strategy class (module:ClassName)",
    )
    parser.add_argument(
        "--direction",
        choices=["long", "short"],
        default="long",
        help="Bias to use for the default open/close strategy",
    )
    parser.add_argument(
        "--data-feed",
        default="iex",
        help="Market data feed to use for the live ticker stream",
    )
    parser.add_argument(
        "--disable-data-stream",
        action="store_true",
        help="Disable the live trade stream",
    )
    return parser


if __name__ == "__main__":
    _configure_logging()
    args = _build_parser().parse_args()
    strategy_kwargs = {}
    if args.strategy == DEFAULT_STRATEGY:
        strategy_kwargs["direction"] = args.direction

    run_open_close_loop(
        universe=_parse_symbols(args.symbols),
        qty=args.qty,
        poll_interval=args.poll_interval,
        strategy_path=args.strategy,
        enable_data_stream=not args.disable_data_stream,
        data_feed=args.data_feed,
        strategy_kwargs=strategy_kwargs,
    )
