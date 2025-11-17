"""Minimal engine helpers with timezone-safe formatting."""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from dateutil import parser


def _ensure_datetime(value: Any) -> dt.datetime:
    """Convert value to a timezone-aware UTC datetime.

    Handles standard ``datetime`` instances, ISO8601 strings, and pandas
    ``Timestamp`` objects without triggering the pandas ``tz_convert`` bug
    encountered when ``astimezone`` was called with no arguments.
    """

    if value is None:
        raise ValueError("Cannot format a missing datetime value")

    if isinstance(value, str):
        value = parser.isoparse(value)

    # pandas.Timestamp implements ``to_pydatetime``; convert early to avoid
    # invoking its ``astimezone`` override which raised during the dry run.
    to_py_dt = getattr(value, "to_pydatetime", None)
    if callable(to_py_dt):
        value = to_py_dt()

    if not isinstance(value, dt.datetime):
        raise TypeError(f"Unsupported datetime type: {type(value)}")

    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    else:
        value = value.astimezone(dt.timezone.utc)

    return value


def fmt_datetime(value: Any) -> str:
    """Format datetime-like values safely for logs."""

    normalized = _ensure_datetime(value)
    return normalized.strftime("%Y-%m-%d %H:%M:%S %Z")


def log_clock(clock: Optional[dict]) -> str:
    """Render a stable clock status message.

    ``clock`` is expected to look like the Alpaca clock payload, e.g.::

        {"timestamp": "2024-01-01T13:00:00Z", "is_open": True}
    """

    if not clock:
        return "(clock unavailable)"

    timestamp = clock.get("timestamp") or clock.get("next_open") or clock.get("next_close")
    return f"Market clock: {fmt_datetime(timestamp)} (open={clock.get('is_open')})"


def run_open_close_loop() -> None:
    """Placeholder loop that demonstrates timezone-safe logging.

    The original dry run surfaced a pandas ``tz_convert`` issue when we attempted
    to format the market clock. This helper keeps the loop simple while ensuring
    any datetime values are normalized before logging.
    """

    from alpaca_trader.alpaca_client import AlpacaClient  # imported lazily

    client = AlpacaClient()
    clock = client.get_clock()
    message = log_clock(clock)
    print(message)
