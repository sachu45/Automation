"""Incremental inbox synchronization, reply matching, and follow-up stopping.

Only messages that can be tied to our outreach (by headers or by a known lead's
address) are imported; unrelated personal mail is ignored and never stored.
"""

from __future__ import annotations

import email
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from email import policy
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import (
    Campaign,
    CampaignRecipient,
    Conversation,
    EmailAccount,
    Lead,
    Message,
    ProviderEvent,
    ReplyClassification,
    ScheduledJob,
    SendAttempt,
    SyncState,
)
from app.models.enums import (
    NON_HUMAN_LABELS,
    AttemptStatus,
    JobStatus,
    LeadStatus,
    MessageDirection,
    RecipientStatus,
    ReplyLabel,
    SuppressionReason,
)
from app.services import accounts as account_svc
from app.services import audit, bounces, classification, suppression
from app.services.settings import get_config

log = logging.getLogger("app.inbox")
MAX_BODY_CHARS = 20_000


def _body_text(msg: EmailMessage) -> str:
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
    except Exception:  # noqa: BLE001
        part = None
    if part is None:
        return ""
    try:
        content = part.get_content()
    except Exception:  # noqa: BLE001
        return ""
    if part.get_content_type() == "text/html":
        from app.services.template_engine import html_to_text

        content = html_to_text(content)
    return str(content)[:MAX_BODY_CHARS]


def _msg_date(msg: EmailMessage) -> datetime:
    try:
        dt = parsedate_to_datetime(str(msg.get("Date") or ""))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        # Sender clocks can be wrong; never record a message as arriving in the future.
        return min(dt.astimezone(UTC), utcnow())
    except Exception:  # noqa: BLE001
        return utcnow()


def _ids(value: str | None) -> list[str]:
    if not value:
        return []
    return [f"<{p.strip('<> ')}>" for p in value.replace(",", " ").split() if p.strip("<> ")]


def stop_recipient_for_reply(
    db: Session, recipient: CampaignRecipient, reason: str, status: RecipientStatus = RecipientStatus.REPLIED
) -> None:
    if recipient.status in (RecipientStatus.ACTIVE, RecipientStatus.PENDING, RecipientStatus.NEEDS_REVIEW):
        recipient.status = status
        recipient.stop_reason = reason
        recipient.next_send_at = None
        db.execute(
            update(ScheduledJob)
            .where(ScheduledJob.recipient_id == recipient.id, ScheduledJob.status == JobStatus.PENDING)
            .values(status=JobStatus.CANCELLED, last_error=f"stopped: {reason}", finished_at=utcnow())
        )


def stop_all_for_lead(db: Session, lead_id: int, reason: str) -> int:
    recs = list(
        db.scalars(
            select(CampaignRecipient).where(
                CampaignRecipient.lead_id == lead_id,
                CampaignRecipient.status.in_([RecipientStatus.ACTIVE, RecipientStatus.PENDING]),
            )
        )
    )
    for r in recs:
        stop_recipient_for_reply(db, r, reason, RecipientStatus.STOPPED if reason == "manual" else RecipientStatus.REPLIED)
    return len(recs)


def ingest_message(db: Session, account: EmailAccount, raw: bytes, *, uid: int | None = None, folder: str | None = None) -> dict[str, Any]:
    """Process one raw RFC 5322 message. Idempotent (dedup by Message-ID per account)."""
    msg: EmailMessage = email.message_from_bytes(raw, policy=policy.default)  # type: ignore[assignment]
    message_id = (msg.get("Message-ID") or "").strip()
    if not message_id:
        message_id = "<synthetic-" + hashlib.sha256(raw).hexdigest()[:32] + "@local>"
    if db.scalar(select(Message.id).where(Message.account_id == account.id, Message.message_id_header == message_id)):
        return {"result": "duplicate"}

    from_name, from_addr = parseaddr(msg.get("From") or "")
    from_addr = from_addr.lower()
    if from_addr == account.email.lower():
        return {"result": "own_message"}

    # --- Bounces -----------------------------------------------------------------
    bounce = bounces.parse(msg)
    if bounce is not None:
        return _handle_bounce(db, account, msg, bounce, message_id)

    # --- Match to our outreach ------------------------------------------------------
    referenced = _ids(msg.get("In-Reply-To")) + _ids(msg.get("References"))
    conv: Conversation | None = None
    method = "none"
    if referenced:
        outbound = db.scalar(
            select(Message).where(
                Message.direction == MessageDirection.OUTBOUND,
                Message.account_id == account.id,
                Message.message_id_header.in_(referenced),
            )
        )
        if outbound is not None:
            conv = db.get(Conversation, outbound.conversation_id)
            method = "header"
    if conv is None:
        lead = db.scalar(select(Lead).where(Lead.email == from_addr))
        if lead is not None:
            conv = db.scalar(
                select(Conversation)
                .where(Conversation.lead_id == lead.id, Conversation.account_id == account.id)
                .order_by(Conversation.last_message_at.desc())
            )
            method = "sender"
            if conv is None:
                conv = Conversation(account_id=account.id, lead_id=lead.id, subject=msg.get("Subject"))
                db.add(conv)
                db.flush()
    if conv is None:
        return {"result": "unrelated"}  # not stored

    body = _body_text(msg)
    auto, auto_label = classification.is_automated(msg)
    if auto and auto_label:
        result_cls = classification.Classification(auto_label, 0.8, "Automated-message headers/subject")
    else:
        result_cls = classification.classify_text(msg.get("Subject") or "", body)
    cfg = get_config(db)
    if cfg.ai_classify_replies and not auto:
        from app.services import ai as ai_svc

        try:
            ai_cls = ai_svc.classify_reply(db, msg.get("Subject") or "", classification.strip_quoted(body))
            if ai_cls is not None:
                result_cls = ai_cls
        except Exception as exc:  # noqa: BLE001 - AI is optional; rules remain
            log.warning("AI classification failed: %s", type(exc).__name__)

    to_addrs = ", ".join(a for _, a in getaddresses(msg.get_all("To", []) or []))
    occurred = _msg_date(msg)
    m = Message(
        conversation_id=conv.id,
        account_id=account.id,
        direction=MessageDirection.INBOUND,
        message_id_header=message_id,
        in_reply_to=(msg.get("In-Reply-To") or None),
        references=(msg.get("References") or None),
        from_email=from_addr,
        from_name=from_name or None,
        to_email=to_addrs[:1000],
        subject=(msg.get("Subject") or "")[:500],
        snippet=classification.strip_quoted(body)[:280],
        body_text=body,
        occurred_at=occurred,
        imap_uid=uid,
        folder=folder,
        is_auto_reply=auto or result_cls.label in NON_HUMAN_LABELS,
        match_method=method,
    )
    db.add(m)
    db.flush()
    db.add(
        ReplyClassification(
            message_id=m.id,
            label=result_cls.label,
            source="ai" if "AI" in result_cls.rationale else "rule",
            confidence=result_cls.confidence,
            rationale=result_cls.rationale[:500],
        )
    )
    conv.last_message_at = max(conv.last_message_at, occurred) if conv.last_message_at else occurred
    conv.has_inbound = True
    conv.status = "open"
    conv.needs_review = True
    if conv.classification_source != "manual":
        conv.classification = result_cls.label
        conv.classification_source = "rule"
    _apply_reply_effects(db, conv, m, result_cls.label, cfg.stop_on_out_of_office, cfg.auto_suppress_unsubscribe_replies)
    return {"result": "imported", "label": str(result_cls.label), "match": method}


def _apply_reply_effects(db: Session, conv: Conversation, m: Message, label: str, stop_on_ooo: bool, auto_suppress: bool) -> None:
    human = label not in NON_HUMAN_LABELS
    lead = db.get(Lead, conv.lead_id) if conv.lead_id else None
    if lead is not None and human:
        lead.last_replied_at = m.occurred_at
        if lead.status in (LeadStatus.NEW, LeadStatus.READY, LeadStatus.CONTACTED):
            lead.status = LeadStatus.REPLIED
    if human or (label == ReplyLabel.OUT_OF_OFFICE and stop_on_ooo):
        if lead is not None:
            # A genuine reply stops follow-ups in every campaign for this lead.
            recs = db.scalars(
                select(CampaignRecipient).where(
                    CampaignRecipient.lead_id == lead.id,
                    CampaignRecipient.status.in_([RecipientStatus.ACTIVE, RecipientStatus.PENDING, RecipientStatus.NEEDS_REVIEW]),
                )
            )
            for rec in recs:
                stop_recipient_for_reply(db, rec, "reply_received" if human else "out_of_office")
    if label == ReplyLabel.UNSUBSCRIBE and auto_suppress and lead is not None:
        suppression.suppress(
            db, email=lead.email, reason=SuppressionReason.UNSUBSCRIBED, source="reply", notes="Unsubscribe request detected in reply"
        )


def _handle_bounce(db: Session, account: EmailAccount, msg: EmailMessage, info: bounces.BounceInfo, message_id: str) -> dict[str, Any]:
    attempt: SendAttempt | None = None
    if info.original_message_id:
        attempt = db.scalar(select(SendAttempt).where(SendAttempt.message_id_header == info.original_message_id.strip()))
    recipient_email = info.recipient or (attempt.to_email if attempt else None)
    lead = db.scalar(select(Lead).where(Lead.email == recipient_email)) if recipient_email else None
    event_type = {"hard": "hard_bounce", "soft": "soft_bounce"}.get(info.kind, "bounce_uncertain")
    dedupe = f"dsn:{account.id}:{message_id}"
    if db.scalar(select(ProviderEvent.id).where(ProviderEvent.dedupe_key == dedupe)):
        return {"result": "duplicate"}
    db.add(
        ProviderEvent(
            dedupe_key=dedupe,
            account_id=account.id,
            attempt_id=attempt.id if attempt else None,
            lead_id=lead.id if lead else None,
            event_type=event_type,
            source="dsn_mailbox",
            email=recipient_email,
            detail={"status": info.status, "action": info.action, "diagnostic": (info.diagnostic or "")[:300]},
            occurred_at=_msg_date(msg),
        )
    )
    # Confirmed hard bounce: only when the DSN is structured AND we can tie it to our send
    # or a known lead address.
    if info.is_hard and recipient_email and (attempt is not None or lead is not None):
        if attempt is not None and not attempt.is_simulation:
            attempt.status = AttemptStatus.BOUNCED
        suppression.suppress(db, email=recipient_email, reason=SuppressionReason.HARD_BOUNCE, source="dsn_mailbox", notes=info.status)
        return {"result": "hard_bounce", "email": recipient_email}
    return {"result": event_type}


def is_candidate(db: Session, account: EmailAccount, header_bytes: bytes) -> bool:
    """Cheap header-only test: could this message be a reply/bounce related to our outreach?

    Only candidates are downloaded in full, so unrelated personal mail is never fetched or stored.
    """
    hdr: EmailMessage = email.message_from_bytes(header_bytes, policy=policy.default)  # type: ignore[assignment]
    sender = parseaddr(hdr.get("From") or "")[1].lower()
    if sender == account.email.lower():
        return False
    if "mailer-daemon" in sender or "postmaster" in sender or hdr.get_content_type() == "multipart/report":
        return True
    referenced = _ids(hdr.get("In-Reply-To")) + _ids(hdr.get("References"))
    if referenced and db.scalar(
        select(Message.id).where(
            Message.direction == MessageDirection.OUTBOUND,
            Message.account_id == account.id,
            Message.message_id_header.in_(referenced),
        )
    ):
        return True
    return bool(sender) and db.scalar(select(Lead.id).where(Lead.email == sender)) is not None


def sync_account(db: Session, account_id: int, limit: int = 200) -> dict[str, Any]:
    account = account_svc.get_account(db, account_id)
    if not account.imap_enabled:
        return {"skipped": "imap disabled"}
    _, reader = account_svc.build_providers(db, account)
    if reader is None:
        return {"skipped": "no reader"}
    folder = account.imap_folder or "INBOX"
    state = db.scalar(select(SyncState).where(SyncState.account_id == account.id, SyncState.folder == folder))
    if state is None:
        state = SyncState(account_id=account.id, folder=folder, last_uid=0)
        db.add(state)
        db.flush()
    since = (account.created_at - timedelta(days=1)).date()  # never scan mailbox history from before the account was added
    try:
        fetched = reader.fetch_since(
            folder, state.uidvalidity, state.last_uid, limit=limit, since=since, want=lambda h: is_candidate(db, account, h)
        )
    except Exception as exc:
        state.last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
        db.commit()
        raise
    if state.uidvalidity is not None and fetched.uidvalidity != state.uidvalidity:
        state.last_uid = 0
    counts: dict[str, int] = {}
    for fm in fetched.messages:
        try:
            with db.begin_nested():
                res = ingest_message(db, account, fm.raw, uid=fm.uid, folder=folder)
        except Exception:  # noqa: BLE001 - one bad message must not block the cursor
            log.exception("Failed to ingest message uid=%s", fm.uid)
            res = {"result": "error"}
        counts[res["result"]] = counts.get(res["result"], 0) + 1
        state.last_uid = max(state.last_uid, fm.uid)
    state.last_uid = max(state.last_uid, fetched.max_uid)
    state.uidvalidity = fetched.uidvalidity
    state.last_error = None
    account.last_sync_at = utcnow()
    db.commit()
    return {"fetched": len(fetched.messages), **counts}


# --- Manual review operations ----------------------------------------------------------------


def set_classification(db: Session, conv_id: int, label: str) -> Conversation:
    from app.core.errors import NotFound, ValidationFailed

    conv = db.get(Conversation, conv_id)
    if conv is None:
        raise NotFound("Conversation not found")
    try:
        lbl = ReplyLabel(label)
    except ValueError as exc:
        raise ValidationFailed("Unknown label") from exc
    previous = conv.classification
    conv.classification = lbl
    conv.classification_source = "manual"
    conv.needs_review = False
    last_inbound = db.scalar(
        select(Message)
        .where(Message.conversation_id == conv.id, Message.direction == MessageDirection.INBOUND)
        .order_by(Message.occurred_at.desc())
    )
    if last_inbound is not None:
        db.add(ReplyClassification(message_id=last_inbound.id, label=lbl, source="manual", confidence=1.0, rationale="Set by operator"))
        last_inbound.is_auto_reply = lbl in NON_HUMAN_LABELS
    lead = db.get(Lead, conv.lead_id) if conv.lead_id else None
    if lead is not None:
        if lbl == ReplyLabel.INTERESTED:
            lead.status = LeadStatus.INTERESTED
        elif lbl == ReplyLabel.NOT_INTERESTED:
            lead.status = LeadStatus.NOT_INTERESTED
        if lbl not in NON_HUMAN_LABELS:
            stop_all_for_lead(db, lead.id, "reply_received")
            if last_inbound is not None:
                lead.last_replied_at = lead.last_replied_at or last_inbound.occurred_at
        if lbl == ReplyLabel.UNSUBSCRIBE:
            suppression.suppress(
                db, email=lead.email, reason=SuppressionReason.UNSUBSCRIBED, source="reply", notes="Classified as unsubscribe by operator"
            )
    audit.record(
        db, "inbox.classified", f"Conversation {conv.id} classified {previous} -> {lbl}", entity_type="conversation", entity_id=conv.id
    )
    return conv


def reassign(db: Session, conv_id: int, lead_id: int | None, campaign_id: int | None) -> Conversation:
    """Manual correction of an incorrect match."""
    from app.core.errors import NotFound

    conv = db.get(Conversation, conv_id)
    if conv is None:
        raise NotFound("Conversation not found")
    if lead_id is not None and db.get(Lead, lead_id) is None:
        raise NotFound("Lead not found")
    if campaign_id is not None and db.get(Campaign, campaign_id) is None:
        raise NotFound("Campaign not found")
    old = (conv.lead_id, conv.campaign_id)
    conv.lead_id = lead_id
    conv.campaign_id = campaign_id
    conv.recipient_id = None
    if lead_id and campaign_id:
        rec = db.scalar(select(CampaignRecipient).where(CampaignRecipient.lead_id == lead_id, CampaignRecipient.campaign_id == campaign_id))
        conv.recipient_id = rec.id if rec else None
    for msg in conv.messages:
        if msg.direction == MessageDirection.INBOUND:
            msg.match_method = "manual"
    audit.record(
        db,
        "inbox.reassigned",
        f"Conversation {conv.id} re-matched",
        entity_type="conversation",
        entity_id=conv.id,
        details={"from": list(old), "to": [lead_id, campaign_id]},
    )
    return conv
