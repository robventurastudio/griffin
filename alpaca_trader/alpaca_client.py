"""Thin wrapper around the Alpaca REST client with safer defaults.

The goal of this module is to make it harder to misconfigure the base URL
or subscribe to the wrong SSE endpoint. It also centralizes the environment
variable names we expect so bootstrapping a new machine is predictable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse, urlunparse

from alpaca_trade_api import REST

__all__ = ["AlpacaClient", "normalize_base_url", "build_events_url"]


def normalize_base_url(raw_url: Optional[str]) -> str:
    """Normalize the Alpaca base URL.

    * Defaults to the paper trading endpoint when nothing is provided.
    * Strips trailing slashes.
    * Removes any trailing "/v2" to avoid double-appending the version.
    * Ensures a scheme is present (defaulting to https).
    """

    default_url = "https://paper-api.alpaca.markets"
    parsed = urlparse(raw_url or default_url)

    # Normalize path: remove trailing slash and stray "/v2" suffixes.
    path = parsed.path.rstrip("/")
    if path.endswith("/v2"):
        path = path[:-3]  # drop the "/v2" suffix
    normalized = parsed._replace(path=path, scheme=parsed.scheme or "https")

    normalized_url = urlunparse(normalized)
    return normalized_url.rstrip("/")


def build_events_url(base_url: str, event_type: str) -> str:
    """Compose a stable SSE events URL.

    Alpaca's events API lives outside the REST versioned path, so we keep it
    separate from the REST client's base URL. This function guards against
    accidental double-appends when the caller provided a base URL that already
    included "/v2".
    """

    supported_events = {"trades", "orders", "news"}
    if event_type not in supported_events:
        raise ValueError(
            f"Unsupported event type '{event_type}'. "
            f"Supported: {', '.join(sorted(supported_events))}"
        )

    clean_base = normalize_base_url(base_url)
    return f"{clean_base}/events/v1/events/{event_type}"


@dataclass
class AlpacaClient:
    """Alpaca client configured from environment variables by default."""

    api_key_id: Optional[str] = None
    api_secret_key: Optional[str] = None
    base_url: Optional[str] = None
    trading_mode: str = "paper"

    def __post_init__(self) -> None:
        self.api_key_id = self.api_key_id or os.getenv("APCA_API_KEY_ID")
        self.api_secret_key = self.api_secret_key or os.getenv("APCA_API_SECRET_KEY")
        raw_base_url = self.base_url or os.getenv("APCA_API_BASE_URL")
        self.base_url = normalize_base_url(raw_base_url)
        self.trading_mode = (
            "paper" if "paper-api" in self.base_url else "live"
        )

        if not self.api_key_id or not self.api_secret_key:
            raise EnvironmentError(
                "APCA_API_KEY_ID and APCA_API_SECRET_KEY must be set before using AlpacaClient"
            )

        self._rest = REST(
            self.api_key_id,
            self.api_secret_key,
            base_url=self.base_url,
            api_version="v2",
        )

    @property
    def rest(self) -> REST:
        return self._rest

    # Convenience wrappers -------------------------------------------------
    def get_account(self):
        return self._rest.get_account()

    def get_clock(self):
        return self._rest.get_clock()

    def latest_bar(self, symbol: str):
        return self._rest.get_latest_bar(symbol)

    def list_positions(self):
        return self._rest.list_positions()

    def submit_order(self, symbol: str, qty: int, side: str) -> None:
        self._rest.submit_order(symbol, qty, side, type="market", time_in_force="day")

    def close_all_positions(self):
        self._rest.close_all_positions()

    def events_endpoint(self, event_type: str) -> str:
        """Return the SSE URL to subscribe to a specific event type."""

        return build_events_url(self.base_url, event_type)

    def get_previous_close(self, symbol: str):
        """Return the prior close for ``symbol`` using a 1D bar lookup."""

        bars = self._rest.get_bars(symbol, "1Day", limit=1)
        if not bars:
            return None

        bar = bars[0]
        return getattr(bar, "c", None) or getattr(bar, "close", None)
