"""Durable job claiming and dispatch (DB-backed queue)."""

from __future__ import annotations

import logging
import os
import socket
from datetime import datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import utcnow
from app.db.session import SessionLocal
from app.models import Campaign, EmailAccount, ScheduledJob
from app.models.enums import CampaignStatus, JobKind, JobStatus
from app.services import inbox_sync, retention, sending
from app.services.settings import get_config

log = logging.getLogger("app.worker")

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
_last_retention: datetime | None = None


def claim_due_jobs(db: Session, now: datetime, limit: int, worker_id: str = WORKER_ID) -> list[int]:
    """Atomically claim due jobs. Safe with multiple workers on SQLite and PostgreSQL."""
    cfg = get_config(db)
    stmt = (
        select(ScheduledJob.id)
        .outerjoin(Campaign, Campaign.id == ScheduledJob.campaign_id)
        .where(ScheduledJob.status == JobStatus.PENDING, ScheduledJob.run_at <= now)
        .where(or_(ScheduledJob.campaign_id.is_(None), Campaign.status.in_([CampaignStatus.ACTIVE, CampaignStatus.SCHEDULED])))
        .order_by(ScheduledJob.run_at, ScheduledJob.id)
        .limit(limit)
    )
    if cfg.emergency_stop:
        stmt = stmt.where(ScheduledJob.kind.not_in([JobKind.SEND_STEP, JobKind.SEND_SINGLE]))
    claimed: list[int] = []
    for job_id in db.scalars(stmt).all():
        res = db.execute(
            update(ScheduledJob)
            .where(ScheduledJob.id == job_id, ScheduledJob.status == JobStatus.PENDING)
            .values(status=JobStatus.RUNNING, locked_by=worker_id, locked_at=now)
        )
        if res.rowcount == 1:  # type: ignore[attr-defined]
            claimed.append(job_id)
    db.commit()
    return claimed


def ensure_sync_jobs(db: Session, now: datetime) -> None:
    """One pending inbox-sync job per IMAP-enabled account, every N minutes."""
    interval = get_settings().inbox_sync_interval_minutes
    accounts = db.scalars(select(EmailAccount).where(EmailAccount.imap_enabled.is_(True)))
    for acc in accounts:
        if acc.status not in ("connected",):
            continue
        slot = int(now.timestamp() // (interval * 60))
        key = f"sync:{acc.id}:{slot}"
        if db.scalar(select(ScheduledJob.id).where(ScheduledJob.idempotency_key == key)) is None:
            pending = db.scalar(
                select(ScheduledJob.id).where(
                    ScheduledJob.kind == JobKind.SYNC_INBOX,
                    ScheduledJob.account_id == acc.id,
                    ScheduledJob.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
                )
            )
            if pending is None:
                db.add(ScheduledJob(kind=JobKind.SYNC_INBOX, idempotency_key=key, run_at=now, account_id=acc.id, max_attempts=1))
    db.commit()


def run_job(job_id: int, now: datetime | None = None) -> str:
    db = SessionLocal()
    try:
        job = db.get(ScheduledJob, job_id)
        if job is None or job.status != JobStatus.RUNNING:
            return "skipped"
        if job.kind == JobKind.SYNC_INBOX:
            try:
                result = inbox_sync.sync_account(db, job.account_id or 0)
                job.status = JobStatus.DONE
                job.last_error = None
                job.payload = {"result": result}
            except Exception as exc:  # noqa: BLE001 - record and continue
                db.rollback()
                job = db.get(ScheduledJob, job_id)
                assert job is not None
                job.status = JobStatus.FAILED
                job.last_error = f"{type(exc).__name__}: {str(exc)[:400]}"
                log.warning("Inbox sync failed for account %s: %s", job.account_id, job.last_error)
            job.finished_at = utcnow()
            job.locked_by = None
            db.commit()
            return str(job.status)
        if job.kind in (JobKind.DISCOVERY_RUN, JobKind.SIGNAL_PROCESS):
            return _run_discovery_job(db, job, now or utcnow())
        return sending.process_job(db, job, now)
    except Exception:
        db.rollback()
        log.exception("Job %s crashed", job_id)
        job = db.get(ScheduledJob, job_id)
        if job is not None and job.status == JobStatus.RUNNING:
            # Leave send jobs for stale recovery (which checks for in-flight attempts).
            job.last_error = "worker exception; see logs"
            db.commit()
        return "error"
    finally:
        db.close()


def _run_discovery_job(db: Session, job: ScheduledJob, now: datetime) -> str:
    """Discovery/drafting jobs: never send email. Temporary failures retry with backoff; permanent ones fail."""
    from app.services.discovery import pipeline
    from app.services.discovery.fetcher import FetchBlocked, FetchFailed
    from app.services.discovery.sources import SourceError

    job_id = job.id
    try:
        if job.kind == JobKind.DISCOVERY_RUN:
            run = pipeline.run_discovery(
                db, trigger=job.payload.get("trigger", "scheduled"), job_id=job.id, only_sources=job.payload.get("only_sources")
            )
            outcome, result = f"discovery_{run.status}", {"run_id": run.id, "stats": run.stats}
        else:
            p = job.payload
            outcome = pipeline.process_signal(
                db, int(p["signal_id"]), version=int(p.get("version", 1)), regenerate=bool(p.get("regenerate"))
            )
            result = {"outcome": outcome}
        job = db.get(ScheduledJob, job_id)  # type: ignore[assignment]
        assert job is not None
        job.status, job.last_error, job.payload = JobStatus.DONE, None, {**job.payload, "result": result}
        job.finished_at, job.locked_by, job.locked_at = utcnow(), None, None
        db.commit()
        return outcome
    except Exception as exc:  # noqa: BLE001 - classify, record, retry if temporary
        db.rollback()
        job = db.get(ScheduledJob, job_id)  # type: ignore[assignment]
        assert job is not None
        permanent = (
            isinstance(exc, FetchBlocked)
            or (isinstance(exc, SourceError) and exc.permanent)
            or (isinstance(exc, FetchFailed) and not exc.temporary)
        )
        job.attempts += 1
        job.last_error = f"{type(exc).__name__}: {str(exc)[:400]}"
        job.locked_by = job.locked_at = None
        if permanent or job.attempts >= job.max_attempts:
            job.status, job.finished_at = JobStatus.FAILED, utcnow()
            _mark_signal_failed(db, job, job.last_error)
        else:
            job.status = JobStatus.PENDING
            job.run_at = now + timedelta(seconds=sending.backoff_seconds(job.attempts))
        log.warning(
            "Discovery job %s failed (%s): %s", job_id, "will retry" if job.status == JobStatus.PENDING else "giving up", job.last_error
        )
        db.commit()
        return "discovery_error"


def _mark_signal_failed(db: Session, job: ScheduledJob, error: str) -> None:
    from app.models import DiscoverySignal

    if job.kind != JobKind.SIGNAL_PROCESS:
        return
    sig = db.get(DiscoverySignal, int(job.payload.get("signal_id", 0)))
    if sig is not None:
        sig.processing_status, sig.error_code, sig.error_detail = "failed", "processing_failed", error[:500]


def run_once(now: datetime | None = None, limit: int | None = None) -> list[str]:
    """One scheduler tick: recover stale jobs, create sync jobs, claim and run due jobs."""
    settings = get_settings()
    now = now or utcnow()
    db = SessionLocal()
    try:
        sending.recover_stale(db, settings.stale_job_minutes, now)
        ensure_sync_jobs(db, now)
        from app.services.discovery.pipeline import ensure_scheduled_run

        ensure_scheduled_run(db, now)
        ids = claim_due_jobs(db, now, limit or settings.worker_batch_size)
    finally:
        db.close()
    outcomes = [run_job(i, now) for i in ids]
    global _last_retention
    if _last_retention is None or now - _last_retention > timedelta(hours=1):
        _last_retention = now
        db = SessionLocal()
        try:
            retention.apply(db)
            db.commit()
        finally:
            db.close()
    return outcomes


def drain(now: datetime | None = None, max_rounds: int = 50) -> list[str]:
    """Test helper: run ticks until nothing is due at `now`."""
    out: list[str] = []
    for _ in range(max_rounds):
        res = run_once(now, limit=100)
        if not res:
            break
        out.extend(res)
    return out


def next_wakeup_hint(db: Session, now: datetime) -> timedelta:
    nxt = db.scalar(select(ScheduledJob.run_at).where(ScheduledJob.status == JobStatus.PENDING).order_by(ScheduledJob.run_at).limit(1))
    if nxt is None:
        return timedelta(seconds=get_settings().worker_poll_seconds)
    return max(timedelta(seconds=1), min(nxt - now, timedelta(seconds=get_settings().worker_poll_seconds)))
