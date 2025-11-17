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

4) Run tests

```bash
python -m unittest discover -v tests
```
