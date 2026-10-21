from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UTCDateTime
from app.models.enums import AttemptStatus, CampaignStatus, JobStatus, RecipientStatus


class Campaign(TimestampMixin, Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default=CampaignStatus.DRAFT, index=True)
    email_account_id: Mapped[int | None] = mapped_column(ForeignKey("email_accounts.id", ondelete="RESTRICT"))
    sequence_id: Mapped[int | None] = mapped_column(ForeignKey("sequences.id", ondelete="SET NULL"))

    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    window_start: Mapped[str] = mapped_column(String(5), default="09:00")  # HH:MM local
    window_end: Mapped[str] = mapped_column(String(5), default="17:00")
    send_days: Mapped[list[int]] = mapped_column(JSON, default=lambda: [0, 1, 2, 3, 4])  # Mon=0
    daily_limit: Mapped[int] = mapped_column(Integer, default=25)
    min_delay_seconds: Mapped[int] = mapped_column(Integer, default=180)
    start_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    end_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    max_emails_per_recipient: Mapped[int] = mapped_column(Integer, default=4)
    # Campaign-level suppression rules: {"exclude_domains": [...], "skip_if_contacted_within_days": int}
    exclusion_rules: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)  # frozen at activation
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    approval_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    activated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    paused_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    sources: Mapped[list[CampaignSource]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    steps: Mapped[list[CampaignStep]] = relationship(cascade="all, delete-orphan", order_by="CampaignStep.step_number", lazy="selectin")


class CampaignSource(Base):
    __tablename__ = "campaign_sources"
    __table_args__ = (UniqueConstraint("campaign_id", "list_id", "segment_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    list_id: Mapped[int | None] = mapped_column(ForeignKey("lists.id", ondelete="CASCADE"))
    segment_id: Mapped[int | None] = mapped_column(ForeignKey("segments.id", ondelete="CASCADE"))


class CampaignStep(Base):
    """Frozen copy of the sequence at approval time, pinned to template versions."""

    __tablename__ = "campaign_steps"
    __table_args__ = (UniqueConstraint("campaign_id", "step_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    step_number: Mapped[int] = mapped_column(Integer)
    template_version_id: Mapped[int] = mapped_column(ForeignKey("template_versions.id", ondelete="RESTRICT"))
    delay_days: Mapped[int] = mapped_column(Integer, default=0)
    delay_hours: Mapped[int] = mapped_column(Integer, default=0)
    send_as_reply: Mapped[bool] = mapped_column(Boolean, default=True)


class CampaignRecipient(TimestampMixin, Base):
    __tablename__ = "campaign_recipients"
    __table_args__ = (
        UniqueConstraint("campaign_id", "lead_id"),  # a lead can only be in a campaign once
        Index("ix_campaign_recipients_status", "campaign_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default=RecipientStatus.PENDING)
    steps_sent: Mapped[int] = mapped_column(Integer, default=0)
    next_step: Mapped[int] = mapped_column(Integer, default=1)
    next_send_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    stop_reason: Mapped[str | None] = mapped_column(String(64))
    thread_message_id: Mapped[str | None] = mapped_column(String(500))  # Message-ID of first email
    thread_subject: Mapped[str | None] = mapped_column(String(500))


class ScheduledJob(TimestampMixin, Base):
    """Durable work item. The worker claims rows atomically; nothing lives only in memory."""

    __tablename__ = "scheduled_jobs"
    __table_args__ = (Index("ix_scheduled_jobs_due", "status", "run_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.PENDING)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    run_at: Mapped[datetime] = mapped_column(UTCDateTime())
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    recipient_id: Mapped[int | None] = mapped_column(ForeignKey("campaign_recipients.id", ondelete="CASCADE"))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("email_accounts.id", ondelete="CASCADE"))
    step_number: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    locked_by: Mapped[str | None] = mapped_column(String(80))
    locked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class SendAttempt(Base):
    __tablename__ = "send_attempts"
    __table_args__ = (
        UniqueConstraint("idempotency_key", "attempt_no"),
        Index("ix_send_attempts_account_created", "account_id", "created_at"),
        Index("ix_send_attempts_campaign", "campaign_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer, default=1)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("scheduled_jobs.id", ondelete="SET NULL"))
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id", ondelete="SET NULL"))
    recipient_id: Mapped[int | None] = mapped_column(ForeignKey("campaign_recipients.id", ondelete="SET NULL"))
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), index=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("email_accounts.id", ondelete="SET NULL"))
    step_number: Mapped[int | None] = mapped_column(Integer)
    to_email: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default=AttemptStatus.QUEUED)
    is_simulation: Mapped[bool] = mapped_column(Boolean, default=False)
    smtp_code: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    message_id_header: Mapped[str | None] = mapped_column(String(500), index=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ProviderEvent(Base):
    __tablename__ = "provider_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(300), unique=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("email_accounts.id", ondelete="SET NULL"))
    attempt_id: Mapped[int | None] = mapped_column(ForeignKey("send_attempts.id", ondelete="SET NULL"), index=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"))
    event_type: Mapped[str] = mapped_column(String(32))  # hard_bounce | soft_bounce | delivered | complaint
    source: Mapped[str] = mapped_column(String(32))  # dsn_mailbox | smtp_response | webhook
    email: Mapped[str | None] = mapped_column(String(320))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
