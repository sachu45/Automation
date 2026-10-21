from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent


def record(
    db: Session,
    action: str,
    summary: str,
    *,
    entity_type: str | None = None,
    entity_id: int | None = None,
    details: dict[str, Any] | None = None,
    actor: str = "admin",
) -> AuditEvent:
    """Add an audit event to the current transaction (caller commits)."""
    ev = AuditEvent(
        action=action,
        summary=summary[:500],
        entity_type=entity_type,
        entity_id=entity_id,
        details=details or {},
        actor=actor,
    )
    db.add(ev)
    return ev
