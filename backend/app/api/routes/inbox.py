from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_auth
from app.core.errors import NotFound, ValidationFailed
from app.models import Campaign, Conversation, EmailAccount, Lead, Message, ReplyClassification
from app.models.enums import MessageDirection, ReplyLabel, SuppressionReason
from app.services import audit, inbox_sync, sending, suppression
from app.services.template_engine import UnresolvedPlaceholderError, assert_no_placeholders

router = APIRouter(prefix="/api/inbox", tags=["inbox"], dependencies=[Depends(require_auth)])


def _conv_summary(db: Session, c: Conversation) -> dict[str, Any]:
    lead = db.get(Lead, c.lead_id) if c.lead_id else None
    campaign = db.get(Campaign, c.campaign_id) if c.campaign_id else None
    last_in = db.scalar(
        select(Message)
        .where(Message.conversation_id == c.id, Message.direction == MessageDirection.INBOUND)
        .order_by(Message.occurred_at.desc())
        .limit(1)
    )
    return {
        "id": c.id,
        "subject": c.subject,
        "status": c.status,
        "classification": c.classification,
        "classification_source": c.classification_source,
        "needs_review": c.needs_review,
        "labels": c.labels or [],
        "last_message_at": c.last_message_at,
        "lead": {"id": lead.id, "email": lead.email, "name": lead.full_name} if lead else None,
        "campaign": {"id": campaign.id, "name": campaign.name} if campaign else None,
        "snippet": last_in.snippet if last_in else None,
        "from_email": last_in.from_email if last_in else None,
        "is_auto_reply": last_in.is_auto_reply if last_in else None,
    }


@router.get("")
def list_conversations(
    q: str | None = None,
    status: str | None = None,
    classification: str | None = None,
    campaign_id: int | None = None,
    needs_review: bool | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(db_session),
) -> dict[str, Any]:
    stmt = select(Conversation).where(Conversation.has_inbound.is_(True))
    if status:
        stmt = stmt.where(Conversation.status == status)
    if classification:
        stmt = stmt.where(Conversation.classification == classification)
    if campaign_id:
        stmt = stmt.where(Conversation.campaign_id == campaign_id)
    if needs_review is not None:
        stmt = stmt.where(Conversation.needs_review.is_(needs_review))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.outerjoin(Lead, Lead.id == Conversation.lead_id).where(
            or_(
                func.lower(func.coalesce(Conversation.subject, "")).like(like),
                func.lower(func.coalesce(Lead.email, "")).like(like),
                func.lower(func.coalesce(Lead.full_name, "")).like(like),
            )
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Conversation.last_message_at.desc()).offset((page - 1) * page_size).limit(page_size))
    count_rows = db.execute(
        select(Conversation.status, func.count()).where(Conversation.has_inbound.is_(True)).group_by(Conversation.status)
    ).all()
    counts: dict[str, int] = {status: n for status, n in count_rows}
    return {"items": [_conv_summary(db, c) for c in rows], "total": total, "page": page, "page_size": page_size, "counts": counts}


@router.get("/{conv_id}")
def get_conversation(conv_id: int, db: Session = Depends(db_session)) -> dict[str, Any]:
    c = db.get(Conversation, conv_id)
    if c is None:
        raise NotFound("Conversation not found")
    out = _conv_summary(db, c)
    out["notes"] = c.notes
    out["suppressed"] = bool(out["lead"] and suppression.is_suppressed(db, out["lead"]["email"]))
    msgs = []
    for m in c.messages:
        classes = db.scalars(
            select(ReplyClassification).where(ReplyClassification.message_id == m.id).order_by(ReplyClassification.created_at.desc())
        )
        msgs.append(
            {
                "id": m.id,
                "direction": m.direction,
                "from_email": m.from_email,
                "from_name": m.from_name,
                "to_email": m.to_email,
                "subject": m.subject,
                "body_text": m.body_text,
                "snippet": m.snippet,
                "occurred_at": m.occurred_at,
                "is_auto_reply": m.is_auto_reply,
                "is_simulation": m.is_simulation,
                "match_method": m.match_method,
                "message_id_header": m.message_id_header,
                "classifications": [
                    {"label": x.label, "source": x.source, "confidence": x.confidence, "rationale": x.rationale} for x in classes
                ],
            }
        )
    out["messages"] = msgs
    return out


class ClassifyIn(BaseModel):
    label: ReplyLabel


@router.post("/{conv_id}/classify")
def classify(conv_id: int, body: ClassifyIn, db: Session = Depends(db_session)) -> dict[str, Any]:
    c = inbox_sync.set_classification(db, conv_id, body.label)
    db.commit()
    return _conv_summary(db, c)


class ConvUpdate(BaseModel):
    status: str | None = Field(default=None, pattern="^(open|handled)$")
    notes: str | None = Field(default=None, max_length=10_000)
    labels: list[str] | None = None


@router.patch("/{conv_id}")
def update(conv_id: int, body: ConvUpdate, db: Session = Depends(db_session)) -> dict[str, Any]:
    c = db.get(Conversation, conv_id)
    if c is None:
        raise NotFound("Conversation not found")
    if body.status is not None:
        c.status = body.status
        if body.status == "handled":
            c.needs_review = False
    if "notes" in body.model_fields_set:
        c.notes = body.notes
    if body.labels is not None:
        c.labels = sorted({lbl.strip().lower()[:40] for lbl in body.labels if lbl.strip()})
    db.commit()
    return _conv_summary(db, c)


@router.post("/{conv_id}/stop-followups")
def stop_followups(conv_id: int, db: Session = Depends(db_session)) -> dict[str, int]:
    c = db.get(Conversation, conv_id)
    if c is None or c.lead_id is None:
        raise NotFound("Conversation has no lead")
    n = inbox_sync.stop_all_for_lead(db, c.lead_id, "manual")
    audit.record(db, "inbox.stop_followups", f"Follow-ups stopped from inbox for lead {c.lead_id}", entity_type="lead", entity_id=c.lead_id)
    db.commit()
    return {"stopped": n}


class SuppressIn(BaseModel):
    reason: SuppressionReason = SuppressionReason.DO_NOT_CONTACT


@router.post("/{conv_id}/suppress")
def suppress(conv_id: int, body: SuppressIn, db: Session = Depends(db_session)) -> dict[str, bool]:
    c = db.get(Conversation, conv_id)
    lead = db.get(Lead, c.lead_id) if c and c.lead_id else None
    if lead is None:
        raise NotFound("Conversation has no lead")
    suppression.suppress(db, email=lead.email, reason=body.reason, source="inbox")
    db.commit()
    return {"ok": True}


class ReassignIn(BaseModel):
    lead_id: int | None = None
    campaign_id: int | None = None


@router.post("/{conv_id}/reassign")
def reassign(conv_id: int, body: ReassignIn, db: Session = Depends(db_session)) -> dict[str, Any]:
    c = inbox_sync.reassign(db, conv_id, body.lead_id, body.campaign_id)
    db.commit()
    return _conv_summary(db, c)


class ReplyIn(BaseModel):
    body: str = Field(min_length=1, max_length=20_000)
    confirm: bool = False
    idempotency_key: str | None = None


@router.post("/{conv_id}/reply")
def reply(conv_id: int, body: ReplyIn, db: Session = Depends(db_session)) -> dict[str, Any]:
    """Queue an operator-written reply. Requires explicit confirmation of the final text; never automatic."""
    if not body.confirm:
        raise ValidationFailed("Confirm the final message before sending", code="confirmation_required")
    c = db.get(Conversation, conv_id)
    if c is None or c.lead_id is None or c.account_id is None:
        raise NotFound("Conversation cannot be replied to")
    lead = db.get(Lead, c.lead_id)
    account = db.get(EmailAccount, c.account_id)
    last_in = db.scalar(
        select(Message)
        .where(Message.conversation_id == c.id, Message.direction == MessageDirection.INBOUND)
        .order_by(Message.occurred_at.desc())
        .limit(1)
    )
    if lead is None or account is None:
        raise NotFound("Lead or account missing")
    subject = (last_in.subject if last_in and last_in.subject else c.subject) or ""
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    try:
        assert_no_placeholders(body.body)
    except UnresolvedPlaceholderError as exc:
        raise ValidationFailed("Replies cannot contain '{{' or '}}'") from exc
    job = sending.enqueue_single(
        db,
        lead=lead,
        account=account,
        subject=subject,
        body=body.body,
        fmt="text",
        idempotency_key=body.idempotency_key or uuid.uuid4().hex,
        is_reply=True,
        literal=True,
        in_reply_to=last_in.message_id_header if last_in else None,
    )
    db.commit()
    return {"queued": True, "job_id": job.id, "simulation": job.payload.get("simulation")}
