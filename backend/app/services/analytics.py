"""Analytics from real records. Every metric's definition is returned alongside its value.

Important: "accepted" means the SMTP server returned 250. It is NOT proof of inbox delivery.
Opens are not tracked, and replies are never inferred from anything but received messages.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import distinct, func, select
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
    ScheduledJob,
    SendAttempt,
    Suppression,
)
from app.models.enums import (
    POSITIVE_REPLY_LABELS,
    AttemptStatus,
    CampaignStatus,
    JobKind,
    JobStatus,
    LeadStatus,
    MessageDirection,
    SuppressionReason,
)

DEFINITIONS = {
    "total_leads": "All lead records currently stored.",
    "ready_leads": "Leads with status new/ready that are not on the suppression list.",
    "queued": "Pending send jobs (initial emails and follow-ups) not yet attempted.",
    "attempts": "Live send attempts where the SMTP server was contacted (excludes dry-run and pre-send blocks).",
    "accepted": "Live messages the SMTP server accepted (250). Acceptance is NOT confirmation of inbox delivery.",
    "delivered": "Deliveries confirmed by a provider event. Generic SMTP never reports this, so it is usually 0.",
    "failures": "Live attempts that failed (temporary or permanent), excluding hard bounces.",
    "hard_bounces": "Confirmed permanent bounces (SMTP 5.x.x rejection or structured DSN).",
    "soft_bounces": "Temporary delivery failures reported by DSN (4.x.x).",
    "replies": "Distinct conversations with at least one human (non-automated) inbound reply in the period.",
    "positive_replies": "Replies whose current classification is interested or referral (classifications are reviewable).",
    "opt_outs": "Unsubscribe suppressions created in the period (links, one-click, or reply requests).",
    "contacted_leads": "Distinct leads with at least one accepted live message in the period — denominator for reply and opt-out rates.",
    "reply_rate": "replies / contacted_leads",
    "positive_rate": "positive_replies / contacted_leads",
    "bounce_rate": "hard_bounces / (accepted + SMTP-time hard bounces)",
    "opt_out_rate": "opt_outs / contacted_leads",
    "simulated": "Dry-run messages rendered and recorded without contacting SMTP.",
    "active_campaigns": "Campaigns currently scheduled or active.",
    "scheduled_followups": "Pending follow-up (step 2+) jobs.",
}


def _range(start: date | None, end: date | None) -> tuple[datetime, datetime]:
    end_dt = datetime.combine(end, datetime.max.time(), tzinfo=UTC) if end else utcnow()
    start_dt = datetime.combine(start, datetime.min.time(), tzinfo=UTC) if start else end_dt - timedelta(days=30)
    return start_dt, end_dt


def _rate(n: int, d: int) -> float | None:
    return round(n / d, 4) if d else None


def _attempt_count(db: Session, start: datetime, end: datetime, *conds: Any) -> int:
    return (
        db.scalar(
            select(func.count()).select_from(SendAttempt).where(SendAttempt.created_at >= start, SendAttempt.created_at <= end, *conds)
        )
        or 0
    )


def summary(
    db: Session, start: date | None = None, end: date | None = None, campaign_id: int | None = None, account_id: int | None = None
) -> dict[str, Any]:
    s, e = _range(start, end)
    scope: list[Any] = []
    if campaign_id:
        scope.append(SendAttempt.campaign_id == campaign_id)
    if account_id:
        scope.append(SendAttempt.account_id == account_id)
    live = [SendAttempt.is_simulation.is_(False), *scope]
    contacted_states = [
        AttemptStatus.SENDING,
        AttemptStatus.ACCEPTED,
        AttemptStatus.DELIVERED,
        AttemptStatus.BOUNCED,
        AttemptStatus.FAILED_TEMPORARY,
        AttemptStatus.FAILED_PERMANENT,
        AttemptStatus.UNKNOWN,
    ]
    attempts = _attempt_count(db, s, e, *live, SendAttempt.status.in_(contacted_states))
    accepted = _attempt_count(db, s, e, *live, SendAttempt.smtp_code == 250)
    delivered = _attempt_count(db, s, e, *live, SendAttempt.status == AttemptStatus.DELIVERED)
    failures = _attempt_count(db, s, e, *live, SendAttempt.status.in_([AttemptStatus.FAILED_TEMPORARY, AttemptStatus.FAILED_PERMANENT]))
    smtp_time_bounces = _attempt_count(db, s, e, *live, SendAttempt.status == AttemptStatus.BOUNCED, SendAttempt.smtp_code != 250)
    simulated = _attempt_count(db, s, e, SendAttempt.is_simulation.is_(True), SendAttempt.status == AttemptStatus.SIMULATED, *scope)
    blocked = _attempt_count(db, s, e, SendAttempt.status == AttemptStatus.BLOCKED, *scope)

    ev_scope: list[Any] = [ProviderEvent.occurred_at >= s, ProviderEvent.occurred_at <= e]
    if account_id:
        ev_scope.append(ProviderEvent.account_id == account_id)
    if campaign_id:
        ev_scope.append(ProviderEvent.attempt_id.in_(select(SendAttempt.id).where(SendAttempt.campaign_id == campaign_id)))
    hard = db.scalar(select(func.count()).select_from(ProviderEvent).where(ProviderEvent.event_type == "hard_bounce", *ev_scope)) or 0
    soft = db.scalar(select(func.count()).select_from(ProviderEvent).where(ProviderEvent.event_type == "soft_bounce", *ev_scope)) or 0

    contacted = (
        db.scalar(
            select(func.count(distinct(SendAttempt.lead_id))).where(
                SendAttempt.created_at >= s, SendAttempt.created_at <= e, *live, SendAttempt.smtp_code == 250
            )
        )
        or 0
    )

    conv_scope: list[Any] = []
    if campaign_id:
        conv_scope.append(Conversation.campaign_id == campaign_id)
    if account_id:
        conv_scope.append(Conversation.account_id == account_id)
    human_inbound = select(Message.conversation_id).where(
        Message.direction == MessageDirection.INBOUND, Message.is_auto_reply.is_(False), Message.occurred_at >= s, Message.occurred_at <= e
    )
    replies = db.scalar(select(func.count()).select_from(Conversation).where(Conversation.id.in_(human_inbound), *conv_scope)) or 0
    positive = (
        db.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(
                Conversation.id.in_(human_inbound), Conversation.classification.in_([str(x) for x in POSITIVE_REPLY_LABELS]), *conv_scope
            )
        )
        or 0
    )
    sup_q = (
        select(func.count())
        .select_from(Suppression)
        .where(Suppression.reason == SuppressionReason.UNSUBSCRIBED, Suppression.created_at >= s, Suppression.created_at <= e)
    )
    if campaign_id:
        sup_q = sup_q.where(
            Suppression.email.in_(
                select(Lead.email)
                .join(CampaignRecipient, CampaignRecipient.lead_id == Lead.id)
                .where(CampaignRecipient.campaign_id == campaign_id)
            )
        )
    opt_outs = db.scalar(sup_q) or 0

    total_leads = db.scalar(select(func.count()).select_from(Lead)) or 0
    active_sup = select(Suppression.email).where(Suppression.active.is_(True), Suppression.email.is_not(None))
    ready = (
        db.scalar(
            select(func.count()).select_from(Lead).where(Lead.status.in_([LeadStatus.NEW, LeadStatus.READY]), Lead.email.not_in(active_sup))
        )
        or 0
    )
    job_scope = [ScheduledJob.status == JobStatus.PENDING, ScheduledJob.kind.in_([JobKind.SEND_STEP, JobKind.SEND_SINGLE])]
    if campaign_id:
        job_scope.append(ScheduledJob.campaign_id == campaign_id)
    queued = db.scalar(select(func.count()).select_from(ScheduledJob).where(*job_scope)) or 0
    followups = db.scalar(select(func.count()).select_from(ScheduledJob).where(*job_scope, ScheduledJob.step_number > 1)) or 0
    active_campaigns = (
        db.scalar(select(func.count()).select_from(Campaign).where(Campaign.status.in_([CampaignStatus.ACTIVE, CampaignStatus.SCHEDULED])))
        or 0
    )

    metrics = {
        "total_leads": total_leads,
        "ready_leads": ready,
        "queued": queued,
        "attempts": attempts,
        "accepted": accepted,
        "delivered": delivered,
        "failures": failures,
        "hard_bounces": hard,
        "soft_bounces": soft,
        "replies": replies,
        "positive_replies": positive,
        "opt_outs": opt_outs,
        "contacted_leads": contacted,
        "simulated": simulated,
        "blocked_presend": blocked,
        "active_campaigns": active_campaigns,
        "scheduled_followups": followups,
        "reply_rate": _rate(replies, contacted),
        "positive_rate": _rate(positive, contacted),
        "bounce_rate": _rate(hard, accepted + smtp_time_bounces),
        "opt_out_rate": _rate(opt_outs, contacted),
    }
    return {"range": {"start": s, "end": e}, "metrics": metrics, "definitions": DEFINITIONS}


def timeseries(db: Session, start: date | None = None, end: date | None = None, campaign_id: int | None = None) -> list[dict[str, Any]]:
    s, e = _range(start, end)
    days: dict[str, dict[str, Any]] = {}
    d = s.date()
    while d <= e.date():
        days[d.isoformat()] = {"date": d.isoformat(), "accepted": 0, "simulated": 0, "failures": 0, "replies": 0, "bounces": 0}
        d += timedelta(days=1)
    q = select(SendAttempt.created_at, SendAttempt.status, SendAttempt.is_simulation, SendAttempt.smtp_code).where(
        SendAttempt.created_at >= s, SendAttempt.created_at <= e
    )
    if campaign_id:
        q = q.where(SendAttempt.campaign_id == campaign_id)
    for created, status, sim, code in db.execute(q):
        key = created.date().isoformat()
        if key not in days:
            continue
        if sim and status == AttemptStatus.SIMULATED:
            days[key]["simulated"] += 1
        elif not sim and code == 250:
            days[key]["accepted"] += 1
        elif not sim and status in (AttemptStatus.FAILED_TEMPORARY, AttemptStatus.FAILED_PERMANENT):
            days[key]["failures"] += 1
    mq = (
        select(Message.occurred_at)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Message.direction == MessageDirection.INBOUND,
            Message.is_auto_reply.is_(False),
            Message.occurred_at >= s,
            Message.occurred_at <= e,
        )
    )
    if campaign_id:
        mq = mq.where(Conversation.campaign_id == campaign_id)
    for (occurred,) in db.execute(mq):
        key = occurred.date().isoformat()
        if key in days:
            days[key]["replies"] += 1
    for (occurred,) in db.execute(
        select(ProviderEvent.occurred_at).where(
            ProviderEvent.event_type == "hard_bounce", ProviderEvent.occurred_at >= s, ProviderEvent.occurred_at <= e
        )
    ):
        key = occurred.date().isoformat()
        if key in days:
            days[key]["bounces"] += 1
    return list(days.values())


def per_campaign(db: Session, start: date | None = None, end: date | None = None) -> list[dict[str, Any]]:
    out = []
    for c in db.scalars(select(Campaign).order_by(Campaign.created_at.desc())):
        m = summary(db, start, end, campaign_id=c.id)["metrics"]
        recipients = db.scalar(select(func.count()).select_from(CampaignRecipient).where(CampaignRecipient.campaign_id == c.id)) or 0
        out.append(
            {
                "campaign_id": c.id,
                "name": c.name,
                "status": c.status,
                "dry_run": c.dry_run,
                "recipients": recipients,
                "accepted": m["accepted"],
                "simulated": m["simulated"],
                "failures": m["failures"],
                "hard_bounces": m["hard_bounces"],
                "replies": m["replies"],
                "positive_replies": m["positive_replies"],
                "opt_outs": m["opt_outs"],
                "contacted_leads": m["contacted_leads"],
                "reply_rate": m["reply_rate"],
                "queued": m["queued"],
            }
        )
    return out


def per_account(db: Session, start: date | None = None, end: date | None = None) -> list[dict[str, Any]]:
    out = []
    for a in db.scalars(select(EmailAccount).order_by(EmailAccount.id)):
        m = summary(db, start, end, account_id=a.id)["metrics"]
        out.append(
            {
                "account_id": a.id,
                "email": a.email,
                "status": a.status,
                "attempts": m["attempts"],
                "accepted": m["accepted"],
                "failures": m["failures"],
                "hard_bounces": m["hard_bounces"],
                "replies": m["replies"],
                "simulated": m["simulated"],
                "bounce_rate": m["bounce_rate"],
            }
        )
    return out
