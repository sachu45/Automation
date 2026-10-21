"""Lead discovery API. Long-running work is queued for the worker; nothing here sends email."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_auth
from app.core import ratelimit
from app.core.config import get_settings
from app.core.errors import NotFound, ValidationFailed
from app.db.base import utcnow
from app.models import AiDraft, DiscoveryRun, DiscoverySignal, Lead, ScheduledJob
from app.models.enums import JobKind, JobStatus
from app.services import ai as ai_svc
from app.services import audit
from app.services.discovery import config as dcfg
from app.services.discovery import pipeline, usage
from app.services.discovery.sources import SourceError, manual_signal

router = APIRouter(prefix="/api/discovery", tags=["discovery"], dependencies=[Depends(require_auth)])

PROVIDER_INFO = {
    "rss": {
        "label": "RSS / Atom feeds",
        "requires_key": False,
        "cost": "Free — public feeds you add (company blogs, job boards that publish feeds).",
    },
    "website": {
        "label": "Company websites",
        "requires_key": False,
        "cost": "Free — official pages only; robots.txt respected; ≤ N pages per domain.",
    },
    "manual": {
        "label": "Manual / LinkedIn paste",
        "requires_key": False,
        "cost": "Free — you paste a URL and the text you're authorised to use. LinkedIn is never fetched.",
    },
    "apollo": {
        "label": "Apollo.io",
        "requires_key": True,
        "cost": "People Search uses 0 credits but returns no emails. Enrichment (emails) uses 1+ credits per person and is off unless enabled.",
    },
    "google_cse": {
        "label": "Google Custom Search",
        "requires_key": True,
        "cost": "Closed to new customers; shuts down 2027-01-01. Only for projects that already have access.",
    },
    "gemini": {
        "label": "Google Gemini (drafting)",
        "requires_key": True,
        "cost": "Free tier on some models with low daily limits (check aistudio.google.com/rate-limit). Billing is never enabled by this app.",
    },
}


def _signal_summary(s: DiscoverySignal, draft: AiDraft | None = None) -> dict[str, Any]:
    return {
        "id": s.id,
        "source_provider": s.source_provider,
        "source_type": s.source_type,
        "source_url": s.source_url,
        "category": s.category,
        "title": s.title,
        "company_name": s.company_name,
        "company_domain": s.company_domain,
        "contact_name": s.contact_name,
        "contact_title": s.contact_title,
        "lead_id": s.lead_id,
        "published_at": s.published_at,
        "discovered_at": s.discovered_at,
        "excerpt": s.excerpt,
        "relevance_score": s.relevance_score,
        "qualification_status": s.qualification_status,
        "processing_status": s.processing_status,
        "error_code": s.error_code,
        "error_detail": s.error_detail,
        "test_mode": s.test_mode,
        "draft": {"id": draft.id, "status": draft.status, "subject": draft.subject} if draft else None,
        "reasons": s.reasons or [],
    }


def _latest_draft(db: Session, signal_id: int) -> AiDraft | None:
    return db.scalar(select(AiDraft).where(AiDraft.signal_id == signal_id).order_by(AiDraft.id.desc()).limit(1))


# --- Status & settings --------------------------------------------------------------------------


@router.get("/status")
def status(db: Session = Depends(db_session)) -> dict[str, Any]:
    cfg = dcfg.get_config(db)
    last_run = db.scalar(select(DiscoveryRun).order_by(DiscoveryRun.started_at.desc()).limit(1))
    last_ok = db.scalar(
        select(DiscoveryRun).where(DiscoveryRun.status.in_(["completed", "partial"])).order_by(DiscoveryRun.started_at.desc()).limit(1)
    )
    counts: dict[str, int] = {
        k: n
        for k, n in db.execute(select(DiscoverySignal.processing_status, func.count()).group_by(DiscoverySignal.processing_status)).all()
    }
    qual: dict[str, int] = {
        k: n
        for k, n in db.execute(
            select(DiscoverySignal.qualification_status, func.count()).group_by(DiscoverySignal.qualification_status)
        ).all()
    }
    duplicates = sum(int((r.stats or {}).get("duplicates", 0)) for r in db.scalars(select(DiscoveryRun)))
    failed_jobs = (
        db.scalar(
            select(func.count())
            .select_from(ScheduledJob)
            .where(ScheduledJob.kind.in_([JobKind.DISCOVERY_RUN, JobKind.SIGNAL_PROCESS]), ScheduledJob.status == JobStatus.FAILED)
        )
        or 0
    )
    queued = (
        db.scalar(
            select(func.count())
            .select_from(ScheduledJob)
            .where(
                ScheduledJob.kind.in_([JobKind.DISCOVERY_RUN, JobKind.SIGNAL_PROCESS]),
                ScheduledJob.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
            )
        )
        or 0
    )
    drafts = db.scalar(select(func.count()).select_from(AiDraft).where(AiDraft.origin == "discovery")) or 0
    pending_review = (
        db.scalar(select(func.count()).select_from(AiDraft).where(AiDraft.origin == "discovery", AiDraft.status == "draft")) or 0
    )
    warnings: list[str] = []
    if cfg.enrichment_enabled and usage.credits_today(db, "apollo_enrichment") >= cfg.enrichment_daily_credit_budget:
        warnings.append("Apollo enrichment credit budget for today is used up")
    for p in ("apollo", "google_cse", "rss", "website"):
        info = usage.latest(db, p)
        if p in cfg.enabled_sources and info["errors_today"]:
            warnings.append(f"{PROVIDER_INFO[p]['label']}: {info['last_error']}")
    return {
        "enabled": cfg.enabled,
        "test_mode": cfg.test_mode,
        "draft_generation_enabled": cfg.draft_generation_enabled,
        "interval_minutes": cfg.interval_minutes,
        "last_run": {
            "id": last_run.id,
            "status": last_run.status,
            "started_at": last_run.started_at,
            "finished_at": last_run.finished_at,
            "stats": last_run.stats,
            "errors": last_run.errors,
        }
        if last_run
        else None,
        "last_successful_run_at": last_ok.started_at if last_ok else None,
        "next_run_at": pipeline.next_run_at(db),
        "signals_total": sum(counts.values()),
        "by_processing_status": counts,
        "by_qualification": qual,
        "qualified": qual.get("qualified", 0),
        "drafts_created": drafts,
        "drafts_awaiting_review": pending_review,
        "duplicates_skipped": duplicates,
        "failed_jobs": failed_jobs,
        "queued_jobs": queued,
        "warnings": warnings,
    }


@router.get("/config")
def get_config(db: Session = Depends(db_session)) -> dict[str, Any]:
    return {"config": dcfg.get_config(db).model_dump(mode="json"), "categories": dcfg.SIGNAL_CATEGORIES, "sources": dcfg.PROVIDERS}


class ConfigPatch(BaseModel):
    changes: dict[str, Any]
    confirm: bool = False


@router.patch("/config")
def patch_config(body: ConfigPatch, db: Session = Depends(db_session)) -> dict[str, Any]:
    if any(k in body.changes for k in ("dry_run", "emergency_stop")):
        raise ValidationFailed("Sending settings can't be changed from discovery")
    cfg = dcfg.update_config(db, body.changes, confirm=body.confirm)
    db.commit()
    return {"config": cfg.model_dump(mode="json")}


# --- Providers ----------------------------------------------------------------------------------


@router.get("/providers")
def providers(db: Session = Depends(db_session)) -> list[dict[str, Any]]:
    cfg = dcfg.get_config(db)
    s = get_settings()
    ai = ai_svc.provider_status(db)
    out = []
    for name, info in PROVIDER_INFO.items():
        configured: bool
        if name in ("rss", "website", "manual"):
            configured = True
        elif name == "google_cse":
            configured = bool(dcfg.secret_source(db, "google_cse")) and bool(s.google_search_engine_id)
        else:
            configured = bool(dcfg.secret_source(db, name))
        detail: dict[str, Any] = {}
        if name == "rss":
            detail["feeds"] = len(cfg.feeds)
        if name == "website":
            detail["watch_domains"] = len(cfg.watch_domains)
        if name == "gemini":
            detail.update(
                {"model": ai["model"] if ai["provider"] == "gemini" else s.gemini_model, "active_for_drafting": ai["provider"] == "gemini"}
            )
        if name == "google_cse":
            detail["engine_id_configured"] = bool(s.google_search_engine_id)
        if name == "apollo":
            detail.update(
                {
                    "enrichment_enabled": cfg.enrichment_enabled,
                    "credit_budget": cfg.enrichment_daily_credit_budget,
                    "credits_used_today": usage.credits_today(db, "apollo_enrichment"),
                }
            )
        out.append(
            {
                "name": name,
                **info,
                "configured": configured,
                "key_source": dcfg.secret_source(db, name) if name in dcfg.SECRET_PROVIDERS else None,
                "enabled": name in cfg.enabled_sources if name not in ("gemini",) else ai["provider"] == "gemini",
                "usage": usage.latest(db, name),
                **detail,
            }
        )
    return out


class KeyIn(BaseModel):
    api_key: str | None = Field(default=None, max_length=500)


@router.put("/providers/{name}/key")
def set_key(name: str, body: KeyIn, db: Session = Depends(db_session)) -> dict[str, Any]:
    if name not in dcfg.SECRET_PROVIDERS:
        raise NotFound("Unknown provider")
    dcfg.set_secret(db, name, body.api_key)
    db.commit()
    return {"configured": bool(dcfg.secret_source(db, name)), "key_source": dcfg.secret_source(db, name)}


@router.post("/providers/{name}/test")
def test_provider(name: str, request: Request, db: Session = Depends(db_session)) -> dict[str, Any]:
    """Free, read-only checks only (no credit-consuming calls)."""
    ratelimit.enforce(request, "discovery_test", 10, 60)
    try:
        if name == "apollo":
            from app.services.discovery.apollo import ApolloSource

            msg = (pipeline._source_overrides.get("apollo") or ApolloSource(dcfg.get_secret(db, "apollo"))).test()  # type: ignore[attr-defined]
        elif name == "google_cse":
            from app.services.discovery.search import GoogleCseSource

            msg = GoogleCseSource(dcfg.get_secret(db, "google_cse"), get_settings().google_search_engine_id).test()
        elif name == "gemini":
            from app.providers.ai.gemini import GeminiProvider

            s = get_settings()
            g = GeminiProvider(dcfg.get_secret(db, "gemini"), s.gemini_model, s.gemini_base_url, timeout=30, max_output_tokens=64)
            g.complete_json(system='Reply with the JSON object {"ok": true}.', user="ping", purpose="test", inputs={})
            msg = f"Gemini responded using {s.gemini_model} (1 request counted against your quota)."
        elif name in ("rss", "website", "manual"):
            msg = "No key needed."
        else:
            raise NotFound("Unknown provider")
        usage.record(db, name, ok=True)
        db.commit()
        return {"ok": True, "message": msg}
    except SourceError as exc:
        usage.record(db, name, ok=False, error=exc.message)
        db.commit()
        return {"ok": False, "message": exc.message, "code": exc.code}
    except Exception as exc:  # noqa: BLE001 - AIProviderError etc.; message only, no secrets
        usage.record(db, name, ok=False, error=str(exc)[:200])
        db.commit()
        return {"ok": False, "message": str(exc)[:300]}


# --- Runs ---------------------------------------------------------------------------------------------


class RunIn(BaseModel):
    only_sources: list[str] | None = None


@router.post("/run")
def run_now(body: RunIn, request: Request, db: Session = Depends(db_session)) -> dict[str, Any]:
    ratelimit.enforce(request, "discovery_run", 6, 60)
    job = pipeline.queue_manual_run(db, body.only_sources)
    db.commit()
    return {"queued": True, "job_id": job.id}


@router.get("/runs")
def runs(limit: int = Query(20, le=100), db: Session = Depends(db_session)) -> list[dict[str, Any]]:
    return [
        {
            "id": r.id,
            "trigger": r.trigger,
            "status": r.status,
            "test_mode": r.test_mode,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
            "stats": r.stats,
            "errors": r.errors,
        }
        for r in db.scalars(select(DiscoveryRun).order_by(DiscoveryRun.started_at.desc()).limit(limit))
    ]


@router.get("/jobs/failed")
def failed_jobs(db: Session = Depends(db_session)) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(ScheduledJob)
        .where(ScheduledJob.kind.in_([JobKind.DISCOVERY_RUN, JobKind.SIGNAL_PROCESS]), ScheduledJob.status == JobStatus.FAILED)
        .order_by(ScheduledJob.id.desc())
        .limit(50)
    )
    return [
        {
            "id": j.id,
            "kind": j.kind,
            "attempts": j.attempts,
            "last_error": j.last_error,
            "finished_at": j.finished_at,
            "signal_id": j.payload.get("signal_id"),
        }
        for j in rows
    ]


# --- Signals ---------------------------------------------------------------------------------------------


@router.get("/signals")
def list_signals(
    q: str | None = None,
    processing_status: str | None = None,
    qualification_status: str | None = None,
    provider: str | None = None,
    category: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(db_session),
) -> dict[str, Any]:
    stmt = select(DiscoverySignal)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(func.coalesce(DiscoverySignal.title, "")).like(like),
                func.lower(func.coalesce(DiscoverySignal.company_name, "")).like(like),
                func.lower(func.coalesce(DiscoverySignal.company_domain, "")).like(like),
            )
        )
    if processing_status:
        stmt = stmt.where(DiscoverySignal.processing_status.in_(processing_status.split(",")))
    else:
        stmt = stmt.where(DiscoverySignal.processing_status != "archived")
    if qualification_status:
        stmt = stmt.where(DiscoverySignal.qualification_status == qualification_status)
    if provider:
        stmt = stmt.where(DiscoverySignal.source_provider == provider)
    if category:
        stmt = stmt.where(DiscoverySignal.category == category)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        db.scalars(
            stmt.order_by(DiscoverySignal.discovered_at.desc(), DiscoverySignal.id.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    )
    return {"items": [_signal_summary(s, _latest_draft(db, s.id)) for s in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/signals/{signal_id}")
def get_signal(signal_id: int, db: Session = Depends(db_session)) -> dict[str, Any]:
    s = db.get(DiscoverySignal, signal_id)
    if s is None:
        raise NotFound("Signal not found")
    out = _signal_summary(s, _latest_draft(db, s.id))
    out.update({"evidence": s.evidence or [], "score_components": s.score_components or {}, "content_hash": s.content_hash})
    lead = db.get(Lead, s.lead_id) if s.lead_id else None
    out["lead"] = {"id": lead.id, "email": lead.email, "name": lead.full_name, "status": lead.status} if lead else None
    out["drafts"] = [
        {"id": d.id, "status": d.status, "subject": d.subject, "label": d.label, "created_at": d.created_at}
        for d in db.scalars(select(AiDraft).where(AiDraft.signal_id == s.id).order_by(AiDraft.id))
    ]
    return out


class ManualIn(BaseModel):
    url: str | None = Field(default=None, max_length=1000)
    text: str | None = Field(default=None, max_length=20_000)
    title: str | None = Field(default=None, max_length=500)
    company_name: str | None = Field(default=None, max_length=255)
    company_domain: str | None = Field(default=None, max_length=255)
    contact_name: str | None = Field(default=None, max_length=255)
    contact_title: str | None = Field(default=None, max_length=255)
    contact_email: str | None = Field(default=None, max_length=320)
    category: str | None = None
    published_at: datetime | None = None
    authorised: bool = False


@router.post("/signals/manual", status_code=201)
def submit_manual(body: ManualIn, request: Request, db: Session = Depends(db_session)) -> dict[str, Any]:
    ratelimit.enforce(request, "discovery_manual", 20, 60)
    if not body.authorised:
        raise ValidationFailed("Confirm you are authorised to use this content", code="confirmation_required")
    if body.category and body.category not in dcfg.SIGNAL_CATEGORIES:
        raise ValidationFailed("Unknown category")
    if body.url and not body.url.lower().startswith(("http://", "https://")):
        raise ValidationFailed("URL must start with http:// or https://")
    if body.published_at and body.published_at.tzinfo is None:
        raise ValidationFailed("published_at needs a timezone")
    cfg = dcfg.get_config(db)
    try:
        raw = manual_signal(
            url=body.url,
            text=body.text,
            title=body.title,
            company_name=body.company_name,
            company_domain=(body.company_domain or "").lower().strip() or None,
            contact_name=body.contact_name,
            contact_title=body.contact_title,
            category=body.category,
            published_at=body.published_at,
            fetcher=pipeline.make_fetcher(db),
            max_chars=cfg.max_evidence_chars,
        )
    except SourceError as exc:
        raise ValidationFailed(exc.message, code=exc.code) from exc
    sig = pipeline.submit_manual(db, raw, body.contact_email)
    db.commit()
    return _signal_summary(sig)


@router.post("/signals/{signal_id}/reprocess")
def reprocess(signal_id: int, db: Session = Depends(db_session)) -> dict[str, Any]:
    """Re-run qualification (and drafting, if it qualifies). Creates a new draft version; never approves or sends."""
    s = db.get(DiscoverySignal, signal_id)
    if s is None:
        raise NotFound("Signal not found")
    if s.processing_status == "archived":
        raise ValidationFailed("Signal is archived")
    version = 1 + (db.scalar(select(func.count()).select_from(AiDraft).where(AiDraft.signal_id == s.id)) or 0)
    s.processing_status, s.qualification_status, s.error_code, s.error_detail = "discovered", "pending", None, None
    job = pipeline.enqueue_processing(db, s, version=version, regenerate=True)
    if job.status != JobStatus.PENDING:
        job.status, job.run_at, job.attempts, job.last_error = JobStatus.PENDING, utcnow(), 0, None
    audit.record(
        db, "discovery.reprocess", f"Signal {s.id} queued for reprocessing (v{version})", entity_type="discovery_signal", entity_id=s.id
    )
    db.commit()
    return {"queued": True, "version": version}


@router.post("/signals/{signal_id}/archive")
def archive(signal_id: int, db: Session = Depends(db_session)) -> dict[str, Any]:
    s = db.get(DiscoverySignal, signal_id)
    if s is None:
        raise NotFound("Signal not found")
    s.processing_status = "archived"
    audit.record(db, "discovery.archived", f"Signal {s.id} archived", entity_type="discovery_signal", entity_id=s.id)
    db.commit()
    return {"ok": True}


@router.post("/signals/{signal_id}/purge-evidence")
def purge_evidence(signal_id: int, db: Session = Depends(db_session)) -> dict[str, Any]:
    s = db.get(DiscoverySignal, signal_id)
    if s is None:
        raise NotFound("Signal not found")
    s.excerpt, s.evidence, s.processing_status = None, [], "archived"
    audit.record(db, "discovery.evidence_purged", f"Evidence deleted for signal {s.id}", entity_type="discovery_signal", entity_id=s.id)
    db.commit()
    return {"ok": True}


# --- Drafts (review queue). Editing/approval use the existing /api/ai/drafts endpoints. --------------------------


@router.get("/drafts")
def drafts(status: str | None = "draft", db: Session = Depends(db_session)) -> list[dict[str, Any]]:
    stmt = select(AiDraft).where(AiDraft.origin == "discovery").order_by(AiDraft.created_at.desc()).limit(100)
    if status:
        stmt = stmt.where(AiDraft.status == status)
    out = []
    for d in db.scalars(stmt):
        s = db.get(DiscoverySignal, d.signal_id) if d.signal_id else None
        lead = db.get(Lead, d.lead_id) if d.lead_id else None
        out.append(
            {
                "id": d.id,
                "status": d.status,
                "subject": d.subject,
                "body": d.body,
                "label": d.label,
                "provider": d.provider,
                "created_at": d.created_at,
                "warnings": (d.output or {}).get("warnings", []),
                "evidence_refs": (d.output or {}).get("evidence_refs", []),
                "test_mode": (d.output or {}).get("test_mode", False),
                "lead": {"id": lead.id, "email": lead.email, "name": lead.full_name, "company": lead.company.name if lead.company else None}
                if lead
                else None,
                "signal": _signal_summary(s) if s else None,
            }
        )
    return out
