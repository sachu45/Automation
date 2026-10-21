"""Lead-discovery pipeline. Runs only in the worker. It never sends email, approves drafts,
creates campaigns or changes sending settings — it saves signals and drafts for review."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import utcnow
from app.models import AiDraft, AiRequest, Company, DiscoveryRun, DiscoverySignal, Lead, ScheduledJob
from app.models.enums import JobKind, JobStatus
from app.schemas.leads import LeadCreate
from app.services import audit
from app.services import leads as lead_svc
from app.services.discovery import config as dcfg
from app.services.discovery import drafting, scoring, usage
from app.services.discovery.apollo import ApolloSource
from app.services.discovery.fetcher import FetchBlocked, FetchFailed, SafeFetcher
from app.services.discovery.search import GoogleCseSource
from app.services.discovery.sources import RawSignal, RssSource, SignalSource, SourceError, WebsiteSource
from app.services.emails import InvalidEmail, validate_address

log = logging.getLogger("app.discovery")

# Tests (and future adapters) can replace how sources/fetchers are built.
_source_overrides: dict[str, SignalSource] = {}
_fetcher_factory = None


def set_source_override(name: str, source: SignalSource | None) -> None:
    if source is None:
        _source_overrides.pop(name, None)
    else:
        _source_overrides[name] = source


def set_fetcher_factory(factory) -> None:  # type: ignore[no-untyped-def]
    global _fetcher_factory
    _fetcher_factory = factory


def make_fetcher(db: Session) -> SafeFetcher:
    return _fetcher_factory(db) if _fetcher_factory else SafeFetcher(db)


def build_source(db: Session, name: str, fetcher: SafeFetcher) -> SignalSource:
    if name in _source_overrides:
        return _source_overrides[name]
    if name == "rss":
        return RssSource(fetcher)
    if name == "website":
        return WebsiteSource(fetcher)
    if name == "apollo":
        return ApolloSource(dcfg.get_secret(db, "apollo"))
    if name == "google_cse":
        return GoogleCseSource(dcfg.get_secret(db, "google_cse"), get_settings().google_search_engine_id)
    raise SourceError(f"Unknown source {name}", permanent=True)


# --- Persistence helpers ---------------------------------------------------------------------------


def insert_signal(db: Session, raw: RawSignal, run: DiscoveryRun | None, test_mode: bool) -> DiscoverySignal | None:
    """Insert unless an identical signal exists. The UNIQUE(dedupe_key) constraint makes this safe across workers."""
    if db.scalar(select(DiscoverySignal.id).where(DiscoverySignal.dedupe_key == raw.dedupe_key)):
        return None
    evidence = list(raw.evidence)
    if raw.attributes and evidence:
        evidence[0] = {**evidence[0], "attributes": raw.attributes}
    sig = DiscoverySignal(
        dedupe_key=raw.dedupe_key,
        run_id=run.id if run else None,
        source_provider=raw.source_provider,
        source_type=raw.source_type,
        source_url=raw.source_url,
        source_external_id=raw.source_external_id,
        category=raw.category,
        title=raw.title,
        company_name=raw.company_name,
        company_domain=(raw.company_domain or "").lower().removeprefix("www.") or None,
        contact_name=raw.contact_name,
        contact_title=raw.contact_title,
        contact_external_id=raw.contact_external_id,
        published_at=raw.published_at,
        excerpt=raw.excerpt,
        evidence=evidence,
        content_hash=raw.content_hash,
        test_mode=test_mode,
        processing_status="discovered",
        qualification_status="pending",
    )
    try:
        with db.begin_nested():
            db.add(sig)
            db.flush()
    except IntegrityError:
        return None
    return sig


def enqueue_processing(db: Session, sig: DiscoverySignal, *, version: int = 1, regenerate: bool = False) -> ScheduledJob:
    key = f"signal:{sig.id}:process:v{version}"
    existing = db.scalar(select(ScheduledJob).where(ScheduledJob.idempotency_key == key))
    if existing is not None:
        return existing
    job = ScheduledJob(
        kind=JobKind.SIGNAL_PROCESS,
        idempotency_key=key,
        run_at=utcnow(),
        max_attempts=4,
        payload={"signal_id": sig.id, "version": version, "regenerate": regenerate},
    )
    db.add(job)
    return job


def signals_today(db: Session) -> int:
    start = datetime.combine(utcnow().date(), datetime.min.time(), tzinfo=UTC)
    return db.scalar(select(func.count()).select_from(DiscoverySignal).where(DiscoverySignal.discovered_at >= start)) or 0


# --- Run -----------------------------------------------------------------------------------------------


def run_discovery(db: Session, *, trigger: str, job_id: int | None = None, only_sources: list[str] | None = None) -> DiscoveryRun:
    config = dcfg.get_config(db)
    run = DiscoveryRun(trigger=trigger, status="running", test_mode=config.test_mode, job_id=job_id, stats={}, errors=[])
    db.add(run)
    db.commit()
    audit.record(
        db,
        "discovery.run_started",
        f"Discovery run {run.id} started ({trigger}{', test mode' if config.test_mode else ''})",
        entity_type="discovery_run",
        entity_id=run.id,
    )
    stats: dict[str, int] = {"found": 0, "new": 0, "duplicates": 0, "queued": 0}
    errors: list[dict[str, Any]] = []
    remaining = min(config.max_results_per_run, max(0, config.daily_limit - signals_today(db)))
    if remaining == 0:
        errors.append({"source": "all", "code": "daily_limit", "message": "Daily discovery limit reached"})
    fetcher = make_fetcher(db)
    sources = [s for s in (only_sources or config.enabled_sources) if s != "manual"]
    for name in sources:
        if remaining <= 0:
            break
        try:
            source = build_source(db, name, fetcher)
            raws = source.discover(config, remaining)
            usage.record(db, name, ok=True)
        except SourceError as exc:
            usage.record(db, name, ok=False, error=exc.message)
            errors.append({"source": name, "code": exc.code, "message": exc.message[:300]})
            db.commit()
            continue
        except (FetchBlocked, FetchFailed) as exc:
            usage.record(db, name, ok=False, error=str(exc))
            errors.append({"source": name, "code": "fetch", "message": str(exc)[:300]})
            db.commit()
            continue
        stats["found"] += len(raws)
        for raw in raws[:remaining]:
            sig = insert_signal(db, raw, run, config.test_mode)
            if sig is None:
                stats["duplicates"] += 1
                continue
            stats["new"] += 1
            remaining -= 1
            enqueue_processing(db, sig)
            stats["queued"] += 1
        db.commit()
    run.stats = stats
    run.errors = errors
    run.finished_at = utcnow()
    run.status = "failed" if errors and stats["found"] == 0 and sources else "partial" if errors else "completed"
    audit.record(
        db,
        "discovery.run_finished",
        f"Discovery run {run.id} {run.status}: {stats['new']} new signal(s)",
        entity_type="discovery_run",
        entity_id=run.id,
        details={"stats": stats, "errors": len(errors)},
    )
    db.commit()
    return run


def submit_manual(db: Session, raw: RawSignal, contact_email: str | None) -> DiscoverySignal:
    from app.core.errors import Conflict

    config = dcfg.get_config(db)
    if contact_email:
        try:
            contact_email = validate_address(contact_email)
        except InvalidEmail as exc:
            from app.core.errors import ValidationFailed

            raise ValidationFailed(f"Contact email is invalid: {exc}") from exc
        raw.attributes["contact_email"] = contact_email
        raw.attributes["contact_email_source"] = "user_supplied"
    sig = insert_signal(db, raw, None, config.test_mode)
    if sig is None:
        raise Conflict("This signal was already submitted")
    enqueue_processing(db, sig)
    audit.record(
        db, "discovery.manual_signal", f"Manual signal submitted ({raw.source_type})", entity_type="discovery_signal", entity_id=sig.id
    )
    return sig


# --- Per-signal processing -------------------------------------------------------------------------------


def _attributes(sig: DiscoverySignal) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    for e in sig.evidence or []:
        attrs.update(e.get("attributes") or {})
    return attrs


def _company_evidence(db: Session, sig: DiscoverySignal, config: dcfg.DiscoveryConfig, fetcher: SafeFetcher) -> None:
    if not sig.company_domain or sig.source_provider == "website" or "website" not in config.enabled_sources:
        return
    if any(e.get("kind") == "company_page" for e in sig.evidence or []):
        return
    try:
        pages = WebsiteSource(fetcher).company_pages(sig.company_domain, min(2, config.max_pages_per_domain))
    except (FetchBlocked, FetchFailed) as exc:
        sig.evidence = [
            *(sig.evidence or []),
            {
                "kind": "company_page_unavailable",
                "url": f"https://{sig.company_domain}/",
                "text": "",
                "error": str(exc)[:200],
                "retrieved_at": utcnow().isoformat(),
            },
        ]
        return
    extra = []
    for page in pages[:2]:
        extra.append(
            {
                "kind": "company_page",
                "url": page.final_url,
                "title": page.title,
                "text": (page.text or "")[:500],
                "retrieved_at": utcnow().isoformat(),
                "provider": "website",
            }
        )
    sig.evidence = [*(sig.evidence or []), *extra]
    if not sig.company_name and pages and pages[0].title:
        sig.company_name = pages[0].title.split("|")[0].split(" - ")[0].strip()[:255]


def _match_company(db: Session, sig: DiscoverySignal) -> Company | None:
    if sig.company_domain:
        c = db.scalar(select(Company).where(Company.domain == sig.company_domain))
        if c:
            return c
    if sig.company_name:
        return db.scalar(select(Company).where(Company.name_key == sig.company_name.lower()))
    return None


def _enrich_with_apollo(db: Session, sig: DiscoverySignal, config: dcfg.DiscoveryConfig) -> dict[str, Any] | None:
    """Credit-consuming. Only runs when enabled + confirmed + within budget + not in test mode."""
    if sig.source_provider != "apollo" or not sig.contact_external_id:
        return None
    if config.test_mode:
        sig.reasons = [*(sig.reasons or []), {"type": "enrichment", "reason": "Test mode: Apollo enrichment skipped (no credits used)"}]
        return None
    if not (config.enrichment_enabled and config.enrichment_confirmed):
        sig.reasons = [
            *(sig.reasons or []),
            {"type": "enrichment", "reason": "Enrichment is off; enable it in Discovery rules to reveal work emails (uses credits)"},
        ]
        return None
    if usage.credits_today(db, "apollo_enrichment") >= config.enrichment_daily_credit_budget:
        sig.reasons = [*(sig.reasons or []), {"type": "enrichment", "reason": "Daily Apollo credit budget reached"}]
        return None
    source = _source_overrides.get("apollo") or ApolloSource(dcfg.get_secret(db, "apollo"))
    try:
        result = source.enrich(sig.contact_external_id)  # type: ignore[attr-defined]
        usage.record(db, "apollo_enrichment", ok=True, credits=1)  # conservative: count 1 credit per call
    except SourceError as exc:
        usage.record(db, "apollo_enrichment", ok=False, credits=0, error=exc.message)
        sig.reasons = [*(sig.reasons or []), {"type": "enrichment", "reason": f"Apollo enrichment failed: {exc.message}"}]
        return None
    audit.record(
        db,
        "discovery.enrichment",
        f"Apollo enrichment for signal {sig.id} (1 credit budgeted)",
        entity_type="discovery_signal",
        entity_id=sig.id,
    )
    data = result.model_dump()
    sig.evidence = [
        *(sig.evidence or []),
        {
            "kind": "enrichment",
            "provider": "apollo",
            "url": None,
            "retrieved_at": utcnow().isoformat(),
            "text": f"{data.get('title') or ''} at {data.get('company_name') or ''}".strip(),
            "attributes": {k: data.get(k) for k in ("industry", "employees", "country") if data.get(k) is not None},
            "email_status": data.get("email_status"),
        },
    ]
    if data.get("company_domain") and not sig.company_domain:
        sig.company_domain = data["company_domain"]
    if data.get("first_name") and data.get("last_name"):
        sig.contact_name = f"{data['first_name']} {data['last_name']}"
    return data


def _resolve_lead(db: Session, sig: DiscoverySignal, config: dcfg.DiscoveryConfig) -> tuple[Lead | None, str | None, str | None]:
    """Returns (lead, contact_email, note). Never guesses an email address."""
    attrs = _attributes(sig)
    email = attrs.get("contact_email")
    note = None
    enrichment = _enrich_with_apollo(db, sig, config)
    if enrichment:
        if enrichment.get("email") and enrichment.get("email_status") == "verified":
            email = enrichment["email"]
        elif enrichment.get("email"):
            note = f"Apollo returned an email with status '{enrichment.get('email_status')}', which is not verified"
    if sig.lead_id:
        lead = db.get(Lead, sig.lead_id)
        return lead, (lead.email if lead else email), note
    if not email:
        return None, None, note or "No verified contact email for this signal"
    lead = lead_svc.find_by_email(db, email)
    if lead is None:
        first, _, last = (sig.contact_name or "").partition(" ")
        lead = lead_svc.create_lead(
            db,
            LeadCreate(
                email=email,
                first_name=(enrichment or {}).get("first_name") or first or None,
                last_name=(enrichment or {}).get("last_name") or last or None,
                job_title=sig.contact_title,
                company=sig.company_name,
                company_website=f"https://{sig.company_domain}" if sig.company_domain else None,
                industry=(enrichment or {}).get("industry"),
                source=f"discovery:{sig.source_provider}",
                linkedin_url=(enrichment or {}).get("linkedin_url"),
                tags=["discovery"],
                notes=f"Discovery signal: {sig.title or ''} ({sig.source_url or sig.source_provider})"[:1000],
            ),
        )
    sig.lead_id = lead.id
    return lead, email, note


def _ai_quota_left(db: Session, config: dcfg.DiscoveryConfig, run_id: int | None) -> tuple[bool, str | None]:
    start = datetime.combine(utcnow().date(), datetime.min.time(), tzinfo=UTC)
    today_n = (
        db.scalar(select(func.count()).select_from(AiRequest).where(AiRequest.purpose == "discovery_draft", AiRequest.created_at >= start))
        or 0
    )
    if today_n >= config.ai_daily_limit:
        return False, "Daily AI drafting limit reached"
    if run_id is not None:
        run_n = (
            db.scalar(
                select(func.count())
                .select_from(AiDraft)
                .join(DiscoverySignal, DiscoverySignal.id == AiDraft.signal_id)
                .where(DiscoverySignal.run_id == run_id)
            )
            or 0
        )
        if run_n >= config.ai_per_run_limit:
            return False, "Per-run AI drafting limit reached"
    return True, None


def process_signal(db: Session, signal_id: int, *, version: int = 1, regenerate: bool = False) -> str:
    sig = db.get(DiscoverySignal, signal_id)
    if sig is None:
        return "missing"
    if sig.processing_status == "archived":
        return "archived"
    if (
        not regenerate
        and sig.processing_status in ("drafted", "rejected", "qualified", "review_required")
        and sig.qualification_status != "pending"
    ):
        return "already_processed"
    key = f"signal:{sig.id}:v{version}"
    existing_draft = db.scalar(select(AiDraft).where(AiDraft.generation_key == key))
    if existing_draft is not None:
        sig.processing_status = "drafted"
        db.commit()
        return "already_drafted"

    config = dcfg.get_config(db)
    sig.processing_status = "fetch_pending"
    sig.last_checked_at = utcnow()
    fetcher = make_fetcher(db)
    _company_evidence(db, sig, config, fetcher)
    sig.processing_status = "fetched"
    company = _match_company(db, sig)
    sig.company_id = company.id if company else None
    sig.reasons = [r for r in (sig.reasons or []) if r.get("type") == "enrichment"]
    lead, email, note = _resolve_lead(db, sig, config)

    dup = scoring.duplicate_of(db, sig)
    if dup is not None:
        sig.qualification_status, sig.processing_status = "rejected", "rejected"
        sig.reasons = [*sig.reasons, {"type": "duplicate", "reason": f"Same content as signal {dup.id}"}]
        db.commit()
        return "duplicate"
    if lead is not None and not regenerate:
        pending = db.scalar(
            select(AiDraft.id).where(
                AiDraft.lead_id == lead.id, AiDraft.origin == "discovery", AiDraft.status == "draft", AiDraft.signal_id != sig.id
            )
        )
        if pending:
            sig.qualification_status, sig.processing_status = "review_required", "review_required"
            sig.reasons = [*sig.reasons, {"type": "duplicate_lead", "reason": f"Lead already has a pending discovery draft (#{pending})"}]
            db.commit()
            return "lead_has_pending_draft"

    q = scoring.qualify(db, sig, config, lead=lead, contact_email=email, attrs={**_attributes(sig), "contact_title": sig.contact_title})
    sig.relevance_score, sig.score_components = q.score, q.components
    sig.reasons = [*sig.reasons, *q.reasons]
    sig.qualification_status = q.status
    audit.record(
        db, "discovery.qualified", f"Signal {sig.id} {q.status} (score {q.score})", entity_type="discovery_signal", entity_id=sig.id
    )
    if q.status == "rejected":
        sig.processing_status = "rejected"
        db.commit()
        return "rejected"
    if q.status == "review_required" and not regenerate:
        sig.processing_status = "review_required"
        db.commit()
        return "review_required"
    if not config.draft_generation_enabled and not regenerate:
        sig.processing_status = "qualified"
        db.commit()
        return "qualified"
    if lead is None:
        sig.processing_status = "review_required"
        sig.reasons = [*sig.reasons, {"type": "contact", "reason": note or "No verified contact email; add one to generate a draft"}]
        db.commit()
        return "needs_contact"
    ok, why = drafting.has_sufficient_evidence(sig)
    if not ok:
        sig.processing_status = "review_required"
        sig.reasons = [*sig.reasons, {"type": "evidence", "reason": why}]
        db.commit()
        return "insufficient_evidence"
    allowed, why = _ai_quota_left(db, config, sig.run_id)
    if not allowed:
        sig.processing_status, sig.error_code = "draft_pending", "ai_limit"
        sig.error_detail = why
        db.commit()
        return "ai_limit"

    sig.processing_status = "draft_pending"
    db.flush()
    draft, warnings = drafting.generate_for_signal(db, sig, lead, config, version=version, test_mode=config.test_mode or sig.test_mode)
    sig.processing_status = "review_required" if warnings else "drafted"
    sig.error_code = sig.error_detail = None
    if warnings:
        sig.reasons = [*sig.reasons, *({"type": "draft_warning", "reason": w} for w in warnings)]
    audit.record(
        db,
        "discovery.draft_created",
        f"Draft #{draft.id} created for signal {sig.id} (needs your approval)",
        entity_type="ai_draft",
        entity_id=draft.id,
        details={"signal_id": sig.id, "warnings": len(warnings)},
    )
    db.commit()
    return "drafted"


# --- Scheduling ----------------------------------------------------------------------------------------------


def ensure_scheduled_run(db: Session, now: datetime) -> ScheduledJob | None:
    config = dcfg.get_config(db)
    if not config.enabled:
        return None
    active = db.scalar(
        select(ScheduledJob.id).where(
            ScheduledJob.kind == JobKind.DISCOVERY_RUN, ScheduledJob.status.in_([JobStatus.PENDING, JobStatus.RUNNING])
        )
    )
    if active:
        return None
    slot = int(now.timestamp() // (config.interval_minutes * 60))
    key = f"discovery:scheduled:{slot}"
    if db.scalar(select(ScheduledJob.id).where(ScheduledJob.idempotency_key == key)):
        return None
    job = ScheduledJob(kind=JobKind.DISCOVERY_RUN, idempotency_key=key, run_at=now, max_attempts=3, payload={"trigger": "scheduled"})
    db.add(job)
    db.commit()
    return job


def queue_manual_run(db: Session, only_sources: list[str] | None = None) -> ScheduledJob:
    from app.core.errors import Conflict

    if db.scalar(
        select(ScheduledJob.id).where(
            ScheduledJob.kind == JobKind.DISCOVERY_RUN, ScheduledJob.status.in_([JobStatus.PENDING, JobStatus.RUNNING])
        )
    ):
        raise Conflict("A discovery run is already queued or running")
    now = utcnow()
    job = ScheduledJob(
        kind=JobKind.DISCOVERY_RUN,
        idempotency_key=f"discovery:manual:{now.timestamp()}",
        run_at=now,
        max_attempts=3,
        payload={"trigger": "manual", "only_sources": only_sources},
    )
    db.add(job)
    audit.record(db, "discovery.run_queued", "Manual discovery run queued")
    return job


def next_run_at(db: Session) -> datetime | None:
    config = dcfg.get_config(db)
    if not config.enabled:
        return None
    last = db.scalar(select(func.max(DiscoveryRun.started_at)).where(DiscoveryRun.trigger == "scheduled"))
    now = utcnow()
    slot = int(now.timestamp() // (config.interval_minutes * 60))
    nxt = datetime.fromtimestamp((slot + 1) * config.interval_minutes * 60, tz=UTC)
    if last is None or last < datetime.fromtimestamp(slot * config.interval_minutes * 60, tz=UTC):
        return now
    return nxt
