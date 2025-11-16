"""Centralized logging configuration for the trading app."""

from __future__ import annotations

import csv
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Optional

APP_LOG_PATH = Path("logs/app.log")
ERROR_LOG_PATH = Path("logs/errors.log")
TRADES_LOG_PATH = Path("logs/trades.csv")


def ensure_log_dir() -> None:
    """Create the logs directory if it does not already exist."""

    APP_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def configure_logging(*, console: bool = True, level: int = logging.INFO) -> logging.Logger:
    """Configure the root ``alpaca_trader`` logger with rotating file handlers."""

    ensure_log_dir()

    logger = logging.getLogger("alpaca_trader")
    logger.setLevel(level)
    logger.handlers = []

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    app_handler = TimedRotatingFileHandler(APP_LOG_PATH, when="midnight", backupCount=7)
    app_handler.setFormatter(formatter)
    app_handler.setLevel(level)
    logger.addHandler(app_handler)

    error_handler = TimedRotatingFileHandler(ERROR_LOG_PATH, when="midnight", backupCount=7)
    error_handler.setFormatter(formatter)
    error_handler.setLevel(logging.ERROR)
    logger.addHandler(error_handler)

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.setLevel(level)
        logger.addHandler(console_handler)

    logger.debug("Logging configured (console=%s, level=%s)", console, logging.getLevelName(level))
    return logger


def append_trade_log(
    *,
    timestamp: str,
    symbol: str,
    side: str,
    qty: int,
    price: Optional[float],
    order_id: Optional[str],
    status: Optional[str],
) -> None:
    """Append a single trade entry to ``logs/trades.csv``.

    The file is created on demand and always uses a header for readability.
    """

    ensure_log_dir()
    file_exists = TRADES_LOG_PATH.exists()
    with TRADES_LOG_PATH.open("a", newline="") as csvfile:
        writer = csv.DictWriter(
            csvfile,
            fieldnames=["timestamp", "symbol", "side", "qty", "price", "order_id", "status"],
        )
        if not file_exists:
            writer.writeheader()
        writer.writerow(
            {
                "timestamp": timestamp,
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "price": price if price is not None else "",
                "order_id": order_id or "",
                "status": status or "",
            }
        )

