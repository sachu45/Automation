from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_auth
from app.core.errors import NotFound
from app.models import Sequence, Template, TemplateVersion
from app.schemas.content import (
    PreviewIn,
    PreviewOut,
    SequenceIn,
    SequenceOut,
    TemplateIn,
    TemplateOut,
    TemplateUpdate,
    TemplateVersionOut,
)
from app.services import templates as svc
from app.services.template_engine import BUILTIN_VARIABLES, LEAD_VARIABLES, SENDER_VARIABLES, SYSTEM_VARIABLES

router = APIRouter(tags=["templates"], dependencies=[Depends(require_auth)])


@router.get("/api/templates/variables")
def variables() -> dict[str, list[str]]:
    return {
        "lead": sorted(LEAD_VARIABLES),
        "sender": sorted(SENDER_VARIABLES),
        "system": sorted(SYSTEM_VARIABLES),
        "all": sorted(BUILTIN_VARIABLES),
    }


@router.get("/api/templates", response_model=list[TemplateOut])
def list_templates(include_archived: bool = False, db: Session = Depends(db_session)) -> list[TemplateOut]:
    stmt = select(Template).order_by(Template.name)
    if not include_archived:
        stmt = stmt.where(Template.archived.is_(False))
    return [svc.to_out(db, t) for t in db.scalars(stmt)]


@router.post("/api/templates", response_model=TemplateOut, status_code=201)
def create_template(body: TemplateIn, db: Session = Depends(db_session)) -> TemplateOut:
    t = svc.create_template(db, body)
    db.commit()
    return svc.to_out(db, t)


@router.post("/api/templates/preview", response_model=PreviewOut)
def preview(body: PreviewIn, db: Session = Depends(db_session)) -> PreviewOut:
    return svc.preview(db, body.subject, body.body, body.format, body.lead_id, body.account_id)


@router.get("/api/templates/{template_id}", response_model=TemplateOut)
def get_template(template_id: int, db: Session = Depends(db_session)) -> TemplateOut:
    return svc.to_out(db, svc.get_template(db, template_id))


@router.get("/api/templates/{template_id}/versions", response_model=list[TemplateVersionOut])
def versions(template_id: int, db: Session = Depends(db_session)) -> list[TemplateVersionOut]:
    svc.get_template(db, template_id)
    rows = db.scalars(select(TemplateVersion).where(TemplateVersion.template_id == template_id).order_by(TemplateVersion.version.desc()))
    return [TemplateVersionOut.model_validate(v) for v in rows]


@router.patch("/api/templates/{template_id}", response_model=TemplateOut)
def update_template(template_id: int, body: TemplateUpdate, db: Session = Depends(db_session)) -> TemplateOut:
    t = svc.update_template(db, template_id, body)
    db.commit()
    return svc.to_out(db, t)


@router.delete("/api/templates/{template_id}")
def delete_template(template_id: int, db: Session = Depends(db_session)) -> dict[str, bool]:
    svc.delete_template(db, template_id)
    db.commit()
    return {"ok": True}


# --- Sequences ------------------------------------------------------------------------------


@router.get("/api/sequences", response_model=list[SequenceOut])
def list_sequences(db: Session = Depends(db_session)) -> list[SequenceOut]:
    return [svc.sequence_out(db, s) for s in db.scalars(select(Sequence).order_by(Sequence.name))]


@router.post("/api/sequences", response_model=SequenceOut, status_code=201)
def create_sequence(body: SequenceIn, db: Session = Depends(db_session)) -> SequenceOut:
    seq = svc.save_sequence(db, body)
    db.commit()
    return svc.sequence_out(db, seq)


@router.get("/api/sequences/{sequence_id}", response_model=SequenceOut)
def get_sequence(sequence_id: int, db: Session = Depends(db_session)) -> SequenceOut:
    seq = db.get(Sequence, sequence_id)
    if seq is None:
        raise NotFound("Sequence not found")
    return svc.sequence_out(db, seq)


@router.put("/api/sequences/{sequence_id}", response_model=SequenceOut)
def update_sequence(sequence_id: int, body: SequenceIn, db: Session = Depends(db_session)) -> SequenceOut:
    seq = svc.save_sequence(db, body, sequence_id)
    db.commit()
    db.refresh(seq)
    return svc.sequence_out(db, seq)


@router.delete("/api/sequences/{sequence_id}")
def delete_sequence(sequence_id: int, db: Session = Depends(db_session)) -> dict[str, bool]:
    svc.delete_sequence(db, sequence_id)
    db.commit()
    return {"ok": True}
