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
- **Integration:** Sets a daily bias flag that downstream intraday strategies (ORB, EMA pullback, VWAP) honor; current implementation tags bullish/bearish/neutral bias off the pre-open gap and blocks long entries when the bias is bearish.

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

## Build & Optimization Notes for the First Wave

These snippets turn the roadmap into shippable work with clear tuning levers.

### ORB (Opening Range Breakout)
- **Build:**
  - Reuse the existing strategy interface; compute the opening range on 5m bars (first 30–60 minutes) and emit long/short signals on range breaks with volume confirmation.
  - Add guardrails: trading hours window, max concurrent symbols, and fail-safe flatten near close.
  - Logging: capture range bounds, entry/exit prices, and volume at signal time for later review.
- **Optimize:**
  - Tune range duration (30 vs 60 minutes) and breakout buffer (ticks/percentage above range).
  - Compare volume filters (e.g., >1.5× average for the bar) and stop placement (range low/high vs ATR-based).
  - Run sample backtests on recent weeks and keep a per-symbol win/loss + expectancy table to prune weak names.

### VWAP Mean Reversion
- **Build:**
  - Maintain rolling VWAP (1m/5m bars) and z-score of price vs VWAP; emit longs when price is ≤ −Zσ and shorts when price is ≥ +Zσ.
  - Exits: primary target at VWAP touch; optional trail after a partial scale-out.
  - Risk: cap size in low-liquidity names; disable in first 30 minutes to avoid ORB overlap.
- **Optimize:**
  - Sweep Z-thresholds (e.g., 1.5–2.5σ) and minimum volume filters.
  - Add a regime filter (e.g., ATR percentile) to avoid running when volatility is spiking.
  - Track slippage per symbol and widen stops/targets where fills degrade performance.

### EMA Pullback (Intraday Trend)
- **Build:**
  - Compute dual EMAs (e.g., 20/50). Bias long when price > slow EMA; wait for pullbacks toward fast EMA with confirmation (e.g., bullish candle close) before entry. Current implementation ships the long side with a configurable warmup (`--ema-min-history`) and exits when price loses the slow EMA.
  - Include higher-timeframe bias from a pre-open scan (gap/previous close context) to avoid countertrend trades.
  - Stops: below the recent swing low for longs (swing high for shorts); targets via R-multiple or trailing EMA.
- **Optimize:**
  - Sweep EMA pairs (10/30, 20/50) and pullback depth (% from slow EMA) to improve participation vs whipsaw.
  - Test an RSI or OBV filter to skip entries when momentum is exhausted.
  - Measure trade duration and require minimum bar closes in favor before scaling size.

### Gap Bias Module
- **Build:**
  - Pre-open compute gap % vs prior close and locate pre-market high/low; tag bias as fade/continue/neutral.
  - Expose the bias to intraday strategies (ORB, EMA pullback) so they throttle or filter signals accordingly.
  - Add safety: ignore low-volume gaps and extreme news-driven moves without liquidity.
- **Optimize:**
  - Tune thresholds for what constitutes a “large” gap (e.g., >1.5–2.0%).
  - Track outcomes conditioned on bias state to refine how aggressively downstream strategies follow/ignore it.
