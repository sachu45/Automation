from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UTCDateTime
from app.models.enums import AccountProvider, AccountStatus, Security


class EmailAccount(TimestampMixin, Base):
    __tablename__ = "email_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(160))
    email: Mapped[str] = mapped_column(String(320), unique=True)
    reply_to: Mapped[str | None] = mapped_column(String(320))
    provider: Mapped[str] = mapped_column(String(32), default=AccountProvider.SMTP_IMAP)

    smtp_host: Mapped[str | None] = mapped_column(String(255))
    smtp_port: Mapped[int | None] = mapped_column(Integer)
    smtp_security: Mapped[str] = mapped_column(String(16), default=Security.STARTTLS)
    smtp_username: Mapped[str | None] = mapped_column(String(320))

    imap_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    imap_host: Mapped[str | None] = mapped_column(String(255))
    imap_port: Mapped[int | None] = mapped_column(Integer)
    imap_security: Mapped[str] = mapped_column(String(16), default=Security.SSL)
    imap_username: Mapped[str | None] = mapped_column(String(320))
    imap_folder: Mapped[str] = mapped_column(String(255), default="INBOX")

    daily_limit: Mapped[int] = mapped_column(Integer, default=30)
    hourly_limit: Mapped[int] = mapped_column(Integer, default=10)
    min_delay_seconds: Mapped[int] = mapped_column(Integer, default=120)

    status: Mapped[str] = mapped_column(String(20), default=AccountStatus.UNVERIFIED)
    status_message: Mapped[str | None] = mapped_column(Text)
    sending_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_test_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_send_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # Cross-worker mutex for spacing sends on this account
    send_lock_until: Mapped[datetime | None] = mapped_column(UTCDateTime())

    credentials: Mapped[list[AccountCredential]] = relationship(back_populates="account", cascade="all, delete-orphan", lazy="selectin")


class AccountCredential(TimestampMixin, Base):
    """Encrypted secret material. Never serialized in API responses."""

    __tablename__ = "account_credentials"
    __table_args__ = (UniqueConstraint("account_id", "kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("email_accounts.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # smtp_password | imap_password | oauth
    encrypted_value: Mapped[str] = mapped_column(Text)
    rotated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    account: Mapped[EmailAccount] = relationship(back_populates="credentials")


class SyncState(TimestampMixin, Base):
    """IMAP incremental sync cursor per account+folder."""

    __tablename__ = "sync_states"
    __table_args__ = (UniqueConstraint("account_id", "folder"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("email_accounts.id", ondelete="CASCADE"))
    folder: Mapped[str] = mapped_column(String(255))
    uidvalidity: Mapped[int | None] = mapped_column(Integer)
    last_uid: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
