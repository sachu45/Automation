"""Scenario 19 plus auth, CSRF, size limits, and safety-setting confirmations."""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.models import AccountCredential
from tests.conftest import ADMIN_PASSWORD

SECRET = "SuperSecretSmtpPassword!42"


def _create_account(api):  # type: ignore[no-untyped-def]
    return api.post(
        "/api/accounts",
        json={
            "display_name": "Sam",
            "email": "sam@sender.test",
            "smtp_host": "smtp.sender.test",
            "smtp_port": 587,
            "smtp_username": "sam@sender.test",
            "smtp_password": SECRET,
            "imap_enabled": True,
            "imap_host": "imap.sender.test",
            "imap_port": 993,
        },
    )


def test_credentials_never_returned_and_encrypted_at_rest(api, db) -> None:  # 19
    r = _create_account(api)
    assert r.status_code == 201, r.text
    assert SECRET not in r.text
    acc = r.json()
    assert acc["has_smtp_password"] is True and acc["status"] == "unverified" and acc["sending_enabled"] is False
    for url in ["/api/accounts", f"/api/accounts/{acc['id']}"]:
        assert SECRET not in api.get(url).text
    cred = db.scalar(select(AccountCredential))
    assert cred.encrypted_value and SECRET not in cred.encrypted_value
    # Saving settings does not mark an account connected; enabling sending needs a passing test
    r = api.post(f"/api/accounts/{acc['id']}/sending", json={"enabled": True})
    assert r.status_code == 422
    props = api.get("/api/openapi.json").json()["components"]["schemas"]["AccountOut"]["properties"]
    assert not {"smtp_password", "imap_password", "encrypted_value", "credentials"} & set(props)


def test_connection_test_sets_status_and_secret_not_logged(api, fake_mail, capsys) -> None:
    from app.core.config import get_settings
    from app.core.logging import configure_logging

    configure_logging("INFO")
    acc = _create_account(api).json()
    r = api.post(f"/api/accounts/{acc['id']}/test").json()
    assert r["result"]["smtp"]["ok"] and r["account"]["status"] == "connected"
    assert api.post(f"/api/accounts/{acc['id']}/sending", json={"enabled": True}).json()["sending_enabled"] is True
    # A log line that accidentally includes the secret is redacted by the root handler
    logging.getLogger("app.test").warning("smtp login failed for password=%s", SECRET)
    logging.getLogger("app.test").warning("raw value %s", SECRET)
    out = capsys.readouterr().out
    assert SECRET not in out and "***" in out
    configure_logging(get_settings().log_level)


def test_disconnect_deletes_credentials(api, db) -> None:
    acc = _create_account(api).json()
    r = api.post(f"/api/accounts/{acc['id']}/disconnect").json()
    assert r["status"] == "disconnected" and r["has_smtp_password"] is False
    assert db.scalar(select(AccountCredential)) is None


def test_authentication_required(raw_client) -> None:
    assert raw_client.get("/api/leads").status_code == 401
    assert raw_client.get("/api/health").status_code == 200
    assert raw_client.get("/api/auth/status").json() == {"setup_required": True, "authenticated": False}


def test_login_logout_and_rate_limit(raw_client, api) -> None:
    api.client.post("/api/auth/logout")
    raw_client.cookies.clear()
    assert raw_client.get("/api/leads").status_code == 401
    assert raw_client.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
    ok = raw_client.post("/api/auth/login", json={"password": ADMIN_PASSWORD})
    assert ok.status_code == 200 and raw_client.get("/api/leads").status_code == 200
    statuses = [raw_client.post("/api/auth/login", json={"password": "wrong"}).status_code for _ in range(6)]
    assert 429 in statuses
    # Setup cannot be repeated
    assert raw_client.post("/api/auth/setup", json={"password": "another-long-password"}).status_code == 403


def test_csrf_required_for_mutations(api) -> None:
    r = api.client.post("/api/leads", json={"email": "a@b.test"})  # no X-CSRF-Token header
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    r = api.client.post("/api/leads", json={"email": "a@b.test"}, headers={"X-CSRF-Token": "forged"})
    assert r.status_code == 403
    assert api.post("/api/leads", json={"email": "a@b.test"}).status_code == 201


def test_request_size_limit(api) -> None:
    big = {"email": "a@b.test", "notes": "x" * 1_200_000}
    assert api.post("/api/leads", json=big).status_code == 413


def test_live_sending_requires_typed_confirmation(api) -> None:
    r = api.patch("/api/settings", json={"changes": {"dry_run": False}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "confirmation_required"
    r = api.patch("/api/settings", json={"changes": {"dry_run": False}, "confirm_safety": True, "confirmation_phrase": "yes"})
    assert r.status_code == 422
    r = api.patch(
        "/api/settings", json={"changes": {"dry_run": False}, "confirm_safety": True, "confirmation_phrase": "ENABLE LIVE SENDING"}
    )
    assert r.status_code == 200 and r.json()["config"]["dry_run"] is False
    # Tightening safety needs no confirmation
    assert api.patch("/api/settings", json={"changes": {"dry_run": True, "daily_sending_limit": 10}}).status_code == 200
    audit = api.get("/api/settings/audit", params={"action": "settings"}).json()
    assert audit["total"] >= 2


def test_opt_outs_cannot_be_lifted_from_ui(api, db) -> None:
    from app.services import suppression

    s = suppression.suppress(db, email="no@x.test", reason="unsubscribed", source="unsubscribe_link")
    m = suppression.suppress(db, email="mistake@x.test", reason="manual", source="manual")
    db.commit()
    r = api.post(f"/api/suppressions/{s.id}/lift", json={"confirm_value": "no@x.test", "reason": "they asked again"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "opt_out_locked"
    r = api.post(f"/api/suppressions/{m.id}/lift", json={"confirm_value": "wrong@x.test", "reason": "typo correction"})
    assert r.status_code == 422
    r = api.post(f"/api/suppressions/{m.id}/lift", json={"confirm_value": "mistake@x.test", "reason": "typo correction"})
    assert r.status_code == 200 and r.json()["active"] is False


def test_security_headers(raw_client) -> None:
    r = raw_client.get("/api/health")
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in r.headers["content-security-policy"]
