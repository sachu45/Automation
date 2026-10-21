from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_auth
from app.core.config import get_settings
from app.models import AuditEvent
from app.services import ai as ai_svc
from app.services import settings as svc

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_auth)])


@router.get("")
def get_settings_route(db: Session = Depends(db_session)) -> dict[str, Any]:
    s = get_settings()
    return {
        "config": svc.get_config(db).model_dump(mode="json"),
        "ai": ai_svc.provider_status(db),
        "environment": {
            "app_env": s.app_env,
            "public_base_url": s.public_base_url or None,
            "unsubscribe_links": bool(s.public_base_url),
            "database": "postgresql" if s.resolved_database_url.startswith("postgres") else "sqlite",
            "bind_host": s.app_host,
            "cookie_secure": s.cookie_secure,
        },
        "live_sending_phrase": svc.LIVE_SENDING_PHRASE,
        "safety_fields": sorted(svc.SAFETY_FIELDS),
    }


class SettingsPatch(BaseModel):
    changes: dict[str, Any]
    confirm_safety: bool = False
    confirmation_phrase: str | None = None


@router.patch("")
def patch(body: SettingsPatch, db: Session = Depends(db_session)) -> dict[str, Any]:
    cfg = svc.update_config(db, body.changes, confirm_safety=body.confirm_safety, confirmation_phrase=body.confirmation_phrase)
    db.commit()
    return {"config": cfg.model_dump(mode="json")}


class EmergencyIn(BaseModel):
    engaged: bool
    confirm: bool = False


@router.post("/emergency-stop")
def emergency_stop(body: EmergencyIn, db: Session = Depends(db_session)) -> dict[str, Any]:
    from app.core.errors import ValidationFailed

    if not body.engaged and not body.confirm:
        raise ValidationFailed("Releasing the emergency stop requires confirmation", code="confirmation_required")
    cfg = svc.set_emergency_stop(db, body.engaged)
    db.commit()
    return {"emergency_stop": cfg.emergency_stop}


class AiKeyIn(BaseModel):
    api_key: str | None = Field(default=None, max_length=500)


@router.put("/ai-key")
def set_ai_key(body: AiKeyIn, db: Session = Depends(db_session)) -> dict[str, Any]:
    svc.set_ai_api_key(db, (body.api_key or "").strip() or None)
    db.commit()
    return ai_svc.provider_status(db)


@router.get("/audit")
def audit_log(
    action: str | None = None,
    entity_type: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(db_session),
) -> dict[str, Any]:
    stmt = select(AuditEvent)
    if action:
        stmt = stmt.where(AuditEvent.action.like(f"{action}%"))
    if entity_type:
        stmt = stmt.where(AuditEvent.entity_type == entity_type)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).offset((page - 1) * page_size).limit(page_size))
    return {
        "items": [
            {
                "id": e.id,
                "action": e.action,
                "summary": e.summary,
                "entity_type": e.entity_type,
                "entity_id": e.entity_id,
                "details": e.details,
                "created_at": e.created_at,
            }
            for e in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/retention/run")
def run_retention(db: Session = Depends(db_session)) -> dict[str, int]:
    from app.services import retention

    out = retention.apply(db)
    db.commit()
    return out
