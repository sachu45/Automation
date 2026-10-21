from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.models.enums import LeadStatus
from app.schemas.common import ORM
from app.services.emails import InvalidEmail, validate_address


def _clean_url(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    if not v:
        return None
    if not v.lower().startswith(("http://", "https://")):
        v = "https://" + v
    if len(v) > 500:
        raise ValueError("URL too long")
    return v


class LeadBase(BaseModel):
    first_name: str | None = Field(default=None, max_length=120)
    last_name: str | None = Field(default=None, max_length=120)
    full_name: str | None = Field(default=None, max_length=255)
    job_title: str | None = Field(default=None, max_length=255)
    company: str | None = Field(default=None, max_length=255)
    company_website: str | None = None
    linkedin_url: str | None = None
    industry: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, max_length=80)
    timezone: str | None = Field(default=None, max_length=64)
    source: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=5000)
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)

    @field_validator("company_website", "linkedin_url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        return _clean_url(v)

    @field_validator("linkedin_url")
    @classmethod
    def _linkedin(cls, v: str | None) -> str | None:
        if v and "linkedin.com/" not in v.lower():
            raise ValueError("LinkedIn URL must be a linkedin.com link")
        return v

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        if not v:
            return None
        from app.services.settings import validate_timezone

        return validate_timezone(v)

    @field_validator("custom_fields")
    @classmethod
    def _custom(cls, v: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, val in v.items():
            key = str(k).strip().lower().replace(" ", "_")
            if not key or len(key) > 60 or not key.replace("_", "").isalnum():
                raise ValueError(f"Invalid custom field name: {k}")
            if val is not None and not isinstance(val, (str, int, float, bool)):
                raise ValueError("Custom field values must be scalars")
            out[key] = val
        if len(out) > 50:
            raise ValueError("Too many custom fields (max 50)")
        return out

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        cleaned = sorted({t.strip().lower()[:80] for t in v if t and t.strip()})
        return cleaned


class LeadCreate(LeadBase):
    email: str
    status: LeadStatus = LeadStatus.NEW

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        try:
            return validate_address(v)
        except InvalidEmail as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("status")
    @classmethod
    def _status(cls, v: LeadStatus) -> LeadStatus:
        if v in {LeadStatus.UNSUBSCRIBED, LeadStatus.BOUNCED}:
            raise ValueError("This status is set by the system via the suppression list")
        return v


class LeadUpdate(LeadBase):
    email: str | None = None
    status: LeadStatus | None = None
    # Explicit null-able fields: only keys present in the request are applied.

    @field_validator("email")
    @classmethod
    def _email(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            return validate_address(v)
        except InvalidEmail as exc:
            raise ValueError(str(exc)) from exc


class CampaignRef(BaseModel):
    id: int
    name: str
    status: str
    recipient_status: str


class LeadOut(ORM):
    id: int
    email: str
    first_name: str | None
    last_name: str | None
    full_name: str | None
    job_title: str | None
    company: str | None = None
    company_id: int | None
    company_website: str | None = None
    linkedin_url: str | None
    industry: str | None
    country: str | None
    timezone: str | None
    source: str | None
    notes: str | None
    custom_fields: dict[str, Any]
    tags: list[str] = []
    status: str
    suppressed: bool = False
    campaigns: list[CampaignRef] = []
    created_at: datetime
    updated_at: datetime
    last_contacted_at: datetime | None
    last_replied_at: datetime | None


class BulkAction(BaseModel):
    lead_ids: list[int] = Field(min_length=1, max_length=5000)
    action: Literal["add_tag", "remove_tag", "add_to_list", "remove_from_list", "set_status", "delete", "do_not_contact"]
    value: str | None = None
    confirm: bool = False


class MergeRequest(BaseModel):
    primary_id: int
    duplicate_ids: list[int] = Field(min_length=1)
    confirm: bool = False


class SegmentRule(BaseModel):
    field: str
    op: str
    value: Any = None


class SegmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    match: Literal["all", "any"] = "all"
    rules: list[SegmentRule] = Field(default_factory=list)


class SegmentOut(ORM):
    id: int
    name: str
    description: str | None
    match: str
    rules: list[dict[str, Any]]
    lead_count: int = 0
    created_at: datetime


class ListIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None


class ListOut(ORM):
    id: int
    name: str
    description: str | None
    lead_count: int = 0
    created_at: datetime


class ListMembersChange(BaseModel):
    lead_ids: list[int] = Field(min_length=1, max_length=5000)
