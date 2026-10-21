from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_auth
from app.core import ratelimit
from app.core.errors import AppError
from app.models import EmailAccount
from app.providers.email import oauth
from app.schemas.accounts import AccountIn, AccountOut, AccountUpdate, CredentialRotate
from app.services import accounts as svc
from app.services import dns_check

router = APIRouter(prefix="/api/accounts", tags=["accounts"])
auth = [Depends(require_auth)]


@router.get("", response_model=list[AccountOut], dependencies=auth)
def list_accounts(db: Session = Depends(db_session)) -> list[AccountOut]:
    return [svc.to_out(db, a) for a in db.scalars(select(EmailAccount).order_by(EmailAccount.id))]


@router.get("/providers", dependencies=auth)
def providers() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [
        {
            "provider": "smtp_imap",
            "label": "Generic SMTP / IMAP",
            "oauth_configured": None,
            "permissions": "Uses an SMTP login to send and (optionally) an IMAP login to read the inbox read-only. "
            "Prefer an app-specific password where your provider supports it.",
        }
    ]
    for p, label in (("gmail_oauth", "Gmail / Google Workspace (OAuth)"), ("microsoft_oauth", "Microsoft 365 (OAuth)")):
        cfg = oauth.provider_config(p)
        out.append(
            {
                "provider": p,
                "label": label,
                "oauth_configured": oauth.is_configured(p),
                "scopes": list(cfg.scopes),
                "permissions": cfg.scope_explanation,
            }
        )
    return out


@router.post("", response_model=AccountOut, status_code=201, dependencies=auth)
def create(body: AccountIn, db: Session = Depends(db_session)) -> AccountOut:
    acc = svc.create_account(db, body)
    db.commit()
    return svc.to_out(db, acc)


@router.get("/{account_id}", response_model=AccountOut, dependencies=auth)
def get(account_id: int, db: Session = Depends(db_session)) -> AccountOut:
    return svc.to_out(db, svc.get_account(db, account_id))


@router.patch("/{account_id}", response_model=AccountOut, dependencies=auth)
def update(account_id: int, body: AccountUpdate, db: Session = Depends(db_session)) -> AccountOut:
    acc = svc.update_account(db, account_id, body)
    db.commit()
    return svc.to_out(db, acc)


@router.delete("/{account_id}", dependencies=auth)
def delete(account_id: int, db: Session = Depends(db_session)) -> dict[str, bool]:
    svc.delete_account(db, account_id)
    db.commit()
    return {"ok": True}


@router.post("/{account_id}/test", dependencies=auth)
def test(account_id: int, request: Request, db: Session = Depends(db_session)) -> dict[str, Any]:
    ratelimit.enforce(request, "account_test", 10, 60)
    result = svc.test_connection(db, account_id)
    db.commit()
    return {"result": result, "account": svc.to_out(db, svc.get_account(db, account_id)).model_dump()}


class EnableIn(BaseModel):
    enabled: bool


@router.post("/{account_id}/sending", response_model=AccountOut, dependencies=auth)
def set_sending(account_id: int, body: EnableIn, db: Session = Depends(db_session)) -> AccountOut:
    acc = svc.set_sending_enabled(db, account_id, body.enabled)
    db.commit()
    return svc.to_out(db, acc)


@router.post("/{account_id}/rotate", response_model=AccountOut, dependencies=auth)
def rotate(account_id: int, body: CredentialRotate, db: Session = Depends(db_session)) -> AccountOut:
    acc = svc.rotate_credentials(db, account_id, body.smtp_password, body.imap_password)
    db.commit()
    return svc.to_out(db, acc)


@router.post("/{account_id}/disconnect", response_model=AccountOut, dependencies=auth)
def disconnect(account_id: int, db: Session = Depends(db_session)) -> AccountOut:
    acc = svc.disconnect(db, account_id)
    db.commit()
    return svc.to_out(db, acc)


@router.post("/{account_id}/sync", dependencies=auth)
def sync_now(account_id: int, request: Request, db: Session = Depends(db_session)) -> dict[str, Any]:
    """Queue an inbox sync for the worker (does not run inline)."""
    from app.db.base import utcnow
    from app.models import ScheduledJob
    from app.models.enums import JobKind

    ratelimit.enforce(request, "sync_now", 6, 60)
    acc = svc.get_account(db, account_id)
    if not acc.imap_enabled:
        raise AppError("Inbox sync is not enabled for this account", status_code=422)
    now = utcnow()
    db.add(
        ScheduledJob(
            kind=JobKind.SYNC_INBOX,
            idempotency_key=f"sync:{acc.id}:manual:{now.timestamp()}",
            run_at=now,
            account_id=acc.id,
            max_attempts=1,
        )
    )
    db.commit()
    return {"queued": True}


@router.get("/{account_id}/dns-check", dependencies=auth)
def dns(
    account_id: int, request: Request, selector: str | None = Query(default=None, max_length=63), db: Session = Depends(db_session)
) -> dict[str, Any]:
    ratelimit.enforce(request, "dns_check", 10, 60)
    acc = svc.get_account(db, account_id)
    return dns_check.check_domain(acc.email.rsplit("@", 1)[-1], selector)


@router.post("/{account_id}/oauth/start", dependencies=auth)
def oauth_start(account_id: int, db: Session = Depends(db_session)) -> dict[str, str]:
    url = svc.oauth_start(db, account_id)
    db.commit()
    return {"authorize_url": url}


@router.get("/oauth/callback")
def oauth_callback(
    request: Request, state: str = "", code: str = "", error: str = "", db: Session = Depends(db_session)
) -> RedirectResponse:
    """Browser redirect target. Authenticated by the single-use, server-stored `state`."""
    ratelimit.enforce(request, "oauth_cb", 10, 60)
    if error or not code or not state:
        return RedirectResponse("/accounts?oauth=denied", status_code=303)
    try:
        acc = svc.oauth_callback(db, state, code)
        db.commit()
    except AppError as exc:
        db.rollback()
        return RedirectResponse(f"/accounts?oauth=error&reason={exc.code}", status_code=303)
    return RedirectResponse(f"/accounts?oauth=ok&account={acc.id}", status_code=303)
