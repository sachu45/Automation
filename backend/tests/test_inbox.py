"""Inbox sync: incremental cursors, dedupe, matching, bounce DSNs, manual correction."""

from __future__ import annotations

from sqlalchemy import func, select

from app.models import CampaignRecipient, Message, ProviderEvent, SyncState
from app.models.enums import RecipientStatus
from app.services import inbox_sync, suppression
from app.services.accounts import get_account
from app.workers.jobs import run_once
from tests.conftest import build_active_campaign, make_account, make_lead


def _dsn(original_id: str, rcpt: str, status: str = "5.1.1", action: str = "failed") -> bytes:
    return (
        "From: Mail Delivery System <MAILER-DAEMON@mx.sender.test>\r\nTo: sam@sender.test\r\n"
        "Subject: Undelivered Mail Returned to Sender\r\nMessage-ID: <dsn-1@mx.sender.test>\r\n"
        "Date: Mon, 19 Oct 2026 10:00:00 +0000\r\nMIME-Version: 1.0\r\n"
        'Content-Type: multipart/report; report-type=delivery-status; boundary="B"\r\n\r\n'
        "--B\r\nContent-Type: text/plain\r\n\r\nDelivery failed.\r\n"
        "--B\r\nContent-Type: message/delivery-status\r\n\r\nReporting-MTA: dns; mx.sender.test\r\n\r\n"
        f"Final-Recipient: rfc822; {rcpt}\r\nAction: {action}\r\nStatus: {status}\r\nDiagnostic-Code: smtp; 550 {status} no such user\r\n"
        "--B\r\nContent-Type: text/rfc822-headers\r\n\r\n"
        f"Message-ID: {original_id}\r\nSubject: Hello\r\n"
        "--B--\r\n"
    ).encode()


def test_sync_imports_replies_incrementally_and_dedupes(db, fake_mail) -> None:
    sender, reader = fake_mail
    acc = make_account(db)
    c = build_active_campaign(db, [make_lead(db, "ann@acme.test")], acc)
    run_once()
    sent = sender.sent[0]
    reader.add(
        (
            f"From: ann@acme.test\r\nTo: sam@sender.test\r\nSubject: Re: Hello\r\nMessage-ID: <r1@acme.test>\r\n"
            f"In-Reply-To: {sent.message_id}\r\nDate: Mon, 19 Oct 2026 10:00:00 +0000\r\n\r\nCan you send pricing?\r\n\r\nOn Mon, Sam wrote:\r\n> Hi Ann\r\n"
        ).encode()
    )
    reader.add(
        b"From: friend@personal.test\r\nTo: sam@sender.test\r\nSubject: Dinner?\r\nMessage-ID: <p1@personal.test>\r\n\r\nPrivate\r\n"
    )
    r1 = inbox_sync.sync_account(db, acc.id)
    assert r1 == {"fetched": 1, "imported": 1}
    assert reader.downloaded == [1]  # the unrelated personal message was never downloaded
    state = db.scalar(select(SyncState).where(SyncState.account_id == acc.id))
    assert state.last_uid == 2 and state.uidvalidity == 1  # cursor still advances past skipped mail
    assert inbox_sync.sync_account(db, acc.id) == {"fetched": 0}  # cursor advanced
    state.last_uid = 0
    db.commit()
    assert inbox_sync.sync_account(db, acc.id)["duplicate"] == 1  # re-reading is idempotent
    inbound = db.scalars(select(Message).where(Message.direction == "inbound")).all()
    assert len(inbound) == 1 and inbound[0].snippet == "Can you send pricing?"  # quoted history stripped
    assert db.scalar(select(func.count()).select_from(Message).where(Message.from_email == "friend@personal.test")) == 0
    rec = db.scalar(select(CampaignRecipient).where(CampaignRecipient.campaign_id == c.id))
    assert rec.status == RecipientStatus.COMPLETED or rec.status == RecipientStatus.REPLIED


def test_hard_bounce_dsn_suppresses_and_soft_does_not(db, fake_mail) -> None:
    sender, _ = fake_mail
    acc = make_account(db)
    build_active_campaign(db, [make_lead(db, "gone@acme.test"), make_lead(db, "full@acme.test")], acc)
    run_once()
    from datetime import timedelta

    from app.db.base import utcnow

    run_once(now=utcnow() + timedelta(seconds=30))
    by_to = {m.to_email: m for m in sender.sent}
    account = get_account(db, acc.id)
    res = inbox_sync.ingest_message(db, account, _dsn(by_to["gone@acme.test"].message_id, "gone@acme.test"))
    db.commit()
    assert res["result"] == "hard_bounce" and suppression.is_suppressed(db, "gone@acme.test")
    soft = _dsn(by_to["full@acme.test"].message_id, "full@acme.test", status="4.2.2", action="delayed").replace(b"dsn-1@", b"dsn-2@")
    res = inbox_sync.ingest_message(db, account, soft)
    db.commit()
    assert res["result"] == "soft_bounce" and not suppression.is_suppressed(db, "full@acme.test")
    assert {e.event_type for e in db.scalars(select(ProviderEvent))} == {"hard_bounce", "soft_bounce"}


def test_unstructured_bounce_is_uncertain(db, fake_mail) -> None:
    acc = make_account(db)
    make_lead(db, "who@acme.test")
    raw = (
        b"From: postmaster@mx.test\r\nTo: sam@sender.test\r\nSubject: Delivery Status Notification (Failure)\r\n"
        b"Message-ID: <x@mx.test>\r\n\r\nCould not deliver to who@acme.test\r\n"
    )
    res = inbox_sync.ingest_message(db, get_account(db, acc.id), raw)
    db.commit()
    assert res["result"] == "bounce_uncertain"
    assert not suppression.is_suppressed(db, "who@acme.test")  # never mislabel uncertain outcomes


def test_manual_classification_and_reassignment(api, db, fake_mail) -> None:
    sender, _ = fake_mail
    acc = make_account(db)
    lead = make_lead(db, "ann@acme.test")
    other = make_lead(db, "bob@acme.test")
    build_active_campaign(db, [lead], acc)
    run_once()
    inbox_sync.ingest_message(
        db,
        get_account(db, acc.id),
        (
            f"From: ann@acme.test\r\nSubject: Re: Hello\r\nMessage-ID: <r9@acme.test>\r\nIn-Reply-To: {sender.sent[0].message_id}\r\n\r\nHmm.\r\n"
        ).encode(),
    )
    db.commit()
    inbox = api.get("/api/inbox").json()
    assert inbox["total"] == 1 and inbox["items"][0]["classification"] == "unknown"
    cid = inbox["items"][0]["id"]
    r = api.post(f"/api/inbox/{cid}/classify", json={"label": "interested"}).json()
    assert r["classification"] == "interested" and r["classification_source"] == "manual" and r["needs_review"] is False
    r = api.post(f"/api/inbox/{cid}/reassign", json={"lead_id": other.id}).json()
    assert r["lead"]["email"] == "bob@acme.test"
    detail = api.get(f"/api/inbox/{cid}").json()
    assert any(m["match_method"] == "manual" for m in detail["messages"] if m["direction"] == "inbound")
    assert api.patch(f"/api/inbox/{cid}", json={"status": "handled", "notes": "called them"}).json()["status"] == "handled"
    # Replying needs explicit confirmation and goes through the worker (dry-run by default)
    assert api.post(f"/api/inbox/{cid}/reply", json={"body": "Thanks!"}).status_code == 422
    q = api.post(f"/api/inbox/{cid}/reply", json={"body": "Thanks!", "confirm": True}).json()
    assert q["queued"] and q["simulation"] is False  # this test enabled live mode


def test_future_dated_reply_is_clamped_to_now(db, fake_mail) -> None:
    from app.db.base import utcnow

    sender, _ = fake_mail
    acc = make_account(db)
    build_active_campaign(db, [make_lead(db, "ann@acme.test")], acc)
    run_once()
    raw = (
        f"From: ann@acme.test\r\nSubject: Re: Hello\r\nMessage-ID: <future@acme.test>\r\nIn-Reply-To: {sender.sent[0].message_id}\r\n"
        "Date: Fri, 01 Jan 2100 00:00:00 +0000\r\n\r\nThanks!\r\n"
    ).encode()
    inbox_sync.ingest_message(db, get_account(db, acc.id), raw)
    db.commit()
    msg = db.scalar(select(Message).where(Message.message_id_header == "<future@acme.test>"))
    assert msg.occurred_at <= utcnow()


class _FakeImapConn:
    """Mimics the imaplib return shapes Gmail produces for SEARCH and batched FETCH."""

    def __init__(self, messages: dict[int, bytes]):
        self.messages = messages
        self.commands: list[tuple[str, ...]] = []

    def select(self, folder, readonly=True):  # type: ignore[no-untyped-def]
        return "OK", [b"3"]

    def response(self, name):  # type: ignore[no-untyped-def]
        return name, [b"7"]

    def uid(self, command, *args):  # type: ignore[no-untyped-def]
        self.commands.append((command, *args))
        if command == "SEARCH":
            return "OK", [b" ".join(str(u).encode() for u in self.messages)]
        uids = [int(u) for u in args[0].split(",")]
        out = []
        for i, u in enumerate(uids, 1):
            raw = self.messages[u]
            if "HEADER" in args[1]:
                hdr = raw.split(b"\r\n\r\n", 1)[0]
                out += [(f"{i} (UID {u} RFC822.SIZE {len(raw)} BODY[HEADER] {{{len(hdr)}}}".encode(), hdr), b")"]
            else:
                out += [(f"{i} (UID {u} BODY[] {{{len(raw)}}}".encode(), raw), b")"]
        return "OK", out

    def logout(self):  # type: ignore[no-untyped-def]
        return "BYE", []


def test_imap_reader_scans_headers_and_downloads_only_wanted(monkeypatch) -> None:
    from datetime import date

    from app.providers.email.imap import ImapReader

    msgs = {
        11: b"From: a@x.test\r\nSubject: keep\r\n\r\nbody one",
        12: b"From: b@x.test\r\nSubject: skip\r\n\r\nbody two",
    }
    conn = _FakeImapConn(msgs)
    reader = ImapReader("imap.test", 993, "ssl", "me@test")
    monkeypatch.setattr(reader, "_connect", lambda: conn)
    res = reader.fetch_since("INBOX", None, 0, since=date(2026, 10, 1), want=lambda h: b"keep" in h)
    assert res.uidvalidity == 7 and res.max_uid == 12
    assert [m.uid for m in res.messages] == [11] and res.messages[0].raw.endswith(b"body one")
    assert conn.commands[0] == ("SEARCH", "SINCE 01-Oct-2026")  # first sync is bounded by date
    assert ("FETCH", "11,12", "(RFC822.SIZE BODY.PEEK[HEADER])") in conn.commands  # one batched header fetch
    assert ("FETCH", "12", "(BODY.PEEK[])") not in conn.commands  # unwanted body never downloaded
    conn.commands.clear()
    reader.fetch_since("INBOX", 7, 12)
    assert conn.commands[0] == ("SEARCH", "UID 13:*")  # later syncs are incremental by UID
