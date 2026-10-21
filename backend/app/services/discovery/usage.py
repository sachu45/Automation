"""Per-provider daily usage counters (requests, credits, errors) used for caps and the UI."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import ProviderUsage


def today() -> str:
    return utcnow().date().isoformat()


def _row(db: Session, provider: str, day: str | None = None) -> ProviderUsage:
    day = day or today()
    row = db.scalar(select(ProviderUsage).where(ProviderUsage.provider == provider, ProviderUsage.day == day))
    if row is None:
        try:
            with db.begin_nested():
                row = ProviderUsage(provider=provider, day=day, requests=0, credits=0, errors=0)
                db.add(row)
        except IntegrityError:  # created concurrently
            row = db.scalar(select(ProviderUsage).where(ProviderUsage.provider == provider, ProviderUsage.day == day))
    assert row is not None
    return row


def record(db: Session, provider: str, *, ok: bool, credits: int = 0, error: str | None = None, when: datetime | None = None) -> None:
    row = _row(db, provider)
    row.requests += 1
    row.credits += credits
    if ok:
        row.last_success_at = when or utcnow()
    else:
        row.errors += 1
        row.last_error = (error or "error")[:300]
        row.last_error_at = when or utcnow()


def used_today(db: Session, provider: str) -> ProviderUsage | None:
    return db.scalar(select(ProviderUsage).where(ProviderUsage.provider == provider, ProviderUsage.day == today()))


def credits_today(db: Session, provider: str) -> int:
    row = used_today(db, provider)
    return row.credits if row else 0


def requests_today(db: Session, provider: str) -> int:
    row = used_today(db, provider)
    return row.requests if row else 0


def latest(db: Session, provider: str) -> dict[str, object]:
    rows = list(db.scalars(select(ProviderUsage).where(ProviderUsage.provider == provider).order_by(ProviderUsage.day.desc()).limit(30)))
    last_ok = max((r.last_success_at for r in rows if r.last_success_at), default=None)
    last_err_row = max((r for r in rows if r.last_error_at), key=lambda r: r.last_error_at, default=None)  # type: ignore[arg-type,return-value]
    t = next((r for r in rows if r.day == today()), None)
    return {
        "requests_today": t.requests if t else 0,
        "credits_today": t.credits if t else 0,
        "errors_today": t.errors if t else 0,
        "last_success_at": last_ok,
        "last_error_at": last_err_row.last_error_at if last_err_row else None,
        "last_error": last_err_row.last_error if last_err_row else None,
    }
