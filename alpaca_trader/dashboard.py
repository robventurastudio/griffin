"""Lightweight local dashboard powered by FastAPI."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

from .alpaca_client import AlpacaClient
from .logging_setup import APP_LOG_PATH, TRADES_LOG_PATH, ensure_log_dir
from .universe import DEFAULT_UNIVERSE

LOGGER = logging.getLogger("alpaca_trader.dashboard")
app = FastAPI(title="Griffin Trader Dashboard", docs_url=None, redoc_url=None)


@app.get("/", response_class=HTMLResponse)
async def overview() -> str:
    client = AlpacaClient()
    clock = client.get_clock()
    positions = client.list_positions()

    now = _fmt_datetime(clock.timestamp)
    open_state = "OPEN" if clock.is_open else "CLOSED"
    countdown_target = clock.next_close if clock.is_open else clock.next_open
    countdown = _format_delta(_ensure_datetime(countdown_target) - _ensure_datetime(clock.timestamp))

    html_positions = "".join(
        f"<tr><td>{p.symbol}</td><td>{p.qty}</td><td>{p.avg_entry_price}</td><td>{getattr(p, 'unrealized_pl', '')}</td></tr>"
        for p in positions
    ) or "<tr><td colspan='4'>No open positions</td></tr>"

    trades_rows = _read_recent_trades()

    return f"""
    <html>
        <head>
            <title>Griffin Dashboard</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 24px; }}
                h1, h2 {{ margin-bottom: 8px; }}
                table {{ border-collapse: collapse; width: 100%; margin-bottom: 16px; }}
                th, td {{ border: 1px solid #ccc; padding: 8px; text-align: left; }}
                .pill {{ padding: 4px 8px; border-radius: 6px; color: white; background: {('#4caf50' if clock.is_open else '#f44336')}; }}
            </style>
        </head>
        <body>
            <h1>Griffin Trader</h1>
            <p>Now (ET): {now}</p>
            <p>Market: <span class='pill'>{open_state}</span> | Countdown: {countdown}</p>
            <p>Universe: {', '.join(DEFAULT_UNIVERSE)}</p>

            <h2>Positions</h2>
            <table>
                <tr><th>Symbol</th><th>Qty</th><th>Avg Price</th><th>Unrealized PnL</th></tr>
                {html_positions}
            </table>

            <h2>Recent Trades</h2>
            <table>
                <tr><th>Timestamp</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Price</th><th>Status</th></tr>
                {trades_rows}
            </table>

            <h2>Log Tail</h2>
            <pre>{_read_log_tail()}</pre>
        </body>
    </html>
    """


def _read_recent_trades(limit: int = 20) -> str:
    ensure_log_dir()
    path = TRADES_LOG_PATH
    if not path.exists():
        return "<tr><td colspan='6'>No trades logged yet</td></tr>"
    rows = path.read_text().splitlines()
    header, *entries = rows
    html_rows = []
    for row in entries[-limit:]:
        cols = row.split(",")
        html_rows.append(
            "<tr>" + "".join(f"<td>{c}</td>" for c in cols) + "</tr>"
        )
    return "".join(html_rows)


def _read_log_tail(limit: int = 100) -> str:
    ensure_log_dir()
    path = APP_LOG_PATH
    if not path.exists():
        return "(no logs yet)"
    return "\n".join(path.read_text().splitlines()[-limit:])


def _ensure_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime()
    raise TypeError(value)


def _fmt_datetime(value) -> str:
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")
    return str(value)


def _format_delta(delta) -> str:
    seconds = int(max(delta.total_seconds(), 0))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def run(*, host: str = "127.0.0.1", port: int = 8000) -> int:
    """Start the dashboard server."""

    ensure_log_dir()
    LOGGER.info("Starting dashboard on %s:%s", host, port)
    uvicorn.run("alpaca_trader.dashboard:app", host=host, port=port, reload=False)
    return 0


if __name__ == "__main__":
    run()
