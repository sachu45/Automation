from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_auth
from app.core import ratelimit
from app.core.config import get_settings
from app.models import AuditEvent, Lead
from app.schemas.common import Page
from app.schemas.leads import BulkAction, LeadCreate, LeadOut, LeadUpdate, MergeRequest
from app.services import imports
from app.services import leads as svc

router = APIRouter(prefix="/api/leads", tags=["leads"], dependencies=[Depends(require_auth)])


@router.get("", response_model=Page[LeadOut])
def list_leads(
    db: Session = Depends(db_session),
    q: str | None = None,
    status: str | None = None,
    tag: str | None = None,
    list_id: int | None = None,
    segment_id: int | None = None,
    source: str | None = None,
    industry: str | None = None,
    contacted: bool | None = None,
    sort: str = "created_at",
    order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
) -> Page[LeadOut]:
    f = svc.LeadFilters(
        q=q,
        status=status,
        tag=tag,
        list_id=list_id,
        segment_id=segment_id,
        source=source,
        industry=industry,
        contacted=contacted,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
    )
    items, total = svc.query_leads(db, f)
    return Page(items=svc.to_out(db, items), total=total, page=page, page_size=page_size)


@router.post("", response_model=LeadOut, status_code=201)
def create(body: LeadCreate, db: Session = Depends(db_session)) -> LeadOut:
    lead = svc.create_lead(db, body)
    db.commit()
    return svc.to_out(db, [lead], with_campaigns=True)[0]


@router.get("/facets")
def facets(db: Session = Depends(db_session)) -> dict[str, list[str]]:
    from sqlalchemy import distinct

    from app.models import Tag

    return {
        "sources": sorted(s for s in db.scalars(select(distinct(Lead.source))) if s),
        "industries": sorted(s for s in db.scalars(select(distinct(Lead.industry))) if s),
        "tags": sorted(db.scalars(select(Tag.name))),
    }


@router.get("/duplicates")
def duplicates(db: Session = Depends(db_session)) -> list[dict[str, Any]]:
    groups = svc.find_duplicate_groups(db)
    out = []
    for g in groups:
        leads = list(db.scalars(select(Lead).where(Lead.id.in_(g["lead_ids"]))))
        out.append({"reason": g["reason"], "leads": [o.model_dump() for o in svc.to_out(db, leads)]})
    return out


@router.post("/merge", response_model=LeadOut)
def merge(body: MergeRequest, db: Session = Depends(db_session)) -> LeadOut:
    lead = svc.merge_leads(db, body.primary_id, body.duplicate_ids, body.confirm)
    db.commit()
    return svc.to_out(db, [lead], with_campaigns=True)[0]


@router.post("/bulk")
def bulk(body: BulkAction, db: Session = Depends(db_session)) -> dict[str, int]:
    n = svc.bulk_action(db, body.lead_ids, body.action, body.value, body.confirm)
    db.commit()
    return {"affected": n}


class ExportIn(BaseModel):
    lead_ids: list[int] | None = Field(default=None, max_length=50_000)
    filters: dict[str, Any] | None = None


@router.post("/export")
def export(body: ExportIn, db: Session = Depends(db_session)) -> Response:
    if body.lead_ids:
        leads = list(db.scalars(select(Lead).where(Lead.id.in_(body.lead_ids)).order_by(Lead.id)))
    else:
        allowed = {"q", "status", "tag", "list_id", "segment_id", "source", "industry", "contacted"}
        f = svc.LeadFilters(**{k: v for k, v in (body.filters or {}).items() if k in allowed and v not in (None, "")})
        leads = list(db.scalars(svc.build_query(db, f).order_by(Lead.id)).unique())
    content = svc.export_csv(db, leads)
    db.commit()
    return Response(
        content="﻿" + content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="leads-export.csv"'},
    )


# --- CSV import ------------------------------------------------------------------------


@router.post("/import/upload")
async def import_upload(request: Request, file: UploadFile = File(...), db: Session = Depends(db_session)) -> dict[str, Any]:
    ratelimit.enforce(request, "import_upload", 20, 60)
    content = await file.read(get_settings().max_upload_bytes + 1)
    imp = imports.parse_upload(db, file.filename or "upload.csv", content, get_settings().max_upload_bytes)
    db.commit()
    rows = imp.rows or []
    return {
        "import_id": imp.id,
        "filename": imp.filename,
        "headers": imp.headers,
        "row_count": imp.row_count,
        "suggested_mapping": imp.mapping,
        "sample_rows": rows[:5],
        "target_fields": imports.TARGET_FIELDS,
    }


class ImportMappingIn(BaseModel):
    mapping: dict[str, str]
    source: str | None = Field(default=None, max_length=120)
    tags: list[str] = Field(default_factory=list)
    list_id: int | None = None
    existing: str = "skip"  # skip | fill_blanks


@router.post("/import/{import_id}/preview")
def import_preview(import_id: int, body: ImportMappingIn, db: Session = Depends(db_session)) -> dict[str, Any]:
    result = imports.preview(db, import_id, body.mapping, body.source, body.tags)
    db.commit()
    return result


@router.post("/import/{import_id}/commit")
def import_commit(import_id: int, body: ImportMappingIn, db: Session = Depends(db_session)) -> dict[str, Any]:
    result = imports.commit(
        db, import_id, body.mapping, source=body.source, tags=body.tags, list_id=body.list_id, existing_mode=body.existing
    )
    db.commit()
    return result


@router.delete("/import/{import_id}")
def import_discard(import_id: int, db: Session = Depends(db_session)) -> dict[str, bool]:
    imports.discard(db, import_id)
    db.commit()
    return {"ok": True}


# --- Single lead -----------------------------------------------------------------------


@router.get("/{lead_id}", response_model=LeadOut)
def get(lead_id: int, db: Session = Depends(db_session)) -> LeadOut:
    return svc.to_out(db, [svc.get_lead(db, lead_id)], with_campaigns=True)[0]


@router.patch("/{lead_id}", response_model=LeadOut)
def update(lead_id: int, body: LeadUpdate, db: Session = Depends(db_session)) -> LeadOut:
    lead = svc.update_lead(db, lead_id, body)
    db.commit()
    return svc.to_out(db, [lead], with_campaigns=True)[0]


@router.delete("/{lead_id}")
def delete(lead_id: int, db: Session = Depends(db_session)) -> dict[str, int]:
    svc.get_lead(db, lead_id)
    n = svc.delete_leads(db, [lead_id])
    db.commit()
    return {"deleted": n}


@router.get("/{lead_id}/activity")
def activity(lead_id: int, db: Session = Depends(db_session)) -> dict[str, Any]:
    from app.models import Conversation, SendAttempt

    svc.get_lead(db, lead_id)
    attempts = db.scalars(select(SendAttempt).where(SendAttempt.lead_id == lead_id).order_by(SendAttempt.created_at.desc()).limit(50))
    convs = db.scalars(select(Conversation).where(Conversation.lead_id == lead_id).order_by(Conversation.last_message_at.desc()))
    events = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.entity_type == "lead", AuditEvent.entity_id == lead_id)
        .order_by(AuditEvent.created_at.desc())
        .limit(50)
    )
    return {
        "attempts": [
            {
                "id": a.id,
                "campaign_id": a.campaign_id,
                "step": a.step_number,
                "status": a.status,
                "simulation": a.is_simulation,
                "subject": a.subject,
                "error": a.error,
                "at": a.created_at,
            }
            for a in attempts
        ],
        "conversations": [
            {"id": c.id, "subject": c.subject, "classification": c.classification, "last_message_at": c.last_message_at} for c in convs
        ],
        "audit": [{"action": e.action, "summary": e.summary, "at": e.created_at} for e in events],
    }
