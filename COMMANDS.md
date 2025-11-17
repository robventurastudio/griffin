Plaintext commands to validate the trading bot locally. Replace placeholder values where noted.

## 1) Environment setup
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# If you don't already have a .env file
cat > .env <<'ENV'
APCA_API_KEY_ID=your-key
APCA_API_SECRET_KEY=your-secret
APCA_API_BASE_URL=https://paper-api.alpaca.markets
ENV

# Load env vars for the current shell
export $(grep -v '^#' .env | xargs)

## 2) Connectivity smoke test
## 2) Basic connectivity and health
python - <<'PY'
from alpaca_trader.alpaca_client import AlpacaClient
client = AlpacaClient()
acct = client.get_account()
print(f"Connected as {acct.account_number} (status={acct.status})")
print(client.get_clock())
PY

## 3) Run the paper-trading loop (Ctrl+C to exit)
python -m alpaca_trader.cli trade \
  --symbols "SPY,QQQ" \
  --qty 1 \
  --per-trade-risk-pct 1 \
  --max-daily-loss-pct 5 \
  --close-buffer-min 10

## 4) Direct script run (works without modifying PYTHONPATH)
python scripts/run_open_close.py

## 5) Unit tests
python -m unittest discover -v tests
PY

python -m alpaca_trader.cli health-check
python -m alpaca_trader.cli status

## 3) Smoke-run the trading loop (press Ctrl+C to exit)
python -m alpaca_trader.cli trade \
  --symbols "SPY,QQQ" \
  --qty 1 \
  --direction long \
  --strategy alpaca_trader.strategies.open_close_dummy:OpenCloseStrategy

# Or via the helper script (legacy argv supported)
./launch_trader.sh --symbols "SPY,QQQ" --qty 1

## 4) Logs and monitoring
python -m alpaca_trader.cli logs --tail 100
python -m alpaca_trader.cli dashboard --port 8000  # visit http://127.0.0.1:8000

## 5) Event stream helper (optional; may 404 on some plans)
python -m alpaca_trader.cli events trades --max-events 5

## 6) Backtest example (requires a real parquet file)
python -m alpaca_trader.cli backtest data/your_file.parquet \
  --strategy alpaca_trader.strategies.orb:OpeningRangeBreakout

## 7) Unit tests and smoke tests
python -m unittest discover -v tests

## 8) Direct script execution check
python scripts/run_open_close.py --symbols "SPY,QQQ" --qty 1
