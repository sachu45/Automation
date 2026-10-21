from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UTCDateTime, utcnow


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_last", "last_message_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("email_accounts.id", ondelete="SET NULL"))
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), index=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id", ondelete="SET NULL"), index=True)
    recipient_id: Mapped[int | None] = mapped_column(ForeignKey("campaign_recipients.id", ondelete="SET NULL"))
    subject: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | handled
    classification: Mapped[str | None] = mapped_column(String(24))
    classification_source: Mapped[str | None] = mapped_column(String(16))  # rule | ai | manual
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    has_inbound: Mapped[bool] = mapped_column(Boolean, default=False)
    labels: Mapped[list[str]] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    last_message_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", order_by="Message.occurred_at", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("account_id", "message_id_header"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("email_accounts.id", ondelete="SET NULL"))
    direction: Mapped[str] = mapped_column(String(10))
    message_id_header: Mapped[str] = mapped_column(String(500), index=True)
    in_reply_to: Mapped[str | None] = mapped_column(String(500))
    references: Mapped[str | None] = mapped_column(Text)
    from_email: Mapped[str | None] = mapped_column(String(320), index=True)
    from_name: Mapped[str | None] = mapped_column(String(255))
    to_email: Mapped[str | None] = mapped_column(String(1000))
    subject: Mapped[str | None] = mapped_column(String(500))
    snippet: Mapped[str | None] = mapped_column(String(300))
    body_text: Mapped[str | None] = mapped_column(Text)  # purged by retention policy
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    imap_uid: Mapped[int | None] = mapped_column(Integer)
    folder: Mapped[str | None] = mapped_column(String(255))
    is_auto_reply: Mapped[bool] = mapped_column(Boolean, default=False)
    is_bounce: Mapped[bool] = mapped_column(Boolean, default=False)
    is_simulation: Mapped[bool] = mapped_column(Boolean, default=False)
    match_method: Mapped[str | None] = mapped_column(String(24))  # header | sender | manual | none
    attempt_id: Mapped[int | None] = mapped_column(ForeignKey("send_attempts.id", ondelete="SET NULL"))

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class ReplyClassification(Base):
    __tablename__ = "reply_classifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(24))
    source: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    rationale: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
