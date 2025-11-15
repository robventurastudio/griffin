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

## Quick start: connect & run

If you just want to make sure your Alpaca keys work and fire up the sample
strategy, follow these five steps:

1. **Install dependencies**

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Export your [Alpaca API credentials](https://docs.alpaca.markets/reference/api-overview)**.
   Paper trading keys are perfect while you experiment:

   ```bash
   export APCA_API_KEY_ID="your-key"
   export APCA_API_SECRET_KEY="your-secret"
   export APCA_API_BASE_URL="https://paper-api.alpaca.markets"
   ```

3. **Verify the connection** by asking Alpaca for your account status.  A
   healthy response confirms that the credentials and network path are valid:

   ```bash
   python - <<'PY'
   from alpaca_trader.alpaca_client import AlpacaClient

   client = AlpacaClient()
   account = client.get_account()
   print(f"Connected as {account.account_number} (status={account.status})")
   PY
   ```

4. **Pick the symbols and sizing** you want to trade.  The defaults live in
   `alpaca_trader/universe.py`, but you can always pass overrides via the CLI
   flags shown below.

5. **Run the trading loop** (this launches the countdown timers, data stream,
   and autonomous execution):

   ```bash
   python scripts/run_open_close.py \
       --symbols "SPY,QQQ" \
       --qty 5 \
       --direction long
   ```

That’s it—you now have a minimal, end-to-end connection to Alpaca plus a sample
strategy you can extend.  The next sections dive into the repository layout and
the configuration knobs that make tinkering easy.

## Pre-flight checklist before syncing to Alpaca

Before you point this loop at a funded live account, run through the following
sanity checks to avoid expensive surprises:

1. **Stay on paper trading until the loop is boring.** Let the engine run for a
   few sessions on the `paper-api` endpoint, confirm the countdowns line up with
   official market hours, and inspect every order the CLI places.
2. **Double-check credentials and permissions.** Use the `get_account()` snippet
   above to confirm the account status is `ACTIVE` (paper) or `APPROVED` (live)
   and that the API key has trading permissions for the venue you intend to use.
3. **Validate symbol lists and sizing.** Ensure your `--symbols` set is tradable
   on Alpaca, that each symbol satisfies your account’s pattern-day-trader (PDT)
   and short-sale locate requirements, and that the configured `--qty` or custom
   sizing logic keeps total exposure within your comfort zone.
4. **Watch the live ticker feed.** Run with `--disable-data-stream` both on and
   off to make sure the websocket reconnects cleanly and does not overwhelm your
   machine or Alpaca’s rate limits when you scale to more symbols.
5. **Rehearse failure handling.** Kill the process mid-run, yank network
   connectivity, or intentionally raise an exception in a strategy to verify the
   engine cancels open orders and flattens positions on restart.
6. **Log everything.** Even a simple CSV (timestamp, symbol, side, qty, price)
   is invaluable for reconciling fills.  Wire up a logger before going live so
   you have proof of intent if Alpaca support needs context.
7. **Add basic risk guardrails.** Hard-code a max dollar position per symbol and
   a daily loss lockout in the engine until the dedicated risk module lands; it
   is the cheapest insurance policy you can add right now.

Only after you can answer “yes” to each item should you point the configuration
at `https://api.alpaca.markets` and trade with real capital.

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

## Roadmap: from sandbox to broad deployment

The starter loop is intentionally lightweight so you can understand each part.
To turn it into a production-grade, widely deployed system, progress through
these milestones:

1. **Reliability hardening** – add automated tests for the countdown/strategy
   orchestration, implement structured logging, and gate merges with CI to keep
   the event loop stable as new strategies are added.
2. **Configuration & secrets management** – switch from shell-exported
   environment variables to a centralized config/secrets store (e.g., AWS
   Parameter Store, Vault) and introduce tiered config files for paper vs.
   live trading venues.
3. **Observability & alerting** – ship logs/metrics/traces to a managed stack
   (CloudWatch, Grafana, etc.), expose heartbeat dashboards for countdowns,
   position states, and order latencies, and set up alerts on disconnects or
   rule breaches.
4. **Strategy lifecycle tooling** – define a registry for strategies with
   versioning, feature flags, and A/B rollout controls so you can hot-swap or
   canary new logic without restarting the service.
5. **Risk, compliance, and controls** – codify position limits, circuit
   breakers, and compliance checks (short locate, PDT rules, concentration
   limits) plus audit logs for all orders.
6. **Scaling infrastructure** – containerize the trader, deploy it through an
   orchestration layer (ECS/Kubernetes) with redundancy across regions/accounts,
   and front it with a job scheduler or control plane that can coordinate
   multiple symbol clusters.
7. **Data & model enrichment** – plug in higher-quality feeds (SIP, alt data),
   manage historical datasets for simulation, and add feature pipelines so more
   advanced strategies can share normalized inputs.

Each milestone keeps the existing architecture intact while layering the
operational guardrails, tooling, and scalability needed for a broader rollout.
