import types

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
