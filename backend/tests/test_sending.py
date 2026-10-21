"""Scenarios 11-13, 16-18: dry run, pre-send suppression, idempotency, failure handling, pause/cancel, limits."""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db.base import utcnow
from app.models import CampaignRecipient, Message, ScheduledJob, SendAttempt, Suppression
from app.models.enums import AttemptStatus, CampaignStatus, JobKind, JobStatus, RecipientStatus
from app.providers.email.base import OutgoingMessage, SendError
from app.services import accounts as account_svc
from app.services import campaigns as camp_svc
from app.services import sending, suppression
from app.workers.jobs import run_once
from tests.conftest import build_active_campaign, make_account, make_lead, set_config


def _attempts(db, **filters):  # type: ignore[no-untyped-def]
    db.expire_all()
    stmt = select(SendAttempt)
    for k, v in filters.items():
        stmt = stmt.where(getattr(SendAttempt, k) == v)
    return list(db.scalars(stmt.order_by(SendAttempt.id)))


def _run_spaced(n: int, start=None, step_seconds: int = 30):  # type: ignore[no-untyped-def]
    t = start or utcnow()
    for i in range(n):
        run_once(now=t + timedelta(seconds=step_seconds * i))
    return t + timedelta(seconds=step_seconds * n)


def test_dry_run_never_contacts_smtp(db) -> None:  # 11
    def explode(db, acc):  # type: ignore[no-untyped-def]
        raise AssertionError("SMTP provider must not be constructed in dry-run mode")

    account_svc.set_provider_factory(explode)
    acc = make_account(db, imap_enabled=False)
    leads = [make_lead(db, f"d{i}@x.test") for i in range(2)]
    c = build_active_campaign(db, leads, acc, live=False)
    assert c.dry_run is True
    _run_spaced(3)
    attempts = _attempts(db, campaign_id=c.id)
    assert len(attempts) == 2
    assert all(a.status == AttemptStatus.SIMULATED and a.is_simulation for a in attempts)
    msgs = list(db.scalars(select(Message).where(Message.is_simulation.is_(True))))
    assert len(msgs) == 2
    db.expire_all()
    assert all(lead.last_contacted_at is None for lead in leads)  # simulations don't count as contact


def test_suppressed_recipient_blocked_immediately_before_send(db, fake_mail) -> None:  # 12
    sender, _ = fake_mail
    acc = make_account(db, imap_enabled=False)
    lead = make_lead(db, "late@blocked.test")
    c = build_active_campaign(db, [lead], acc)
    # Suppression arrives out-of-band after scheduling (e.g. a domain-level entry added directly)
    db.add(Suppression(domain="blocked.test", reason="do_not_contact", source="test"))
    db.commit()
    run_once()
    assert sender.sent == []
    attempts = _attempts(db, campaign_id=c.id)
    assert [a.status for a in attempts] == [AttemptStatus.BLOCKED]
    rec = db.scalar(select(CampaignRecipient).where(CampaignRecipient.campaign_id == c.id))
    assert rec.status == RecipientStatus.SUPPRESSED


def test_suppress_cancels_scheduled_jobs(db, fake_mail) -> None:
    sender, _ = fake_mail
    acc = make_account(db, imap_enabled=False)
    lead = make_lead(db, "x@y.test")
    c = build_active_campaign(db, [lead], acc)
    suppression.suppress(db, email=lead.email, reason="unsubscribed", source="manual")
    db.commit()
    run_once()
    assert sender.sent == []
    job = db.scalar(select(ScheduledJob).where(ScheduledJob.campaign_id == c.id))
    assert job.status == JobStatus.CANCELLED


def test_live_send_contents_and_headers(db, fake_mail) -> None:
    sender, _ = fake_mail
    set_config(db, signature="Sam\nSender Co")
    acc = make_account(db, imap_enabled=False, reply_to="replies@sender.test")
    lead = make_lead(db, "ann@acme.test", first_name="Ann", company="Acme")
    build_active_campaign(db, [lead], acc)
    run_once()
    assert len(sender.sent) == 1
    m: OutgoingMessage = sender.sent[0]
    assert m.subject == "Hello Ann" and m.to_email == "ann@acme.test"
    assert "Quick note about Acme." in m.text and "Sam\nSender Co" in m.text
    assert "unsubscribe" in m.text.lower()  # opt-out footer appended
    assert "{{" not in m.text and m.reply_to == "replies@sender.test"
    assert m.headers["List-Unsubscribe"].startswith("<mailto:replies@sender.test")
    a = _attempts(db)[0]
    assert a.status == AttemptStatus.ACCEPTED and a.smtp_code == 250 and a.message_id_header == m.message_id


def test_duplicate_send_prevented_on_job_retry(db, fake_mail) -> None:  # 13
    sender, _ = fake_mail
    acc = make_account(db, imap_enabled=False)
    lead = make_lead(db, "once@x.test")
    c = build_active_campaign(db, [lead], acc)
    run_once()
    assert len(sender.sent) == 1
    job = db.scalar(select(ScheduledJob).where(ScheduledJob.campaign_id == c.id, ScheduledJob.step_number == 1))
    # Simulate the queue redelivering the same job (e.g. a worker restarted after sending)
    job.status = JobStatus.RUNNING
    job.locked_at = utcnow()
    db.commit()
    rec = db.get(CampaignRecipient, job.recipient_id)
    rec.status, rec.next_step = RecipientStatus.ACTIVE, 1  # even if recipient state was rolled back
    db.commit()
    assert sending.process_job(db, job) == "duplicate_prevented"
    assert len(sender.sent) == 1
    # A second job with the same idempotency key cannot even be created
    from sqlalchemy.exc import IntegrityError

    db.add(ScheduledJob(kind=JobKind.SEND_STEP, idempotency_key=job.idempotency_key, run_at=utcnow()))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_crash_mid_send_is_never_resent(db, fake_mail) -> None:
    sender, _ = fake_mail
    acc = make_account(db, imap_enabled=False)
    lead = make_lead(db, "crash@x.test")
    c = build_active_campaign(db, [lead], acc)
    job = db.scalar(select(ScheduledJob).where(ScheduledJob.campaign_id == c.id))
    # Worker committed the 'sending' attempt and then died before recording the outcome
    db.add(
        SendAttempt(
            idempotency_key=job.idempotency_key,
            attempt_no=1,
            job_id=job.id,
            campaign_id=c.id,
            recipient_id=job.recipient_id,
            lead_id=lead.id,
            account_id=acc.id,
            step_number=1,
            to_email=lead.email,
            status=AttemptStatus.SENDING,
            created_at=utcnow() - timedelta(hours=1),
        )
    )
    job.status, job.locked_at = JobStatus.RUNNING, utcnow() - timedelta(hours=1)
    db.commit()
    run_once()
    assert sender.sent == []
    db.expire_all()
    assert db.get(ScheduledJob, job.id).status == JobStatus.NEEDS_REVIEW
    rec = db.get(CampaignRecipient, job.recipient_id)
    assert rec.status == RecipientStatus.NEEDS_REVIEW
    assert _attempts(db)[0].status == AttemptStatus.UNKNOWN
    # Operator says it was NOT sent -> it may be retried exactly once
    sending.resolve_unknown(db, rec.id, was_sent=False)
    db.commit()
    run_once()
    assert len(sender.sent) == 1


def test_concurrent_workers_send_once(db, fake_mail) -> None:
    sender, _ = fake_mail
    acc = make_account(db, imap_enabled=False)
    build_active_campaign(db, [make_lead(db, "race@x.test")], acc)
    now = utcnow()
    threads = [threading.Thread(target=run_once, kwargs={"now": now}) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(sender.sent) == 1


def test_temporary_vs_permanent_failures(db, fake_mail) -> None:  # 16
    sender, _ = fake_mail
    acc = make_account(db, imap_enabled=False)
    temp, hard, perm = make_lead(db, "temp@x.test"), make_lead(db, "hard@x.test"), make_lead(db, "perm@x.test")
    c = build_active_campaign(db, [temp, hard, perm], acc)
    sender.errors = [
        SendError("421 try later", permanent=False, smtp_code=421),
        SendError("550 5.1.1 user unknown", permanent=True, smtp_code=550, hard_bounce=True),
        SendError("552 message rejected by policy", permanent=True, smtp_code=552),
    ]
    t = _run_spaced(2, step_seconds=30)  # 3rd job is spaced to the second tick; retry backoff is ~60s
    db.expire_all()
    recs = {r.lead_id: r for r in db.scalars(select(CampaignRecipient).where(CampaignRecipient.campaign_id == c.id))}
    assert recs[temp.id].status == RecipientStatus.ACTIVE  # will retry
    assert recs[hard.id].status == RecipientStatus.BOUNCED
    assert recs[perm.id].status == RecipientStatus.FAILED
    assert suppression.is_suppressed(db, "hard@x.test") and not suppression.is_suppressed(db, "perm@x.test")
    temp_job = db.scalar(select(ScheduledJob).where(ScheduledJob.recipient_id == recs[temp.id].id))
    assert temp_job.status == JobStatus.PENDING and temp_job.run_at > t - timedelta(seconds=60)
    # Retry later succeeds; permanent ones are never retried
    run_once(now=temp_job.run_at + timedelta(seconds=1))
    assert [m.to_email for m in sender.sent] == ["temp@x.test"]
    statuses = [a.status for a in _attempts(db, lead_id=temp.id)]
    assert statuses == [AttemptStatus.FAILED_TEMPORARY, AttemptStatus.ACCEPTED]
    run_once(now=temp_job.run_at + timedelta(days=2))
    assert len(sender.sent) == 1


def test_temporary_failures_are_bounded(db, fake_mail) -> None:
    sender, _ = fake_mail
    acc = make_account(db, imap_enabled=False)
    c = build_active_campaign(db, [make_lead(db, "flaky@x.test")], acc)
    sender.errors = [SendError("451 busy", permanent=False, smtp_code=451) for _ in range(10)]
    t = utcnow()
    for _ in range(8):
        run_once(now=t)
        t += timedelta(hours=7)  # beyond the max backoff
    job = db.scalar(select(ScheduledJob).where(ScheduledJob.campaign_id == c.id))
    db.refresh(job)
    assert job.status == JobStatus.FAILED and job.attempts == job.max_attempts == 5
    assert sender.sent == []


def test_auth_failure_disables_account_and_pauses(db, fake_mail) -> None:
    sender, _ = fake_mail
    acc = make_account(db, imap_enabled=False)
    c = build_active_campaign(db, [make_lead(db, "a@x.test")], acc)
    sender.errors = [SendError("535 auth failed", permanent=True, smtp_code=535, auth=True)]
    run_once()
    db.expire_all()
    assert db.get(type(acc), acc.id).status == "auth_failed"
    assert db.get(type(c), c.id).status == CampaignStatus.PAUSED
    rec = db.scalar(select(CampaignRecipient).where(CampaignRecipient.campaign_id == c.id))
    assert rec.status == RecipientStatus.ACTIVE  # recipient not penalised


def test_pause_resume_and_cancel(db, fake_mail) -> None:  # 17
    sender, _ = fake_mail
    acc = make_account(db, imap_enabled=False)
    leads = [make_lead(db, f"p{i}@x.test") for i in range(3)]
    c = build_active_campaign(db, leads, acc)
    camp_svc.pause(db, c.id)
    db.commit()
    _run_spaced(3)
    assert sender.sent == []
    camp_svc.resume(db, c.id)
    db.commit()
    run_once()
    assert len(sender.sent) == 1
    camp_svc.cancel(db, c.id, confirm=True)
    db.commit()
    _run_spaced(3, start=utcnow() + timedelta(minutes=5))
    assert len(sender.sent) == 1
    db.expire_all()
    pending = db.scalars(select(ScheduledJob).where(ScheduledJob.campaign_id == c.id, ScheduledJob.status == JobStatus.PENDING)).all()
    assert pending == []


def test_emergency_stop_halts_everything(db, fake_mail) -> None:
    sender, _ = fake_mail
    acc = make_account(db, imap_enabled=False)
    build_active_campaign(db, [make_lead(db, "e@x.test")], acc)
    from app.services.settings import set_emergency_stop

    set_emergency_stop(db, True)
    db.commit()
    run_once()
    assert sender.sent == []
    set_emergency_stop(db, False)
    db.commit()
    run_once(now=utcnow() + timedelta(minutes=6))
    assert len(sender.sent) == 1


def test_account_daily_and_spacing_limits(db, fake_mail) -> None:  # 18
    sender, _ = fake_mail
    acc = make_account(db, imap_enabled=False, daily_limit=2, hourly_limit=2, min_delay_seconds=60)
    leads = [make_lead(db, f"l{i}@x.test") for i in range(5)]
    c = build_active_campaign(db, leads, acc)
    t0 = utcnow()
    run_once(now=t0)
    run_once(now=t0 + timedelta(seconds=20))  # within min delay -> spaced
    assert len(sender.sent) == 1
    for i in range(1, 8):
        run_once(now=t0 + timedelta(seconds=61 * i))
    assert len(sender.sent) == 2  # daily limit reached
    held = db.scalars(select(ScheduledJob).where(ScheduledJob.campaign_id == c.id, ScheduledJob.status == JobStatus.PENDING)).all()
    assert len(held) == 3 and all(j.run_at >= t0 + timedelta(hours=23) for j in held)
    assert "rate limited" in (held[0].last_error or "")


def test_global_daily_limit(db, fake_mail) -> None:
    sender, _ = fake_mail
    acc = make_account(db, imap_enabled=False)
    leads = [make_lead(db, f"g{i}@x.test") for i in range(3)]
    build_active_campaign(db, leads, acc)
    from app.services import settings as settings_svc

    settings_svc.update_config(db, {"daily_sending_limit": 1})
    db.commit()
    _run_spaced(4, step_seconds=30)
    assert len(sender.sent) == 1


def test_live_activation_requires_verified_account(db, fake_mail) -> None:
    acc = make_account(db, connected=False, imap_enabled=False)
    with pytest.raises(Exception) as e:
        build_active_campaign(db, [make_lead(db, "v@x.test")], acc)
    assert any("connection test" in p for p in e.value.details)


def test_smtp_adapter_against_local_mock_server() -> None:
    """Exercises the real smtplib adapter against an in-process SMTP server on localhost."""
    from aiosmtpd.controller import Controller

    from app.providers.email.smtp import SmtpSender

    received = []

    class Handler:
        async def handle_RCPT(self, server, session, envelope, address, rcpt_options):  # noqa: N802
            if address.startswith("unknown@"):
                return "550 5.1.1 User unknown"
            envelope.rcpt_tos.append(address)
            return "250 OK"

        async def handle_DATA(self, server, session, envelope):  # noqa: N802
            received.append(envelope.content.decode())
            return "250 Message accepted"

    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    ctl = Controller(Handler(), hostname="127.0.0.1", port=port)
    ctl.start()
    try:
        s = SmtpSender("127.0.0.1", port, "none", None, None, timeout=10)
        msg = OutgoingMessage(
            from_email="sam@sender.test",
            from_name="Sam",
            to_email="ann@acme.test",
            subject="Hi",
            text="Hello",
            message_id="<abc@sender.test>",
            headers={"List-Unsubscribe": "<mailto:x@y.test>"},
        )
        assert s.send(msg).accepted
        assert "Message-ID: <abc@sender.test>" in received[0] and "List-Unsubscribe" in received[0]
        msg.to_email = "unknown@acme.test"
        with pytest.raises(SendError) as e:
            s.send(msg)
        assert e.value.permanent and e.value.hard_bounce and e.value.smtp_code == 550
    finally:
        ctl.stop()
