# Alpaca Retail Trader

This repository is the very first cut of a retail-trading playground powered by
[Alpaca](https://alpaca.markets/).  The goal is to provide a tiny but complete
example that buys a basket of ETFs/equities at the opening bell and sells the
entire position when the market closes.  From here you can iterate on the
strategy, plug in new data sources, or rip out pieces for other experiments.

## Project layout

```
.
├── alpaca_trader/
│   ├── alpaca_client.py    # lightweight REST wrapper
│   ├── config.py           # shared configuration knobs
│   ├── data_stream.py      # live trade feed helper
│   ├── engine.py           # orchestrates the strategy loop & countdowns
│   ├── strategies/
│   │   └── open_close_dummy.py
│   └── universe.py         # list of symbols to trade
├── scripts/
│   └── run_open_close.py   # CLI entrypoint
└── requirements.txt
```

## Getting started

1. Create a virtual environment and install the dependencies:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Export your [Alpaca API credentials](https://docs.alpaca.markets/reference/api-overview):

   ```bash
   export APCA_API_KEY_ID="your-key"
   export APCA_API_SECRET_KEY="your-secret"
   export APCA_API_BASE_URL="https://paper-api.alpaca.markets"
   ```

3. Run the trading loop:

   ```bash
    python scripts/run_open_close.py \
        --symbols "SPY,QQQ" \
        --qty 5 \
        --direction long
   ```

   Key runtime features:

   * **Countdown timers** – every poll logs the wall-clock time, whether the
     market is open, and the HH:MM:SS countdown to the next opening/closing
     bell.
   * **Live ticker stream** – a background websocket connection subscribes to
     the configured symbols and surfaces a rolling snapshot of the latest trade
     prices and sizes. Use `--disable-data-stream` to turn it off or
     `--data-feed sip` to switch providers.
   * **Autonomous execution** – when the countdown reaches the opening bell the
     engine cancels lingering orders, enters the configured strategy positions
     (long or short), and automatically exits at the close.
   * **Strategy hot-swapping** – pass a custom implementation via
     `--strategy module.path:ClassName` and any strategy-specific keyword args
     through `--direction` or by editing the script; everything else (clock
     monitoring, countdowns, graceful shutdown) stays the same.

## Customising the strategy

The intent is to make tinkering safe and easy:

* Update `alpaca_trader/universe.py` with your preferred list of symbols or pass
  `--symbols` at runtime for ad-hoc scans.
* Override `TRADER_QTY`, pass `--qty`, or inject sizing logic inside your
  strategy class.
* Drop new files under `alpaca_trader/strategies/` and select them with the
  `--strategy` flag (the loader understands `package.module:ClassName`).
* Build more advanced executions by reusing `alpaca_trader.data_stream` for
  streaming signals and `alpaca_trader.engine` for the countdown/loop plumbing.

Because everything is kept intentionally small you can follow the code in a
single sitting before making it your own.
