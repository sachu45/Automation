"""Signed, encrypted opt-out tokens. The URL never contains a readable email address."""

from __future__ import annotations

import json

from cryptography.fernet import InvalidToken
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import _fernet
from app.models.enums import SuppressionReason
from app.services import audit, suppression


def make_token(email: str, campaign_id: int | None) -> str:
    payload = json.dumps({"e": email.lower(), "c": campaign_id}, separators=(",", ":"))
    return _fernet().encrypt(payload.encode()).decode()


def read_token(token: str) -> dict | None:  # type: ignore[type-arg]
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except (InvalidToken, ValueError):
        return None


def unsubscribe_url(email: str, campaign_id: int | None) -> str | None:
    base = get_settings().public_base_url.rstrip("/")
    if not base:
        return None
    return f"{base}/u/{make_token(email, campaign_id)}"


def list_unsubscribe_headers(url: str | None, reply_address: str) -> dict[str, str]:
    parts = []
    if url:
        parts.append(f"<{url}>")
    parts.append(f"<mailto:{reply_address}?subject=unsubscribe>")
    headers = {"List-Unsubscribe": ", ".join(parts)}
    if url and url.startswith("https://"):
        headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"  # RFC 8058
    return headers


def process(db: Session, token: str, source: str) -> bool:
    data = read_token(token)
    if not data or not data.get("e"):
        return False
    email = data["e"]
    existing = suppression.find_active(db, email)
    if existing is None or existing.email != email:
        suppression.suppress(
            db,
            email=email,
            reason=SuppressionReason.UNSUBSCRIBED,
            source=source,
            notes=f"campaign {data.get('c')}" if data.get("c") else None,
        )
    else:
        audit.record(db, "suppression.unsubscribe_repeat", "Repeated unsubscribe request", entity_type="suppression", entity_id=existing.id)
    return True
