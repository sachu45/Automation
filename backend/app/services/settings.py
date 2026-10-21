"""Typed application settings stored in the database."""

from __future__ import annotations

import re
from typing import Any
from zoneinfo import available_timezones

from pydantic import BaseModel, EmailStr, Field, ValidationError, field_validator, model_validator
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailed
from app.core.security import decrypt_secret, encrypt_secret
from app.db.base import utcnow
from app.models import AppSetting
from app.services import audit

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
LIVE_SENDING_PHRASE = "ENABLE LIVE SENDING"


def validate_timezone(tz: str) -> str:
    if tz not in available_timezones() and tz != "UTC":
        raise ValueError(f"Unknown timezone: {tz}")
    return tz


def validate_hhmm(v: str) -> str:
    if not _TIME_RE.match(v):
        raise ValueError("Time must be HH:MM (24h)")
    return v


class AppConfig(BaseModel):
    # Sender identity — shown to recipients, must be honest
    sender_name: str = ""
    default_sender_email: EmailStr | None = None
    reply_to_email: EmailStr | None = None
    company_name: str = ""
    company_website: str = ""
    mailing_address: str = ""  # many jurisdictions require a postal address in commercial email
    signature: str = ""

    # Scheduling defaults
    default_timezone: str = "UTC"
    default_window_start: str = "09:00"
    default_window_end: str = "17:00"
    default_send_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])

    # Safety limits
    daily_sending_limit: int = Field(default=50, ge=1, le=2000)  # global, all accounts
    min_delay_seconds: int = Field(default=120, ge=10, le=86400)
    followup_default_delay_days: int = Field(default=3, ge=1, le=60)
    max_followups: int = Field(default=3, ge=0, le=10)
    dry_run: bool = True
    emergency_stop: bool = False
    append_opt_out_footer: bool = True
    opt_out_text: str = "If you'd prefer not to hear from me again, reply \"unsubscribe\" and I won't contact you further."
    auto_suppress_unsubscribe_replies: bool = True
    stop_on_out_of_office: bool = False

    # AI (API key stored separately, encrypted)
    ai_provider: str = "env"  # env => use environment config; template | openai_compatible | gemini
    ai_base_url: str = ""
    ai_model: str = ""
    ai_classify_replies: bool = False

    # Data retention (0 = keep forever)
    retention_inbound_body_days: int = Field(default=180, ge=0)
    retention_outbound_body_days: int = Field(default=180, ge=0)
    retention_audit_days: int = Field(default=0, ge=0)

    @field_validator("default_timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        return validate_timezone(v)

    @field_validator("default_window_start", "default_window_end")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        return validate_hhmm(v)

    @field_validator("default_send_days")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        if not v or any(d < 0 or d > 6 for d in v):
            raise ValueError("send days must be 0 (Mon) .. 6 (Sun)")
        return sorted(set(v))

    @field_validator("ai_provider")
    @classmethod
    def _prov(cls, v: str) -> str:
        if v not in {"env", "template", "openai_compatible", "gemini", "mock"}:
            raise ValueError("Unknown AI provider")
        return v

    @model_validator(mode="after")
    def _window(self) -> AppConfig:
        if self.default_window_start >= self.default_window_end:
            raise ValueError("Sending window start must be before end")
        return self


SAFETY_FIELDS = {
    "dry_run",
    "emergency_stop",
    "daily_sending_limit",
    "min_delay_seconds",
    "append_opt_out_footer",
    "auto_suppress_unsubscribe_replies",
}

_KEY = "app_config"
_AI_KEY = "ai_api_key"
_AUTH_KEY = "auth_password_hash"
_SESSION_VERSION = "auth_session_version"


def get_config(db: Session) -> AppConfig:
    row = db.get(AppSetting, _KEY)
    if row is None:
        return AppConfig()
    return AppConfig.model_validate(row.value)


def _put(db: Session, key: str, value: Any) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value
        row.updated_at = utcnow()


def is_safety_relaxation(old: AppConfig, new: AppConfig) -> list[str]:
    """Return the safety-related changes that make the system *less* conservative."""
    changes: list[str] = []
    if old.dry_run and not new.dry_run:
        changes.append("dry_run")
    if old.emergency_stop and not new.emergency_stop:
        changes.append("emergency_stop")
    if new.daily_sending_limit > old.daily_sending_limit:
        changes.append("daily_sending_limit")
    if new.min_delay_seconds < old.min_delay_seconds:
        changes.append("min_delay_seconds")
    if old.append_opt_out_footer and not new.append_opt_out_footer:
        changes.append("append_opt_out_footer")
    if old.auto_suppress_unsubscribe_replies and not new.auto_suppress_unsubscribe_replies:
        changes.append("auto_suppress_unsubscribe_replies")
    return changes


def update_config(db: Session, patch: dict[str, Any], *, confirm_safety: bool = False, confirmation_phrase: str | None = None) -> AppConfig:
    old = get_config(db)
    merged = old.model_dump(mode="json")
    merged.update(patch)
    try:
        new = AppConfig.model_validate(merged)
    except ValidationError as exc:
        details = [{"loc": [str(p) for p in e["loc"]], "msg": e["msg"]} for e in exc.errors()]
        raise ValidationFailed("Invalid settings", details=details) from exc

    relaxed = is_safety_relaxation(old, new)
    if relaxed and not confirm_safety:
        raise ValidationFailed(
            "These changes relax safety defaults and require explicit confirmation",
            code="confirmation_required",
            details={"fields": relaxed},
        )
    if "dry_run" in relaxed and (confirmation_phrase or "").strip() != LIVE_SENDING_PHRASE:
        raise ValidationFailed(
            f'Enabling live sending requires typing "{LIVE_SENDING_PHRASE}"',
            code="confirmation_required",
            details={"fields": ["dry_run"], "phrase": LIVE_SENDING_PHRASE},
        )
    _put(db, _KEY, new.model_dump(mode="json"))
    changed = sorted(k for k in patch if merged.get(k) != old.model_dump(mode="json").get(k))
    if changed:
        audit.record(
            db,
            "settings.updated",
            f"Settings changed: {', '.join(changed)}",
            details={"fields": changed, "safety_relaxed": relaxed},
        )
    return new


def set_emergency_stop(db: Session, on: bool) -> AppConfig:
    cfg = get_config(db)
    cfg.emergency_stop = on
    _put(db, _KEY, cfg.model_dump(mode="json"))
    audit.record(db, "sending.emergency_stop", "Emergency stop " + ("ENGAGED" if on else "released"))
    return cfg


# --- AI API key (encrypted) ------------------------------------------------------


def set_ai_api_key(db: Session, key: str | None) -> None:
    _put(db, _AI_KEY, encrypt_secret(key) if key else None)
    audit.record(db, "settings.ai_key", "AI API key " + ("updated" if key else "removed"))


def get_ai_api_key(db: Session) -> str | None:
    row = db.get(AppSetting, _AI_KEY)
    if row is None or not row.value:
        return None
    return decrypt_secret(row.value)


# --- Admin auth --------------------------------------------------------------------


def get_password_hash(db: Session) -> str | None:
    row = db.get(AppSetting, _AUTH_KEY)
    return row.value if row else None


def set_password_hash(db: Session, value: str) -> None:
    _put(db, _AUTH_KEY, value)
    bump_session_version(db)


def get_session_version(db: Session) -> int:
    row = db.get(AppSetting, _SESSION_VERSION)
    return int(row.value) if row else 1


def bump_session_version(db: Session) -> None:
    _put(db, _SESSION_VERSION, get_session_version(db) + 1)
