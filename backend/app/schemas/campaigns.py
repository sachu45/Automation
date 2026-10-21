from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.common import ORM
from app.services.settings import validate_hhmm, validate_timezone


class ExclusionRules(BaseModel):
    exclude_domains: list[str] = Field(default_factory=list)
    skip_if_contacted_within_days: int | None = Field(default=None, ge=1, le=3650)
    exclude_active_in_other_campaigns: bool = True

    @field_validator("exclude_domains")
    @classmethod
    def _domains(cls, v: list[str]) -> list[str]:
        return sorted({d.strip().lower().lstrip("@") for d in v if d.strip()})


class CampaignIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    email_account_id: int | None = None
    sequence_id: int | None = None
    list_ids: list[int] = Field(default_factory=list)
    segment_ids: list[int] = Field(default_factory=list)
    timezone: str = "UTC"
    window_start: str = "09:00"
    window_end: str = "17:00"
    send_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    daily_limit: int = Field(default=25, ge=1, le=2000)
    min_delay_seconds: int = Field(default=180, ge=10, le=86400)
    start_at: datetime | None = None
    end_at: datetime | None = None
    max_emails_per_recipient: int = Field(default=4, ge=1, le=10)
    exclusion_rules: ExclusionRules = Field(default_factory=ExclusionRules)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        return validate_timezone(v)

    @field_validator("window_start", "window_end")
    @classmethod
    def _t(cls, v: str) -> str:
        return validate_hhmm(v)

    @field_validator("send_days")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        if not v or any(d < 0 or d > 6 for d in v):
            raise ValueError("send_days must contain values 0 (Mon) to 6 (Sun)")
        return sorted(set(v))

    @field_validator("start_at", "end_at")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("Datetimes must include a timezone offset")
        return v

    @model_validator(mode="after")
    def _check(self) -> CampaignIn:
        if self.window_start >= self.window_end:
            raise ValueError("Window start must be before window end")
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            raise ValueError("End date must be after start date")
        return self


class CampaignOperationalUpdate(BaseModel):
    """Edits allowed while a campaign is approved/active/paused (no content/recipient changes)."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    daily_limit: int | None = Field(default=None, ge=1, le=2000)
    min_delay_seconds: int | None = Field(default=None, ge=10, le=86400)
    window_start: str | None = None
    window_end: str | None = None
    send_days: list[int] | None = None
    end_at: datetime | None = None


class CampaignOut(ORM):
    id: int
    name: str
    description: str | None
    status: str
    email_account_id: int | None
    sequence_id: int | None
    timezone: str
    window_start: str
    window_end: str
    send_days: list[int]
    daily_limit: int
    min_delay_seconds: int
    start_at: datetime | None
    end_at: datetime | None
    max_emails_per_recipient: int
    exclusion_rules: dict[str, Any]
    dry_run: bool
    approved_at: datetime | None
    approval_summary: dict[str, Any] | None
    activated_at: datetime | None
    paused_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    created_at: datetime
    updated_at: datetime
    list_ids: list[int] = []
    segment_ids: list[int] = []
    recipient_counts: dict[str, int] = {}
    step_count: int = 0


class ConfirmIn(BaseModel):
    confirm: bool = False
    confirm_name: str | None = None


class RecipientOut(BaseModel):
    id: int
    lead_id: int
    email: str
    name: str | None
    company: str | None
    status: str
    steps_sent: int
    next_step: int
    next_send_at: datetime | None
    last_sent_at: datetime | None
    stop_reason: str | None
