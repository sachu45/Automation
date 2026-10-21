from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, ForeignKey, Index, Integer, String, Table, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UTCDateTime, utcnow
from app.models.enums import LeadStatus

lead_tags = Table(
    "lead_tags",
    Base.metadata,
    Column("lead_id", ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Company(TimestampMixin, Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    name_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)  # lowercased name
    website: Mapped[str | None] = mapped_column(String(500))
    domain: Mapped[str | None] = mapped_column(String(255), index=True)
    notes: Mapped[str | None] = mapped_column(Text)

    leads: Mapped[list[Lead]] = relationship(back_populates="company")


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)


class Lead(TimestampMixin, Base):
    __tablename__ = "leads"
    __table_args__ = (
        Index("ix_leads_status", "status"),
        Index("ix_leads_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)  # normalized lowercase
    first_name: Mapped[str | None] = mapped_column(String(120))
    last_name: Mapped[str | None] = mapped_column(String(120))
    full_name: Mapped[str | None] = mapped_column(String(255))
    job_title: Mapped[str | None] = mapped_column(String(255))
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"), index=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    industry: Mapped[str | None] = mapped_column(String(120), index=True)
    country: Mapped[str | None] = mapped_column(String(80))
    timezone: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str | None] = mapped_column(String(120), index=True)
    notes: Mapped[str | None] = mapped_column(Text)  # personalization notes supplied by the operator
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default=LeadStatus.NEW)
    last_contacted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_replied_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    company: Mapped[Company | None] = relationship(back_populates="leads", lazy="joined")
    tags: Mapped[list[Tag]] = relationship(secondary=lead_tags, lazy="selectin", order_by=Tag.name)


class LeadList(TimestampMixin, Base):
    __tablename__ = "lists"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    description: Mapped[str | None] = mapped_column(Text)


class ListMembership(Base):
    __tablename__ = "list_memberships"
    __table_args__ = (UniqueConstraint("list_id", "lead_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    list_id: Mapped[int] = mapped_column(ForeignKey("lists.id", ondelete="CASCADE"), index=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    added_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Segment(TimestampMixin, Base):
    """Rule-based segment. `rules` is compiled into SQL filters at query time."""

    __tablename__ = "segments"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    match: Mapped[str] = mapped_column(String(8), default="all")  # all | any
    rules: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


class CsvImport(TimestampMixin, Base):
    __tablename__ = "csv_imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="uploaded")  # uploaded|previewed|committed|discarded
    headers: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Raw rows are kept only until the import is committed or discarded, then purged.
    rows: Mapped[list[list[str]] | None] = mapped_column(JSON)
    mapping: Mapped[dict[str, str] | None] = mapped_column(JSON)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    list_id: Mapped[int | None] = mapped_column(ForeignKey("lists.id", ondelete="SET NULL"))
    source_label: Mapped[str | None] = mapped_column(String(120))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
