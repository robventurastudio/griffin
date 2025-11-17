"""Lightweight bar-by-bar backtester with proper session handling."""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd
import numpy as np

LOGGER = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    equity_curve: List[float]
    trades: List[Dict[str, str]]
    metrics: Dict[str, float]


class BacktestBroker:
    """Simple fill simulator with position tracking."""

    def __init__(self, starting_cash: float = 100_000.0) -> None:
        self.cash = starting_cash
        self.positions: Dict[str, float] = {}
        self.avg_prices: Dict[str, float] = {}
        self.trades: List[Dict[str, str]] = []

    def submit(
        self, *, symbol: str, side: str, qty: int, price: float, timestamp
    ) -> None:
        """Execute a simulated fill."""
        fill_cost = price * qty
        
        if side.lower() == "buy":
            self.cash -= fill_cost
            prev_qty = self.positions.get(symbol, 0.0)
            prev_avg = self.avg_prices.get(symbol, 0.0)
            new_qty = prev_qty + qty
            new_avg = ((prev_avg * prev_qty) + fill_cost) / max(new_qty, 1)
            self.positions[symbol] = new_qty
            self.avg_prices[symbol] = new_avg
        else:  # sell
            self.cash += fill_cost
            self.positions[symbol] = self.positions.get(symbol, 0.0) - qty
        
        self.trades.append(
            {
                "timestamp": str(timestamp),
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "price": price,
            }
        )

    def equity(self, prices: Dict[str, float]) -> float:
        """Calculate total portfolio value."""
        value = self.cash
        for symbol, qty in self.positions.items():
            value += prices.get(symbol, 0.0) * qty
        return value

    def has_position(self, symbol: str) -> bool:
        """Check if we have an open position."""
        return abs(self.positions.get(symbol, 0.0)) > 0.001


class Backtester:
    """Replay historical bars through a strategy with session awareness."""

    def __init__(
        self,
        *,
        data_path: str,
        strategy_path: str,
        universe: Optional[Iterable[str]] = None,
        starting_cash: float = 100_000.0,
        strategy_kwargs: Optional[dict] = None,
    ) -> None:
        self.data_path = Path(data_path)
        self.strategy_path = strategy_path
        self.universe = [s.upper() for s in (universe or [])]
        self.broker = BacktestBroker(starting_cash=starting_cash)
        self.strategy_kwargs = strategy_kwargs or {}
        self._current_session: Optional[str] = None

    def run(self) -> BacktestResult:
        """Execute the backtest and return results."""
        data = self._load_data()
        prices: Dict[str, float] = {}
        strategy = self._load_strategy()
        equity_curve: List[float] = []
        
        LOGGER.info("Starting backtest with %d bars", len(data))

        for row in data.itertuples(index=False):
            symbol = str(row.symbol).upper()
            
            if self.universe and symbol not in self.universe:
                continue

            # Extract bar data
            bar = {
                "timestamp": row.timestamp,
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
                "volume": float(getattr(row, "volume", 0.0)),
            }
            
            # Update current prices
            prices[symbol] = bar["close"]
            
            # Check for session boundary (new day)
            session_date = pd.Timestamp(row.timestamp).strftime("%Y-%m-%d")
            if session_date != self._current_session:
                LOGGER.debug("New session: %s", session_date)
                self._handle_session_change(strategy, session_date)
                self._current_session = session_date
            
            # Let strategy process the bar
            if hasattr(strategy, "on_bar"):
                actions = strategy.on_bar(symbol, bar, self.broker) or []
                for action in actions:
                    self._execute_action(symbol, bar, action)
            
            # Record equity
            equity_curve.append(self.broker.equity(prices))

        # Compute final metrics
        metrics = self._compute_metrics(equity_curve, data)
        
        LOGGER.info("Backtest complete: final_equity=%.2f trades=%d", 
                   equity_curve[-1] if equity_curve else 0, 
                   len(self.broker.trades))
        
        return BacktestResult(
            equity_curve=equity_curve, 
            trades=self.broker.trades, 
            metrics=metrics
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _load_strategy(self):
        """Dynamically load the strategy class."""
        module_path, _, class_name = self.strategy_path.partition(":")
        if not class_name:
            raise ValueError("strategy_path must be module:ClassName")
        
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        
        # Create strategy instance with None client (backtesting mode)
        return cls(
            client=None, 
            universe=self.universe, 
            **self.strategy_kwargs
        )

    def _load_data(self) -> pd.DataFrame:
        """Load and validate OHLCV data."""
        if not self.data_path.exists():
            raise FileNotFoundError(f"Data file not found: {self.data_path}")
        
        # Load based on file extension
        if self.data_path.suffix.lower() == ".parquet":
            df = pd.read_parquet(self.data_path)
        else:
            df = pd.read_csv(self.data_path)
        
        # Validate required columns
        expected_cols = {"timestamp", "symbol", "open", "high", "low", "close"}
        missing = expected_cols - set(df.columns)
        if missing:
            raise ValueError(f"Data missing required columns: {missing}")
        
        # Parse timestamps
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        
        # Sort by timestamp
        df = df.sort_values("timestamp").reset_index(drop=True)
        
        LOGGER.info("Loaded %d bars from %s", len(df), self.data_path)
        return df

    def _handle_session_change(self, strategy, session_date: str) -> None:
        """Handle new trading session (e.g., reset intraday state)."""
        # Flatten positions at end of day if strategy requires it
        if hasattr(strategy, "exit_positions"):
            # Only exit if we actually have positions
            if any(self.broker.has_position(s) for s in self.universe):
                LOGGER.debug("Flattening positions at end of session")
                try:
                    strategy.exit_positions()
                except Exception:
                    LOGGER.exception("Error exiting positions at session boundary")
        
        # Reset strategy state for new session if method exists
        if hasattr(strategy, "_maybe_reset_daily_state"):
            from datetime import datetime, timezone
            session_dt = datetime.fromisoformat(session_date).replace(tzinfo=timezone.utc)
            try:
                strategy._maybe_reset_daily_state(session_dt)
            except Exception:
                LOGGER.exception("Error resetting daily state")

    def _execute_action(
        self, symbol: str, bar: Dict[str, float], action: Dict[str, str]
    ) -> None:
        """Execute a trade action from the strategy."""
        side = action.get("side")
        qty = int(action.get("qty", 0))
        
        if not side or qty <= 0:
            return
        
        # Use close price for fills (realistic for EOD data)
        fill_price = bar["close"]
        
        self.broker.submit(
            symbol=symbol,
            side=side,
            qty=qty,
            price=fill_price,
            timestamp=bar["timestamp"],
        )

    def _compute_metrics(self, equity_curve: List[float], data: pd.DataFrame) -> Dict[str, float]:
        """Calculate performance metrics from equity curve."""
        if not equity_curve:
            return {}
        
        start = equity_curve[0]
        end = equity_curve[-1]
        
        # Calculate returns
        returns = [
            (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
            for i in range(1, len(equity_curve))
        ]
        
        # Win rate
        winning_trades = sum(1 for r in returns if r > 0)
        win_rate = winning_trades / max(len(returns), 1)
        
        # Drawdown
        drawdown = 0.0
        peak = start
        for value in equity_curve:
            if value > peak:
                peak = value
            drawdown = min(drawdown, (value - peak) / peak)
        
        # Sharpe ratio (annualized, assuming daily bars)
        if len(returns) > 1:
            mean_return = np.mean(returns)
            std_return = np.std(returns)
            sharpe = (mean_return / std_return * np.sqrt(252)) if std_return > 0 else 0.0
        else:
            sharpe = 0.0
        
        # Calculate trade metrics
        trades = self.broker.trades
        trade_pnls = []
        for i, trade in enumerate(trades):
            if trade["side"] == "sell" and i > 0:
                # Find corresponding buy
                for j in range(i - 1, -1, -1):
                    if (trades[j]["symbol"] == trade["symbol"] 
                        and trades[j]["side"] == "buy"):
                        pnl = (float(trade["price"]) - float(trades[j]["price"])) * float(trade["qty"])
                        trade_pnls.append(pnl)
                        break
        
        avg_win = np.mean([p for p in trade_pnls if p > 0]) if trade_pnls else 0.0
        avg_loss = np.mean([p for p in trade_pnls if p < 0]) if trade_pnls else 0.0
        profit_factor = abs(sum([p for p in trade_pnls if p > 0]) / sum([p for p in trade_pnls if p < 0])) if trade_pnls and any(p < 0 for p in trade_pnls) else 0.0
        
        # Date range
        start_date = data["timestamp"].min()
        end_date = data["timestamp"].max()
        days = (end_date - start_date).days
        
        return {
            "net_return": (end - start) / start,
            "total_return_pct": ((end - start) / start) * 100,
            "win_rate": win_rate,
            "max_drawdown": drawdown,
            "max_drawdown_pct": drawdown * 100,
            "final_equity": end,
            "starting_equity": start,
            "sharpe_ratio": sharpe,
            "total_trades": len(trades),
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "days": days,
            "cagr": ((end / start) ** (365 / max(days, 1)) - 1) if days > 0 else 0.0,
        }
