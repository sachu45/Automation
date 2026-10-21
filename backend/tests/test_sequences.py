"""Scenarios 14-15 plus follow-up threading, max attempts, and DST-aware scheduling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db.base import utcnow
from app.models import CampaignRecipient, Conversation, ScheduledJob
from app.models.enums import JobStatus, RecipientStatus
from app.services import inbox_sync, schedule, unsubscribe
from app.services.accounts import get_account
from app.workers.jobs import run_once
from tests.conftest import build_active_campaign, make_account, make_lead

TWO_STEPS = [
    ("Hello {{first_name}}", "Hi {{first_name}},\n\nFirst note.\n\n{{signature}}", 0),
    ("ignored", "Hi {{first_name}}, following up.\n\n{{signature}}", 2),
]


def _reply(to_msg_id: str, from_addr: str, body: str, subject: str = "Re: Hello", extra: str = "") -> bytes:
    return (
        f"From: Ann <{from_addr}>\r\nTo: sam@sender.test\r\nSubject: {subject}\r\n"
        f"Message-ID: <reply-{abs(hash(body))}@acme.test>\r\nIn-Reply-To: {to_msg_id}\r\nReferences: {to_msg_id}\r\n"
        f"Date: Mon, 19 Oct 2026 10:00:00 +0000\r\n{extra}Content-Type: text/plain; charset=utf-8\r\n\r\n{body}\r\n"
    ).encode()


def _recipient(db, campaign_id):  # type: ignore[no-untyped-def]
    db.expire_all()
    return db.scalar(select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign_id))


def test_followup_is_threaded_and_scheduled(db, fake_mail) -> None:
    sender, _ = fake_mail
    acc = make_account(db, imap_enabled=False)
    c = build_active_campaign(db, [make_lead(db, "ann@acme.test", first_name="Ann")], acc, steps=TWO_STEPS)
    t0 = utcnow()
    run_once(now=t0)
    rec = _recipient(db, c.id)
    assert rec.steps_sent == 1 and rec.next_step == 2
    assert rec.next_send_at >= t0 + timedelta(days=2) - timedelta(seconds=5)
    run_once(now=t0 + timedelta(days=1))
    assert len(sender.sent) == 1  # not due yet
    run_once(now=rec.next_send_at + timedelta(seconds=1))
    assert len(sender.sent) == 2
    first, second = sender.sent
    assert second.subject == "Re: Hello Ann" and second.in_reply_to == first.message_id
    assert _recipient(db, c.id).status == RecipientStatus.COMPLETED


def test_sequence_stops_after_reply(db, fake_mail) -> None:  # 14
    sender, _ = fake_mail
    acc = make_account(db, imap_enabled=False)
    c = build_active_campaign(db, [make_lead(db, "ann@acme.test")], acc, steps=TWO_STEPS)
    t0 = utcnow()
    run_once(now=t0)
    first = sender.sent[0]
    res = inbox_sync.ingest_message(
        db, get_account(db, acc.id), _reply(first.message_id, "ann@acme.test", "Sounds interesting, tell me more")
    )
    db.commit()
    assert res == {"result": "imported", "label": "interested", "match": "header"}
    rec = _recipient(db, c.id)
    assert rec.status == RecipientStatus.REPLIED
    job2 = db.scalar(select(ScheduledJob).where(ScheduledJob.recipient_id == rec.id, ScheduledJob.step_number == 2))
    assert job2.status == JobStatus.CANCELLED
    run_once(now=t0 + timedelta(days=5))
    assert len(sender.sent) == 1
    conv = db.scalar(select(Conversation).where(Conversation.campaign_id == c.id))
    assert conv.has_inbound and conv.classification == "interested" and conv.needs_review


def test_out_of_office_does_not_stop_sequence_by_default(db, fake_mail) -> None:
    sender, _ = fake_mail
    acc = make_account(db, imap_enabled=False)
    c = build_active_campaign(db, [make_lead(db, "ann@acme.test")], acc, steps=TWO_STEPS)
    run_once()
    raw = _reply(
        sender.sent[0].message_id,
        "ann@acme.test",
        "I am out of the office until Monday.",
        subject="Automatic reply: Hello",
        extra="Auto-Submitted: auto-replied\r\n",
    )
    res = inbox_sync.ingest_message(db, get_account(db, acc.id), raw)
    db.commit()
    assert res["label"] == "out_of_office"
    assert _recipient(db, c.id).status == RecipientStatus.ACTIVE


def test_sequence_stops_after_opt_out_link(db, fake_mail, settings, api) -> None:  # 15, 20
    sender, _ = fake_mail
    settings.public_base_url = "https://outreach.example.test"
    try:
        acc = make_account(db, imap_enabled=False)
        c = build_active_campaign(db, [make_lead(db, "ann@acme.test")], acc, steps=TWO_STEPS)
        t0 = utcnow()
        run_once(now=t0)
        msg = sender.sent[0]
        header = msg.headers["List-Unsubscribe"]
        assert "https://outreach.example.test/u/" in header and msg.headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        token = header.split("/u/")[1].split(">")[0]
        assert "ann" not in token and "acme" not in token  # no personal data in the URL
        assert token in msg.text  # visible link in the footer too
        page = api.client.get(f"/u/{token}")
        assert page.status_code == 200 and "Yes, unsubscribe me" in page.text
        assert _recipient(db, c.id).status == RecipientStatus.ACTIVE  # GET must not change state
        done = api.client.post(f"/u/{token}")
        assert done.status_code == 200 and "unsubscribed" in done.text.lower()
        rec = _recipient(db, c.id)
        assert rec.status == RecipientStatus.UNSUBSCRIBED
        run_once(now=t0 + timedelta(days=5))
        assert len(sender.sent) == 1
        assert api.client.post("/u/not-a-valid-token").status_code == 404
        one_click = api.client.post(
            f"/u/{unsubscribe.make_token('other@x.test', None)}",
            content=b"List-Unsubscribe=One-Click",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        assert one_click.status_code == 200 and one_click.text == "ok"
        assert api.get("/api/suppressions/check", params={"email": "other@x.test"}).json()["record"]["source"] == "one_click"
    finally:
        settings.public_base_url = ""


def test_unsubscribe_reply_is_suppressed(db, fake_mail) -> None:
    sender, _ = fake_mail
    acc = make_account(db, imap_enabled=False)
    c = build_active_campaign(db, [make_lead(db, "ann@acme.test")], acc, steps=TWO_STEPS)
    run_once()
    inbox_sync.ingest_message(
        db, get_account(db, acc.id), _reply(sender.sent[0].message_id, "ann@acme.test", "Please remove me from your list.")
    )
    db.commit()
    from app.services import suppression

    assert suppression.is_suppressed(db, "ann@acme.test")
    assert _recipient(db, c.id).status in (RecipientStatus.REPLIED, RecipientStatus.UNSUBSCRIBED)


def test_max_emails_per_recipient(db, fake_mail) -> None:
    sender, _ = fake_mail
    acc = make_account(db, imap_enabled=False)
    c = build_active_campaign(db, [make_lead(db, "ann@acme.test")], acc, steps=TWO_STEPS, max_emails_per_recipient=1)
    run_once()
    run_once(now=utcnow() + timedelta(days=10))
    assert len(sender.sent) == 1
    assert _recipient(db, c.id).status == RecipientStatus.COMPLETED


def test_lead_in_multiple_lists_gets_one_email(db, fake_mail) -> None:
    from app.models import LeadList
    from app.services import campaigns as camp_svc
    from app.services import leads as lead_svc

    sender, _ = fake_mail
    acc = make_account(db, imap_enabled=False)
    lead = make_lead(db, "multi@x.test")
    c = build_active_campaign(db, [lead], acc)
    extra = LeadList(name="second")
    db.add(extra)
    db.flush()
    lead_svc.add_to_list(db, extra.id, [lead.id])
    db.commit()
    dup = camp_svc.duplicate(db, c.id)
    from app.models import CampaignSource

    dup.sources.append(CampaignSource(list_id=extra.id))
    db.commit()
    review = camp_svc.public_review(db, dup.id)
    assert review["duplicates_across_sources"] == 1 and review["candidates"] == 1
    run_once()
    assert len(sender.sent) == 1


def test_window_and_dst_arithmetic() -> None:
    ny = "America/New_York"
    # Friday 18:00 local -> next window is Monday 09:00 local
    fri = datetime(2026, 10, 16, 22, 0, tzinfo=UTC)  # 18:00 EDT
    nxt = schedule.next_window_start(fri, ny, "09:00", "17:00", [0, 1, 2, 3, 4])
    assert nxt.astimezone(ZoneInfo(ny)) == datetime(2026, 10, 19, 9, 0, tzinfo=ZoneInfo(ny))
    # Follow-up "1 day" across spring-forward keeps the local wall-clock time
    base = datetime(2026, 3, 7, 19, 0, tzinfo=UTC)  # 14:00 EST
    later = schedule.add_delay(base, ny, 1, 0)
    assert later.astimezone(ZoneInfo(ny)).hour == 14 and later == datetime(2026, 3, 8, 18, 0, tzinfo=UTC)
    # Window start inside the DST gap (02:30 on 2026-03-08 doesn't exist) shifts forward
    gap = schedule.local_to_utc(datetime(2026, 3, 8).date(), datetime.strptime("02:30", "%H:%M").time(), ZoneInfo(ny))
    assert gap.astimezone(ZoneInfo(ny)).hour == 3
    assert schedule.in_window(datetime(2026, 10, 19, 14, 0, tzinfo=UTC), ny, "09:00", "17:00", [0])  # Mon 10:00 EDT
    plan = schedule.estimate_schedule(25, fri, ny, "09:00", "17:00", [0, 1, 2, 3, 4], 10, 120)
    assert [p["emails"] for p in plan] == [10, 10, 5]
