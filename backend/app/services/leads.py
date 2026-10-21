"""Lead management: CRUD, filtering, duplicates, merge, bulk actions, export."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import Select, delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.models import (
    AiDraft,
    Campaign,
    CampaignRecipient,
    Company,
    Conversation,
    Lead,
    LeadList,
    ListMembership,
    Segment,
    SendAttempt,
    Tag,
)
from app.models.enums import LeadStatus, SuppressionReason
from app.schemas.leads import CampaignRef, LeadCreate, LeadOut, LeadUpdate
from app.services import audit, suppression
from app.services.segments import segment_condition

SORTABLE = {
    "created_at": Lead.created_at,
    "updated_at": Lead.updated_at,
    "email": Lead.email,
    "last_name": Lead.last_name,
    "first_name": Lead.first_name,
    "status": Lead.status,
    "last_contacted_at": Lead.last_contacted_at,
}


# --- Companies & tags ------------------------------------------------------------


def _domain_of(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(url if "://" in url else "https://" + url).hostname or ""
    return host.lower().removeprefix("www.") or None


def get_or_create_company(db: Session, name: str | None, website: str | None) -> Company | None:
    name = (name or "").strip()
    if not name:
        return None
    key = name.lower()
    company = db.scalar(select(Company).where(Company.name_key == key))
    if company is None:
        company = Company(name=name, name_key=key, website=website, domain=_domain_of(website))
        db.add(company)
        db.flush()
    elif website and not company.website:
        company.website = website  # fill blanks only; never overwrite
        company.domain = _domain_of(website)
    return company


def _tags(db: Session, names: list[str]) -> list[Tag]:
    out: list[Tag] = []
    for n in names:
        tag = db.scalar(select(Tag).where(Tag.name == n))
        if tag is None:
            tag = Tag(name=n)
            db.add(tag)
            db.flush()
        out.append(tag)
    return out


# --- CRUD ----------------------------------------------------------------------------


def get_lead(db: Session, lead_id: int) -> Lead:
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise NotFound("Lead not found")
    return lead


def find_by_email(db: Session, email: str) -> Lead | None:
    return db.scalar(select(Lead).where(Lead.email == email.strip().lower()))


def create_lead(db: Session, data: LeadCreate, *, audit_it: bool = True) -> Lead:
    existing = find_by_email(db, data.email)
    if existing:
        raise Conflict(
            f"A lead with {data.email} already exists",
            code="duplicate_email",
            details={"existing_id": existing.id},
        )
    company = get_or_create_company(db, data.company, data.company_website)
    lead = Lead(
        email=data.email,
        first_name=data.first_name,
        last_name=data.last_name,
        full_name=data.full_name or (" ".join(p for p in [data.first_name, data.last_name] if p) or None),
        job_title=data.job_title,
        company=company,
        linkedin_url=data.linkedin_url,
        industry=data.industry,
        country=data.country,
        timezone=data.timezone,
        source=data.source,
        notes=data.notes,
        custom_fields=data.custom_fields,
        status=data.status,
    )
    db.add(lead)
    if suppression.is_suppressed(db, data.email):
        sup = suppression.find_active(db, data.email)
        lead.status = suppression.REASON_TO_LEAD_STATUS.get(
            SuppressionReason(sup.reason) if sup else SuppressionReason.MANUAL, LeadStatus.DO_NOT_CONTACT
        )
    lead.tags = _tags(db, data.tags)
    db.flush()
    if audit_it:
        audit.record(db, "lead.created", f"Lead {lead.email} created", entity_type="lead", entity_id=lead.id)
    return lead


def update_lead(db: Session, lead_id: int, data: LeadUpdate) -> Lead:
    lead = get_lead(db, lead_id)
    fields = data.model_fields_set
    changes: dict[str, Any] = {}
    if "email" in fields and data.email and data.email != lead.email:
        if find_by_email(db, data.email):
            raise Conflict(f"Another lead already uses {data.email}", code="duplicate_email")
        changes["email"] = [lead.email, data.email]
        lead.email = data.email
    simple = [
        "first_name",
        "last_name",
        "full_name",
        "job_title",
        "linkedin_url",
        "industry",
        "country",
        "timezone",
        "source",
        "notes",
        "custom_fields",
    ]
    for f in simple:
        if f in fields:
            new = getattr(data, f)
            if getattr(lead, f) != new:
                changes[f] = True
                setattr(lead, f, new)
    if "company" in fields or "company_website" in fields:
        name = data.company if "company" in fields else (lead.company.name if lead.company else None)
        website = data.company_website if "company_website" in fields else None
        lead.company = get_or_create_company(db, name, website)
        if lead.company and "company_website" in fields and data.company_website:
            lead.company.website = data.company_website
            lead.company.domain = _domain_of(data.company_website)
        changes["company"] = True
    if "tags" in fields:
        lead.tags = _tags(db, data.tags)
        changes["tags"] = True
    if "status" in fields and data.status and data.status != lead.status:
        if suppression.is_suppressed(db, lead.email) and data.status not in {
            LeadStatus.DO_NOT_CONTACT,
            LeadStatus.UNSUBSCRIBED,
            LeadStatus.BOUNCED,
        }:
            raise ValidationFailed("This lead is on the suppression list; its status cannot be changed to an outreach-eligible state")
        if data.status in {LeadStatus.UNSUBSCRIBED, LeadStatus.BOUNCED}:
            raise ValidationFailed("Use the suppression list to mark unsubscribes and bounces")
        changes["status"] = [lead.status, data.status]
        lead.status = data.status
        if data.status == LeadStatus.DO_NOT_CONTACT:
            suppression.suppress(db, email=lead.email, reason=SuppressionReason.DO_NOT_CONTACT, source="lead_status")
    if changes:
        audit.record(
            db,
            "lead.updated",
            f"Lead {lead.email} updated",
            entity_type="lead",
            entity_id=lead.id,
            details={"fields": sorted(changes)},
        )
    db.flush()
    return lead


def delete_leads(db: Session, ids: list[int]) -> int:
    """Delete leads. Suppression records are kept (they are keyed by address, not lead id)."""
    leads = list(db.scalars(select(Lead).where(Lead.id.in_(ids))))
    for lead in leads:
        db.delete(lead)
    if leads:
        audit.record(
            db,
            "lead.deleted",
            f"Deleted {len(leads)} lead(s)",
            entity_type="lead",
            details={"ids": [lead.id for lead in leads][:200]},
        )
    return len(leads)


# --- Query -----------------------------------------------------------------------------


@dataclass
class LeadFilters:
    q: str | None = None
    status: str | None = None
    tag: str | None = None
    list_id: int | None = None
    segment_id: int | None = None
    source: str | None = None
    industry: str | None = None
    contacted: bool | None = None
    sort: str = "created_at"
    order: str = "desc"
    page: int = 1
    page_size: int = 25


def build_query(db: Session, f: LeadFilters) -> Select[tuple[Lead]]:
    stmt = select(Lead)
    if f.q:
        like = f"%{f.q.strip().lower()}%"
        stmt = stmt.outerjoin(Company, Company.id == Lead.company_id).where(
            or_(
                func.lower(Lead.email).like(like),
                func.lower(func.coalesce(Lead.full_name, "")).like(like),
                func.lower(func.coalesce(Lead.first_name, "")).like(like),
                func.lower(func.coalesce(Lead.last_name, "")).like(like),
                func.lower(func.coalesce(Lead.job_title, "")).like(like),
                func.lower(func.coalesce(Company.name, "")).like(like),
            )
        )
    if f.status:
        stmt = stmt.where(Lead.status.in_(f.status.split(",")))
    if f.source:
        stmt = stmt.where(Lead.source == f.source)
    if f.industry:
        stmt = stmt.where(func.lower(Lead.industry) == f.industry.lower())
    if f.tag:
        stmt = stmt.where(Lead.tags.any(Tag.name == f.tag.lower()))
    if f.list_id:
        stmt = stmt.where(Lead.id.in_(select(ListMembership.lead_id).where(ListMembership.list_id == f.list_id)))
    if f.segment_id:
        seg = db.get(Segment, f.segment_id)
        if seg is None:
            raise NotFound("Segment not found")
        stmt = stmt.where(segment_condition(seg.match, seg.rules))
    if f.contacted is True:
        stmt = stmt.where(Lead.last_contacted_at.is_not(None))
    elif f.contacted is False:
        stmt = stmt.where(Lead.last_contacted_at.is_(None))
    return stmt


def query_leads(db: Session, f: LeadFilters) -> tuple[list[Lead], int]:
    stmt = build_query(db, f)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    col = SORTABLE.get(f.sort, Lead.created_at)
    stmt = stmt.order_by(col.desc() if f.order == "desc" else col.asc(), Lead.id.desc())
    stmt = stmt.offset((max(f.page, 1) - 1) * f.page_size).limit(f.page_size)
    return list(db.scalars(stmt).unique()), total


def to_out(db: Session, leads: list[Lead], *, with_campaigns: bool = False) -> list[LeadOut]:
    suppressed = suppression.suppressed_set(db, [lead.email for lead in leads])
    camp_map: dict[int, list[CampaignRef]] = {}
    if with_campaigns and leads:
        rows = db.execute(
            select(CampaignRecipient.lead_id, Campaign.id, Campaign.name, Campaign.status, CampaignRecipient.status)
            .join(Campaign, Campaign.id == CampaignRecipient.campaign_id)
            .where(CampaignRecipient.lead_id.in_([lead.id for lead in leads]))
        ).all()
        for lead_id, cid, name, cstatus, rstatus in rows:
            camp_map.setdefault(lead_id, []).append(CampaignRef(id=cid, name=name, status=cstatus, recipient_status=rstatus))
    out = []
    for lead in leads:
        fields = {
            k: getattr(lead, k) for k in LeadOut.model_fields if k not in ("company", "company_website", "tags", "suppressed", "campaigns")
        }
        out.append(
            LeadOut(
                **fields,
                company=lead.company.name if lead.company else None,
                company_website=lead.company.website if lead.company else None,
                tags=[t.name for t in lead.tags],
                suppressed=lead.email in suppressed,
                campaigns=camp_map.get(lead.id, []),
            )
        )
    return out


# --- Duplicates & merge --------------------------------------------------------------


def canonical_email(email: str) -> str:
    local, _, domain = email.lower().partition("@")
    local = local.split("+", 1)[0]
    if domain in {"gmail.com", "googlemail.com"}:
        local = local.replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


def find_duplicate_groups(db: Session) -> list[dict[str, Any]]:
    """Potential duplicates: same canonical address (plus-tags/Gmail dots) or same name at the same company."""
    leads = list(db.scalars(select(Lead).order_by(Lead.created_at)))
    by_canon: dict[str, list[Lead]] = {}
    by_name: dict[tuple[str, int], list[Lead]] = {}
    for lead in leads:
        by_canon.setdefault(canonical_email(lead.email), []).append(lead)
        name = (lead.full_name or f"{lead.first_name or ''} {lead.last_name or ''}").strip().lower()
        name = re.sub(r"\s+", " ", name)
        if name and lead.company_id:
            by_name.setdefault((name, lead.company_id), []).append(lead)
    groups: list[dict[str, Any]] = []
    seen: set[frozenset[int]] = set()
    for reason, mapping in (("same_address", by_canon), ("same_name_company", by_name)):
        for members in mapping.values():
            if len(members) < 2:
                continue
            key = frozenset(m.id for m in members)
            if key in seen:
                continue
            seen.add(key)
            groups.append({"reason": reason, "lead_ids": [m.id for m in members]})
    return groups


MERGE_FIELDS = [
    "first_name",
    "last_name",
    "full_name",
    "job_title",
    "company_id",
    "linkedin_url",
    "industry",
    "country",
    "timezone",
    "source",
    "notes",
]


def merge_leads(db: Session, primary_id: int, duplicate_ids: list[int], confirm: bool) -> Lead:
    if not confirm:
        raise ValidationFailed("Merging requires explicit confirmation", code="confirmation_required")
    if primary_id in duplicate_ids:
        raise ValidationFailed("Primary lead cannot also be a duplicate")
    primary = get_lead(db, primary_id)
    dups = list(db.scalars(select(Lead).where(Lead.id.in_(duplicate_ids))))
    if len(dups) != len(set(duplicate_ids)):
        raise NotFound("One or more duplicate leads not found")
    for dup in dups:
        for f in MERGE_FIELDS:  # fill blanks only — primary's existing values win
            if getattr(primary, f) in (None, "") and getattr(dup, f) not in (None, ""):
                setattr(primary, f, getattr(dup, f))
        merged_custom = dict(dup.custom_fields or {})
        merged_custom.update(primary.custom_fields or {})
        primary.custom_fields = merged_custom
        primary.tags = list({t.id: t for t in [*primary.tags, *dup.tags]}.values())
        # Move memberships that don't conflict
        existing_lists = set(db.scalars(select(ListMembership.list_id).where(ListMembership.lead_id == primary.id)))
        for m in db.scalars(select(ListMembership).where(ListMembership.lead_id == dup.id)):
            if m.list_id not in existing_lists:
                m.lead_id = primary.id
        existing_camps = set(db.scalars(select(CampaignRecipient.campaign_id).where(CampaignRecipient.lead_id == primary.id)))
        for r in db.scalars(select(CampaignRecipient).where(CampaignRecipient.lead_id == dup.id)):
            if r.campaign_id not in existing_camps:
                r.lead_id = primary.id
        db.execute(update(Conversation).where(Conversation.lead_id == dup.id).values(lead_id=primary.id))
        db.execute(update(SendAttempt).where(SendAttempt.lead_id == dup.id).values(lead_id=primary.id))
        db.execute(update(AiDraft).where(AiDraft.lead_id == dup.id).values(lead_id=primary.id))
        for ts in ("last_contacted_at", "last_replied_at"):
            a, b = getattr(primary, ts), getattr(dup, ts)
            if b and (a is None or b > a):
                setattr(primary, ts, b)
        db.flush()
        db.delete(dup)
    audit.record(
        db,
        "lead.merged",
        f"Merged {len(dups)} duplicate(s) into {primary.email}",
        entity_type="lead",
        entity_id=primary.id,
        details={"merged": [{"id": d.id, "email": d.email} for d in dups]},
    )
    db.flush()
    return primary


# --- Bulk -----------------------------------------------------------------------------------


def bulk_action(db: Session, ids: list[int], action: str, value: str | None, confirm: bool) -> int:
    leads = list(db.scalars(select(Lead).where(Lead.id.in_(ids))))
    if action == "delete":
        if not confirm:
            raise ValidationFailed("Bulk delete requires confirmation", code="confirmation_required")
        return delete_leads(db, ids)
    if action in {"add_tag", "remove_tag"}:
        name = (value or "").strip().lower()
        if not name:
            raise ValidationFailed("Tag name required")
        tag = _tags(db, [name])[0]
        for lead in leads:
            if action == "add_tag" and tag not in lead.tags:
                lead.tags.append(tag)
            elif action == "remove_tag" and tag in lead.tags:
                lead.tags.remove(tag)
    elif action in {"add_to_list", "remove_from_list"}:
        try:
            list_id = int(value or "")
        except ValueError as exc:
            raise ValidationFailed("List id required") from exc
        if db.get(LeadList, list_id) is None:
            raise NotFound("List not found")
        if action == "add_to_list":
            add_to_list(db, list_id, [lead.id for lead in leads])
        else:
            db.execute(
                delete(ListMembership).where(ListMembership.list_id == list_id, ListMembership.lead_id.in_([lead.id for lead in leads]))
            )
    elif action == "set_status":
        try:
            status = LeadStatus(value or "")
        except ValueError as exc:
            raise ValidationFailed("Invalid status") from exc
        for lead in leads:
            update_lead(db, lead.id, LeadUpdate(status=status))
    elif action == "do_not_contact":
        if not confirm:
            raise ValidationFailed("Marking do-not-contact requires confirmation", code="confirmation_required")
        for lead in leads:
            suppression.suppress(db, email=lead.email, reason=SuppressionReason.DO_NOT_CONTACT, source="bulk")
    else:
        raise ValidationFailed(f"Unknown action {action}")
    audit.record(db, f"lead.bulk.{action}", f"Bulk {action} on {len(leads)} lead(s)", details={"value": value})
    return len(leads)


def add_to_list(db: Session, list_id: int, lead_ids: list[int]) -> int:
    existing = set(db.scalars(select(ListMembership.lead_id).where(ListMembership.list_id == list_id)))
    valid = set(db.scalars(select(Lead.id).where(Lead.id.in_(lead_ids))))
    added = 0
    for lid in lead_ids:
        if lid in valid and lid not in existing:
            db.add(ListMembership(list_id=list_id, lead_id=lid))
            existing.add(lid)
            added += 1
    return added


# --- Export ------------------------------------------------------------------------------

EXPORT_COLUMNS = [
    "email",
    "first_name",
    "last_name",
    "full_name",
    "job_title",
    "company",
    "company_website",
    "linkedin_url",
    "industry",
    "country",
    "timezone",
    "source",
    "status",
    "tags",
    "notes",
    "created_at",
    "last_contacted_at",
    "last_replied_at",
]


def csv_safe(value: Any) -> str:
    """Neutralise spreadsheet formula injection (=, +, -, @, tab, CR)."""
    s = "" if value is None else str(value)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


def export_csv(db: Session, leads: list[Lead]) -> str:
    buf = io.StringIO()
    custom_keys = sorted({k for lead in leads for k in (lead.custom_fields or {})})
    writer = csv.writer(buf)
    writer.writerow(EXPORT_COLUMNS + [f"custom.{k}" for k in custom_keys])
    for lead in leads:
        row = {
            "email": lead.email,
            "first_name": lead.first_name,
            "last_name": lead.last_name,
            "full_name": lead.full_name,
            "job_title": lead.job_title,
            "company": lead.company.name if lead.company else "",
            "company_website": lead.company.website if lead.company else "",
            "linkedin_url": lead.linkedin_url,
            "industry": lead.industry,
            "country": lead.country,
            "timezone": lead.timezone,
            "source": lead.source,
            "status": lead.status,
            "tags": ";".join(t.name for t in lead.tags),
            "notes": lead.notes,
            "created_at": lead.created_at.isoformat() if lead.created_at else "",
            "last_contacted_at": lead.last_contacted_at.isoformat() if lead.last_contacted_at else "",
            "last_replied_at": lead.last_replied_at.isoformat() if lead.last_replied_at else "",
        }
        writer.writerow([csv_safe(row[c]) for c in EXPORT_COLUMNS] + [csv_safe((lead.custom_fields or {}).get(k)) for k in custom_keys])
    audit.record(db, "lead.exported", f"Exported {len(leads)} lead(s) to CSV")
    return buf.getvalue()
