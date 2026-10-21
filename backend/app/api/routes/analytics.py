from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_auth
from app.services import analytics as svc

router = APIRouter(prefix="/api/analytics", tags=["analytics"], dependencies=[Depends(require_auth)])


@router.get("/summary")
def summary(
    start: date | None = None,
    end: date | None = None,
    campaign_id: int | None = None,
    account_id: int | None = None,
    db: Session = Depends(db_session),
) -> dict[str, Any]:
    return svc.summary(db, start, end, campaign_id, account_id)


@router.get("/timeseries")
def timeseries(
    start: date | None = None, end: date | None = None, campaign_id: int | None = None, db: Session = Depends(db_session)
) -> list[dict[str, Any]]:
    return svc.timeseries(db, start, end, campaign_id)


@router.get("/campaigns")
def campaigns(start: date | None = None, end: date | None = None, db: Session = Depends(db_session)) -> list[dict[str, Any]]:
    return svc.per_campaign(db, start, end)


@router.get("/accounts")
def accounts(start: date | None = None, end: date | None = None, db: Session = Depends(db_session)) -> list[dict[str, Any]]:
    return svc.per_account(db, start, end)
