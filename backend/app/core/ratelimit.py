"""Small in-process sliding-window rate limiter.

Adequate for a single-process, single-user deployment. Behind multiple API processes
use a reverse proxy limit as well (see docs/DEPLOYMENT.md).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import Request

from app.core.errors import AppError


class RateLimited(AppError):
    status_code = 429
    code = "rate_limited"


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_seconds: float) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_seconds:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if len(self._hits) > 10_000:  # bound memory
                for k in list(self._hits)[:1000]:
                    if not self._hits[k]:
                        del self._hits[k]
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = SlidingWindowLimiter()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def enforce(request: Request, bucket: str, limit: int, window_seconds: float) -> None:
    if not limiter.hit(f"{bucket}:{client_ip(request)}", limit, window_seconds):
        raise RateLimited("Too many requests — please wait a moment and try again")
