from __future__ import annotations

import ipaddress
import secrets

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import CSRF_COOKIE, SESSION_COOKIE, Unauthorized, db_session, require_auth, session_cookie_kwargs
from app.core import ratelimit
from app.core.config import get_settings
from app.core.errors import Forbidden, ValidationFailed
from app.core.security import hash_password, make_token, read_token, verify_password
from app.services import audit
from app.services.settings import get_password_hash, get_session_version, set_password_hash

router = APIRouter(prefix="/api/auth", tags=["auth"])


class PasswordIn(BaseModel):
    password: str = Field(min_length=1, max_length=500)


class SetupIn(BaseModel):
    password: str = Field(min_length=10, max_length=500)


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=500)


def _is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost", "testclient")


def _issue_session(response: Response, db: Session) -> str:
    csrf = secrets.token_urlsafe(32)
    s = get_settings()
    token = make_token("session", {"v": get_session_version(db), "c": csrf}, max_age_seconds=s.session_max_age_hours * 3600)
    response.set_cookie(SESSION_COOKIE, token, **session_cookie_kwargs())
    kw = session_cookie_kwargs()
    kw["httponly"] = False  # the SPA reads it and echoes it in X-CSRF-Token
    response.set_cookie(CSRF_COOKIE, csrf, **kw)
    return csrf


@router.get("/status")
def status(request: Request, db: Session = Depends(db_session)) -> dict:  # type: ignore[type-arg]
    token = request.cookies.get(SESSION_COOKIE)
    data = read_token("session", token) if token else None
    authed = bool(data and int(data.get("v", 0)) == get_session_version(db))
    return {"setup_required": get_password_hash(db) is None, "authenticated": authed}


@router.post("/setup")
def setup(body: SetupIn, request: Request, response: Response, db: Session = Depends(db_session)) -> dict:  # type: ignore[type-arg]
    ratelimit.enforce(request, "auth_setup", 5, 60)
    if get_password_hash(db) is not None:
        raise Forbidden("Setup has already been completed")
    if not _is_loopback(request):
        raise Forbidden("First-run setup is only allowed from this computer (localhost). Set ADMIN_PASSWORD for remote setup.")
    set_password_hash(db, hash_password(body.password))
    audit.record(db, "auth.setup", "Admin password created")
    db.commit()
    csrf = _issue_session(response, db)
    return {"ok": True, "csrf": csrf}


@router.post("/login")
def login(body: PasswordIn, request: Request, response: Response, db: Session = Depends(db_session)) -> dict:  # type: ignore[type-arg]
    ratelimit.enforce(request, "auth_login", 5, 60)
    stored = get_password_hash(db)
    if stored is None:
        raise ValidationFailed("Setup required", code="setup_required")
    if not verify_password(body.password, stored):
        audit.record(db, "auth.login_failed", "Failed sign-in attempt", details={"ip": ratelimit.client_ip(request)})
        db.commit()
        raise Unauthorized("Incorrect password")
    csrf = _issue_session(response, db)
    audit.record(db, "auth.login", "Signed in")
    db.commit()
    return {"ok": True, "csrf": csrf}


@router.post("/logout")
def logout(response: Response) -> dict:  # type: ignore[type-arg]
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}


@router.post("/change-password", dependencies=[Depends(require_auth)])
def change_password(body: ChangePasswordIn, request: Request, response: Response, db: Session = Depends(db_session)) -> dict:  # type: ignore[type-arg]
    ratelimit.enforce(request, "auth_change", 5, 60)
    stored = get_password_hash(db)
    if not stored or not verify_password(body.current_password, stored):
        raise Unauthorized("Current password is incorrect")
    set_password_hash(db, hash_password(body.new_password))  # bumps session version: signs out other sessions
    audit.record(db, "auth.password_changed", "Admin password changed; all sessions invalidated")
    db.commit()
    csrf = _issue_session(response, db)
    return {"ok": True, "csrf": csrf}
