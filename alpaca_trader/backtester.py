"""Lightweight bar-by-bar backtester sharing the live strategy interface."""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

LOGGER = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    equity_curve: List[float]
    trades: List[Dict[str, str]]
    metrics: Dict[str, float]


class BacktestBroker:
    """Simple fill simulator used by the backtester."""

    def __init__(self, starting_cash: float = 100_000.0) -> None:
        self.cash = starting_cash
        self.positions: Dict[str, float] = {}
        self.avg_prices: Dict[str, float] = {}
        self.trades: List[Dict[str, str]] = []

    def submit(self, *, symbol: str, side: str, qty: int, price: float, timestamp) -> None:
        fill_cost = price * qty
        if side.lower() == "buy":
            self.cash -= fill_cost
            prev_qty = self.positions.get(symbol, 0.0)
            prev_avg = self.avg_prices.get(symbol, 0.0)
            new_qty = prev_qty + qty
            new_avg = ((prev_avg * prev_qty) + fill_cost) / max(new_qty, 1)
            self.positions[symbol] = new_qty
            self.avg_prices[symbol] = new_avg
        else:
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
        value = self.cash
        for symbol, qty in self.positions.items():
            value += prices.get(symbol, 0.0) * qty
        return value


class Backtester:
    """Replay historical bars through a strategy implementing ``on_bar``."""

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

    def run(self) -> BacktestResult:
        data = self._load_data()
        prices: Dict[str, float] = {}
        strategy = self._load_strategy()
        equity_curve: List[float] = []

        for _, row in data.iterrows():
            symbol = row["symbol"].upper()
            if self.universe and symbol not in self.universe:
                continue
            bar = {
                "timestamp": row["timestamp"],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume", 0.0)),
            }
            prices[symbol] = bar["close"]
            if hasattr(strategy, "on_bar"):
                actions = strategy.on_bar(symbol, bar, self.broker) or []
                for action in actions:
                    self._execute_action(symbol, bar, action)
            equity_curve.append(self.broker.equity(prices))

        metrics = self._compute_metrics(equity_curve)
        return BacktestResult(equity_curve=equity_curve, trades=self.broker.trades, metrics=metrics)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _load_strategy(self):
        module_path, _, class_name = self.strategy_path.partition(":")
        if not class_name:
            raise ValueError("strategy_path must be module:ClassName")
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        return cls(client=None, universe=self.universe, **self.strategy_kwargs)

    def _load_data(self) -> pd.DataFrame:
        if not self.data_path.exists():
            raise FileNotFoundError(self.data_path)
        if self.data_path.suffix.lower() == ".parquet":
            df = pd.read_parquet(self.data_path)
        else:
            df = pd.read_csv(self.data_path)
        expected_cols = {"timestamp", "symbol", "open", "high", "low", "close"}
        missing = expected_cols - set(df.columns)
        if missing:
            raise ValueError(f"Data missing required columns: {missing}")
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df.sort_values("timestamp", inplace=True)
        return df

    def _execute_action(self, symbol: str, bar: Dict[str, float], action: Dict[str, str]) -> None:
        side = action.get("side")
        qty = int(action.get("qty", 0))
        if not side or qty <= 0:
            return
        self.broker.submit(symbol=symbol, side=side, qty=qty, price=bar["close"], timestamp=bar["timestamp"])

    def _compute_metrics(self, equity_curve: List[float]) -> Dict[str, float]:
        if not equity_curve:
            return {}
        start = equity_curve[0]
        end = equity_curve[-1]
        returns = [(equity_curve[i] - equity_curve[i - 1]) for i in range(1, len(equity_curve))]
        win_rate = sum(1 for r in returns if r > 0) / max(len(returns), 1)
        drawdown = 0.0
        peak = start
        for value in equity_curve:
            if value > peak:
                peak = value
            drawdown = min(drawdown, (value - peak) / peak)
        return {
            "net_return": (end - start) / start,
            "win_rate": win_rate,
            "max_drawdown": drawdown,
            "final_equity": end,
        }
