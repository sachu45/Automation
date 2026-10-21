from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.api.deps import db_session
from app.db.base import utcnow
from app.models import Campaign, ScheduledJob
from app.models.enums import CampaignStatus, JobStatus

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("")
def health(db: Session = Depends(db_session)) -> dict:  # type: ignore[type-arg]
    db.execute(text("SELECT 1"))
    # Worker liveness heuristic: claimable jobs that are long overdue suggest the worker is not running.
    oldest_due = db.scalar(
        select(ScheduledJob.run_at)
        .outerjoin(Campaign, Campaign.id == ScheduledJob.campaign_id)
        .where(ScheduledJob.status == JobStatus.PENDING, ScheduledJob.run_at <= utcnow())
        .where(or_(ScheduledJob.campaign_id.is_(None), Campaign.status.in_([CampaignStatus.ACTIVE, CampaignStatus.SCHEDULED])))
        .order_by(ScheduledJob.run_at)
        .limit(1)
    )
    lag = (utcnow() - oldest_due).total_seconds() if oldest_due else 0
    return {"status": "ok", "database": "ok", "oldest_due_job_seconds": int(lag), "worker_suspected_down": lag > 600}
