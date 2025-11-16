"""Light-weight wrapper around :mod:`alpaca_trade_api` for the strategy code."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Iterable, Optional

from alpaca_trade_api import REST
from alpaca_trade_api.rest import APIError

from .logging_setup import append_trade_log

LOGGER = logging.getLogger(__name__)


class AlpacaClient:
    """Minimal convenience layer for interacting with the Alpaca REST API.

    The official ``alpaca-trade-api`` SDK already exposes a comprehensive API
    surface.  This wrapper simply centralizes credential loading and offers a
    couple of ergonomic helpers that keep the strategy implementation tidy and
    easy to read.
    """

    def __init__(
        self,
        *,
        key_id: Optional[str] = None,
        secret_key: Optional[str] = None,
        base_url: Optional[str] = None,
        api_version: str = "v2",
    ) -> None:
        self._key_id = key_id or _require_env("APCA_API_KEY_ID")
        self._secret_key = secret_key or _require_env("APCA_API_SECRET_KEY")
        self._base_url = base_url or _require_env("APCA_API_BASE_URL")
        self._rest = REST(
            self._key_id,
            self._secret_key,
            self._base_url,
            api_version=api_version,
        )

    # ------------------------------------------------------------------
    # Basic REST passthrough methods
    # ------------------------------------------------------------------
    def get_clock(self):
        """Return the current market clock."""

        return self._rest.get_clock()

    def get_account(self):
        """Return the authenticated account details."""

        return self._rest.get_account()

    def get_latest_bar(self, symbol: str, *, feed: str = "iex"):
        """Return the latest bar for ``symbol`` using the configured feed."""

        return self._rest.get_latest_bar(symbol, feed=feed)

    def get_latest_trade(self, symbol: str, *, feed: str = "iex"):
        """Return the latest trade for ``symbol`` using the configured feed."""

        return self._rest.get_latest_trade(symbol, feed=feed)

    def get_bars(self, symbol: str, timeframe: str, *, start, end=None, limit: int = 500, feed: str = "iex"):
        """Fetch historical bars for ``symbol`` within the provided window."""

        return self._rest.get_bars(symbol, timeframe, start=start, end=end, limit=limit, feed=feed)

    def list_positions(self):
        """Return all currently-open positions in the account."""

        return self._rest.list_positions()

    def submit_order(self, **kwargs):
        """Submit an order verbatim to the Alpaca API."""

        try:
            return self._rest.submit_order(**kwargs)
        except APIError as exc:  # pragma: no cover - network dependent
            if exc.status_code == 429:
                LOGGER.warning("Rate limited while submitting order; consider slowing requests")
            LOGGER.exception("Order submission failed")
            raise

    # ------------------------------------------------------------------
    # Higher-level convenience helpers used by the demo strategy
    # ------------------------------------------------------------------
    def has_position_for(self, symbols: Iterable[str]) -> bool:
        """Return ``True`` if any of ``symbols`` currently have a position."""

        open_positions = {position.symbol for position in self.list_positions()}
        return any(symbol in open_positions for symbol in symbols)

    def submit_market_order(self, symbol: str, qty: int, side: str):
        """Submit a simple market order for ``symbol`` with ``qty`` shares."""

        LOGGER.info("Submitting %s order for %s (%s shares)", side, symbol, qty)
        order = self.submit_order(
            symbol=symbol,
            qty=qty,
            side=side,
            type="market",
            time_in_force="day",
        )
        _log_trade(order, side=side, qty=qty)
        return order

    def close_position(self, symbol: str):
        """Attempt to close the open position in ``symbol``.

        The REST API raises :class:`alpaca_trade_api.rest.APIError` if there is
        no open position for the requested symbol.  Swallowing that error keeps
        the strategy loop idempotent and allows the script to be re-run after
        a partial failure without manual cleanup.
        """

        LOGGER.info("Closing any remaining position in %s", symbol)
        try:
            return self._rest.close_position(symbol)
        except APIError as exc:  # pragma: no cover - requires network access
            if exc.status_code == 404:
                LOGGER.debug("No open position for %s, skipping", symbol)
                return None
            raise

    def cancel_all_orders(self):
        """Cancel any open orders lingering from previous runs."""

        LOGGER.info("Cancelling any outstanding orders")
        return self._rest.cancel_all_orders()


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        LOGGER.error("Missing required environment variable: %s", name)
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _log_trade(order, *, side: str, qty: int) -> None:
    timestamp = getattr(order, "submitted_at", None) or datetime.utcnow()
    order_id = getattr(order, "id", None)
    price = getattr(order, "filled_avg_price", None) or getattr(order, "limit_price", None)
    status = getattr(order, "status", None)
    symbol = getattr(order, "symbol", "")
    append_trade_log(
        timestamp=str(timestamp),
        symbol=symbol,
        side=side,
        qty=qty,
        price=float(price) if price is not None else None,
        order_id=order_id,
        status=status,
    )
