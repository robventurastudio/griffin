import datetime as dt
import unittest

from alpaca_trader.engine import (
    OpenCloseMarketStrategy,
    PositionSizer,
    RiskLimits,
    RiskManager,
    run_trading_session,
)


class _FakeBar:
    def __init__(self, close: float) -> None:
        self.c = close


class _FakePosition:
    def __init__(self, symbol: str, qty: int) -> None:
        self.symbol = symbol
        self.qty = qty


class _FakeClock:
    def __init__(self, now: dt.datetime, is_open: bool, next_close: dt.datetime, next_open: dt.datetime):
        self.timestamp = now
        self.is_open = is_open
        self.next_close = next_close
        self.next_open = next_open


class _FakeAccount:
    def __init__(self, equity: float) -> None:
        self.equity = equity


class _FakeClient:
    def __init__(
        self,
        *,
        clock: _FakeClock,
        account: _FakeAccount,
        prices: dict[str, float],
        positions=None,
        account_values: list[float] | None = None,
    ):
        self._clock = clock
        self._account = account
        self._prices = prices
        self._orders: list[tuple[str, int, str]] = []
        self._closed = False
        self._positions = positions or {}
        self._account_values = list(account_values or [])

    def get_clock(self):
        return self._clock

    def get_account(self):
        if self._account_values:
            self._account.equity = self._account_values.pop(0)
        return self._account

    def latest_bar(self, symbol: str):
        return _FakeBar(self._prices[symbol])

    def list_positions(self):
        return [_FakePosition(sym, qty) for sym, qty in self._positions.items()]

    def submit_order(self, symbol: str, qty: int, side: str):
        self._orders.append((symbol, qty, side))
        current = self._positions.get(symbol, 0)
        if side == "buy":
            self._positions[symbol] = current + qty
        else:
            self._positions[symbol] = max(0, current - qty)

    def close_all_positions(self):
        self._closed = True
        self._positions = {sym: 0 for sym in self._positions}


class RiskManagerTest(unittest.TestCase):
    def test_breach_when_equity_below_limit(self):
        rm = RiskManager(100_000, RiskLimits(max_daily_loss_pct=5))
        self.assertTrue(rm.breached(94_000))
        self.assertFalse(rm.breached(96_000))


class PositionSizerTest(unittest.TestCase):
    def test_respects_base_and_percent(self):
        sizer = PositionSizer(RiskLimits(per_trade_risk_pct=1), base_qty=5)
        self.assertEqual(sizer.size_for_price(100_000, 200), 5)  # base wins
        # 1% of 100k = $1k, at $10/share -> 100 shares
        self.assertEqual(sizer.size_for_price(100_000, 10), 100)


class StrategyTest(unittest.TestCase):
    def test_buys_missing_positions(self):
        sizer = PositionSizer(RiskLimits(per_trade_risk_pct=1), base_qty=1)
        strat = OpenCloseMarketStrategy(["SPY", "QQQ"], sizer, close_buffer_minutes=10)
        now = dt.datetime(2024, 1, 1, 14, 0, tzinfo=dt.timezone.utc)
        close = now + dt.timedelta(hours=2)
        orders = strat.plan_orders(
            now=now,
            next_close=close,
            positions={"SPY": 0},
            prices={"SPY": 100, "QQQ": 50},
            equity=100_000,
        )
        symbols = {o.symbol for o in orders}
        self.assertEqual(symbols, {"SPY", "QQQ"})
        self.assertTrue(all(o.side == "buy" for o in orders))

    def test_sells_near_close(self):
        sizer = PositionSizer(RiskLimits(per_trade_risk_pct=1), base_qty=1)
        strat = OpenCloseMarketStrategy(["SPY"], sizer, close_buffer_minutes=10)
        now = dt.datetime(2024, 1, 1, 20, 55, tzinfo=dt.timezone.utc)
        close = now + dt.timedelta(minutes=5)
        orders = strat.plan_orders(
            now=now,
            next_close=close,
            positions={"SPY": 10},
            prices={"SPY": 100},
            equity=100_000,
        )
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].side, "sell")
        self.assertEqual(orders[0].qty, 10)


class TradingLoopTest(unittest.TestCase):
    def test_submits_orders_when_open(self):
        now = dt.datetime(2024, 1, 1, 14, 0, tzinfo=dt.timezone.utc)
        clock = _FakeClock(now, True, now + dt.timedelta(hours=6), now)
        client = _FakeClient(
            clock=clock,
            account=_FakeAccount(100_000),
            prices={"SPY": 100},
            positions={"SPY": 0},
        )

        run_trading_session(
            symbols=["SPY"],
            base_qty=1,
            per_trade_risk_pct=1.0,
            max_daily_loss_pct=5.0,
            close_buffer_minutes=10,
            poll_seconds=1,
            client=client,
            sleep_fn=lambda _: None,
            max_cycles=1,
        )

        self.assertIn(("SPY", 10, "buy"), client._orders)

    def test_stops_on_risk_breach(self):
        now = dt.datetime(2024, 1, 1, 14, 0, tzinfo=dt.timezone.utc)
        clock = _FakeClock(now, True, now + dt.timedelta(hours=6), now)
        client = _FakeClient(
            clock=clock,
            account=_FakeAccount(0),
            account_values=[100_000, 89_000],
            prices={"SPY": 100},
            positions={"SPY": 1},
        )

        # Starting equity recorded at 100k, then drops to 89k which breaches the 1% loss stop.
        run_trading_session(
            symbols=["SPY"],
            base_qty=1,
            per_trade_risk_pct=1.0,
            max_daily_loss_pct=1.0,
            close_buffer_minutes=10,
            poll_seconds=1,
            client=client,
            sleep_fn=lambda _: None,
            max_cycles=1,
        )

        self.assertTrue(client._closed)

    def test_exits_near_close(self):
        now = dt.datetime(2024, 1, 1, 20, 55, tzinfo=dt.timezone.utc)
        clock = _FakeClock(now, True, now + dt.timedelta(minutes=5), now)
        client = _FakeClient(
            clock=clock,
            account=_FakeAccount(100_000),
            prices={"SPY": 100},
            positions={"SPY": 10},
        )

        run_trading_session(
            symbols=["SPY"],
            base_qty=1,
            per_trade_risk_pct=1.0,
            max_daily_loss_pct=5.0,
            close_buffer_minutes=10,
            poll_seconds=1,
            client=client,
            sleep_fn=lambda _: None,
            max_cycles=1,
        )

        self.assertIn(("SPY", 10, "sell"), client._orders)


if __name__ == "__main__":
    unittest.main()
