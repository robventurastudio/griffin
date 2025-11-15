"""Lightweight helper that streams live trade data for configured symbols."""

from __future__ import annotations

import logging
import os
import threading
from typing import Dict, Iterable, List, Optional

from alpaca_trade_api.stream import Stream

LOGGER = logging.getLogger(__name__)


class LiveTickerFeed:
    """Subscribe to Alpaca's streaming API and keep the last trade per symbol."""

    def __init__(
        self,
        *,
        symbols: Iterable[str],
        key_id: Optional[str] = None,
        secret_key: Optional[str] = None,
        base_url: Optional[str] = None,
        data_feed: str = "iex",
    ) -> None:
        self.symbols: List[str] = sorted({symbol.upper() for symbol in symbols})
        self._key_id = key_id or os.getenv("APCA_API_KEY_ID")
        self._secret_key = secret_key or os.getenv("APCA_API_SECRET_KEY")
        self._base_url = base_url or os.getenv("APCA_API_BASE_URL")
        self._data_feed = data_feed

        self._latest: Dict[str, Dict[str, str]] = {}
        self._lock = threading.Lock()
        self._stream: Optional[Stream] = None
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        if not self.symbols:
            LOGGER.warning("No symbols provided for live ticker feed; skipping stream")
            return
        if self._thread and self._thread.is_alive():
            return
        if not self._key_id or not self._secret_key:
            raise RuntimeError("Alpaca credentials are required for live data streaming")

        LOGGER.info(
            "Starting live ticker feed for %s (data=%s)", ", ".join(self.symbols), self._data_feed
        )
        self._stream = Stream(
            self._key_id,
            self._secret_key,
            base_url=self._base_url,
            data_feed=self._data_feed,
        )
        for symbol in self.symbols:
            async def _handler(trade, *, _symbol=symbol):
                self._handle_trade(trade.symbol or _symbol, trade)

            self._stream.subscribe_trades(_handler, symbol)

        self._thread = threading.Thread(target=self._stream.run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._stream:
            LOGGER.info("Stopping live ticker feed")
            self._stream.stop()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    # ------------------------------------------------------------------
    # Data accessors
    # ------------------------------------------------------------------
    def snapshot(self, *, limit: Optional[int] = None) -> Dict[str, Dict[str, str]]:
        with self._lock:
            items = list(self._latest.items())
        if limit:
            items = items[:limit]
        return {symbol: payload for symbol, payload in items}

    def format_snapshot(self, *, limit: Optional[int] = None) -> str:
        data = self.snapshot(limit=limit)
        if not data:
            return "(no ticks yet)"
        parts = []
        for symbol, payload in data.items():
            price = payload.get("price")
            size = payload.get("size")
            parts.append(f"{symbol}=${price} ({size} shares)")
        return ", ".join(parts)

    def get_last_trade(self, symbol: str) -> Optional[Dict[str, str]]:
        with self._lock:
            return self._latest.get(symbol.upper())

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _handle_trade(self, symbol: str, trade) -> None:
        payload = {
            "price": getattr(trade, "price", None),
            "size": getattr(trade, "size", None),
            "timestamp": getattr(trade, "timestamp", None),
        }
        with self._lock:
            self._latest[symbol] = payload
