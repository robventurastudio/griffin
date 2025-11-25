# Alpaca Retail Trader

Simple starter that now includes a minimal paper-trading loop using Alpaca's REST API.

## Quick start

1) Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2) Configure credentials (paper trading)

```bash
cat > .env <<'ENV'
APCA_API_KEY_ID=your-key
APCA_API_SECRET_KEY=your-secret
APCA_API_BASE_URL=https://paper-api.alpaca.markets
ENV
export $(grep -v '^#' .env | xargs)
```

3) Run the paper-trading loop

```bash
python -m alpaca_trader.cli trade \
  --symbols "SPY,QQQ" \
  --qty 1 \
  --per-trade-risk-pct 1 \
  --max-daily-loss-pct 5 \
  --close-buffer-min 10
```

The loop sizes positions per symbol using the specified risk percentage, waits for
market open, respects a max daily loss stop, and exits positions as the close
window approaches.

If you see `ModuleNotFoundError` for `alpaca_trade_api` or `pandas`, ensure your
virtual environment is active and install dependencies with:

```bash
pip install -r requirements.txt
```

4) Run tests

```bash
python -m unittest discover -v tests
```
Starter scaffolding for experimenting with Alpaca's paper trading APIs.

## Strategy stack roadmap

See [STRATEGY_STACK.md](STRATEGY_STACK.md) for the prioritized list of intraday and swing modules (ORB, VWAP mean reversion, EMA pullback, gap bias, range scalper, and session close plays) that will round out Griffin's toolbox beyond the opening-range breakout.

## Quick start
1. Create a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. Copy the sample environment and fill in your keys:
   ```bash
   cp .env.example .env
   export $(grep -v '^#' .env | xargs)
   ```
3. Run the lightweight smoke tests to validate formatting and URL helpers:
   ```bash
   python -m unittest discover -v tests
   ```
4. Start the open/close clock monitor (requires valid API keys):
   ```bash
   python scripts/run_open_close.py
   ```

   You should see log lines like:

   ```
   2024-01-01 09:00:00,000 | INFO | Market clock (closed): now=2024-01-01 14:00:00 UTC | next_open=2024-01-01 14:30:00 UTC | next_close=2024-01-01 21:00:00 UTC
   ```

## What changed after the dry run
- Base URLs are normalized to avoid accidental double `/v2` segments.
- Clock logging is timezone-safe even when Alpaca returns pandas `Timestamp` objects.
- Sample `.env` is provided so you can export credentials quickly.
- Smoke tests cover URL normalization, events URL building, and datetime formatting for pandas/ISO strings.
