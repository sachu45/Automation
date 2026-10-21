from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UTCDateTime
from app.models.enums import TemplateFormat


class Template(TimestampMixin, Base):
    __tablename__ = "templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)

    versions: Mapped[list[TemplateVersion]] = relationship(
        back_populates="template", order_by="TemplateVersion.version", cascade="all, delete-orphan"
    )


class TemplateVersion(TimestampMixin, Base):
    """Immutable once created. Editing a template creates a new version."""

    __tablename__ = "template_versions"
    __table_args__ = (UniqueConstraint("template_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("templates.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    subject: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    format: Mapped[str] = mapped_column(String(8), default=TemplateFormat.TEXT)
    variables: Mapped[list[str]] = mapped_column(JSON, default=list)
    note: Mapped[str | None] = mapped_column(String(255))

    template: Mapped[Template] = relationship(back_populates="versions")


class Sequence(TimestampMixin, Base):
    """Reusable step blueprint. Campaigns snapshot it into CampaignStep at approval."""

    __tablename__ = "sequences"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    description: Mapped[str | None] = mapped_column(Text)

    steps: Mapped[list[SequenceStep]] = relationship(
        back_populates="sequence", order_by="SequenceStep.position", cascade="all, delete-orphan", lazy="selectin"
    )


class SequenceStep(TimestampMixin, Base):
    __tablename__ = "sequence_steps"
    __table_args__ = (UniqueConstraint("sequence_id", "position"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sequence_id: Mapped[int] = mapped_column(ForeignKey("sequences.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)  # 1 = initial email
    template_id: Mapped[int] = mapped_column(ForeignKey("templates.id", ondelete="RESTRICT"))
    delay_days: Mapped[int] = mapped_column(Integer, default=0)  # wait after previous step
    delay_hours: Mapped[int] = mapped_column(Integer, default=0)
    send_as_reply: Mapped[bool] = mapped_column(Boolean, default=True)  # thread follow-ups

    sequence: Mapped[Sequence] = relationship(back_populates="steps")
    template: Mapped[Template] = relationship(lazy="joined")


class AiPrompt(TimestampMixin, Base):
    __tablename__ = "ai_prompts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AiDraft(TimestampMixin, Base):
    __tablename__ = "ai_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("ai_drafts.id", ondelete="SET NULL"))
    label: Mapped[str | None] = mapped_column(String(120))
    provider: Mapped[str] = mapped_column(String(40))
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    subject: Mapped[str] = mapped_column(String(500), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | approved
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # Provenance for drafts created by lead discovery (null for drafts written in the AI workspace)
    origin: Mapped[str] = mapped_column(String(16), default="workspace", server_default="workspace")  # workspace | discovery
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("discovery_signals.id", ondelete="SET NULL"), index=True)
    generation_key: Mapped[str | None] = mapped_column(String(120), unique=True)  # idempotency for automatic drafts
    content_hash: Mapped[str | None] = mapped_column(String(64))
