"""Backward-compatible entrypoint that delegates to the new CLI."""

from alpaca_trader.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
