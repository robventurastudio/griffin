"""Light-weight wrapper around :mod:`alpaca_trade_api` for the strategy code."""

from __future__ import annotations

import logging
import os
from typing import Iterable, Optional

from alpaca_trade_api import REST
from alpaca_trade_api.rest import APIError

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
        self._rest = REST(
            key_id or os.getenv("APCA_API_KEY_ID"),
            secret_key or os.getenv("APCA_API_SECRET_KEY"),
            base_url or os.getenv("APCA_API_BASE_URL"),
            api_version=api_version,
        )

    # ------------------------------------------------------------------
    # Basic REST passthrough methods
    # ------------------------------------------------------------------
    def get_clock(self):
        """Return the current market clock."""

        return self._rest.get_clock()

    def list_positions(self):
        """Return all currently-open positions in the account."""

        return self._rest.list_positions()

    def submit_order(self, **kwargs):
        """Submit an order verbatim to the Alpaca API."""

        return self._rest.submit_order(**kwargs)

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
        return self.submit_order(
            symbol=symbol,
            qty=qty,
            side=side,
            type="market",
            time_in_force="day",
        )

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
