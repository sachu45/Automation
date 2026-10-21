from __future__ import annotations

import json
import logging
import time
import uuid

from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger("app.http")

UPLOAD_PATHS = ("/api/leads/import",)


class BodySizeLimitMiddleware:
    """Reject request bodies over the configured size (counts streamed bytes, not just Content-Length)."""

    def __init__(self, app: ASGIApp, max_bytes: int, max_upload_bytes: int):
        self.app = app
        self.max_bytes = max_bytes
        self.max_upload_bytes = max_upload_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        limit = self.max_upload_bytes if path.startswith(UPLOAD_PATHS) else self.max_bytes
        headers = dict(scope.get("headers") or [])
        cl = headers.get(b"content-length")
        if cl is not None and cl.isdigit() and int(cl) > limit:
            await _reject(send, limit)
            return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # FastAPI re-raises HTTPException from body parsing, so this becomes a clean 413.
                    raise HTTPException(status_code=413, detail=f"Request body exceeds {limit} bytes")
            return message

        await self.app(scope, limited_receive, send)


async def _reject(send: Send, limit: int) -> None:
    body = json.dumps({"error": {"code": "payload_too_large", "message": f"Request body exceeds {limit} bytes", "details": None}}).encode()
    await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": body})


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid.uuid4().hex[:12]
        started = time.monotonic()
        status_holder = {"status": 0}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = list(message.get("headers", []))
                headers += [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-request-id", request_id.encode()),
                    (
                        b"content-security-policy",
                        b"default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                        b"script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
                    ),
                ]
                if scope.get("path", "").startswith("/api/"):
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)
        path = scope.get("path", "")
        if not path.startswith("/assets"):
            # Never log query strings or bodies (may contain personal data)
            log.info(
                "%s %s -> %s (%dms) rid=%s",
                scope.get("method"),
                path,
                status_holder["status"],
                int((time.monotonic() - started) * 1000),
                request_id,
            )
