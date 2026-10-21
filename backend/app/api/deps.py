from __future__ import annotations

import hmac
from collections.abc import Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, Forbidden
from app.core.security import read_token
from app.db.session import get_db
from app.services.settings import get_session_version

SESSION_COOKIE = "po_session"
CSRF_COOKIE = "po_csrf"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


def db_session() -> Iterator[Session]:
    yield from get_db()


def require_auth(request: Request, db: Session = Depends(db_session)) -> dict:  # type: ignore[type-arg]
    token = request.cookies.get(SESSION_COOKIE)
    data = read_token("session", token) if token else None
    if not data or int(data.get("v", 0)) != get_session_version(db):
        raise Unauthorized("Sign in required")
    if request.method not in SAFE_METHODS:
        header = request.headers.get(CSRF_HEADER, "")
        cookie = request.cookies.get(CSRF_COOKIE, "")
        bound = str(data.get("c", ""))
        if not header or not hmac.compare_digest(header, cookie) or not hmac.compare_digest(header, bound):
            raise Forbidden("CSRF token missing or invalid", code="csrf_failed")
    return data


def session_cookie_kwargs() -> dict:  # type: ignore[type-arg]
    s = get_settings()
    return {
        "httponly": True,
        "secure": s.cookie_secure,
        "samesite": "strict",
        "max_age": s.session_max_age_hours * 3600,
        "path": "/",
    }
