"""Command-line entrypoints for the Alpaca trader."""
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

    trade = sub.add_parser("trade", help="Run the trading loop with a strategy")
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
    trade.add_argument(
        "--strategy",
        choices=["orb", "open-close", "vwap-reversion", "ema-pullback"],
        default="orb",
        help="Select which trading strategy to run",
    )
    trade.add_argument(
        "--orb-range-minutes",
        type=int,
        default=30,
        help="Opening range duration in minutes for ORB",
    )
    trade.add_argument(
        "--orb-breakout-buffer",
        type=float,
        default=0.001,
        help="Fractional buffer above the range high required to trigger an ORB entry",
    )
    trade.add_argument(
        "--vwap-z",
        type=float,
        default=2.0,
        help="Z-score threshold for VWAP reversion entries",
    )
    trade.add_argument(
        "--ema-fast",
        type=int,
        default=12,
        help="Fast EMA span for the pullback strategy",
    )
    trade.add_argument(
        "--ema-slow",
        type=int,
        default=26,
        help="Slow EMA span for the pullback strategy",
    )
    trade.add_argument(
        "--ema-pullback-buffer",
        type=float,
        default=0.001,
        help="How far above the fast EMA we allow entries (fractional)",
    )
    trade.add_argument(
        "--ema-min-history",
        type=int,
        default=5,
        help="Minimum number of bars before the EMA pullback strategy can enter",
    )
    trade.add_argument(
        "--gap-threshold-pct",
        type=float,
        default=1.5,
        help="Gap percentage needed to tag a bullish/bearish bias for the session",
    )
    trade.add_argument(
        "--trading-mode",
        choices=["paper", "live"],
        default="paper",
        help="Explicitly label the Alpaca endpoint mode",
    )
    trade.add_argument(
        "--base-url",
        help="Override the Alpaca base URL (implies mode based on URL)",
        default=None,
    )
    trade.set_defaults(handler=_handle_trade)

    return parser


def _handle_trade(args: argparse.Namespace) -> int:
    symbols = _parse_symbols(args.symbols)
    LOG.info("Starting trade loop for symbols: %s", ",".join(symbols))

    from alpaca_trader.alpaca_client import AlpacaClient  # local import to avoid slow CLI startup

    base_url = args.base_url
    if not base_url and args.trading_mode == "live":
        base_url = "https://api.alpaca.markets"
    client = AlpacaClient(base_url=base_url)

    strategy_config = {}
    if args.strategy == "orb":
        strategy_config.update(
            {
                "range_minutes": args.orb_range_minutes,
                "breakout_buffer": args.orb_breakout_buffer,
            }
        )
    if args.strategy == "vwap-reversion":
        strategy_config["z_threshold"] = args.vwap_z
    if args.strategy == "ema-pullback":
        strategy_config.update(
            {
                "fast_span": args.ema_fast,
                "slow_span": args.ema_slow,
                "pullback_buffer": args.ema_pullback_buffer,
                "min_history": args.ema_min_history,
            }
        )

    run_trading_session(
        symbols=symbols,
        base_qty=args.qty,
        per_trade_risk_pct=args.per_trade_risk_pct,
        max_daily_loss_pct=args.max_daily_loss_pct,
        close_buffer_minutes=args.close_buffer_min,
        poll_seconds=args.poll_seconds,
        strategy_name=args.strategy,
        strategy_config=strategy_config,
        client=client,
        gap_threshold_pct=args.gap_threshold_pct,
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
