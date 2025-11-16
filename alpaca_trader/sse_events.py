"""Server-Sent Events (SSE) helper for Alpaca Broker event streams.

This module keeps the SSE handling lightweight, resilient, and explicit:
- reconnects with exponential backoff on failures
- propagates the last seen ULID/ID so consumers can resume without gaps
- ignores heartbeat comments while keeping an eye on stalled connections
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Callable, Dict, Optional, Union

import requests

LOGGER = logging.getLogger(__name__)

JsonCallback = Callable[[Dict], None]


class SSEEventClient:
    """Stream Alpaca Broker events using the SSE protocol with reconnection support."""

    def __init__(
        self,
        *,
        key_id: Optional[str] = None,
        secret_key: Optional[str] = None,
        events_base_url: Optional[str] = None,
        api_version: str = "v1",
        request_timeout: int = 30,
        backoff_initial: int = 5,
        backoff_max: int = 60,
    ) -> None:
        self._key_id = key_id or _require_env("APCA_API_KEY_ID")
        self._secret_key = secret_key or _require_env("APCA_API_SECRET_KEY")
        self._base_url = _resolve_events_base(events_base_url)
        self._api_version = api_version.strip("/")
        self._request_timeout = request_timeout
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max

    def stream_events(
        self,
        event_type: str,
        *,
        since_ulid: Optional[str] = None,
        until_ulid: Optional[str] = None,
        since: Optional[Union[str, datetime]] = None,
        until: Optional[Union[str, datetime]] = None,
        on_event: Optional[JsonCallback] = None,
        stop_event: Optional[threading.Event] = None,
        heartbeat_timeout: int = 90,
    ) -> None:
        """Consume an SSE stream and invoke ``on_event`` for each JSON payload.

        The stream automatically reconnects with exponential backoff, resumes
        from the last seen ULID/ID, and stops when ``stop_event`` is set or the
        caller raises out of the callback.
        """

        last_ulid = since_ulid
        last_cursor: Optional[str] = _normalize_since(since)
        backoff = self._backoff_initial
        session = requests.Session()

        while not (stop_event and stop_event.is_set()):
            url = f"{self._base_url}/{self._api_version}/events/{event_type}"
            params = _build_params(last_ulid, until_ulid, last_cursor, _normalize_since(until))
            LOGGER.info("Connecting to SSE %s with params %s", url, params)

            try:
                with session.get(
                    url,
                    headers=_auth_headers(self._key_id, self._secret_key),
                    params=params,
                    stream=True,
                    timeout=self._request_timeout,
                ) as resp:
                    resp.raise_for_status()
                    backoff = self._backoff_initial
                    last_heartbeat = time.monotonic()

                    for payload in _iter_sse_events(resp):
                        if stop_event and stop_event.is_set():
                            LOGGER.info("Stop requested; closing SSE stream")
                            return

                        if payload is None:
                            continue

                        if payload.get("type") == "heartbeat":
                            last_heartbeat = time.monotonic()
                            LOGGER.debug("Heartbeat from SSE stream")
                            continue

                        last_ulid = payload.get("event_ulid") or last_ulid
                        last_cursor = payload.get("event_id") or last_cursor
                        if on_event:
                            on_event(payload)

                        if heartbeat_timeout and (time.monotonic() - last_heartbeat) > heartbeat_timeout:
                            LOGGER.warning("No SSE heartbeat for %ss; reconnecting", heartbeat_timeout)
                            break
            except Exception:
                LOGGER.exception("SSE stream error; reconnecting after %ss", backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, self._backoff_max)
            else:
                LOGGER.info("SSE stream ended; reconnecting after %ss", backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, self._backoff_max)

        session.close()


def _iter_sse_events(response: requests.Response):
    """Yield parsed SSE events from a streaming HTTP response."""

    data_lines = []
    event_id: Optional[str] = None
    event_type: Optional[str] = None

    for raw_line in response.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue
        line = raw_line.strip()
        if not line:
            payload = _assemble_payload(data_lines, event_id, event_type)
            data_lines = []
            event_id = None
            event_type = None
            if payload is not None:
                yield payload
            continue

        if line.startswith(":"):
            comment = line.lstrip(":").strip()
            if not comment:
                continue
            lowered = comment.lower()
            if "heartbeat" in lowered:
                yield {"type": "heartbeat", "comment": comment}
            elif "internal server error" in lowered:
                LOGGER.warning("SSE server reported internal error: %s", comment)
                yield {"type": "server_error", "comment": comment}
            elif "reading too slowly" in lowered:
                LOGGER.warning("SSE server signaled slow client: %s", comment)
            else:
                LOGGER.debug("SSE comment: %s", comment)
            continue

        if line.startswith("id:"):
            event_id = line[len("id:") :].strip()
            continue

        if line.startswith("event:"):
            event_type = line[len("event:") :].strip()
            continue

        if line.startswith("data:"):
            data_lines.append(line[len("data:") :].strip())
            continue

    # Flush any trailing data without a closing newline
    if data_lines:
        payload = _assemble_payload(data_lines, event_id, event_type)
        if payload is not None:
            yield payload


def _assemble_payload(data_lines, event_id: Optional[str], event_type: Optional[str]):
    if not data_lines:
        return None
    body = "\n".join(data_lines)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        LOGGER.warning("Non-JSON SSE payload received: %s", body)
        return {"type": event_type or "unknown", "raw": body, "event_id": event_id}

    if isinstance(payload, dict):
        if event_id and "event_id" not in payload:
            payload["event_id"] = event_id
        if event_type and "type" not in payload:
            payload["type"] = event_type
    return payload


def _auth_headers(key_id: str, secret_key: str) -> Dict[str, str]:
    return {
        "Accept": "text/event-stream",
        "Cache-Control": "no-cache",
        "APCA-API-KEY-ID": key_id,
        "APCA-API-SECRET-KEY": secret_key,
    }


def _build_params(
    since_ulid: Optional[str],
    until_ulid: Optional[str],
    since: Optional[str],
    until: Optional[str],
) -> Dict[str, str]:
    params: Dict[str, str] = {}
    if since_ulid:
        params["since_ulid"] = since_ulid
    elif since:
        params["since"] = since

    if until_ulid:
        params["until_ulid"] = until_ulid
    elif until:
        params["until"] = until
    return params


def _normalize_since(value: Optional[Union[str, datetime]]) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        # RFC3339 formatting; ensure timezone awareness if provided
        return value.isoformat()
    return str(value)


def _resolve_events_base(events_base_url: Optional[str]) -> str:
    if events_base_url:
        return events_base_url.rstrip("/")

    env_base = os.getenv("APCA_EVENTS_BASE_URL")
    if env_base:
        return env_base.rstrip("/")

    api_base = os.getenv("APCA_API_BASE_URL")
    if not api_base:
        raise RuntimeError("APCA_EVENTS_BASE_URL or APCA_API_BASE_URL must be set for SSE")
    return f"{api_base.rstrip('/')}/events"


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        LOGGER.error("Missing required environment variable: %s", name)
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


__all__ = ["SSEEventClient"]
