"""Data-retention enforcement (runs from the worker hourly)."""

from __future__ import annotations

import time
from datetime import timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import AiRequest, AppSetting, AuditEvent, CsvImport, Message
from app.models.enums import MessageDirection
from app.services.settings import get_config


def apply(db: Session) -> dict[str, int]:
    cfg = get_config(db)
    now = utcnow()
    out: dict[str, int] = {}
    if cfg.retention_inbound_body_days:
        r = db.execute(
            update(Message)
            .where(
                Message.direction == MessageDirection.INBOUND,
                Message.occurred_at < now - timedelta(days=cfg.retention_inbound_body_days),
                Message.body_text.is_not(None),
            )
            .values(body_text=None)
        )
        out["inbound_bodies_purged"] = r.rowcount  # type: ignore[attr-defined]
    if cfg.retention_outbound_body_days:
        r = db.execute(
            update(Message)
            .where(
                Message.direction == MessageDirection.OUTBOUND,
                Message.occurred_at < now - timedelta(days=cfg.retention_outbound_body_days),
                Message.body_text.is_not(None),
            )
            .values(body_text=None)
        )
        out["outbound_bodies_purged"] = r.rowcount  # type: ignore[attr-defined]
    if cfg.retention_audit_days:
        r = db.execute(delete(AuditEvent).where(AuditEvent.created_at < now - timedelta(days=cfg.retention_audit_days)))
        out["audit_deleted"] = r.rowcount  # type: ignore[attr-defined]
    # Abandoned CSV uploads keep raw rows only for 24h
    r = db.execute(
        update(CsvImport)
        .where(CsvImport.status.in_(["uploaded", "previewed"]), CsvImport.created_at < now - timedelta(days=1))
        .values(rows=None, status="discarded")
    )
    out["stale_imports"] = r.rowcount  # type: ignore[attr-defined]
    db.execute(delete(AiRequest).where(AiRequest.created_at < now - timedelta(days=90)))
    # Discovery: third-party evidence text is kept only as long as configured; page cache for 30 days
    from app.models import DiscoverySignal, FetchedPage
    from app.services.discovery.config import get_config as get_discovery_config

    dcfg = get_discovery_config(db)
    if dcfg.evidence_retention_days:
        r = db.execute(
            update(DiscoverySignal)
            .where(
                DiscoverySignal.discovered_at < now - timedelta(days=dcfg.evidence_retention_days),
                DiscoverySignal.processing_status.in_(["rejected", "archived", "failed"]),
                DiscoverySignal.excerpt.is_not(None),
            )
            .values(excerpt=None, evidence=[])
        )
        out["discovery_evidence_purged"] = r.rowcount  # type: ignore[attr-defined]
    r = db.execute(delete(FetchedPage).where(FetchedPage.fetched_at < now - timedelta(days=30)))
    out["page_cache_deleted"] = r.rowcount  # type: ignore[attr-defined]
    for row in db.scalars(select(AppSetting).where(AppSetting.key.like("oauth_state:%"))):
        if time.time() - float((row.value or {}).get("ts", 0)) > 3600:
            db.delete(row)
    return out
