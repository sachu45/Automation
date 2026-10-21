from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_auth
from app.core import ratelimit
from app.core.errors import NotFound
from app.models import AiDraft, AiPrompt
from app.schemas.content import AiPromptIn, TemplateIn
from app.services import ai as svc
from app.services import templates as template_svc

router = APIRouter(prefix="/api/ai", tags=["ai"], dependencies=[Depends(require_auth)])


def _draft_out(d: AiDraft) -> dict[str, Any]:
    return {
        "id": d.id,
        "lead_id": d.lead_id,
        "parent_id": d.parent_id,
        "label": d.label,
        "provider": d.provider,
        "inputs": d.inputs,
        "output": d.output,
        "subject": d.subject,
        "body": d.body,
        "status": d.status,
        "approved_at": d.approved_at,
        "created_at": d.created_at,
        "updated_at": d.updated_at,
    }


@router.get("/status")
def status(db: Session = Depends(db_session)) -> dict[str, Any]:
    return svc.provider_status(db)


@router.post("/generate")
def generate(body: svc.GenerateInput, request: Request, db: Session = Depends(db_session)) -> dict[str, Any]:
    ratelimit.enforce(request, "ai_generate", 20, 60)
    d = svc.generate(db, body)
    db.commit()
    return _draft_out(d)


@router.get("/drafts")
def list_drafts(
    lead_id: int | None = None, status: str | None = None, limit: int = Query(50, le=200), db: Session = Depends(db_session)
) -> list[dict[str, Any]]:
    stmt = select(AiDraft).order_by(AiDraft.created_at.desc()).limit(limit)
    if lead_id:
        stmt = stmt.where(AiDraft.lead_id == lead_id)
    if status:
        stmt = stmt.where(AiDraft.status == status)
    return [_draft_out(d) for d in db.scalars(stmt)]


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: int, db: Session = Depends(db_session)) -> dict[str, Any]:
    d = svc.get_draft(db, draft_id)
    root = d.parent_id or d.id
    versions = db.scalars(select(AiDraft).where((AiDraft.id == root) | (AiDraft.parent_id == root)).order_by(AiDraft.created_at))
    out = _draft_out(d)
    out["versions"] = [
        {"id": v.id, "label": v.label, "subject": v.subject, "status": v.status, "created_at": v.created_at} for v in versions
    ]
    return out


@router.post("/drafts/{draft_id}/regenerate")
def regenerate(draft_id: int, request: Request, db: Session = Depends(db_session)) -> dict[str, Any]:
    ratelimit.enforce(request, "ai_generate", 20, 60)
    d = svc.regenerate(db, draft_id)
    db.commit()
    return _draft_out(d)


class DraftUpdate(BaseModel):
    subject: str | None = Field(default=None, max_length=500)
    body: str | None = Field(default=None, max_length=20_000)


@router.patch("/drafts/{draft_id}")
def update_draft(draft_id: int, body: DraftUpdate, db: Session = Depends(db_session)) -> dict[str, Any]:
    d = svc.update_draft(db, draft_id, body.subject, body.body)
    db.commit()
    return _draft_out(d)


@router.post("/drafts/{draft_id}/approve")
def approve(draft_id: int, db: Session = Depends(db_session)) -> dict[str, Any]:
    d = svc.approve_draft(db, draft_id)
    db.commit()
    return _draft_out(d)


@router.delete("/drafts/{draft_id}")
def delete_draft(draft_id: int, db: Session = Depends(db_session)) -> dict[str, bool]:
    d = svc.get_draft(db, draft_id)
    db.delete(d)
    db.commit()
    return {"ok": True}


class SaveAsTemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None


@router.post("/drafts/{draft_id}/save-template")
def save_as_template(draft_id: int, body: SaveAsTemplateIn, db: Session = Depends(db_session)) -> dict[str, Any]:
    from app.core.errors import ValidationFailed

    d = svc.get_draft(db, draft_id)
    if d.status != "approved":
        raise ValidationFailed("Approve the draft before saving it as a template")
    t = template_svc.create_template(
        db,
        TemplateIn(
            name=body.name, description=body.description, subject=d.subject, body=d.body, format="text", note=f"From AI draft {d.id}"
        ),
    )
    db.commit()
    return template_svc.to_out(db, t).model_dump()


@router.get("/prompts")
def list_prompts(db: Session = Depends(db_session)) -> list[dict[str, Any]]:
    return [
        {"id": p.id, "name": p.name, "inputs": p.inputs, "updated_at": p.updated_at}
        for p in db.scalars(select(AiPrompt).order_by(AiPrompt.name))
    ]


@router.post("/prompts")
def save_prompt(body: AiPromptIn, db: Session = Depends(db_session)) -> dict[str, Any]:
    p = svc.save_prompt(db, body.name, body.inputs)
    db.commit()
    return {"id": p.id, "name": p.name, "inputs": p.inputs}


@router.delete("/prompts/{prompt_id}")
def delete_prompt(prompt_id: int, db: Session = Depends(db_session)) -> dict[str, bool]:
    p = db.get(AiPrompt, prompt_id)
    if p is None:
        raise NotFound("Prompt not found")
    db.delete(p)
    db.commit()
    return {"ok": True}


@router.get("/usage")
def usage(db: Session = Depends(db_session)) -> dict[str, Any]:
    return svc.usage_summary(db)
