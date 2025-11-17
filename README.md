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
4. Start the placeholder open/close loop (requires valid API keys):
   ```bash
   python scripts/run_open_close.py
   ```
