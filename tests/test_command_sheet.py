import json
import tempfile
import unittest
from pathlib import Path

from alpaca_trader.command_sheet import (
    CommandAction,
    execute_command,
    format_commands,
    load_command_sheet,
)


class _FakeClient:
    def __init__(self) -> None:
        self.orders: list[tuple[str, int, str]] = []
        self.closed = False

    def submit_order(self, symbol: str, qty: int, side: str) -> None:
        self.orders.append((symbol, qty, side))

    def close_all_positions(self) -> None:
        self.closed = True


class CommandSheetTest(unittest.TestCase):
    def _write_sheet(self, payload: dict) -> Path:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        tmp.close()
        path = Path(tmp.name)
        path.write_text(json.dumps(payload))
        return path

    def test_loads_sheet_and_normalizes(self):
        path = self._write_sheet(
            {
                "commands": [
                    {"codeword": "BUY10_SPY", "action": "buy", "symbol": "SPY", "qty": 10},
                    {"codeword": "flatten", "action": "close_all", "note": "exit everything"},
                ]
            }
        )

        commands = load_command_sheet(path)
        self.assertIn("BUY10_SPY", commands)
        self.assertIn("FLATTEN", commands)
        self.assertEqual(commands["BUY10_SPY"].symbol, "SPY")

    def test_supports_mapping_shaped_sheet(self):
        path = self._write_sheet(
            {
                "BUY10_SPY": {"action": "buy", "symbol": "SPY", "qty": 10},
                "SELL20_QQQ": {"action": "sell", "symbol": "QQQ", "qty": 20},
            }
        )

        commands = load_command_sheet(path)
        self.assertEqual(commands["SELL20_QQQ"].side, "sell")

    def test_executes_orders_and_respects_dry_run(self):
        path = self._write_sheet(
            {
                "commands": [
                    {"codeword": "BUY10_SPY", "action": "market_order", "symbol": "SPY", "qty": 10, "side": "buy"},
                    {"codeword": "FLATTEN", "action": "close_all"},
                ]
            }
        )

        commands = load_command_sheet(path)
        client = _FakeClient()

        execute_command("BUY10_SPY", client, commands)
        self.assertEqual(client.orders, [("SPY", 10, "buy")])

        execute_command("flatten", client, commands, dry_run=True)
        self.assertFalse(client.closed)

        execute_command("FLATTEN", client, commands, dry_run=False)
        self.assertTrue(client.closed)

    def test_formats_commands_table(self):
        commands = {
            "TEST": CommandAction(
                codeword="TEST", action="buy", symbol="XYZ", qty=5, side="buy", note="demo"
            )
        }
        rendered = format_commands(commands)
        self.assertIn("Codeword", rendered[0])
        self.assertTrue(any("TEST" in line for line in rendered))


if __name__ == "__main__":
    unittest.main()
