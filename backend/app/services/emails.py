"""Email address helpers."""

from __future__ import annotations

from email_validator import EmailNotValidError, validate_email


class InvalidEmail(ValueError):
    pass


def normalize_email(value: str) -> str:
    return (value or "").strip().lower()


def email_domain(value: str) -> str:
    return normalize_email(value).rsplit("@", 1)[-1]


def validate_address(value: str) -> str:
    """Syntax validation (no network). Returns the normalized address or raises InvalidEmail."""
    raw = (value or "").strip()
    if not raw:
        raise InvalidEmail("Email address is required")
    try:
        result = validate_email(raw, check_deliverability=False)
    except EmailNotValidError as exc:
        raise InvalidEmail(str(exc)) from exc
    return result.normalized.lower()


def is_valid(value: str) -> bool:
    try:
        validate_address(value)
        return True
    except InvalidEmail:
        return False
