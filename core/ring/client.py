"""Async client for the Ring partner API (https://api.amazonvision.com).

Only endpoints and fields verified against the Developer Playground are used; see docs/gates.md.
All calls are server-side (the API blocks browsers via CORS).
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import urljoin, urlparse

import httpx

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.amazonvision.com"
# Seen from the Playground gateway (FL-04). Only retried for methods that are safe to repeat.
TRANSIENT_5XX = {500, 502, 503, 504}
IDEMPOTENT = {"GET", "HEAD", "DELETE"}


def redact(secret: str | None) -> str:
    """Show at most the first 8 characters of a secret, for logs."""
    if not secret:
        return "<none>"
    return secret[:8] + "…"


class RingError(Exception):
    def __init__(self, message: str, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


class RingAuthError(RingError):
    pass


class RingMediaNotFound(RingError):
    """No recorded media around the requested timestamp (HTTP 416 MEDIA_NOT_FOUND)."""


@dataclass
class LiveViewSession:
    device_id: str
    session_url: str
    sdp_answer: str


class RingClient:
    def __init__(
        self,
        token: str,
        base_url: str = DEFAULT_BASE_URL,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        media_transport: httpx.AsyncBaseTransport | None = None,
        max_retries: int = 5,
        backoff_base: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        timeout: float = 20.0,
    ):
        if not token:
            raise RingAuthError("RING_ACCESS_TOKEN is not set — generate a token in the Ring Developer Playground")
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {token}"},
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
        )
        # Signed media URLs must not receive the Bearer token, so they get their own client.
        self._media = httpx.AsyncClient(transport=media_transport, timeout=timeout, follow_redirects=True)
        log.debug("RingClient base=%s token=%s", self._base_url, redact(token))

    @classmethod
    def from_env(cls, **kwargs: Any) -> "RingClient":
        from dotenv import load_dotenv

        load_dotenv()
        return cls(os.environ.get("RING_ACCESS_TOKEN", ""), os.environ.get("RING_API_BASE", DEFAULT_BASE_URL), **kwargs)

    async def __aenter__(self) -> "RingClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()
        await self._media.aclose()

    # --- transport ---------------------------------------------------------

    def _same_host(self, url: str) -> bool:
        target = urlparse(urljoin(self._base_url + "/", url))
        return target.netloc == urlparse(self._base_url).netloc

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if not self._same_host(url):
            raise RingError(f"refusing to send credentials to foreign host: {urlparse(url).netloc}")
        idempotent = method in IDEMPOTENT
        for attempt in range(self._max_retries + 1):
            last = attempt == self._max_retries
            try:
                resp = await self._http.request(method, url, **kwargs)
            except httpx.TransportError as e:
                if not idempotent or last:
                    raise RingError(f"Ring API {method} {url}: {type(e).__name__}: {e}") from e
                delay = self._backoff_base * (2 ** attempt)
                log.warning("Ring %s on %s %s; retrying in %.2fs", type(e).__name__, method, url, delay)
                await self._sleep(delay)
                continue
            retryable = resp.status_code == 429 or (idempotent and resp.status_code in TRANSIENT_5XX)
            if not retryable or last:
                break
            delay = self._retry_delay(resp, attempt)
            log.warning("Ring %s on %s %s; retrying in %.2fs", resp.status_code, method, url, delay)
            await self._sleep(delay)
        if resp.status_code == 401:
            raise RingAuthError("Ring token expired or invalid — generate a new one in the Developer Playground",
                                status=401, body=resp.text)
        if resp.status_code == 416:
            raise RingMediaNotFound("No media at that time (MEDIA_NOT_FOUND)", status=416, body=resp.text)
        if resp.status_code >= 400:
            raise RingError(f"Ring API {method} {url} -> {resp.status_code}: {resp.text[:300]}",
                            status=resp.status_code, body=resp.text)
        return resp

    def _retry_delay(self, resp: httpx.Response, attempt: int) -> float:
        backoff = self._backoff_base * (2 ** attempt)
        try:
            return max(float(resp.headers.get("Retry-After", "")), backoff)
        except ValueError:
            return backoff

    # --- devices & events --------------------------------------------------

    async def list_devices(self) -> list[dict[str, Any]]:
        resp = await self._request("GET", "/v1/devices")
        return resp.json().get("data", [])

    async def get_events(self, device_id: str, max_pages: int = 20) -> list[dict[str, Any]]:
        """All history events for a device, following links.next pagination."""
        events: list[dict[str, Any]] = []
        url: str | None = f"/v1/history/devices/{device_id}/events"
        seen: set[str] = set()
        for _ in range(max_pages):
            if url is None or url in seen:
                break
            seen.add(url)
            body = (await self._request("GET", url)).json()
            events.extend(body.get("data", []))
            url = (body.get("links") or {}).get("next")
        return events

    async def latest_on_demand_event(self, device_id: str) -> dict[str, Any] | None:
        on_demand = [e for e in await self.get_events(device_id)
                     if e.get("attributes", {}).get("event_type") == "on_demand"]
        return max(on_demand, key=lambda e: e["attributes"].get("start", 0), default=None)

    # --- media ---------------------------------------------------------------

    async def get_snapshot(self, device_id: str, ts_ms: int) -> bytes:
        """JPEG closest to ts_ms. The API answers 303 to a short-lived signed URL, fetched without auth."""
        resp = await self._request(
            "POST", f"/v1/devices/{device_id}/media/image/download",
            json={"type": "at_timestamp", "timestamp": ts_ms},
        )
        if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image/"):
            return resp.content
        location = resp.headers.get("Location")
        if resp.status_code not in (301, 302, 303, 307) or not location:
            raise RingError(f"unexpected snapshot response {resp.status_code}", status=resp.status_code, body=resp.text)
        media = await self._media.get(urljoin(self._base_url + "/", location))
        if media.status_code == 416:
            raise RingMediaNotFound("No media at that time (MEDIA_NOT_FOUND)", status=416, body=media.text)
        if media.status_code >= 400:
            raise RingError(f"signed media URL -> {media.status_code}", status=media.status_code, body=media.text)
        return media.content

    # --- live view (WHEP) ----------------------------------------------------

    async def start_live_view(self, device_id: str, sdp_offer: str) -> LiveViewSession:
        resp = await self._request(
            "POST", f"/v1/devices/{device_id}/media/streaming/whep/sessions",
            content=sdp_offer.encode(), headers={"Content-Type": "application/sdp"},
        )
        location = resp.headers.get("Location")
        if not location:
            raise RingError("WHEP response had no Location header", status=resp.status_code, body=resp.text)
        return LiveViewSession(device_id, urljoin(self._base_url + "/", location), resp.text)

    async def stop_live_view(self, session: LiveViewSession | str) -> None:
        url = session.session_url if isinstance(session, LiveViewSession) else session
        await self._request("DELETE", url)
