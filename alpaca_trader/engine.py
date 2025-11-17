"""Engine helpers with timezone-safe formatting and clock reporting."""
from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any, Optional

from dateutil import parser


LOG = logging.getLogger(__name__)


def _ensure_timezone(dt_obj: dt.datetime) -> dt.datetime:
    """Ensure the datetime is timezone-aware in UTC."""

    if dt_obj.tzinfo is None:
        return dt_obj.replace(tzinfo=dt.timezone.utc)
    return dt_obj.astimezone(dt.timezone.utc)


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

    return _ensure_timezone(value)


def fmt_datetime(value: Any) -> str:
    """Format datetime-like values safely for logs."""

    normalized = _ensure_datetime(value)
    return normalized.strftime("%Y-%m-%d %H:%M:%S %Z")


def _get_clock_field(clock: Any, field: str) -> Any:
    if clock is None:
        return None
    if isinstance(clock, dict):
        return clock.get(field)
    return getattr(clock, field, None)


def log_clock(clock: Optional[dict]) -> str:
    """Render a stable clock status message for Alpaca `Clock` payloads."""

    if not clock:
        return "(clock unavailable)"

    timestamp = (
        _get_clock_field(clock, "timestamp")
        or _get_clock_field(clock, "next_open")
        or _get_clock_field(clock, "next_close")
    )
    is_open = _get_clock_field(clock, "is_open")
    next_open = _get_clock_field(clock, "next_open")
    next_close = _get_clock_field(clock, "next_close")

    parts = [f"now={fmt_datetime(timestamp)}"]
    if next_open:
        parts.append(f"next_open={fmt_datetime(next_open)}")
    if next_close:
        parts.append(f"next_close={fmt_datetime(next_close)}")

    return f"Market clock ({'open' if is_open else 'closed'}): " + " | ".join(parts)


def run_open_close_loop(
    *,
    poll_seconds: int | None = None,
    universe=None,
    qty=None,
    direction=None,
    strategy=None,
    data=None,
    risk_limits=None,
    client=None,
    **_: Any,
) -> None:
    """Continuously log Alpaca market clock status with safe timezone handling.

    Accepts a broad set of keyword arguments so calls from older CLI entrypoints
    (e.g., ``run_open_close_loop(universe=..., qty=..., strategy=...)``) do not
    raise ``TypeError``. All parameters other than ``poll_seconds`` and
    ``client`` are ignored because this helper only monitors the market clock.
    """

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    from alpaca_trader.alpaca_client import AlpacaClient  # imported lazily

    poll_seconds = poll_seconds or 30
    client = client or AlpacaClient()
    LOG.info("Starting open/close loop (poll=%ss)", poll_seconds)

    try:
        while True:
            try:
                clock = client.get_clock()
                LOG.info(log_clock(clock))
            except Exception:
                LOG.exception("Failed to fetch or format market clock")

            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        LOG.info("Open/close loop interrupted by user; exiting cleanly")
