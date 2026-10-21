from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, model_validator

from app.models.enums import AccountProvider, Security
from app.schemas.common import ORM


class AccountIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=160)
    email: EmailStr
    reply_to: EmailStr | None = None
    provider: AccountProvider = AccountProvider.SMTP_IMAP

    smtp_host: str | None = Field(default=None, max_length=255)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_security: Security = Security.STARTTLS
    smtp_username: str | None = Field(default=None, max_length=320)
    smtp_password: str | None = Field(default=None, max_length=1000)  # write-only

    imap_enabled: bool = False
    imap_host: str | None = Field(default=None, max_length=255)
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    imap_security: Security = Security.SSL
    imap_username: str | None = Field(default=None, max_length=320)
    imap_password: str | None = Field(default=None, max_length=1000)  # write-only; defaults to SMTP password
    imap_folder: str = Field(default="INBOX", max_length=255)

    daily_limit: int = Field(default=30, ge=1, le=2000)
    hourly_limit: int = Field(default=10, ge=1, le=500)
    min_delay_seconds: int = Field(default=120, ge=10, le=86400)

    @model_validator(mode="after")
    def _check(self) -> AccountIn:
        if self.provider == AccountProvider.SMTP_IMAP:
            if not self.smtp_host or not self.smtp_port:
                raise ValueError("SMTP host and port are required for SMTP/IMAP accounts")
            if self.imap_enabled and (not self.imap_host or not self.imap_port):
                raise ValueError("IMAP host and port are required when inbox sync is enabled")
        if self.hourly_limit > self.daily_limit:
            raise ValueError("Hourly limit cannot exceed daily limit")
        return self


class AccountUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    reply_to: EmailStr | None = None
    smtp_host: str | None = None
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_security: Security | None = None
    smtp_username: str | None = None
    imap_enabled: bool | None = None
    imap_host: str | None = None
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    imap_security: Security | None = None
    imap_username: str | None = None
    imap_folder: str | None = None
    daily_limit: int | None = Field(default=None, ge=1, le=2000)
    hourly_limit: int | None = Field(default=None, ge=1, le=500)
    min_delay_seconds: int | None = Field(default=None, ge=10, le=86400)


class CredentialRotate(BaseModel):
    smtp_password: str | None = Field(default=None, max_length=1000)
    imap_password: str | None = Field(default=None, max_length=1000)


class AccountOut(ORM):
    id: int
    display_name: str
    email: str
    reply_to: str | None
    provider: str
    smtp_host: str | None
    smtp_port: int | None
    smtp_security: str
    smtp_username: str | None
    imap_enabled: bool
    imap_host: str | None
    imap_port: int | None
    imap_security: str
    imap_username: str | None
    imap_folder: str
    daily_limit: int
    hourly_limit: int
    min_delay_seconds: int
    status: str
    status_message: str | None
    sending_enabled: bool
    last_test_at: datetime | None
    last_send_at: datetime | None
    last_sync_at: datetime | None
    created_at: datetime
    # Presence flags only — secret values are never returned
    has_smtp_password: bool = False
    has_imap_password: bool = False
    has_oauth_token: bool = False
    sent_last_24h: int = 0
