from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_auth
from app.models import Campaign
from app.schemas.campaigns import CampaignIn, CampaignOperationalUpdate, CampaignOut, ConfirmIn, RecipientOut
from app.schemas.common import Page
from app.services import campaigns as svc
from app.services import sending

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"], dependencies=[Depends(require_auth)])


@router.get("", response_model=list[CampaignOut])
def list_campaigns(status: str | None = None, db: Session = Depends(db_session)) -> list[CampaignOut]:
    stmt = select(Campaign).order_by(Campaign.created_at.desc())
    if status:
        stmt = stmt.where(Campaign.status.in_(status.split(",")))
    return [svc.to_out(db, c) for c in db.scalars(stmt)]


@router.post("", response_model=CampaignOut, status_code=201)
def create(body: CampaignIn, db: Session = Depends(db_session)) -> CampaignOut:
    c = svc.create_campaign(db, body)
    db.commit()
    return svc.to_out(db, c)


@router.get("/{campaign_id}", response_model=CampaignOut)
def get(campaign_id: int, db: Session = Depends(db_session)) -> CampaignOut:
    return svc.to_out(db, svc.get_campaign(db, campaign_id))


@router.put("/{campaign_id}", response_model=CampaignOut)
def update(campaign_id: int, body: CampaignIn, db: Session = Depends(db_session)) -> CampaignOut:
    c = svc.update_campaign(db, campaign_id, body)
    db.commit()
    return svc.to_out(db, c)


@router.patch("/{campaign_id}/operational", response_model=CampaignOut)
def operational(campaign_id: int, body: CampaignOperationalUpdate, db: Session = Depends(db_session)) -> CampaignOut:
    c = svc.operational_update(db, campaign_id, body)
    db.commit()
    return svc.to_out(db, c)


@router.delete("/{campaign_id}")
def delete(campaign_id: int, db: Session = Depends(db_session)) -> dict[str, bool]:
    svc.delete_campaign(db, campaign_id)
    db.commit()
    return {"ok": True}


@router.post("/{campaign_id}/duplicate", response_model=CampaignOut)
def duplicate(campaign_id: int, db: Session = Depends(db_session)) -> CampaignOut:
    c = svc.duplicate(db, campaign_id)
    db.commit()
    return svc.to_out(db, c)


@router.get("/{campaign_id}/review")
def review(campaign_id: int, db: Session = Depends(db_session)) -> dict[str, Any]:
    return svc.public_review(db, campaign_id)


@router.post("/{campaign_id}/submit", response_model=CampaignOut)
def submit(campaign_id: int, db: Session = Depends(db_session)) -> CampaignOut:
    c = svc.submit_for_review(db, campaign_id)
    db.commit()
    return svc.to_out(db, c)


@router.post("/{campaign_id}/return-to-draft", response_model=CampaignOut)
def return_to_draft(campaign_id: int, db: Session = Depends(db_session)) -> CampaignOut:
    from app.core.errors import ValidationFailed
    from app.models.enums import CampaignStatus

    c = svc.get_campaign(db, campaign_id)
    if c.status not in (CampaignStatus.READY_FOR_REVIEW, CampaignStatus.APPROVED):
        raise ValidationFailed("Only campaigns in review or approved can return to draft")
    svc._reset_to_draft(db, c, "returned by operator")
    db.commit()
    return svc.to_out(db, c)


@router.post("/{campaign_id}/approve", response_model=CampaignOut)
def approve(campaign_id: int, body: ConfirmIn, db: Session = Depends(db_session)) -> CampaignOut:
    c = svc.approve(db, campaign_id, body.confirm)
    db.commit()
    return svc.to_out(db, c)


@router.post("/{campaign_id}/activate", response_model=CampaignOut)
def activate(campaign_id: int, body: ConfirmIn, db: Session = Depends(db_session)) -> CampaignOut:
    c = svc.activate(db, campaign_id, body.confirm, body.confirm_name)
    db.commit()
    return svc.to_out(db, c)


@router.post("/{campaign_id}/pause", response_model=CampaignOut)
def pause(campaign_id: int, db: Session = Depends(db_session)) -> CampaignOut:
    c = svc.pause(db, campaign_id)
    db.commit()
    return svc.to_out(db, c)


@router.post("/{campaign_id}/resume", response_model=CampaignOut)
def resume(campaign_id: int, db: Session = Depends(db_session)) -> CampaignOut:
    c = svc.resume(db, campaign_id)
    db.commit()
    return svc.to_out(db, c)


@router.post("/{campaign_id}/cancel", response_model=CampaignOut)
def cancel(campaign_id: int, body: ConfirmIn, db: Session = Depends(db_session)) -> CampaignOut:
    c = svc.cancel(db, campaign_id, body.confirm)
    db.commit()
    return svc.to_out(db, c)


@router.get("/{campaign_id}/recipients", response_model=Page[RecipientOut])
def recipients(
    campaign_id: int,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(db_session),
) -> Page[RecipientOut]:
    items, total = svc.list_recipients(db, campaign_id, status, page, page_size)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/{campaign_id}/recipients/{recipient_id}/stop")
def stop_recipient(campaign_id: int, recipient_id: int, db: Session = Depends(db_session)) -> dict[str, str]:
    rec = svc.stop_recipient(db, campaign_id, recipient_id)
    db.commit()
    return {"status": rec.status}


class ResolveIn(BaseModel):
    was_sent: bool


@router.post("/{campaign_id}/recipients/{recipient_id}/resolve")
def resolve(campaign_id: int, recipient_id: int, body: ResolveIn, db: Session = Depends(db_session)) -> dict[str, str]:
    rec = sending.resolve_unknown(db, recipient_id, body.was_sent)
    db.commit()
    return {"status": rec.status}


@router.get("/{campaign_id}/attempts")
def attempts(campaign_id: int, limit: int = Query(100, le=500), db: Session = Depends(db_session)) -> dict[str, Any]:
    from app.models import SendAttempt

    svc.get_campaign(db, campaign_id)
    rows = db.scalars(
        select(SendAttempt).where(SendAttempt.campaign_id == campaign_id).order_by(SendAttempt.created_at.desc()).limit(limit)
    )
    return {
        "counts": svc.attempt_counts(db, campaign_id),
        "items": [
            {
                "id": a.id,
                "to": a.to_email,
                "step": a.step_number,
                "status": a.status,
                "simulation": a.is_simulation,
                "attempt_no": a.attempt_no,
                "smtp_code": a.smtp_code,
                "error": a.error,
                "subject": a.subject,
                "at": a.created_at,
            }
            for a in rows
        ],
    }
