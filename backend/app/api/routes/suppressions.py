from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_auth
from app.models import Suppression
from app.models.enums import SuppressionReason
from app.services import suppression as svc
from app.services.emails import InvalidEmail, validate_address

router = APIRouter(prefix="/api/suppressions", tags=["suppressions"], dependencies=[Depends(require_auth)])


def _out(s: Suppression) -> dict[str, Any]:
    return {
        "id": s.id,
        "email": s.email,
        "domain": s.domain,
        "reason": s.reason,
        "source": s.source,
        "notes": s.notes,
        "active": s.active,
        "created_at": s.created_at,
        "lifted_at": s.lifted_at,
        "lifted_reason": s.lifted_reason,
    }


@router.get("")
def list_suppressions(
    q: str | None = None,
    reason: str | None = None,
    active: bool | None = True,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(db_session),
) -> dict[str, Any]:
    stmt = select(Suppression)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(Suppression.email.like(like), Suppression.domain.like(like)))
    if reason:
        stmt = stmt.where(Suppression.reason == reason)
    if active is not None:
        stmt = stmt.where(Suppression.active.is_(active))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Suppression.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
    return {"items": [_out(s) for s in rows], "total": total, "page": page, "page_size": page_size}


class SuppressionIn(BaseModel):
    email: str | None = None
    domain: str | None = None
    reason: SuppressionReason = SuppressionReason.MANUAL
    notes: str | None = Field(default=None, max_length=1000)


@router.post("", status_code=201)
def add(body: SuppressionIn, db: Session = Depends(db_session)) -> dict[str, Any]:
    email = None
    if body.email:
        try:
            email = validate_address(body.email)
        except InvalidEmail:
            email = body.email.strip().lower()  # invalid addresses can still be suppressed
    s = svc.suppress(db, email=email, domain=body.domain, reason=body.reason, source="manual", notes=body.notes)
    db.commit()
    return _out(s)


class BulkSuppressIn(BaseModel):
    text: str = Field(max_length=500_000)
    reason: SuppressionReason = SuppressionReason.MANUAL


@router.post("/bulk")
def bulk(body: BulkSuppressIn, db: Session = Depends(db_session)) -> dict[str, int]:
    """Paste or upload a list of addresses (one per line or CSV first column)."""
    added = 0
    for row in csv.reader(io.StringIO(body.text)):
        if not row:
            continue
        value = row[0].strip().lower()
        if not value or value in ("email", "email address"):
            continue
        if "@" in value:
            svc.suppress(db, email=value, reason=body.reason, source="bulk_import")
            added += 1
        elif "." in value:
            svc.suppress(db, domain=value, reason=body.reason, source="bulk_import")
            added += 1
    db.commit()
    return {"added": added}


@router.get("/check")
def check(email: str, db: Session = Depends(db_session)) -> dict[str, Any]:
    s = svc.find_active(db, email)
    return {"suppressed": s is not None, "record": _out(s) if s else None}


class LiftIn(BaseModel):
    confirm_value: str
    reason: str = Field(min_length=5, max_length=1000)


@router.post("/{suppression_id}/lift")
def lift(suppression_id: int, body: LiftIn, db: Session = Depends(db_session)) -> dict[str, Any]:
    s = svc.lift(db, suppression_id, reason=body.reason, confirm_value=body.confirm_value)
    db.commit()
    return _out(s)
