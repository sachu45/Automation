"""Test fixtures. Every test gets a fresh, Alembic-migrated SQLite database and fake
email providers — no test can contact a real SMTP/IMAP server or AI API."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_TMP = Path(tempfile.mkdtemp(prefix="outreach-tests-"))
os.environ.update(
    {
        "APP_ENV": "test",
        "DATA_DIR": str(_TMP),
        "SECRET_KEY": "test-secret-key-for-unit-tests-only-0123456789",
        "ENCRYPTION_KEY": "Z3Vlc3NtZW5vdHRoaXNpc2F0ZXN0a2V5MDEyMzQ1Njc=",
        "AI_PROVIDER": "template",
        "AI_API_KEY": "",
        "PUBLIC_BASE_URL": "",
        "ADMIN_PASSWORD": "",
        "LOG_LEVEL": "WARNING",
    }
)

import email_validator  # noqa: E402
import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from alembic import command  # noqa: E402
from app.core.config import BACKEND_DIR, get_settings  # noqa: E402
from app.core.ratelimit import limiter  # noqa: E402
from app.db.session import SessionLocal, make_engine, set_engine  # noqa: E402
from app.models import EmailAccount, Lead  # noqa: E402
from app.models.enums import AccountStatus  # noqa: E402
from app.providers.email.base import FetchedMessage, FetchResult, OutgoingMessage, SendError, SendResult  # noqa: E402
from app.services import accounts as account_svc  # noqa: E402
from app.services import ai as ai_svc  # noqa: E402

TEMPLATE_DB = _TMP / "template.db"
email_validator.TEST_ENVIRONMENT = True  # allow reserved *.test domains in fixtures


def run_migrations(url: str) -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["skip_logging"] = True
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session", autouse=True)
def _migrated_template() -> Iterator[None]:
    run_migrations(f"sqlite:///{TEMPLATE_DB.as_posix()}")
    yield
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture(autouse=True)
def db_engine(tmp_path: Path):  # type: ignore[no-untyped-def]
    path = tmp_path / "test.db"
    shutil.copy(TEMPLATE_DB, path)
    engine = make_engine(f"sqlite:///{path.as_posix()}")
    set_engine(engine)
    limiter.reset()
    yield engine
    account_svc.set_provider_factory(None)
    ai_svc.set_provider_override(None)
    engine.dispose()


@pytest.fixture
def db() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


# --- Fake email providers -------------------------------------------------------------------


class FakeSender:
    def __init__(self) -> None:
        self.sent: list[OutgoingMessage] = []
        self.errors: list[SendError] = []  # raised in order, one per send call
        self.test_calls = 0

    def send(self, message: OutgoingMessage) -> SendResult:
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append(message)
        return SendResult(accepted=True, smtp_code=250, response="accepted")

    def test(self) -> str:
        self.test_calls += 1
        return "fake ok"


class FakeReader:
    def __init__(self) -> None:
        self.messages: list[FetchedMessage] = []
        self.uidvalidity = 1

    def add(self, raw: bytes) -> None:
        self.messages.append(FetchedMessage(uid=len(self.messages) + 1, raw=raw))

    def test(self) -> str:
        return "fake imap ok"

    def fetch_since(self, folder, uidvalidity, last_uid, limit=200, since=None, want=None) -> FetchResult:  # type: ignore[no-untyped-def]
        scanned = [m for m in self.messages if m.uid > last_uid][:limit]
        self.downloaded = [m.uid for m in scanned if want is None or want(m.raw.split(b"\r\n\r\n", 1)[0])]
        return FetchResult(
            uidvalidity=self.uidvalidity,
            messages=[m for m in scanned if m.uid in self.downloaded],
            max_uid=max((m.uid for m in scanned), default=last_uid),
        )


@pytest.fixture
def fake_mail() -> tuple[FakeSender, FakeReader]:
    sender, reader = FakeSender(), FakeReader()
    account_svc.set_provider_factory(lambda db, acc: (sender, reader))
    return sender, reader


# --- API client -------------------------------------------------------------------------------


class AuthedClient:
    def __init__(self, client: TestClient, csrf: str):
        self.client = client
        self.csrf = csrf

    def _h(self, kw: dict[str, Any]) -> dict[str, Any]:
        headers = dict(kw.pop("headers", {}) or {})
        headers["X-CSRF-Token"] = self.csrf
        kw["headers"] = headers
        return kw

    def get(self, url: str, **kw: Any):  # type: ignore[no-untyped-def]
        return self.client.get(url, **kw)

    def post(self, url: str, **kw: Any):  # type: ignore[no-untyped-def]
        return self.client.post(url, **self._h(kw))

    def put(self, url: str, **kw: Any):  # type: ignore[no-untyped-def]
        return self.client.put(url, **self._h(kw))

    def patch(self, url: str, **kw: Any):  # type: ignore[no-untyped-def]
        return self.client.patch(url, **self._h(kw))

    def delete(self, url: str, **kw: Any):  # type: ignore[no-untyped-def]
        return self.client.delete(url, **self._h(kw))


ADMIN_PASSWORD = "correct horse battery staple"


@pytest.fixture
def raw_client() -> Iterator[TestClient]:
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def api(raw_client: TestClient) -> AuthedClient:
    r = raw_client.post("/api/auth/setup", json={"password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    return AuthedClient(raw_client, r.json()["csrf"])


# --- Factories --------------------------------------------------------------------------------


def make_lead(db: Session, email: str = "alex@acme.test", **kw: Any) -> Lead:
    from app.schemas.leads import LeadCreate
    from app.services import leads as lead_svc

    data = {"email": email, "first_name": "Alex", "last_name": "Doe", "company": "Acme", "job_title": "CTO", **kw}
    lead = lead_svc.create_lead(db, LeadCreate.model_validate(data))
    db.commit()
    return lead


def make_account(db: Session, *, connected: bool = True, **kw: Any) -> EmailAccount:
    from app.schemas.accounts import AccountIn

    data = {
        "display_name": "Sam Sender",
        "email": "sam@sender.test",
        "smtp_host": "smtp.sender.test",
        "smtp_port": 587,
        "smtp_password": "smtp-secret-pw-123",
        "imap_enabled": True,
        "imap_host": "imap.sender.test",
        "imap_port": 993,
        "daily_limit": 100,
        "hourly_limit": 50,
        "min_delay_seconds": 10,
        **kw,
    }
    acc = account_svc.create_account(db, AccountIn.model_validate(data))
    if connected:
        acc.status = AccountStatus.CONNECTED
        acc.sending_enabled = True
    db.commit()
    return acc


def set_config(db: Session, **changes: Any) -> None:
    from app.services import settings as settings_svc

    phrase = settings_svc.LIVE_SENDING_PHRASE if changes.get("dry_run") is False else None
    settings_svc.update_config(db, changes, confirm_safety=True, confirmation_phrase=phrase)
    db.commit()


def build_active_campaign(
    db: Session,
    leads: list[Lead],
    account: EmailAccount,
    *,
    steps: list[tuple[str, str, int]] | None = None,
    live: bool = True,
    **campaign_kw: Any,
):  # type: ignore[no-untyped-def]
    """Create template(s)+sequence+list+campaign and run it through review, approval, activation."""
    from app.models import LeadList
    from app.schemas.campaigns import CampaignIn
    from app.schemas.content import SequenceIn, SequenceStepIn, TemplateIn
    from app.services import campaigns as camp_svc
    from app.services import leads as lead_svc
    from app.services import templates as tpl_svc

    if live:
        set_config(db, dry_run=False, min_delay_seconds=10)
    else:
        set_config(db, min_delay_seconds=10)
    steps = steps or [("Hello {{first_name}}", "Hi {{first_name}},\n\nQuick note about {{company}}.\n\n{{signature}}", 0)]
    step_ins = []
    for i, (subj, body, delay) in enumerate(steps, start=1):
        t = tpl_svc.create_template(db, TemplateIn(name=f"tpl-{i}-{datetime.now(UTC).timestamp()}", subject=subj, body=body))
        step_ins.append(SequenceStepIn(template_id=t.id, delay_days=delay))
    seq = tpl_svc.save_sequence(db, SequenceIn(name=f"seq-{datetime.now(UTC).timestamp()}", steps=step_ins))
    lst = LeadList(name=f"list-{datetime.now(UTC).timestamp()}")
    db.add(lst)
    db.flush()
    lead_svc.add_to_list(db, lst.id, [lead.id for lead in leads])
    params = {
        "name": "Test campaign",
        "email_account_id": account.id,
        "sequence_id": seq.id,
        "list_ids": [lst.id],
        "timezone": "UTC",
        "window_start": "00:00",
        "window_end": "23:59",
        "send_days": [0, 1, 2, 3, 4, 5, 6],
        "daily_limit": 100,
        "min_delay_seconds": 10,
        **campaign_kw,
    }
    c = camp_svc.create_campaign(db, CampaignIn.model_validate(params))
    db.commit()
    camp_svc.submit_for_review(db, c.id)
    camp_svc.approve(db, c.id, confirm=True)
    camp_svc.activate(db, c.id, confirm=True, confirm_name=c.name)
    db.commit()
    return c


@pytest.fixture
def settings():  # type: ignore[no-untyped-def]
    return get_settings()
