from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORM


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=50_000)
    format: Literal["text", "html"] = "text"
    note: str | None = Field(default=None, max_length=255)


class TemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = None
    subject: str | None = Field(default=None, min_length=1, max_length=500)
    body: str | None = Field(default=None, min_length=1, max_length=50_000)
    format: Literal["text", "html"] | None = None
    note: str | None = Field(default=None, max_length=255)
    archived: bool | None = None


class TemplateVersionOut(ORM):
    id: int
    version: int
    subject: str
    body: str
    format: str
    variables: list[str]
    note: str | None
    created_at: datetime


class TemplateOut(ORM):
    id: int
    name: str
    description: str | None
    archived: bool
    created_at: datetime
    updated_at: datetime
    current: TemplateVersionOut | None = None
    version_count: int = 0


class PreviewIn(BaseModel):
    subject: str
    body: str
    format: Literal["text", "html"] = "text"
    lead_id: int | None = None
    account_id: int | None = None


class PreviewOut(BaseModel):
    subject: str | None
    text: str | None
    html: str | None
    missing: list[str]
    errors: list[str]
    variables: list[str]
    unknown_variables: list[str]
    lead_id: int | None
    sample_data: bool


class SequenceStepIn(BaseModel):
    template_id: int
    delay_days: int = Field(default=0, ge=0, le=365)
    delay_hours: int = Field(default=0, ge=0, le=23)
    send_as_reply: bool = True


class SequenceIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    steps: list[SequenceStepIn] = Field(min_length=1, max_length=10)


class SequenceStepOut(ORM):
    id: int
    position: int
    template_id: int
    template_name: str | None = None
    delay_days: int
    delay_hours: int
    send_as_reply: bool


class SequenceOut(ORM):
    id: int
    name: str
    description: str | None
    steps: list[SequenceStepOut]
    created_at: datetime
    campaign_count: int = 0


class AiPromptIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    inputs: dict[str, Any]
