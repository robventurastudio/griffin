# Alpaca Retail Trader

This repository is a minimal but production-minded retail trading playground for [Alpaca](https://alpaca.markets/). It includes countdown-aware open/close automation, a live data stream, pluggable strategies, and now a ergonomic CLI, logging stack, launch helper, and lightweight dashboard so you can operate everything from a Mac terminal.

## Project layout

```
.
├── alpaca_trader/
│   ├── alpaca_client.py    # lightweight REST wrapper
│   ├── cli.py              # argparse-based command line interface
│   ├── config.py           # shared configuration knobs
│   ├── data_stream.py      # live trade feed helper
│   ├── dashboard.py        # FastAPI-powered local dashboard
│   ├── engine.py           # orchestrates the strategy loop & countdowns
│   ├── logging_setup.py    # rotating log configuration + trade CSV writer
│   ├── strategies/
│   │   └── open_close_dummy.py
│   └── universe.py         # list of symbols to trade
├── scripts/
│   ├── run_open_close.py   # legacy entrypoint -> delegates to CLI
│   └── rotate_logs.py      # prune old log files
├── launch_trader.sh        # convenience launcher for daily use
├── requirements.txt
└── .env.example            # template for required environment variables
```

## Setup

1. **Create a virtual environment and install dependencies**

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Export your Alpaca credentials** (paper trading is recommended while experimenting):

   ```bash
   cp .env.example .env  # optional helper if you use direnv
   export APCA_API_KEY_ID="your-key"
   export APCA_API_SECRET_KEY="your-secret"
   export APCA_API_BASE_URL="https://paper-api.alpaca.markets"
   # Optional: override the broker events base if provided by Alpaca
   # export APCA_EVENTS_BASE_URL="https://paper-api.alpaca.markets/events"
   ```

3. **Verify connectivity**

   ```bash
   python - <<'PY'
   from alpaca_trader.alpaca_client import AlpacaClient

   client = AlpacaClient()
   account = client.get_account()
   print(f"Connected as {account.account_number} (status={account.status})")
   PY
   ```

## Command-line interface

All operations run through `python -m alpaca_trader.cli` (or `./launch_trader.sh`). Use `--help` on any subcommand for details.

### Trade

Start the open/close loop:

```bash
python -m alpaca_trader.cli trade \
  --symbols "SPY,QQQ" \
  --qty 5 \
  --direction long \
  --strategy alpaca_trader.strategies.open_close_dummy:OpenCloseStrategy
```

Flags:
- `--symbols`: comma-separated overrides for the universe (defaults to `alpaca_trader/universe.py`).
- `--qty`: number of shares per symbol (default from `alpaca_trader/config.py`).
- `--direction`: `long` or `short`.
- `--disable-data-stream`: skip the websocket feed.
- `--strategy`: custom strategy path in `module:Class` format.
- `--poll-interval`: seconds between clock polls (defaults to `POLL_INTERVAL_SECONDS`).

### Status

```bash
python -m alpaca_trader.cli status
```

Prints current time, market open/closed state, countdown to next bell, configured universe, and open positions.

### Logs

```bash
python -m alpaca_trader.cli logs --tail 100
```

Tails `logs/app.log` (created automatically).

### Health check

```bash
python -m alpaca_trader.cli health-check
```

Confirms required environment variables, Alpaca account status, and fetches the latest bar for the default universe to ensure data access. Returns non-zero on failure.

### Events (SSE)

```bash
python -m alpaca_trader.cli events trades --max-events 5
```

Streams broker SSE channels (e.g., `trades`, `journal`, `transfers`, `account`) with automatic heartbeat handling, backoff/reconnect, and optional ULID offsets via `--since-ulid`/`--until-ulid`. Use this to monitor order events or resubscribe from a prior cursor without missing messages.

### Dashboard

```bash
python -m alpaca_trader.cli dashboard --port 8000
```

Starts a local-only FastAPI dashboard at `http://127.0.0.1:8000` showing current time/countdown, universe, positions, recent trades (`logs/trades.csv`), and a tail of the application log.

## Logging & observability

Logging is configured via `alpaca_trader/logging_setup.py` and kicks in as soon as the CLI starts:

- `logs/app.log`: main application log (rotates daily).
- `logs/errors.log`: errors/exceptions (rotates daily).
- `logs/trades.csv`: append-only trade log capturing order submissions.

The logs directory is created automatically. Secrets are never logged; missing env vars are reported by name only. Use `scripts/rotate_logs.py` to prune old files (`--days` flag, defaults to 7).

## Launch script

For everyday use, run:

```bash
./launch_trader.sh --symbols "QQQ,NVDA" --qty 2
```

The script activates `.venv` if present and then invokes the CLI `trade` subcommand with any additional arguments you provide.

## Operational hygiene

- **Environment-only secrets**: credentials are read strictly from `APCA_API_KEY_ID`, `APCA_API_SECRET_KEY`, and `APCA_API_BASE_URL`. Do not hardcode secrets.
- **Resilient loop**: the engine wraps the main loop with exception logging and exponential backoff so transient failures retry without exiting. `Ctrl+C` still exits cleanly.
- **Rate limits**: polling cadence is centralized via `POLL_INTERVAL_SECONDS`; API errors (including rate limits) are logged and trigger backoff.
- **Safety checks**: use `health-check` before the session starts and review `logs/app.log`/`logs/errors.log` for anomalies.

## Pre-flight checklist before syncing to Alpaca

Run through these checks before hitting a live account:

1. **Paper trade until boring**: validate countdown alignment and order flow against the paper endpoint first.
2. **Confirm credentials**: `health-check` should pass and `account.status` should be `ACTIVE` (paper) or `APPROVED` (live).
3. **Validate symbol list and sizing**: ensure symbols are tradable for your account and `--qty` keeps exposure within your limits.
4. **Exercise the data stream**: toggle `--disable-data-stream` to confirm websocket stability and resource usage.
5. **Rehearse failures**: interrupt the process and restore it to confirm idempotent cleanup and backoff behavior.
6. **Log and review**: inspect `logs/trades.csv` and `logs/app.log` after a dry run.

## Customising the strategy

- Edit `alpaca_trader/universe.py` to change the default watchlist or override via `--symbols` at runtime.
- Adjust `TRADER_QTY` in `alpaca_trader/config.py` or pass `--qty` for ad-hoc sizing.
- Add new strategies under `alpaca_trader/strategies/` and select them with `--strategy module.path:ClassName`.
- Reuse `alpaca_trader.data_stream.LiveTickerFeed` for streaming signals and `alpaca_trader.engine.run_open_close_loop` for countdown orchestration.

## Roadmap

See `CODEX_BRIEF.md` and `CHATGPT_SUMMARY.md` for the broader strategy, risk, backtesting, and observability roadmap.
