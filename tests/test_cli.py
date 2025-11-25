import io
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from alpaca_trader import cli


def test_trade_parser_builds_and_parses():
    parser = cli._build_parser()
    args = parser.parse_args([
        "trade",
        "--symbols",
        "SPY,QQQ",
        "--qty",
        "2",
        "--per-trade-risk-pct",
        "1.5",
        "--max-daily-loss-pct",
        "4",
        "--close-buffer-min",
        "15",
        "--poll-seconds",
        "20",
    ])
    assert isinstance(args, types.SimpleNamespace) or hasattr(args, "handler")
    assert args.symbols == "SPY,QQQ"
    assert args.qty == 2
    assert args.per_trade_risk_pct == 1.5
    assert args.max_daily_loss_pct == 4
    assert args.close_buffer_min == 15
    assert args.poll_seconds == 20


def test_trade_handler_invokes_session(monkeypatch):
    called = {}

    def fake_session(**kwargs):
        called.update(kwargs)

    monkeypatch.setattr(cli, "run_trading_session", fake_session)
    args = cli._build_parser().parse_args(["trade", "--symbols", "AAPL"])
    cli._handle_trade(args)

    assert called["symbols"] == ["AAPL"]
    assert called["base_qty"] == args.qty


class CommandSheetCliTest(unittest.TestCase):
    def _write_sheet(self) -> Path:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        tmp.close()
        path = Path(tmp.name)
        path.write_text(
            json.dumps(
                {
                    "commands": [
                        {"codeword": "BUY5_SPY", "action": "buy", "symbol": "SPY", "qty": 5},
                        {"codeword": "FLATTEN", "action": "close_all"},
                    ]
                }
            )
        )
        return path

    def test_commands_handler_lists_sheet(self):
        sheet = self._write_sheet()
        parser = cli._build_parser()
        args = parser.parse_args(["commands", "--sheet", str(sheet)])

        buffer = io.StringIO()
        with mock.patch("sys.stdout", buffer):
            cli._handle_commands(args)

        output = buffer.getvalue()
        self.assertIn("Codeword", output)
        self.assertIn("BUY5_SPY", output)

    def test_commands_handler_executes_codeword_dry_run(self):
        sheet = self._write_sheet()
        parser = cli._build_parser()
        args = parser.parse_args(
            ["commands", "--sheet", str(sheet), "--codeword", "BUY5_SPY", "--dry-run"]
        )

        fake_client = mock.Mock()
        with mock.patch.object(cli, "_make_client", return_value=fake_client):
            cli._handle_commands(args)

        self.assertFalse(fake_client.submit_order.called)
