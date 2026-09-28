"""The HTTP client providers use: an honest User-Agent, short timeouts, a fresh client per request
(serverless functions may not keep an event loop between invocations)."""

import httpx

from app.core.config import get_settings

APP_VERSION = "0.1.0"

# tests replace this with an httpx.MockTransport (no real network in tests)
transport: httpx.AsyncBaseTransport | None = None


def user_agent() -> str:
    contact = get_settings().provider_contact.strip()
    return f"UyenThuCac/{APP_VERSION}" + (f" (+{contact})" if contact else "")


def new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=transport,
        headers={"User-Agent": user_agent(), "Accept": "application/json"},
        timeout=httpx.Timeout(8.0, connect=4.0),
        follow_redirects=True,
    )
