"""Central suppression list — consulted before import into campaigns and before every send."""

from __future__ import annotations

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core.errors import NotFound, ValidationFailed
from app.db.base import utcnow
from app.models import CampaignRecipient, Lead, ScheduledJob, Suppression
from app.models.enums import JobKind, JobStatus, LeadStatus, RecipientStatus, SuppressionReason
from app.services import audit
from app.services.emails import email_domain, normalize_email

REASON_TO_LEAD_STATUS = {
    SuppressionReason.UNSUBSCRIBED: LeadStatus.UNSUBSCRIBED,
    SuppressionReason.HARD_BOUNCE: LeadStatus.BOUNCED,
    SuppressionReason.DO_NOT_CONTACT: LeadStatus.DO_NOT_CONTACT,
    SuppressionReason.INVALID_ADDRESS: LeadStatus.BOUNCED,
    SuppressionReason.COMPLAINT: LeadStatus.DO_NOT_CONTACT,
}

REASON_TO_RECIPIENT_STATUS = {
    SuppressionReason.UNSUBSCRIBED: RecipientStatus.UNSUBSCRIBED,
    SuppressionReason.HARD_BOUNCE: RecipientStatus.BOUNCED,
    SuppressionReason.INVALID_ADDRESS: RecipientStatus.BOUNCED,
}


def find_active(db: Session, email: str) -> Suppression | None:
    email = normalize_email(email)
    domain = email_domain(email)
    return db.scalar(select(Suppression).where(Suppression.active.is_(True), or_(Suppression.email == email, Suppression.domain == domain)))


def is_suppressed(db: Session, email: str) -> bool:
    return find_active(db, email) is not None


def suppressed_set(db: Session, emails: list[str]) -> set[str]:
    """Bulk check: returns the subset of normalized emails that are suppressed."""
    if not emails:
        return set()
    norm = {normalize_email(e) for e in emails}
    domains = {email_domain(e) for e in norm}
    rows = db.execute(
        select(Suppression.email, Suppression.domain).where(
            Suppression.active.is_(True), or_(Suppression.email.in_(norm), Suppression.domain.in_(domains))
        )
    ).all()
    blocked_emails = {r.email for r in rows if r.email}
    blocked_domains = {r.domain for r in rows if r.domain}
    return {e for e in norm if e in blocked_emails or email_domain(e) in blocked_domains}


def suppress(
    db: Session,
    *,
    email: str | None = None,
    domain: str | None = None,
    reason: str = SuppressionReason.MANUAL,
    source: str = "manual",
    notes: str | None = None,
) -> Suppression:
    """Add (or re-activate) a suppression and stop any pending outreach to it. Caller commits."""
    if bool(email) == bool(domain):
        raise ValidationFailed("Provide exactly one of email or domain")
    if reason not in SuppressionReason.__members__.values():
        raise ValidationFailed(f"Unknown suppression reason: {reason}")
    if email:
        email = normalize_email(email)
        existing = db.scalar(select(Suppression).where(Suppression.email == email))
    else:
        domain = (domain or "").strip().lower().lstrip("@")
        if "." not in domain:
            raise ValidationFailed("Invalid domain")
        existing = db.scalar(select(Suppression).where(Suppression.domain == domain))

    if existing:
        if not existing.active:
            existing.active = True
            existing.lifted_at = None
            existing.lifted_reason = None
        existing.reason = reason
        existing.source = source
        if notes:
            existing.notes = notes
        sup = existing
    else:
        sup = Suppression(email=email, domain=domain, reason=reason, source=source, notes=notes)
        db.add(sup)
    db.flush()
    audit.record(
        db,
        "suppression.added",
        f"Suppressed {email or '@' + str(domain)} ({reason})",
        entity_type="suppression",
        entity_id=sup.id,
        details={"reason": reason, "source": source},
    )
    stop_outreach_for(db, email=email, domain=domain, reason=reason)
    return sup


def stop_outreach_for(db: Session, *, email: str | None, domain: str | None, reason: str) -> int:
    """Stop queued/scheduled messages for matching leads. Returns number of recipients stopped."""
    lead_q = select(Lead)
    if email:
        lead_q = lead_q.where(Lead.email == email)
    else:
        lead_q = lead_q.where(Lead.email.like(f"%@{domain}"))
    leads = list(db.scalars(lead_q))
    if not leads:
        return 0
    lead_status = REASON_TO_LEAD_STATUS.get(SuppressionReason(reason))
    rec_status = REASON_TO_RECIPIENT_STATUS.get(SuppressionReason(reason), RecipientStatus.SUPPRESSED)
    lead_ids = [lead.id for lead in leads]
    if lead_status:
        for lead in leads:
            lead.status = lead_status
    active_states = [RecipientStatus.PENDING, RecipientStatus.ACTIVE]
    recipients = list(
        db.scalars(select(CampaignRecipient).where(CampaignRecipient.lead_id.in_(lead_ids), CampaignRecipient.status.in_(active_states)))
    )
    for r in recipients:
        r.status = rec_status
        r.stop_reason = f"suppressed:{reason}"
        r.next_send_at = None
    if recipients:
        db.execute(
            update(ScheduledJob)
            .where(
                ScheduledJob.recipient_id.in_([r.id for r in recipients]),
                ScheduledJob.status == JobStatus.PENDING,
            )
            .values(status=JobStatus.CANCELLED, last_error="recipient suppressed", finished_at=utcnow())
        )
    # Individual (non-campaign) queued sends
    pending_single = db.scalars(
        select(ScheduledJob).where(ScheduledJob.kind == JobKind.SEND_SINGLE, ScheduledJob.status == JobStatus.PENDING)
    )
    for job in pending_single:
        if job.payload.get("lead_id") in lead_ids:
            job.status = JobStatus.CANCELLED
            job.last_error = "recipient suppressed"
            job.finished_at = utcnow()
    return len(recipients)


def lift(db: Session, suppression_id: int, *, reason: str, confirm_value: str) -> Suppression:
    """Administrative correction. Requires the operator to retype the address/domain and give a reason."""
    sup = db.get(Suppression, suppression_id)
    if sup is None:
        raise NotFound("Suppression not found")
    target = sup.email or sup.domain or ""
    if confirm_value.strip().lower() != target:
        raise ValidationFailed("Confirmation value does not match the suppressed address/domain")
    if len((reason or "").strip()) < 5:
        raise ValidationFailed("A reason (at least 5 characters) is required to lift a suppression")
    recipient_originated = sup.source in {"unsubscribe_link", "one_click", "reply"}
    if sup.reason == SuppressionReason.COMPLAINT or (sup.reason == SuppressionReason.UNSUBSCRIBED and recipient_originated):
        raise ValidationFailed(
            "Opt-outs and complaints cannot be lifted from the app. The recipient must opt back in directly; "
            "see docs/OPERATIONS.md for the manual database procedure.",
            code="opt_out_locked",
        )
    sup.active = False
    sup.lifted_at = utcnow()
    sup.lifted_reason = reason.strip()
    audit.record(
        db,
        "suppression.lifted",
        f"Suppression lifted for {target}",
        entity_type="suppression",
        entity_id=sup.id,
        details={"reason": reason.strip(), "original_reason": sup.reason},
    )
    return sup
