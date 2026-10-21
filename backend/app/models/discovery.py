"""Lead-discovery persistence: signals (with evidence), runs, provider usage, fetched-page cache."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UTCDateTime, utcnow


class DiscoveryRun(Base):
    __tablename__ = "discovery_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    trigger: Mapped[str] = mapped_column(String(16))  # scheduled | manual
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | completed | partial | failed
    test_mode: Mapped[bool] = mapped_column(default=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("scheduled_jobs.id", ondelete="SET NULL"))


class DiscoverySignal(TimestampMixin, Base):
    """One business signal from one source. Kept separate from leads: a company can have many signals."""

    __tablename__ = "discovery_signals"
    __table_args__ = (
        UniqueConstraint("dedupe_key"),  # concurrency-safe dedupe across runs and workers
        Index("ix_discovery_signals_status", "processing_status", "qualification_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(80))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("discovery_runs.id", ondelete="SET NULL"), index=True)
    source_provider: Mapped[str] = mapped_column(String(32), index=True)  # rss | website | apollo | google_cse | manual
    source_type: Mapped[str] = mapped_column(
        String(32)
    )  # feed_item | web_page | people_search | search_result | user_submitted | linkedin_post
    source_url: Mapped[str | None] = mapped_column(String(1000))
    source_external_id: Mapped[str | None] = mapped_column(String(255))
    category: Mapped[str | None] = mapped_column(String(40))
    title: Mapped[str | None] = mapped_column(String(500))
    company_name: Mapped[str | None] = mapped_column(String(255))
    company_domain: Mapped[str | None] = mapped_column(String(255), index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"))
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), index=True)
    contact_name: Mapped[str | None] = mapped_column(String(255))
    contact_title: Mapped[str | None] = mapped_column(String(255))
    contact_external_id: Mapped[str | None] = mapped_column(String(255))
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    discovered_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    excerpt: Mapped[str | None] = mapped_column(Text)
    # [{kind, url, text, retrieved_at, provider}] — untrusted third-party text, purged by retention
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    relevance_score: Mapped[float | None] = mapped_column(Float)
    score_components: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    qualification_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | qualified | rejected | review_required
    processing_status: Mapped[str] = mapped_column(String(20), default="discovered")
    test_mode: Mapped[bool] = mapped_column(default=False)
    error_code: Mapped[str | None] = mapped_column(String(40))
    error_detail: Mapped[str | None] = mapped_column(String(500))
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ProviderUsage(Base):
    """Daily request/credit counters per external provider (UTC day)."""

    __tablename__ = "provider_usage"
    __table_args__ = (UniqueConstraint("provider", "day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    day: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD
    requests: Mapped[int] = mapped_column(Integer, default=0)
    credits: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(300))
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class FetchedPage(Base):
    """Cache of public pages fetched by the constrained fetcher (also used for robots.txt)."""

    __tablename__ = "fetched_pages"
    __table_args__ = (UniqueConstraint("url_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    url_hash: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(String(1000))
    final_url: Mapped[str | None] = mapped_column(String(1000))
    status_code: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(100))
    title: Mapped[str | None] = mapped_column(String(500))
    text: Mapped[str | None] = mapped_column(Text)  # extracted readable text, bounded
    links: Mapped[list[str]] = mapped_column(JSON, default=list)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    etag: Mapped[str | None] = mapped_column(String(255))
    last_modified: Mapped[str | None] = mapped_column(String(100))
    error: Mapped[str | None] = mapped_column(String(300))
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
