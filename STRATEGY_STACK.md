# Strategy Stack Roadmap

Griffin should evolve beyond a single ORB play by shipping a small, modular collection of intraday and swing strategies. Each module shares the existing engine/strategy interface and can reuse shared risk/position sizing helpers.

## 1) Opening Range Breakout (ORB)
- **Role:** Morning aggression and trend capture during the first 30–60 minutes.
- **Signal basics:** Define opening range on 5m bars; long above range high (with volume confirmation), short below range low; stop at the opposite side of the range; target via R-multiple.
- **Integration status:** Strategy skeleton exists; needs full live wiring in the engine and CLI plus risk checks.

## 2) VWAP Mean Reversion
- **Role:** Mid-day gravity play after the open.
- **Signal basics:** Maintain rolling VWAP per symbol; compute z-score of (last price – VWAP) using intraday bars; short if price is +Zσ above VWAP, long if −Zσ below; exit on VWAP touch or trailing stop.
- **Requirements:** 1m–5m bar stream or rolling tick aggregation; reuse open positions/flattening rules.
- **Why it pairs with ORB:** Catches exhaustion after ORB-driven expansions; provides a counter-trend module for choppy tapes.

## 3) Intraday Trend Pullback (EMA-based)
- **Role:** Ride the session trend after the open.
- **Signal basics:** Use dual EMAs (e.g., 20/50). Bias long when price > 50 EMA and buy pullbacks toward the faster EMA; bias short when price < 50 EMA and sell bounces. Optional RSI filter to avoid overbought/oversold extremes.
- **Requirements:** Intraday bar history and a poll loop; aligns with higher-timeframe bias set pre-open.

## 4) Range-Bound Bollinger Scalper
- **Role:** Provide a low-volatility, chop-mode playbook.
- **Signal basics:** Detect range conditions via low ATR or compressed Bollinger Bandwidth; fade upper band touches and buy lower band dips with tight stops and small size; quick profit targets near midline or band reversion.
- **Requirements:** Intraday bars with standard deviation calculations; guardrails to disable when volatility expands.

## 5) Gap Strategy (Fade or Continuation)
- **Role:** Pre-market setup based on overnight moves.
- **Signal basics:** Measure gap vs prior close and pre-market high/low; fade overextended gaps into resistance/VWAP or follow-through when gap aligns with broader trend and volume.
- **Integration:** Sets a daily bias flag that downstream intraday strategies (ORB, EMA pullback) can honor.

## 6) Session Close Strategy
- **Role:** Final 30–60 minutes management.
- **Signal basics:** Reversion to VWAP into the close or continuation if trend + volume ramp persists; enforce "no overnight" flattening unless explicitly allowed.
- **Requirements:** Same intraday data feed; integrates with existing close-buffer handling in the engine.

## 7) Multi-Day Swing Trend (Later Phase)
- **Role:** 2–10 day holds to diversify away from intraday-only edges.
- **Signal basics:** Daily bars for breakouts from consolidations or pullbacks to 20/50-day MAs in strong names; uses same Alpaca client with a slower scheduler.
- **Requirements:** Daily historical data ingestion and swing-specific risk sizing; optional backtester support.

## Suggested Build Order
1. Finish ORB live wiring (engine + CLI + tests).
2. Add VWAP mean reversion module that plugs into the current strategy interface.
3. Ship the EMA pullback module for intraday trend participation.
4. Layer in a gap-bias module to inform intraday strategies.
5. Later, add range/Bollinger logic and the swing module once backtesting is available.

This sequence yields coverage for morning trend, mid-day reversion, sustained trends, and special gap days without overcomplicating the codebase.
