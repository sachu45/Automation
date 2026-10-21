"""Campaign lifecycle: draft -> ready_for_review -> approved -> scheduled/active -> paused -> completed/cancelled."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.errors import NotFound, ValidationFailed
from app.db.base import utcnow
from app.models import (
    Campaign,
    CampaignRecipient,
    CampaignSource,
    CampaignStep,
    EmailAccount,
    Lead,
    LeadList,
    ListMembership,
    ScheduledJob,
    Segment,
    SendAttempt,
    Sequence,
)
from app.models.enums import (
    BLOCKED_LEAD_STATUSES,
    AccountStatus,
    CampaignStatus,
    JobKind,
    JobStatus,
    RecipientStatus,
)
from app.schemas.campaigns import CampaignIn, CampaignOperationalUpdate, CampaignOut, RecipientOut
from app.services import audit, schedule, suppression
from app.services import templates as template_svc
from app.services.emails import email_domain, is_valid
from app.services.segments import segment_condition
from app.services.sending import send_idempotency_key
from app.services.settings import get_config

EDITABLE_STATES = {CampaignStatus.DRAFT, CampaignStatus.READY_FOR_REVIEW, CampaignStatus.APPROVED}
RUNNING_STATES = {CampaignStatus.SCHEDULED, CampaignStatus.ACTIVE}


def get_campaign(db: Session, campaign_id: int) -> Campaign:
    c = db.get(Campaign, campaign_id)
    if c is None:
        raise NotFound("Campaign not found")
    return c


def recipient_counts(db: Session, campaign_id: int) -> dict[str, int]:
    rows = db.execute(
        select(CampaignRecipient.status, func.count())
        .where(CampaignRecipient.campaign_id == campaign_id)
        .group_by(CampaignRecipient.status)
    ).all()
    return {status: n for status, n in rows}


def to_out(db: Session, c: Campaign) -> CampaignOut:
    out = CampaignOut.model_validate(c)
    out.list_ids = [s.list_id for s in c.sources if s.list_id]
    out.segment_ids = [s.segment_id for s in c.sources if s.segment_id]
    out.recipient_counts = recipient_counts(db, c.id)
    if c.steps:
        out.step_count = len(c.steps)
    elif c.sequence_id:
        seq = db.get(Sequence, c.sequence_id)
        out.step_count = len(seq.steps) if seq else 0
    return out


def _apply(db: Session, c: Campaign, data: CampaignIn) -> None:
    if data.email_account_id is not None and db.get(EmailAccount, data.email_account_id) is None:
        raise NotFound("Sending account not found")
    if data.sequence_id is not None and db.get(Sequence, data.sequence_id) is None:
        raise NotFound("Sequence not found")
    for lid in data.list_ids:
        if db.get(LeadList, lid) is None:
            raise NotFound(f"List {lid} not found")
    for sid in data.segment_ids:
        if db.get(Segment, sid) is None:
            raise NotFound(f"Segment {sid} not found")
    for field in [
        "name",
        "description",
        "email_account_id",
        "sequence_id",
        "timezone",
        "window_start",
        "window_end",
        "send_days",
        "daily_limit",
        "min_delay_seconds",
        "start_at",
        "end_at",
        "max_emails_per_recipient",
    ]:
        setattr(c, field, getattr(data, field))
    c.exclusion_rules = data.exclusion_rules.model_dump()
    c.sources.clear()
    db.flush()
    for lid in sorted(set(data.list_ids)):
        c.sources.append(CampaignSource(list_id=lid))
    for sid in sorted(set(data.segment_ids)):
        c.sources.append(CampaignSource(segment_id=sid))


def create_campaign(db: Session, data: CampaignIn) -> Campaign:
    c = Campaign(status=CampaignStatus.DRAFT, name=data.name)
    db.add(c)
    _apply(db, c, data)
    db.flush()
    audit.record(db, "campaign.created", f"Campaign '{c.name}' created", entity_type="campaign", entity_id=c.id)
    return c


def update_campaign(db: Session, campaign_id: int, data: CampaignIn) -> Campaign:
    c = get_campaign(db, campaign_id)
    if c.status not in EDITABLE_STATES:
        raise ValidationFailed("Only draft, in-review, or approved (not yet active) campaigns can be fully edited")
    if c.status != CampaignStatus.DRAFT:
        _reset_to_draft(db, c, "edited after review/approval")
    _apply(db, c, data)
    audit.record(db, "campaign.updated", f"Campaign '{c.name}' updated", entity_type="campaign", entity_id=c.id)
    return c


def operational_update(db: Session, campaign_id: int, data: CampaignOperationalUpdate) -> Campaign:
    c = get_campaign(db, campaign_id)
    if c.status in (CampaignStatus.COMPLETED, CampaignStatus.CANCELLED):
        raise ValidationFailed("Finished campaigns cannot be edited")
    fields = data.model_fields_set
    for f in fields:
        v = getattr(data, f)
        if v is None and f not in ("description", "end_at"):
            continue
        setattr(c, f, v)
    merged = CampaignIn.model_validate(
        {
            **{
                k: getattr(c, k)
                for k in [
                    "name",
                    "timezone",
                    "window_start",
                    "window_end",
                    "send_days",
                    "daily_limit",
                    "min_delay_seconds",
                    "start_at",
                    "end_at",
                ]
            }
        }
    )
    c.send_days = merged.send_days
    audit.record(
        db,
        "campaign.operational_update",
        f"Campaign '{c.name}' settings changed",
        entity_type="campaign",
        entity_id=c.id,
        details={"fields": sorted(fields)},
    )
    return c


def _reset_to_draft(db: Session, c: Campaign, why: str) -> None:
    c.status = CampaignStatus.DRAFT
    c.approved_at = None
    c.approval_summary = None
    c.steps.clear()
    db.flush()
    for r in db.scalars(select(CampaignRecipient).where(CampaignRecipient.campaign_id == c.id)):
        db.delete(r)
    audit.record(db, "campaign.reset_to_draft", f"Campaign '{c.name}' returned to draft ({why})", entity_type="campaign", entity_id=c.id)


def delete_campaign(db: Session, campaign_id: int) -> None:
    c = get_campaign(db, campaign_id)
    if c.status not in (CampaignStatus.DRAFT, CampaignStatus.READY_FOR_REVIEW, CampaignStatus.APPROVED):
        raise ValidationFailed("Only campaigns that never started can be deleted; cancel it instead")
    audit.record(db, "campaign.deleted", f"Campaign '{c.name}' deleted", entity_type="campaign", entity_id=c.id)
    db.delete(c)


def duplicate(db: Session, campaign_id: int) -> Campaign:
    src = get_campaign(db, campaign_id)
    data = CampaignIn(
        name=f"{src.name} (copy)"[:200],
        description=src.description,
        email_account_id=src.email_account_id,
        sequence_id=src.sequence_id,
        list_ids=[s.list_id for s in src.sources if s.list_id],
        segment_ids=[s.segment_id for s in src.sources if s.segment_id],
        timezone=src.timezone,
        window_start=src.window_start,
        window_end=src.window_end,
        send_days=src.send_days,
        daily_limit=src.daily_limit,
        min_delay_seconds=src.min_delay_seconds,
        start_at=None,
        end_at=None,
        max_emails_per_recipient=src.max_emails_per_recipient,
        exclusion_rules=src.exclusion_rules or {},
    )
    return create_campaign(db, data)


# --- Review -----------------------------------------------------------------------------------


def _source_lead_ids(db: Session, c: Campaign) -> tuple[list[int], int]:
    """Union of leads across sources; returns (unique ids, duplicate-membership count)."""
    all_ids: list[int] = []
    for src in c.sources:
        if src.list_id:
            all_ids.extend(db.scalars(select(ListMembership.lead_id).where(ListMembership.list_id == src.list_id)))
        elif src.segment_id:
            seg = db.get(Segment, src.segment_id)
            if seg:
                all_ids.extend(db.scalars(select(Lead.id).where(segment_condition(seg.match, seg.rules))))
    unique = list(dict.fromkeys(all_ids))
    return unique, len(all_ids) - len(unique)


def _steps_preview(db: Session, c: Campaign) -> list[dict[str, Any]]:
    if c.steps:
        out = []
        for s in c.steps:
            from app.models import TemplateVersion

            v = db.get(TemplateVersion, s.template_version_id)
            out.append(
                {
                    "step_number": s.step_number,
                    "template_id": v.template_id if v else None,
                    "template_version_id": s.template_version_id,
                    "version": v.version if v else None,
                    "subject": v.subject if v else None,
                    "delay_days": s.delay_days,
                    "delay_hours": s.delay_hours,
                    "send_as_reply": s.send_as_reply,
                }
            )
        return out
    seq = db.get(Sequence, c.sequence_id) if c.sequence_id else None
    if seq is None:
        return []
    out = []
    for st in seq.steps:
        v = template_svc.current_version(db, st.template_id)
        out.append(
            {
                "step_number": st.position,
                "template_id": st.template_id,
                "template_version_id": v.id if v else None,
                "template_name": st.template.name,
                "version": v.version if v else None,
                "subject": v.subject if v else None,
                "delay_days": st.delay_days,
                "delay_hours": st.delay_hours,
                "send_as_reply": st.send_as_reply,
            }
        )
    return out


def review(db: Session, campaign_id: int, sample_limit: int = 25) -> dict[str, Any]:
    c = get_campaign(db, campaign_id)
    cfg = get_config(db)
    problems: list[str] = []
    warnings: list[str] = []
    steps = _steps_preview(db, c)
    if not steps:
        problems.append("No sequence selected (a sequence with at least one step is required)")
    account = db.get(EmailAccount, c.email_account_id) if c.email_account_id else None
    if account is None:
        problems.append("No sending account selected")

    lead_ids, dup_count = _source_lead_ids(db, c)
    if not c.sources:
        problems.append("No target lists or segments selected")
    leads = list(db.scalars(select(Lead).where(Lead.id.in_(lead_ids)))) if lead_ids else []
    suppressed = suppression.suppressed_set(db, [lead.email for lead in leads])
    rules = c.exclusion_rules or {}
    excl_domains = set(rules.get("exclude_domains") or [])
    within = rules.get("skip_if_contacted_within_days")
    cutoff = utcnow() - timedelta(days=int(within)) if within else None
    other_active: set[int] = set()
    if rules.get("exclude_active_in_other_campaigns", True) and lead_ids:
        other_active = set(
            db.scalars(
                select(CampaignRecipient.lead_id)
                .join(Campaign, Campaign.id == CampaignRecipient.campaign_id)
                .where(
                    Campaign.id != c.id,
                    Campaign.status.in_([CampaignStatus.ACTIVE, CampaignStatus.SCHEDULED, CampaignStatus.PAUSED, CampaignStatus.APPROVED]),
                    CampaignRecipient.status.in_([RecipientStatus.ACTIVE, RecipientStatus.PENDING]),
                )
            )
        )
    from app.models import TemplateVersion

    versions = [db.get(TemplateVersion, s["template_version_id"]) for s in steps if s.get("template_version_id")]
    excluded: dict[str, int] = {
        "invalid_email": 0,
        "suppressed": 0,
        "blocked_status": 0,
        "excluded_domain": 0,
        "recently_contacted": 0,
        "in_other_active_campaign": 0,
        "missing_fields": 0,
    }
    missing_counter: dict[str, int] = {}
    missing_samples: list[dict[str, Any]] = []
    eligible: list[Lead] = []
    for lead in leads:
        if not is_valid(lead.email):
            excluded["invalid_email"] += 1
        elif lead.email in suppressed:
            excluded["suppressed"] += 1
        elif lead.status in BLOCKED_LEAD_STATUSES:
            excluded["blocked_status"] += 1
        elif email_domain(lead.email) in excl_domains:
            excluded["excluded_domain"] += 1
        elif cutoff and lead.last_contacted_at and lead.last_contacted_at >= cutoff:
            excluded["recently_contacted"] += 1
        elif lead.id in other_active:
            excluded["in_other_active_campaign"] += 1
        else:
            missing: list[str] = []
            if account is not None:
                for v in versions:
                    if v is not None:
                        missing.extend(m for m in template_svc.missing_for_lead(db, v, lead, account) if m not in missing)
            if missing:
                excluded["missing_fields"] += 1
                for m in missing:
                    missing_counter[m] = missing_counter.get(m, 0) + 1
                if len(missing_samples) < sample_limit:
                    missing_samples.append({"lead_id": lead.id, "email": lead.email, "missing": missing})
            else:
                eligible.append(lead)
    if excluded["missing_fields"]:
        warnings.append(f"{excluded['missing_fields']} lead(s) are missing personalization fields and will be excluded")
    acct_info = None
    if account is not None:
        live_ready = account.status == AccountStatus.CONNECTED and account.sending_enabled
        acct_info = {
            "id": account.id,
            "email": account.email,
            "status": account.status,
            "sending_enabled": account.sending_enabled,
            "live_ready": live_ready,
            "daily_limit": account.daily_limit,
            "hourly_limit": account.hourly_limit,
        }
        if not cfg.dry_run and not live_ready:
            problems.append("Live sending is enabled but the account has not passed a connection test or sending is disabled")
        elif cfg.dry_run and not live_ready:
            warnings.append("Account is not verified — fine for dry run, but required before live sending")
    if not eligible and c.sources:
        problems.append("No eligible recipients")
    if cfg.emergency_stop:
        warnings.append("Emergency stop is engaged: nothing will send until it is released")
    effective_daily = min(c.daily_limit, account.daily_limit if account else c.daily_limit, cfg.daily_sending_limit)
    min_delay = max(c.min_delay_seconds, account.min_delay_seconds if account else 0, cfg.min_delay_seconds)
    start = max(utcnow(), c.start_at) if c.start_at else utcnow()
    plan = schedule.estimate_schedule(
        len(eligible), start, c.timezone, c.window_start, c.window_end, c.send_days, effective_daily, min_delay
    )
    return {
        "campaign_id": c.id,
        "status": c.status,
        "mode": "dry_run" if cfg.dry_run else "live",
        "candidates": len(leads),
        "duplicates_across_sources": dup_count,
        "eligible": len(eligible),
        "eligible_sample": [{"lead_id": lead.id, "email": lead.email} for lead in eligible[:sample_limit]],
        "excluded": excluded,
        "missing_fields": [{"variable": k, "count": v} for k, v in sorted(missing_counter.items(), key=lambda x: -x[1])],
        "missing_samples": missing_samples,
        "account": acct_info,
        "steps": steps,
        "limits": {
            "campaign_daily": c.daily_limit,
            "account_daily": account.daily_limit if account else None,
            "account_hourly": account.hourly_limit if account else None,
            "global_daily": cfg.daily_sending_limit,
            "effective_daily": effective_daily,
            "min_delay_seconds": min_delay,
            "max_emails_per_recipient": c.max_emails_per_recipient,
        },
        "window": {"timezone": c.timezone, "start": c.window_start, "end": c.window_end, "days": c.send_days},
        "estimated_schedule": plan,
        "estimated_initial_send_days": len(plan),
        "problems": problems,
        "warnings": warnings,
        "_eligible_ids": [lead.id for lead in eligible],
    }


def public_review(db: Session, campaign_id: int) -> dict[str, Any]:
    return {k: v for k, v in review(db, campaign_id).items() if not k.startswith("_")}


# --- Transitions -------------------------------------------------------------------------------


def submit_for_review(db: Session, campaign_id: int) -> Campaign:
    c = get_campaign(db, campaign_id)
    if c.status != CampaignStatus.DRAFT:
        raise ValidationFailed(f"Cannot submit a campaign in status {c.status}")
    if not c.sequence_id or not c.email_account_id or not c.sources:
        raise ValidationFailed("Select a sequence, a sending account, and at least one list or segment first")
    c.status = CampaignStatus.READY_FOR_REVIEW
    audit.record(db, "campaign.submitted", f"Campaign '{c.name}' ready for review", entity_type="campaign", entity_id=c.id)
    return c


def approve(db: Session, campaign_id: int, confirm: bool) -> Campaign:
    c = get_campaign(db, campaign_id)
    if c.status != CampaignStatus.READY_FOR_REVIEW:
        raise ValidationFailed("Only campaigns that are ready for review can be approved")
    if not confirm:
        raise ValidationFailed("Approval requires explicit confirmation", code="confirmation_required")
    r = review(db, campaign_id)
    if r["problems"]:
        raise ValidationFailed("Campaign has blocking problems", details=r["problems"])
    # Freeze the sequence, pinned to the exact template versions reviewed
    c.steps.clear()
    db.flush()
    for s in r["steps"]:
        c.steps.append(
            CampaignStep(
                step_number=s["step_number"],
                template_version_id=s["template_version_id"],
                delay_days=s["delay_days"],
                delay_hours=s["delay_hours"],
                send_as_reply=s["send_as_reply"],
            )
        )
    for lead_id in r["_eligible_ids"]:
        db.add(CampaignRecipient(campaign_id=c.id, lead_id=lead_id, status=RecipientStatus.PENDING))
    c.status = CampaignStatus.APPROVED
    c.approved_at = utcnow()
    c.approval_summary = {k: v for k, v in r.items() if not k.startswith("_") and k not in ("eligible_sample", "missing_samples")}
    audit.record(
        db,
        "campaign.approved",
        f"Campaign '{c.name}' approved for {r['eligible']} recipient(s)",
        entity_type="campaign",
        entity_id=c.id,
        details={"eligible": r["eligible"], "excluded": r["excluded"]},
    )
    return c


def activate(db: Session, campaign_id: int, confirm: bool, confirm_name: str | None) -> Campaign:
    c = get_campaign(db, campaign_id)
    if c.status != CampaignStatus.APPROVED:
        raise ValidationFailed("Only approved campaigns can be activated")
    if not confirm or (confirm_name or "").strip() != c.name.strip():
        raise ValidationFailed("Type the campaign name to confirm activation", code="confirmation_required")
    cfg = get_config(db)
    account = db.get(EmailAccount, c.email_account_id) if c.email_account_id else None
    if account is None:
        raise ValidationFailed("Campaign has no sending account")
    if not cfg.dry_run and not (account.status == AccountStatus.CONNECTED and account.sending_enabled):
        raise ValidationFailed("Live sending requires a connected account with sending enabled")
    c.dry_run = cfg.dry_run
    now = utcnow()
    start = max(now, c.start_at) if c.start_at else now
    first_slot = schedule.next_window_start(start, c.timezone, c.window_start, c.window_end, c.send_days)
    recipients = list(
        db.scalars(
            select(CampaignRecipient).where(CampaignRecipient.campaign_id == c.id, CampaignRecipient.status == RecipientStatus.PENDING)
        )
    )
    for rec in recipients:
        rec.status = RecipientStatus.ACTIVE
        rec.next_step = 1
        rec.next_send_at = first_slot
        key = send_idempotency_key(rec.id, 1, c.dry_run)
        if db.scalar(select(ScheduledJob.id).where(ScheduledJob.idempotency_key == key)) is None:
            db.add(
                ScheduledJob(
                    kind=JobKind.SEND_STEP,
                    idempotency_key=key,
                    run_at=first_slot,
                    campaign_id=c.id,
                    recipient_id=rec.id,
                    account_id=c.email_account_id,
                    step_number=1,
                )
            )
    c.status = CampaignStatus.SCHEDULED if (c.start_at and c.start_at > now) else CampaignStatus.ACTIVE
    c.activated_at = now
    audit.record(
        db,
        "campaign.activated",
        f"Campaign '{c.name}' activated in {'DRY-RUN' if c.dry_run else 'LIVE'} mode for {len(recipients)} recipient(s)",
        entity_type="campaign",
        entity_id=c.id,
        details={"dry_run": c.dry_run},
    )
    return c


def pause(db: Session, campaign_id: int) -> Campaign:
    c = get_campaign(db, campaign_id)
    if c.status not in RUNNING_STATES:
        raise ValidationFailed("Only scheduled or active campaigns can be paused")
    c.status = CampaignStatus.PAUSED
    c.paused_at = utcnow()
    audit.record(db, "campaign.paused", f"Campaign '{c.name}' paused", entity_type="campaign", entity_id=c.id)
    return c


def resume(db: Session, campaign_id: int) -> Campaign:
    c = get_campaign(db, campaign_id)
    if c.status != CampaignStatus.PAUSED:
        raise ValidationFailed("Only paused campaigns can be resumed")
    if not c.dry_run:
        account = db.get(EmailAccount, c.email_account_id) if c.email_account_id else None
        if account is None or account.status != AccountStatus.CONNECTED or not account.sending_enabled:
            raise ValidationFailed("The sending account must be connected with sending enabled before resuming")
    c.status = CampaignStatus.ACTIVE
    c.paused_at = None
    audit.record(db, "campaign.resumed", f"Campaign '{c.name}' resumed", entity_type="campaign", entity_id=c.id)
    return c


def cancel(db: Session, campaign_id: int, confirm: bool) -> Campaign:
    c = get_campaign(db, campaign_id)
    if c.status in (CampaignStatus.COMPLETED, CampaignStatus.CANCELLED):
        raise ValidationFailed(f"Campaign already {c.status}")
    if not confirm:
        raise ValidationFailed("Cancellation requires confirmation", code="confirmation_required")
    c.status = CampaignStatus.CANCELLED
    c.cancelled_at = utcnow()
    db.execute(
        update(ScheduledJob)
        .where(ScheduledJob.campaign_id == c.id, ScheduledJob.status == JobStatus.PENDING)
        .values(status=JobStatus.CANCELLED, last_error="campaign cancelled", finished_at=utcnow())
    )
    db.execute(
        update(CampaignRecipient)
        .where(CampaignRecipient.campaign_id == c.id, CampaignRecipient.status.in_([RecipientStatus.ACTIVE, RecipientStatus.PENDING]))
        .values(status=RecipientStatus.STOPPED, stop_reason="campaign_cancelled", next_send_at=None)
    )
    audit.record(db, "campaign.cancelled", f"Campaign '{c.name}' cancelled", entity_type="campaign", entity_id=c.id)
    return c


def stop_recipient(db: Session, campaign_id: int, recipient_id: int) -> CampaignRecipient:
    rec = db.get(CampaignRecipient, recipient_id)
    if rec is None or rec.campaign_id != campaign_id:
        raise NotFound("Recipient not found")
    from app.services.inbox_sync import stop_recipient_for_reply

    stop_recipient_for_reply(db, rec, "manual", RecipientStatus.STOPPED)
    audit.record(db, "campaign.recipient_stopped", f"Recipient {recipient_id} stopped manually", entity_type="recipient", entity_id=rec.id)
    return rec


def list_recipients(db: Session, campaign_id: int, status: str | None, page: int, page_size: int) -> tuple[list[RecipientOut], int]:
    get_campaign(db, campaign_id)
    stmt = (
        select(CampaignRecipient, Lead).join(Lead, Lead.id == CampaignRecipient.lead_id).where(CampaignRecipient.campaign_id == campaign_id)
    )
    if status:
        stmt = stmt.where(CampaignRecipient.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(CampaignRecipient.id).offset((page - 1) * page_size).limit(page_size)).all()
    return [
        RecipientOut(
            id=r.id,
            lead_id=lead.id,
            email=lead.email,
            name=lead.full_name,
            company=lead.company.name if lead.company else None,
            status=r.status,
            steps_sent=r.steps_sent,
            next_step=r.next_step,
            next_send_at=r.next_send_at,
            last_sent_at=r.last_sent_at,
            stop_reason=r.stop_reason,
        )
        for r, lead in rows
    ], total


def attempt_counts(db: Session, campaign_id: int) -> dict[str, int]:
    rows = db.execute(
        select(SendAttempt.status, func.count()).where(SendAttempt.campaign_id == campaign_id).group_by(SendAttempt.status)
    ).all()
    return {s: n for s, n in rows}
