from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_auth
from app.core.errors import Conflict, NotFound
from app.models import CampaignSource, LeadList, ListMembership, Segment
from app.schemas.leads import ListIn, ListMembersChange, ListOut, SegmentIn, SegmentOut
from app.services import audit
from app.services import leads as lead_svc
from app.services import segments as seg_svc

router = APIRouter(tags=["lists"], dependencies=[Depends(require_auth)])


def _list_out(db: Session, lst: LeadList) -> ListOut:
    out = ListOut.model_validate(lst)
    out.lead_count = db.scalar(select(func.count()).select_from(ListMembership).where(ListMembership.list_id == lst.id)) or 0
    return out


@router.get("/api/lists", response_model=list[ListOut])
def list_lists(db: Session = Depends(db_session)) -> list[ListOut]:
    return [_list_out(db, lst) for lst in db.scalars(select(LeadList).order_by(LeadList.name))]


@router.post("/api/lists", response_model=ListOut, status_code=201)
def create_list(body: ListIn, db: Session = Depends(db_session)) -> ListOut:
    if db.scalar(select(LeadList).where(LeadList.name == body.name)):
        raise Conflict("A list with this name already exists")
    lst = LeadList(name=body.name, description=body.description)
    db.add(lst)
    db.flush()
    audit.record(db, "list.created", f"List '{lst.name}' created", entity_type="list", entity_id=lst.id)
    db.commit()
    return _list_out(db, lst)


def _get_list(db: Session, list_id: int) -> LeadList:
    lst = db.get(LeadList, list_id)
    if lst is None:
        raise NotFound("List not found")
    return lst


@router.patch("/api/lists/{list_id}", response_model=ListOut)
def update_list(list_id: int, body: ListIn, db: Session = Depends(db_session)) -> ListOut:
    lst = _get_list(db, list_id)
    if db.scalar(select(LeadList).where(LeadList.name == body.name, LeadList.id != list_id)):
        raise Conflict("A list with this name already exists")
    lst.name, lst.description = body.name, body.description
    db.commit()
    return _list_out(db, lst)


@router.delete("/api/lists/{list_id}")
def delete_list(list_id: int, db: Session = Depends(db_session)) -> dict[str, bool]:
    lst = _get_list(db, list_id)
    if db.scalar(select(func.count()).select_from(CampaignSource).where(CampaignSource.list_id == list_id)):
        raise Conflict("This list is targeted by a campaign")
    audit.record(db, "list.deleted", f"List '{lst.name}' deleted (leads kept)", entity_type="list", entity_id=lst.id)
    db.delete(lst)
    db.commit()
    return {"ok": True}


@router.post("/api/lists/{list_id}/members")
def add_members(list_id: int, body: ListMembersChange, db: Session = Depends(db_session)) -> dict[str, int]:
    _get_list(db, list_id)
    n = lead_svc.add_to_list(db, list_id, body.lead_ids)
    db.commit()
    return {"added": n}


@router.post("/api/lists/{list_id}/members/remove")
def remove_members(list_id: int, body: ListMembersChange, db: Session = Depends(db_session)) -> dict[str, int]:
    _get_list(db, list_id)
    res = db.execute(delete(ListMembership).where(ListMembership.list_id == list_id, ListMembership.lead_id.in_(body.lead_ids)))
    db.commit()
    return {"removed": res.rowcount}  # type: ignore[attr-defined]


# --- Segments ------------------------------------------------------------------------------


def _seg_out(db: Session, seg: Segment) -> SegmentOut:
    out = SegmentOut.model_validate(seg)
    out.lead_count = seg_svc.count_segment(db, seg)
    return out


@router.get("/api/segments/fields")
def segment_fields() -> dict[str, Any]:
    return {"fields": seg_svc.FIELD_CATALOG, "text_ops": sorted(seg_svc.TEXT_OPS), "date_ops": sorted(seg_svc.DATE_OPS)}


@router.get("/api/segments", response_model=list[SegmentOut])
def list_segments(db: Session = Depends(db_session)) -> list[SegmentOut]:
    return [_seg_out(db, s) for s in db.scalars(select(Segment).order_by(Segment.name))]


@router.post("/api/segments/preview")
def preview_segment(body: SegmentIn, db: Session = Depends(db_session)) -> dict[str, Any]:
    rules = [r.model_dump() for r in body.rules]
    seg_svc.validate_rules(body.match, rules)
    total, leads = seg_svc.preview_rules(db, body.match, rules)
    return {"total": total, "sample": [o.model_dump() for o in lead_svc.to_out(db, leads)]}


@router.post("/api/segments", response_model=SegmentOut, status_code=201)
def create_segment(body: SegmentIn, db: Session = Depends(db_session)) -> SegmentOut:
    rules = [r.model_dump() for r in body.rules]
    seg_svc.validate_rules(body.match, rules)
    if db.scalar(select(Segment).where(Segment.name == body.name)):
        raise Conflict("A segment with this name already exists")
    seg = Segment(name=body.name, description=body.description, match=body.match, rules=rules)
    db.add(seg)
    db.flush()
    audit.record(db, "segment.created", f"Segment '{seg.name}' created", entity_type="segment", entity_id=seg.id)
    db.commit()
    return _seg_out(db, seg)


@router.put("/api/segments/{segment_id}", response_model=SegmentOut)
def update_segment(segment_id: int, body: SegmentIn, db: Session = Depends(db_session)) -> SegmentOut:
    seg = db.get(Segment, segment_id)
    if seg is None:
        raise NotFound("Segment not found")
    rules = [r.model_dump() for r in body.rules]
    seg_svc.validate_rules(body.match, rules)
    seg.name, seg.description, seg.match, seg.rules = body.name, body.description, body.match, rules
    audit.record(db, "segment.updated", f"Segment '{seg.name}' updated", entity_type="segment", entity_id=seg.id)
    db.commit()
    return _seg_out(db, seg)


@router.delete("/api/segments/{segment_id}")
def delete_segment(segment_id: int, db: Session = Depends(db_session)) -> dict[str, bool]:
    seg = db.get(Segment, segment_id)
    if seg is None:
        raise NotFound("Segment not found")
    if db.scalar(select(func.count()).select_from(CampaignSource).where(CampaignSource.segment_id == segment_id)):
        raise Conflict("This segment is targeted by a campaign")
    db.delete(seg)
    db.commit()
    return {"ok": True}
