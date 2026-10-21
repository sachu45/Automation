"""The sending engine. Runs only inside the worker process, never in HTTP handlers.

Safety properties:
* Every send re-checks emergency stop, campaign state, sending window, limits,
  suppression, lead eligibility, recipient state, and whether this step was already sent.
* At-most-once: an attempt row in state `sending` is committed *before* the SMTP
  conversation starts. If the worker dies mid-send, recovery marks it `unknown` and the
  recipient `needs_review`; it is never resent automatically.
* Dry-run attempts are recorded as `simulated` with `is_simulation=True` and never
  touch SMTP.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import make_msgid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import (
    Campaign,
    CampaignRecipient,
    CampaignStep,
    EmailAccount,
    Lead,
    ProviderEvent,
    ScheduledJob,
    SendAttempt,
    TemplateVersion,
)
from app.models.enums import (
    BLOCKED_LEAD_STATUSES,
    AccountStatus,
    AttemptStatus,
    CampaignStatus,
    JobKind,
    JobStatus,
    LeadStatus,
    RecipientStatus,
    SuppressionReason,
)
from app.providers.email.base import ConnectionTestError, OutgoingMessage, SendError
from app.services import accounts as account_svc
from app.services import audit, conversations, schedule, suppression, unsubscribe
from app.services.settings import AppConfig, get_config
from app.services.template_engine import (
    MissingVariablesError,
    RenderedEmail,
    TemplateSyntaxError,
    UnresolvedPlaceholderError,
    assert_no_placeholders,
    build_context,
    render_email,
)

log = logging.getLogger("app.sending")

ALREADY_SENT_STATUSES = [
    AttemptStatus.SENDING,
    AttemptStatus.ACCEPTED,
    AttemptStatus.DELIVERED,
    AttemptStatus.BOUNCED,
    AttemptStatus.UNKNOWN,
    AttemptStatus.SIMULATED,
]
QUOTA_STATUSES = ALREADY_SENT_STATUSES + [AttemptStatus.FAILED_PERMANENT]
MAX_BACKOFF_SECONDS = 6 * 3600


# --- Composition ----------------------------------------------------------------------


def sender_context(cfg: AppConfig, account: EmailAccount) -> dict[str, Any]:
    return {
        "sender_name": account.display_name or cfg.sender_name,
        "sender_email": account.email,
        "sender_company": cfg.company_name,
        "sender_website": cfg.company_website,
        # An empty signature falls back to the sender's name rather than blocking every send
        "signature": cfg.signature or account.display_name or cfg.sender_name,
        "mailing_address": cfg.mailing_address,
        "opt_out_text": cfg.opt_out_text,
    }


def _uses_opt_out_vars(subject: str, body: str) -> bool:
    return any(v in (subject + body) for v in ("unsubscribe_url", "opt_out_text"))


@dataclass
class Composed:
    rendered: RenderedEmail
    headers: dict[str, str]


def compose(
    *,
    cfg: AppConfig,
    lead: Lead,
    account: EmailAccount,
    subject: str,
    body: str,
    fmt: str,
    campaign_id: int | None,
    is_reply: bool = False,
    strict: bool = True,
    literal: bool = False,
) -> Composed:
    """Render a message. `literal=True` sends operator-written text verbatim (no variables),
    but the unresolved-placeholder guard still applies."""
    reply_address = account.reply_to or cfg.reply_to_email or account.email
    unsub_url = unsubscribe.unsubscribe_url(lead.email, campaign_id)
    extra: dict[str, Any] = {}
    if unsub_url:
        extra["unsubscribe_url"] = unsub_url
    ctx = build_context(lead, sender_context(cfg, account), extra)
    footer = None
    if cfg.append_opt_out_footer and not is_reply and not _uses_opt_out_vars(subject, body):
        footer = cfg.opt_out_text + (f"\nUnsubscribe: {unsub_url}" if unsub_url else "")
        if cfg.mailing_address:
            footer += f"\n{cfg.mailing_address}"
    if literal:
        text = body.strip() + (f"\n\n--\n{footer}" if footer else "")
        rendered = RenderedEmail(subject=" ".join(subject.split()), text=text, html=None)
        assert_no_placeholders(rendered.subject, rendered.text)
    else:
        rendered = render_email(subject, body, fmt, ctx, strict=strict, footer_text=footer)
    headers = {} if is_reply else unsubscribe.list_unsubscribe_headers(unsub_url, str(reply_address))
    return Composed(rendered=rendered, headers=headers)


# --- Limits ------------------------------------------------------------------------------


def _count_since(db: Session, since: datetime, simulation: bool, **filters: Any) -> int:
    stmt = (
        select(func.count())
        .select_from(SendAttempt)
        .where(
            SendAttempt.created_at >= since,
            SendAttempt.is_simulation.is_(simulation),
            SendAttempt.status.in_(QUOTA_STATUSES),
        )
    )
    for k, v in filters.items():
        stmt = stmt.where(getattr(SendAttempt, k) == v)
    return db.scalar(stmt) or 0


def _oldest_since(db: Session, since: datetime, simulation: bool, limit_index: int, **filters: Any) -> datetime | None:
    """Timestamp of the attempt whose expiry frees one unit of quota."""
    stmt = (
        select(SendAttempt.created_at)
        .where(
            SendAttempt.created_at >= since,
            SendAttempt.is_simulation.is_(simulation),
            SendAttempt.status.in_(QUOTA_STATUSES),
        )
        .order_by(SendAttempt.created_at.asc())
        .offset(limit_index)
        .limit(1)
    )
    for k, v in filters.items():
        stmt = stmt.where(getattr(SendAttempt, k) == v)
    return db.scalar(stmt)


@dataclass
class LimitDecision:
    allowed: bool
    retry_at: datetime | None = None
    reason: str | None = None


def check_limits(
    db: Session, *, account: EmailAccount, campaign: Campaign | None, cfg: AppConfig, now: datetime, simulation: bool
) -> LimitDecision:
    """Rolling-window limits. Simulated and live sends are counted separately."""
    day_ago = now - timedelta(hours=24)
    hour_ago = now - timedelta(hours=1)
    checks: list[tuple[str, int, datetime, timedelta, dict[str, Any]]] = [
        ("global daily limit", cfg.daily_sending_limit, day_ago, timedelta(hours=24), {}),
        ("account daily limit", account.daily_limit, day_ago, timedelta(hours=24), {"account_id": account.id}),
        ("account hourly limit", account.hourly_limit, hour_ago, timedelta(hours=1), {"account_id": account.id}),
    ]
    if campaign is not None:
        checks.append(("campaign daily limit", campaign.daily_limit, day_ago, timedelta(hours=24), {"campaign_id": campaign.id}))
    for reason, limit, since, window, filters in checks:
        used = _count_since(db, since, simulation, **filters)
        if used >= limit:
            oldest = _oldest_since(db, since, simulation, used - limit, **filters)
            retry = (oldest + window + timedelta(seconds=5)) if oldest else now + timedelta(minutes=30)
            return LimitDecision(False, retry, reason)
    min_delay = max(account.min_delay_seconds, cfg.min_delay_seconds, campaign.min_delay_seconds if campaign else 0)
    last = db.scalar(
        select(func.max(SendAttempt.created_at)).where(
            SendAttempt.account_id == account.id,
            SendAttempt.is_simulation.is_(simulation),
            SendAttempt.status.in_(QUOTA_STATUSES),
        )
    )
    if last and (now - last).total_seconds() < min_delay:
        return LimitDecision(False, last + timedelta(seconds=min_delay), "minimum delay between messages")
    return LimitDecision(True)


def _acquire_account_lock(db: Session, account_id: int, now: datetime, seconds: int = 120) -> bool:
    res = db.execute(
        update(EmailAccount)
        .where(
            EmailAccount.id == account_id,
            (EmailAccount.send_lock_until.is_(None)) | (EmailAccount.send_lock_until < now),
        )
        .values(send_lock_until=now + timedelta(seconds=seconds))
    )
    db.commit()
    return res.rowcount == 1  # type: ignore[attr-defined]


def _release_account_lock(db: Session, account_id: int) -> None:
    db.execute(update(EmailAccount).where(EmailAccount.id == account_id).values(send_lock_until=None))


# --- Job helpers ----------------------------------------------------------------------------


def _release(job: ScheduledJob, run_at: datetime | None, note: str | None) -> None:
    job.status = JobStatus.PENDING
    job.locked_by = None
    job.locked_at = None
    if run_at is not None:
        job.run_at = run_at
    if note:
        job.last_error = note


def _finish(job: ScheduledJob, status: JobStatus, note: str | None = None) -> None:
    job.status = status
    job.finished_at = utcnow()
    job.locked_by = None
    job.locked_at = None
    if note:
        job.last_error = note


def _stop_recipient(recipient: CampaignRecipient, status: RecipientStatus, reason: str) -> None:
    recipient.status = status
    recipient.stop_reason = reason
    recipient.next_send_at = None


def _blocked_attempt(db: Session, job: ScheduledJob, lead: Lead | None, account_id: int | None, reason: str, simulation: bool) -> None:
    db.add(
        SendAttempt(
            idempotency_key=job.idempotency_key,
            attempt_no=_next_attempt_no(db, job.idempotency_key),
            job_id=job.id,
            campaign_id=job.campaign_id,
            recipient_id=job.recipient_id,
            lead_id=lead.id if lead else None,
            account_id=account_id,
            step_number=job.step_number,
            to_email=lead.email if lead else "",
            status=AttemptStatus.BLOCKED,
            is_simulation=simulation,
            error=reason[:500],
            created_at=utcnow(),
            completed_at=utcnow(),
        )
    )


def _next_attempt_no(db: Session, key: str) -> int:
    return (db.scalar(select(func.max(SendAttempt.attempt_no)).where(SendAttempt.idempotency_key == key)) or 0) + 1


def _already_sent(db: Session, key: str) -> SendAttempt | None:
    return db.scalar(select(SendAttempt).where(SendAttempt.idempotency_key == key, SendAttempt.status.in_(ALREADY_SENT_STATUSES)))


def backoff_seconds(attempts: int) -> int:
    base = min(MAX_BACKOFF_SECONDS, 60 * (2 ** max(attempts - 1, 0)))
    return int(base * random.uniform(0.85, 1.15))


def send_idempotency_key(recipient_id: int, step: int, simulation: bool) -> str:
    return f"step:{recipient_id}:{step}:{'sim' if simulation else 'live'}"


# --- Main entry point ------------------------------------------------------------------------


def process_job(db: Session, job: ScheduledJob, now: datetime | None = None) -> str:
    """Process one claimed send job. Returns a short outcome label (for logs/tests)."""
    now = now or utcnow()
    if job.kind == JobKind.SEND_STEP:
        return _process_step(db, job, now)
    if job.kind == JobKind.SEND_SINGLE:
        return _process_single(db, job, now)
    _finish(job, JobStatus.FAILED, f"unknown job kind {job.kind}")
    db.commit()
    return "unknown_kind"


def _process_step(db: Session, job: ScheduledJob, now: datetime) -> str:
    cfg = get_config(db)
    campaign = db.get(Campaign, job.campaign_id)
    recipient = db.get(CampaignRecipient, job.recipient_id)
    if campaign is None or recipient is None:
        _finish(job, JobStatus.CANCELLED, "campaign or recipient deleted")
        db.commit()
        return "cancelled"
    simulation = campaign.dry_run

    # 1. Global emergency stop
    if cfg.emergency_stop:
        _release(job, now + timedelta(minutes=5), "emergency stop engaged")
        db.commit()
        return "emergency_stop"

    # 2. Campaign state
    if campaign.status in (CampaignStatus.CANCELLED, CampaignStatus.COMPLETED):
        _finish(job, JobStatus.CANCELLED, f"campaign {campaign.status}")
        db.commit()
        return "cancelled"
    if campaign.status == CampaignStatus.PAUSED:
        _release(job, None, "campaign paused")
        db.commit()
        return "paused"
    if campaign.status not in (CampaignStatus.ACTIVE, CampaignStatus.SCHEDULED):
        _release(job, now + timedelta(minutes=10), f"campaign not active ({campaign.status})")
        db.commit()
        return "not_active"
    if campaign.start_at and now < campaign.start_at:
        _release(job, campaign.start_at, "before campaign start")
        db.commit()
        return "too_early"
    if campaign.status == CampaignStatus.SCHEDULED:
        campaign.status = CampaignStatus.ACTIVE

    # 3. Recipient state and step
    if recipient.status != RecipientStatus.ACTIVE or recipient.next_step != job.step_number:
        _finish(job, JobStatus.CANCELLED, f"recipient {recipient.status}, next step {recipient.next_step}")
        db.commit()
        return "stale"
    lead = db.get(Lead, recipient.lead_id)
    if lead is None:
        _stop_recipient(recipient, RecipientStatus.STOPPED, "lead_deleted")
        _finish(job, JobStatus.CANCELLED, "lead deleted")
        db.commit()
        return "cancelled"

    # 4. Already sent at this step? (idempotency)
    prior = _already_sent(db, job.idempotency_key)
    if prior is not None:
        _finish(job, JobStatus.DONE, f"already sent (attempt {prior.id}, {prior.status})")
        db.commit()
        return "duplicate_prevented"

    # 5. Eligibility: suppression + lead status + caps + campaign end
    sup = suppression.find_active(db, lead.email)
    if sup is not None:
        _stop_recipient(recipient, RecipientStatus.SUPPRESSED, f"suppressed:{sup.reason}")
        _blocked_attempt(db, job, lead, campaign.email_account_id, f"suppressed ({sup.reason})", simulation)
        _finish(job, JobStatus.CANCELLED, "recipient suppressed")
        db.commit()
        return "suppressed"
    if lead.status in BLOCKED_LEAD_STATUSES:
        _stop_recipient(recipient, RecipientStatus.STOPPED, f"lead_status:{lead.status}")
        _blocked_attempt(db, job, lead, campaign.email_account_id, f"lead status {lead.status}", simulation)
        _finish(job, JobStatus.CANCELLED, f"lead status {lead.status}")
        db.commit()
        return "lead_blocked"
    if recipient.steps_sent >= campaign.max_emails_per_recipient:
        _stop_recipient(recipient, RecipientStatus.STOPPED, "max_attempts_reached")
        _finish(job, JobStatus.CANCELLED, "max emails per recipient reached")
        _maybe_complete(db, campaign)
        db.commit()
        return "max_reached"
    if campaign.end_at and now >= campaign.end_at:
        _stop_recipient(recipient, RecipientStatus.STOPPED, "campaign_ended")
        _finish(job, JobStatus.CANCELLED, "campaign end date passed")
        _maybe_complete(db, campaign)
        db.commit()
        return "ended"

    # 6. Sending window
    if not schedule.in_window(now, campaign.timezone, campaign.window_start, campaign.window_end, campaign.send_days):
        nxt = schedule.next_window_start(now, campaign.timezone, campaign.window_start, campaign.window_end, campaign.send_days)
        _release(job, nxt, "outside sending window")
        recipient.next_send_at = nxt
        db.commit()
        return "outside_window"

    # 7. Account readiness
    account = db.get(EmailAccount, campaign.email_account_id) if campaign.email_account_id else None
    if account is None:
        campaign.status = CampaignStatus.PAUSED
        campaign.paused_at = now
        audit.record(
            db,
            "campaign.auto_paused",
            f"Campaign '{campaign.name}' paused: no sending account",
            entity_type="campaign",
            entity_id=campaign.id,
        )
        _release(job, None, "no sending account")
        db.commit()
        return "no_account"
    if not simulation and (account.status != AccountStatus.CONNECTED or not account.sending_enabled):
        if account.status == AccountStatus.AUTH_FAILED:
            campaign.status = CampaignStatus.PAUSED
            campaign.paused_at = now
            audit.record(
                db,
                "campaign.auto_paused",
                f"Campaign '{campaign.name}' paused: account {account.email} authentication failed",
                entity_type="campaign",
                entity_id=campaign.id,
            )
        _release(job, now + timedelta(minutes=15), f"account not ready ({account.status})")
        db.commit()
        return "account_not_ready"

    # 8. Rate limits
    decision = check_limits(db, account=account, campaign=campaign, cfg=cfg, now=now, simulation=simulation)
    if not decision.allowed:
        _release(job, decision.retry_at, f"rate limited: {decision.reason}")
        recipient.next_send_at = decision.retry_at
        db.commit()
        return "rate_limited"

    # 9. Render (strict: never send unresolved placeholders)
    step = db.scalar(select(CampaignStep).where(CampaignStep.campaign_id == campaign.id, CampaignStep.step_number == job.step_number))
    if step is None:
        _stop_recipient(recipient, RecipientStatus.COMPLETED, "no_more_steps")
        _finish(job, JobStatus.CANCELLED, "step missing")
        _maybe_complete(db, campaign)
        db.commit()
        return "no_step"
    version = db.get(TemplateVersion, step.template_version_id)
    assert version is not None
    is_followup = job.step_number > 1 and step.send_as_reply and bool(recipient.thread_message_id)
    subject_tpl = version.subject
    try:
        composed = compose(
            cfg=cfg,
            lead=lead,
            account=account,
            subject=subject_tpl,
            body=version.body,
            fmt=version.format,
            campaign_id=campaign.id,
        )
    except (MissingVariablesError, TemplateSyntaxError, UnresolvedPlaceholderError) as exc:
        _stop_recipient(recipient, RecipientStatus.FAILED, "render_error")
        _blocked_attempt(db, job, lead, account.id, f"render error: {exc}", simulation)
        _finish(job, JobStatus.FAILED, f"render error: {exc}")
        _maybe_complete(db, campaign)
        db.commit()
        return "render_error"
    subject = composed.rendered.subject
    if is_followup and recipient.thread_subject:
        subject = recipient.thread_subject if recipient.thread_subject.lower().startswith("re:") else f"Re: {recipient.thread_subject}"

    return _deliver(
        db,
        job=job,
        cfg=cfg,
        account=account,
        lead=lead,
        campaign=campaign,
        recipient=recipient,
        subject=subject,
        composed=composed,
        in_reply_to=recipient.thread_message_id if is_followup else None,
        simulation=simulation,
        now=now,
    )


def _deliver(
    db: Session,
    *,
    job: ScheduledJob,
    cfg: AppConfig,
    account: EmailAccount,
    lead: Lead,
    campaign: Campaign | None,
    recipient: CampaignRecipient | None,
    subject: str,
    composed: Composed,
    in_reply_to: str | None,
    simulation: bool,
    now: datetime,
) -> str:
    if not _acquire_account_lock(db, account.id, now):
        _release(job, now + timedelta(seconds=30), "account busy")
        db.commit()
        return "account_busy"
    try:
        domain = account.email.rsplit("@", 1)[-1]
        message_id = make_msgid(domain=domain)
        attempt = SendAttempt(
            idempotency_key=job.idempotency_key,
            attempt_no=_next_attempt_no(db, job.idempotency_key),
            job_id=job.id,
            campaign_id=campaign.id if campaign else None,
            recipient_id=recipient.id if recipient else None,
            lead_id=lead.id,
            account_id=account.id,
            step_number=job.step_number,
            to_email=lead.email,
            subject=subject,
            status=AttemptStatus.SIMULATED if simulation else AttemptStatus.SENDING,
            is_simulation=simulation,
            message_id_header=message_id,
            created_at=now,
            completed_at=now if simulation else None,
        )
        db.add(attempt)
        job.attempts += 1
        db.commit()  # <- durable record BEFORE contacting SMTP

        if not simulation:
            out = OutgoingMessage(
                from_email=account.email,
                from_name=account.display_name or cfg.sender_name,
                to_email=lead.email,
                subject=subject,
                text=composed.rendered.text,
                html=composed.rendered.html,
                reply_to=account.reply_to or (str(cfg.reply_to_email) if cfg.reply_to_email else None),
                message_id=message_id,
                in_reply_to=in_reply_to,
                references=in_reply_to,
                headers=composed.headers,
            )
            assert_no_placeholders(out.subject, out.text, out.html or "")
            try:
                sender, _ = account_svc.build_providers(db, account)
                result = sender.send(out)
            except ConnectionTestError as exc:
                return _handle_failure(
                    db,
                    job,
                    attempt,
                    account,
                    lead,
                    campaign,
                    recipient,
                    SendError(exc.message, permanent=False, auth=exc.auth),
                    now,
                )
            except SendError as exc:
                return _handle_failure(db, job, attempt, account, lead, campaign, recipient, exc, now)
            attempt.status = AttemptStatus.ACCEPTED
            attempt.smtp_code = result.smtp_code
            attempt.provider_message_id = result.provider_message_id
            attempt.completed_at = utcnow()
            account.last_send_at = attempt.completed_at
        _on_success(db, job, attempt, account, lead, campaign, recipient, composed, subject, in_reply_to, now)
        db.commit()
        return "simulated" if simulation else "accepted"
    finally:
        _release_account_lock(db, account.id)
        db.commit()


def _on_success(
    db: Session,
    job: ScheduledJob,
    attempt: SendAttempt,
    account: EmailAccount,
    lead: Lead,
    campaign: Campaign | None,
    recipient: CampaignRecipient | None,
    composed: Composed,
    subject: str,
    in_reply_to: str | None,
    now: datetime,
) -> None:
    sent_at = attempt.completed_at or now
    conversations.record_outbound(
        db,
        account_id=account.id,
        lead_id=lead.id,
        campaign_id=campaign.id if campaign else None,
        recipient_id=recipient.id if recipient else None,
        attempt_id=attempt.id,
        message_id=attempt.message_id_header or "",
        in_reply_to=in_reply_to,
        from_email=account.email,
        from_name=account.display_name,
        to_email=lead.email,
        subject=subject,
        body_text=composed.rendered.text,
        occurred_at=sent_at,
        is_simulation=attempt.is_simulation,
    )
    if not attempt.is_simulation:
        lead.last_contacted_at = sent_at
        if lead.status in (LeadStatus.NEW, LeadStatus.READY):
            lead.status = LeadStatus.CONTACTED
    _finish(job, JobStatus.DONE)
    if recipient is None or campaign is None:
        return
    recipient.steps_sent += 1
    recipient.last_sent_at = sent_at
    if recipient.thread_message_id is None:
        recipient.thread_message_id = attempt.message_id_header
        recipient.thread_subject = subject
    _schedule_next_step(db, campaign, recipient, sent_at)


def _schedule_next_step(db: Session, campaign: Campaign, recipient: CampaignRecipient, sent_at: datetime) -> None:
    next_number = recipient.next_step + 1
    step = db.scalar(select(CampaignStep).where(CampaignStep.campaign_id == campaign.id, CampaignStep.step_number == next_number))
    recipient.next_step = next_number
    if step is None or recipient.steps_sent >= campaign.max_emails_per_recipient:
        _stop_recipient(recipient, RecipientStatus.COMPLETED, "sequence_finished")
        _maybe_complete(db, campaign)
        return
    due = schedule.add_delay(sent_at, campaign.timezone, step.delay_days, step.delay_hours)
    due = schedule.next_window_start(due, campaign.timezone, campaign.window_start, campaign.window_end, campaign.send_days)
    recipient.next_send_at = due
    key = send_idempotency_key(recipient.id, next_number, campaign.dry_run)
    if db.scalar(select(ScheduledJob.id).where(ScheduledJob.idempotency_key == key)) is None:
        db.add(
            ScheduledJob(
                kind=JobKind.SEND_STEP,
                idempotency_key=key,
                run_at=due,
                campaign_id=campaign.id,
                recipient_id=recipient.id,
                account_id=campaign.email_account_id,
                step_number=next_number,
            )
        )


def _handle_failure(
    db: Session,
    job: ScheduledJob,
    attempt: SendAttempt,
    account: EmailAccount,
    lead: Lead,
    campaign: Campaign | None,
    recipient: CampaignRecipient | None,
    exc: SendError,
    now: datetime,
) -> str:
    attempt.completed_at = utcnow()
    attempt.smtp_code = exc.smtp_code
    attempt.error = exc.message[:500]
    if exc.auth:
        # Not the recipient's fault and nothing was sent: disable the account, keep the job.
        attempt.status = AttemptStatus.FAILED_TEMPORARY
        account_svc.mark_auth_failure(db, account, exc.message)
        if campaign is not None:
            campaign.status = CampaignStatus.PAUSED
            campaign.paused_at = now
            audit.record(
                db,
                "campaign.auto_paused",
                f"Campaign '{campaign.name}' paused after authentication failure",
                entity_type="campaign",
                entity_id=campaign.id,
            )
        _release(job, now + timedelta(minutes=15), f"auth failure: {exc.message}")
        db.commit()
        return "auth_failed"
    if exc.permanent:
        if exc.hard_bounce:
            attempt.status = AttemptStatus.BOUNCED
            db.add(
                ProviderEvent(
                    dedupe_key=f"smtp:{attempt.id}",
                    account_id=account.id,
                    attempt_id=attempt.id,
                    lead_id=lead.id,
                    event_type="hard_bounce",
                    source="smtp_response",
                    email=lead.email,
                    detail={"code": exc.smtp_code, "message": exc.message[:300]},
                    occurred_at=now,
                )
            )
            suppression.suppress(db, email=lead.email, reason=SuppressionReason.HARD_BOUNCE, source="smtp_response")
            if recipient is not None:
                _stop_recipient(recipient, RecipientStatus.BOUNCED, "hard_bounce")
        else:
            attempt.status = AttemptStatus.FAILED_PERMANENT
            if recipient is not None:
                _stop_recipient(recipient, RecipientStatus.FAILED, f"permanent_failure:{exc.smtp_code}")
        _finish(job, JobStatus.FAILED, exc.message[:500])
        if campaign is not None:
            _maybe_complete(db, campaign)
        db.commit()
        return "bounced" if exc.hard_bounce else "failed_permanent"
    # Temporary: bounded exponential backoff
    attempt.status = AttemptStatus.FAILED_TEMPORARY
    if job.attempts >= job.max_attempts:
        _finish(job, JobStatus.FAILED, f"gave up after {job.attempts} attempts: {exc.message[:300]}")
        if recipient is not None:
            _stop_recipient(recipient, RecipientStatus.FAILED, "temporary_failures_exhausted")
        if campaign is not None:
            _maybe_complete(db, campaign)
        db.commit()
        return "failed_exhausted"
    retry_at = now + timedelta(seconds=backoff_seconds(job.attempts))
    _release(job, retry_at, f"temporary failure: {exc.message[:300]}")
    if recipient is not None:
        recipient.next_send_at = retry_at
    db.commit()
    return "retry_scheduled"


def _maybe_complete(db: Session, campaign: Campaign) -> None:
    db.flush()
    remaining = db.scalar(
        select(func.count())
        .select_from(CampaignRecipient)
        .where(
            CampaignRecipient.campaign_id == campaign.id,
            CampaignRecipient.status.in_([RecipientStatus.ACTIVE, RecipientStatus.PENDING, RecipientStatus.NEEDS_REVIEW]),
        )
    )
    if not remaining and campaign.status in (CampaignStatus.ACTIVE, CampaignStatus.SCHEDULED):
        campaign.status = CampaignStatus.COMPLETED
        campaign.completed_at = utcnow()
        audit.record(db, "campaign.completed", f"Campaign '{campaign.name}' completed", entity_type="campaign", entity_id=campaign.id)


# --- Individual sends ---------------------------------------------------------------------------


def enqueue_single(
    db: Session,
    *,
    lead: Lead,
    account: EmailAccount,
    subject: str,
    body: str,
    fmt: str,
    idempotency_key: str,
    is_reply: bool = False,
    in_reply_to: str | None = None,
    draft_id: int | None = None,
    literal: bool = False,
) -> ScheduledJob:
    cfg = get_config(db)
    simulation = cfg.dry_run
    existing = db.scalar(select(ScheduledJob).where(ScheduledJob.idempotency_key == f"single:{idempotency_key}"))
    if existing is not None:
        return existing
    job = ScheduledJob(
        kind=JobKind.SEND_SINGLE,
        idempotency_key=f"single:{idempotency_key}",
        run_at=utcnow(),
        account_id=account.id,
        max_attempts=3,
        payload={
            "lead_id": lead.id,
            "subject": subject,
            "body": body,
            "format": fmt,
            "simulation": simulation,
            "is_reply": is_reply,
            "in_reply_to": in_reply_to,
            "draft_id": draft_id,
            "literal": literal,
        },
    )
    db.add(job)
    audit.record(
        db,
        "send.single_queued",
        f"{'Simulated' if simulation else 'Live'} email to {lead.email} queued",
        entity_type="lead",
        entity_id=lead.id,
    )
    return job


def _process_single(db: Session, job: ScheduledJob, now: datetime) -> str:
    cfg = get_config(db)
    p = job.payload
    simulation = bool(p.get("simulation", True))
    lead = db.get(Lead, p.get("lead_id"))
    account = db.get(EmailAccount, job.account_id) if job.account_id else None
    if cfg.emergency_stop:
        _release(job, now + timedelta(minutes=5), "emergency stop engaged")
        db.commit()
        return "emergency_stop"
    if lead is None or account is None:
        _finish(job, JobStatus.CANCELLED, "lead or account missing")
        db.commit()
        return "cancelled"
    if _already_sent(db, job.idempotency_key):
        _finish(job, JobStatus.DONE, "already sent")
        db.commit()
        return "duplicate_prevented"
    if suppression.is_suppressed(db, lead.email) or lead.status in BLOCKED_LEAD_STATUSES:
        _blocked_attempt(db, job, lead, account.id, "recipient suppressed or not contactable", simulation)
        _finish(job, JobStatus.CANCELLED, "recipient suppressed")
        db.commit()
        return "suppressed"
    if not simulation and (account.status != AccountStatus.CONNECTED or not account.sending_enabled):
        _blocked_attempt(db, job, lead, account.id, f"account not ready ({account.status})", simulation)
        _finish(job, JobStatus.FAILED, "account not ready")
        db.commit()
        return "account_not_ready"
    decision = check_limits(db, account=account, campaign=None, cfg=cfg, now=now, simulation=simulation)
    if not decision.allowed:
        _release(job, decision.retry_at, f"rate limited: {decision.reason}")
        db.commit()
        return "rate_limited"
    try:
        composed = compose(
            cfg=cfg,
            lead=lead,
            account=account,
            subject=p["subject"],
            body=p["body"],
            fmt=p.get("format", "text"),
            campaign_id=None,
            is_reply=bool(p.get("is_reply")),
            literal=bool(p.get("literal")),
        )
    except (MissingVariablesError, TemplateSyntaxError, UnresolvedPlaceholderError) as exc:
        _blocked_attempt(db, job, lead, account.id, f"render error: {exc}", simulation)
        _finish(job, JobStatus.FAILED, f"render error: {exc}")
        db.commit()
        return "render_error"
    return _deliver(
        db,
        job=job,
        cfg=cfg,
        account=account,
        lead=lead,
        campaign=None,
        recipient=None,
        subject=composed.rendered.subject,
        composed=composed,
        in_reply_to=p.get("in_reply_to"),
        simulation=simulation,
        now=now,
    )


# --- Crash recovery -------------------------------------------------------------------------------


def recover_stale(db: Session, stale_minutes: int, now: datetime | None = None) -> int:
    """Jobs stuck in `running` (worker crashed). Never resend if SMTP may have been contacted."""
    now = now or utcnow()
    cutoff = now - timedelta(minutes=stale_minutes)
    stuck = list(db.scalars(select(ScheduledJob).where(ScheduledJob.status == JobStatus.RUNNING, ScheduledJob.locked_at < cutoff)))
    for job in stuck:
        in_flight = db.scalar(
            select(SendAttempt).where(SendAttempt.idempotency_key == job.idempotency_key, SendAttempt.status == AttemptStatus.SENDING)
        )
        if in_flight is not None:
            in_flight.status = AttemptStatus.UNKNOWN
            in_flight.error = "worker stopped during send; outcome unknown"
            in_flight.completed_at = now
            _finish(job, JobStatus.NEEDS_REVIEW, "outcome unknown after worker crash")
            if job.recipient_id:
                rec = db.get(CampaignRecipient, job.recipient_id)
                if rec is not None:
                    rec.status = RecipientStatus.NEEDS_REVIEW
                    rec.stop_reason = "send_outcome_unknown"
            audit.record(
                db, "send.unknown_outcome", f"Send outcome unknown for job {job.id}; needs review", entity_type="job", entity_id=job.id
            )
        else:
            _release(job, now, "recovered after worker restart")
    db.commit()
    return len(stuck)


def resolve_unknown(db: Session, recipient_id: int, was_sent: bool) -> CampaignRecipient:
    """Operator decision for a recipient whose last send outcome is unknown."""
    from app.core.errors import NotFound, ValidationFailed

    rec = db.get(CampaignRecipient, recipient_id)
    if rec is None:
        raise NotFound("Recipient not found")
    if rec.status != RecipientStatus.NEEDS_REVIEW:
        raise ValidationFailed("Recipient is not awaiting review")
    campaign = db.get(Campaign, rec.campaign_id)
    assert campaign is not None
    key = send_idempotency_key(rec.id, rec.next_step, campaign.dry_run)
    attempt = db.scalar(select(SendAttempt).where(SendAttempt.idempotency_key == key, SendAttempt.status == AttemptStatus.UNKNOWN))
    rec.status = RecipientStatus.ACTIVE
    rec.stop_reason = None
    if was_sent:
        if attempt is not None:
            attempt.status = AttemptStatus.ACCEPTED
            attempt.error = (attempt.error or "") + " | operator confirmed sent"
        rec.steps_sent += 1
        rec.last_sent_at = attempt.created_at if attempt else utcnow()
        if rec.thread_message_id is None and attempt is not None:
            rec.thread_message_id = attempt.message_id_header
            rec.thread_subject = attempt.subject
        _schedule_next_step(db, campaign, rec, rec.last_sent_at or utcnow())
    else:
        if attempt is not None:
            attempt.status = AttemptStatus.FAILED_TEMPORARY
            attempt.error = (attempt.error or "") + " | operator confirmed NOT sent"
        job = db.scalar(select(ScheduledJob).where(ScheduledJob.idempotency_key == key))
        if job is not None:
            _release(job, utcnow(), "operator requested retry")
    audit.record(
        db,
        "send.unknown_resolved",
        f"Unknown send outcome resolved as {'sent' if was_sent else 'not sent'}",
        entity_type="recipient",
        entity_id=rec.id,
    )
    return rec
