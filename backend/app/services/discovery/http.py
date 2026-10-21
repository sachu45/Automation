"""Bounded-retry JSON HTTP helper for external discovery APIs (Apollo, Google)."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.services.discovery.sources import SourceError

RETRYABLE = {429, 500, 502, 503, 504}


def request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    provider: str,
    headers: dict[str, str] | None = None,
    params: Any = None,
    json: Any = None,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Retries timeouts/429/5xx with exponential backoff + jitter. Never retries auth, validation or quota errors."""
    last: str = ""
    for attempt in range(1, max_attempts + 1):
        try:
            resp = client.request(method, url, headers=headers, params=params, json=json)
        except httpx.TimeoutException:
            last = "timeout"
        except httpx.HTTPError as exc:
            last = f"network error ({type(exc).__name__})"
        else:
            if resp.status_code in (401, 403):
                raise SourceError(
                    f"{provider} rejected the API key or this plan lacks access ({resp.status_code})", code="auth", permanent=True
                )
            if resp.status_code == 402:
                raise SourceError(f"{provider} quota or credits exhausted (402)", code="quota", permanent=True)
            if resp.status_code == 429:
                retry_after = resp.headers.get("retry-after", "")
                last = "rate limited (429)"
                if attempt < max_attempts:
                    delay = float(retry_after) if retry_after.isdigit() else base_delay * 2 ** (attempt - 1)
                    sleep(min(delay, 30) * random.uniform(0.85, 1.15))
                    continue
                raise SourceError(f"{provider} rate limit reached; will retry on the next run", code="rate_limited")
            if resp.status_code in RETRYABLE:
                last = f"server error ({resp.status_code})"
            elif resp.status_code >= 400:
                text = resp.text[:200].lower()
                if "credit" in text or "quota" in text:
                    raise SourceError(f"{provider} quota or credits exhausted ({resp.status_code})", code="quota", permanent=True)
                raise SourceError(f"{provider} rejected the request ({resp.status_code})", code="bad_request", permanent=True)
            else:
                try:
                    data = resp.json()
                except ValueError as exc:
                    raise SourceError(f"{provider} returned a non-JSON response", code="invalid_response", permanent=True) from exc
                if not isinstance(data, dict):
                    raise SourceError(f"{provider} returned an unexpected response", code="invalid_response", permanent=True)
                return data
        if attempt < max_attempts:
            sleep(base_delay * 2 ** (attempt - 1) * random.uniform(0.85, 1.15))
    raise SourceError(f"{provider} request failed after {max_attempts} attempts: {last}", code="unavailable")
