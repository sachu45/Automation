"""Individual (non-campaign) sends. Always queued for the worker; never sent inside the request."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_auth
from app.core import ratelimit
from app.core.errors import NotFound, ValidationFailed
from app.models import EmailAccount, ScheduledJob, SendAttempt
from app.models.enums import JobKind
from app.services import ai as ai_svc
from app.services import leads as lead_svc
from app.services import sending, suppression
from app.services import templates as template_svc

router = APIRouter(prefix="/api/sends", tags=["sends"], dependencies=[Depends(require_auth)])


class SingleSendIn(BaseModel):
    lead_id: int
    account_id: int
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=50_000)
    format: Literal["text", "html"] = "text"
    draft_id: int | None = None
    idempotency_key: str = Field(min_length=8, max_length=100)
    confirm: bool = False


@router.post("/preview")
def preview(body: SingleSendIn, db: Session = Depends(db_session)) -> dict[str, Any]:
    out = template_svc.preview(db, body.subject, body.body, body.format, body.lead_id, body.account_id).model_dump()
    lead = lead_svc.get_lead(db, body.lead_id)
    out["suppressed"] = suppression.is_suppressed(db, lead.email)
    out["to"] = lead.email
    return out


@router.post("")
def send_single(body: SingleSendIn, request: Request, db: Session = Depends(db_session)) -> dict[str, Any]:
    ratelimit.enforce(request, "single_send", 30, 60)
    if not body.confirm:
        raise ValidationFailed("Review the final message and confirm before sending", code="confirmation_required")
    lead = lead_svc.get_lead(db, body.lead_id)
    account = db.get(EmailAccount, body.account_id)
    if account is None:
        raise NotFound("Account not found")
    if body.draft_id is not None:
        d = ai_svc.get_draft(db, body.draft_id)
        if d.status != "approved":
            raise ValidationFailed("AI drafts must be approved before they can be sent")
    if suppression.is_suppressed(db, lead.email):
        raise ValidationFailed("This recipient is on the suppression list", code="suppressed")
    pv = template_svc.preview(db, body.subject, body.body, body.format, lead.id, account.id)
    if pv.errors or pv.missing:
        raise ValidationFailed(
            "Message cannot be sent: unresolved variables or syntax errors", details={"missing": pv.missing, "errors": pv.errors}
        )
    job = sending.enqueue_single(
        db,
        lead=lead,
        account=account,
        subject=body.subject,
        body=body.body,
        fmt=body.format,
        idempotency_key=body.idempotency_key,
        draft_id=body.draft_id,
    )
    db.commit()
    return {"job_id": job.id, "status": job.status, "simulation": job.payload.get("simulation")}


@router.get("/recent")
def recent(db: Session = Depends(db_session)) -> list[dict[str, Any]]:
    jobs = db.scalars(
        select(ScheduledJob).where(ScheduledJob.kind == JobKind.SEND_SINGLE).order_by(ScheduledJob.created_at.desc()).limit(25)
    )
    out = []
    for j in jobs:
        attempt = db.scalar(
            select(SendAttempt).where(SendAttempt.idempotency_key == j.idempotency_key).order_by(SendAttempt.id.desc()).limit(1)
        )
        out.append(
            {
                "job_id": j.id,
                "status": j.status,
                "simulation": j.payload.get("simulation"),
                "subject": j.payload.get("subject"),
                "lead_id": j.payload.get("lead_id"),
                "created_at": j.created_at,
                "last_error": j.last_error,
                "attempt_status": attempt.status if attempt else None,
            }
        )
    return out
