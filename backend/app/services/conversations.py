from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Conversation, Message
from app.models.enums import MessageDirection


def get_or_create(
    db: Session,
    *,
    account_id: int | None,
    lead_id: int | None,
    campaign_id: int | None,
    recipient_id: int | None,
    subject: str | None,
) -> Conversation:
    stmt = select(Conversation).where(Conversation.account_id == account_id, Conversation.lead_id == lead_id)
    stmt = stmt.where(Conversation.campaign_id == campaign_id) if campaign_id else stmt.where(Conversation.campaign_id.is_(None))
    conv = db.scalar(stmt.order_by(Conversation.id.desc()))
    if conv is None:
        conv = Conversation(account_id=account_id, lead_id=lead_id, campaign_id=campaign_id, recipient_id=recipient_id, subject=subject)
        db.add(conv)
        db.flush()
    return conv


def record_outbound(
    db: Session,
    *,
    account_id: int | None,
    lead_id: int | None,
    campaign_id: int | None,
    recipient_id: int | None,
    attempt_id: int,
    message_id: str,
    in_reply_to: str | None,
    from_email: str,
    from_name: str,
    to_email: str,
    subject: str,
    body_text: str,
    occurred_at: datetime,
    is_simulation: bool,
) -> Message:
    conv = get_or_create(db, account_id=account_id, lead_id=lead_id, campaign_id=campaign_id, recipient_id=recipient_id, subject=subject)
    msg = Message(
        conversation_id=conv.id,
        account_id=account_id,
        direction=MessageDirection.OUTBOUND,
        message_id_header=message_id,
        in_reply_to=in_reply_to,
        references=in_reply_to,
        from_email=from_email,
        from_name=from_name,
        to_email=to_email,
        subject=subject,
        snippet=body_text[:280],
        body_text=body_text,
        occurred_at=occurred_at,
        is_simulation=is_simulation,
        attempt_id=attempt_id,
        match_method="outbound",
    )
    db.add(msg)
    conv.last_message_at = occurred_at
    return msg
