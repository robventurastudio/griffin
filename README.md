# Alpaca Retail Trader

Starter scaffolding for experimenting with Alpaca's paper trading APIs.

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
