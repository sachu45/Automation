"""Sending-account management. Secrets are encrypted at rest and never returned."""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.logging import register_secret
from app.core.security import decrypt_secret, encrypt_secret
from app.db.base import utcnow
from app.models import AccountCredential, AppSetting, EmailAccount, SendAttempt
from app.models.enums import AccountProvider, AccountStatus, AttemptStatus
from app.providers.email import oauth
from app.providers.email.base import ConnectionTestError, EmailSender, InboxReader
from app.providers.email.imap import ImapReader
from app.providers.email.smtp import SmtpSender
from app.schemas.accounts import AccountIn, AccountOut, AccountUpdate
from app.services import audit

# Tests (and future adapters) can override how providers are constructed.
ProviderFactory = Callable[[Session, EmailAccount], tuple[EmailSender, InboxReader | None]]
_factory_override: ProviderFactory | None = None


def set_provider_factory(factory: ProviderFactory | None) -> None:
    global _factory_override
    _factory_override = factory


COUNTED_STATUSES = [
    AttemptStatus.SENDING,
    AttemptStatus.ACCEPTED,
    AttemptStatus.DELIVERED,
    AttemptStatus.BOUNCED,
    AttemptStatus.UNKNOWN,
    AttemptStatus.FAILED_PERMANENT,
]


def get_account(db: Session, account_id: int) -> EmailAccount:
    acc = db.get(EmailAccount, account_id)
    if acc is None:
        raise NotFound("Email account not found")
    return acc


def _set_credential(db: Session, account: EmailAccount, kind: str, value: str | None) -> None:
    existing = next((c for c in account.credentials if c.kind == kind), None)
    if not value:
        return
    register_secret(value)
    if existing:
        existing.encrypted_value = encrypt_secret(value)
        existing.rotated_at = utcnow()
    else:
        account.credentials.append(AccountCredential(kind=kind, encrypted_value=encrypt_secret(value)))


def _get_credential(account: EmailAccount, kind: str) -> str | None:
    cred = next((c for c in account.credentials if c.kind == kind), None)
    if cred is None:
        return None
    value = decrypt_secret(cred.encrypted_value)
    register_secret(value)
    return value


def sent_last_24h(db: Session, account_id: int) -> int:
    since = utcnow() - timedelta(hours=24)
    return (
        db.scalar(
            select(func.count())
            .select_from(SendAttempt)
            .where(
                SendAttempt.account_id == account_id,
                SendAttempt.is_simulation.is_(False),
                SendAttempt.status.in_(COUNTED_STATUSES),
                SendAttempt.created_at >= since,
            )
        )
        or 0
    )


def to_out(db: Session, acc: EmailAccount) -> AccountOut:
    out = AccountOut.model_validate(acc)
    kinds = {c.kind for c in acc.credentials}
    out.has_smtp_password = "smtp_password" in kinds
    out.has_imap_password = "imap_password" in kinds
    out.has_oauth_token = "oauth" in kinds
    out.sent_last_24h = sent_last_24h(db, acc.id)
    return out


def create_account(db: Session, data: AccountIn) -> EmailAccount:
    if db.scalar(select(EmailAccount).where(EmailAccount.email == data.email.lower())):
        raise Conflict("An account with this email already exists")
    acc = EmailAccount(
        display_name=data.display_name,
        email=data.email.lower(),
        reply_to=data.reply_to,
        provider=data.provider,
        daily_limit=data.daily_limit,
        hourly_limit=data.hourly_limit,
        min_delay_seconds=data.min_delay_seconds,
        status=AccountStatus.UNVERIFIED,
        sending_enabled=False,
        imap_folder=data.imap_folder or "INBOX",
    )
    if data.provider == AccountProvider.SMTP_IMAP:
        acc.smtp_host, acc.smtp_port, acc.smtp_security = data.smtp_host, data.smtp_port, data.smtp_security
        acc.smtp_username = data.smtp_username or data.email
        acc.imap_enabled = data.imap_enabled
        acc.imap_host, acc.imap_port, acc.imap_security = data.imap_host, data.imap_port, data.imap_security
        acc.imap_username = data.imap_username or acc.smtp_username
    else:
        cfg = oauth.provider_config(data.provider)
        acc.smtp_host, acc.smtp_port = cfg.smtp_host, cfg.smtp_port
        acc.smtp_security = "ssl" if cfg.smtp_port == 465 else "starttls"
        acc.smtp_username = data.email.lower()
        acc.imap_enabled = True
        acc.imap_host, acc.imap_port, acc.imap_security = cfg.imap_host, cfg.imap_port, "ssl"
        acc.imap_username = data.email.lower()
    db.add(acc)
    db.flush()
    _set_credential(db, acc, "smtp_password", data.smtp_password)
    _set_credential(db, acc, "imap_password", data.imap_password)
    audit.record(db, "account.created", f"Email account {acc.email} added", entity_type="account", entity_id=acc.id)
    return acc


CONNECTION_FIELDS = {
    "smtp_host",
    "smtp_port",
    "smtp_security",
    "smtp_username",
    "imap_host",
    "imap_port",
    "imap_security",
    "imap_username",
    "imap_enabled",
}


def update_account(db: Session, account_id: int, data: AccountUpdate) -> EmailAccount:
    acc = get_account(db, account_id)
    changed = []
    for field in data.model_fields_set:
        value = getattr(data, field)
        if getattr(acc, field) != value:
            setattr(acc, field, value)
            changed.append(field)
    if acc.hourly_limit > acc.daily_limit:
        raise ValidationFailed("Hourly limit cannot exceed daily limit")
    if CONNECTION_FIELDS & set(changed):
        # Connection settings changed: require a fresh successful test before sending again.
        acc.status = AccountStatus.UNVERIFIED
        acc.sending_enabled = False
        acc.status_message = "Connection settings changed — run a connection test"
    if changed:
        audit.record(
            db,
            "account.updated",
            f"Account {acc.email} updated",
            entity_type="account",
            entity_id=acc.id,
            details={"fields": sorted(changed)},
        )
    return acc


def rotate_credentials(db: Session, account_id: int, smtp_password: str | None, imap_password: str | None) -> EmailAccount:
    acc = get_account(db, account_id)
    if not smtp_password and not imap_password:
        raise ValidationFailed("Provide at least one new credential")
    _set_credential(db, acc, "smtp_password", smtp_password)
    _set_credential(db, acc, "imap_password", imap_password)
    acc.status = AccountStatus.UNVERIFIED
    acc.sending_enabled = False
    acc.status_message = "Credentials rotated — run a connection test"
    audit.record(db, "account.credentials_rotated", f"Credentials rotated for {acc.email}", entity_type="account", entity_id=acc.id)
    return acc


def disconnect(db: Session, account_id: int) -> EmailAccount:
    acc = get_account(db, account_id)
    for cred in list(acc.credentials):
        acc.credentials.remove(cred)
    acc.status = AccountStatus.DISCONNECTED
    acc.sending_enabled = False
    acc.status_message = "Disconnected; stored credentials were deleted"
    audit.record(db, "account.disconnected", f"Account {acc.email} disconnected", entity_type="account", entity_id=acc.id)
    return acc


def delete_account(db: Session, account_id: int) -> None:
    from app.models import Campaign

    acc = get_account(db, account_id)
    if db.scalar(select(func.count()).select_from(Campaign).where(Campaign.email_account_id == acc.id)):
        raise Conflict("This account is used by campaigns; disconnect it instead of deleting")
    audit.record(db, "account.deleted", f"Account {acc.email} deleted", entity_type="account", entity_id=acc.id)
    db.delete(acc)


# --- Provider construction ------------------------------------------------------------


def _oauth_token_getter(db: Session, acc: EmailAccount) -> Callable[[], str]:
    def get() -> str:
        raw = _get_credential(acc, "oauth")
        if not raw:
            raise ConnectionTestError("No OAuth token stored — connect the account first", auth=True)
        tokens = json.loads(raw)
        if tokens.get("expires_at", 0) <= time.time() or not tokens.get("access_token"):
            try:
                tokens = oauth.refresh(acc.provider, tokens["refresh_token"])
            except oauth.OAuthError as exc:
                raise ConnectionTestError(exc.message, auth=exc.revoked) from exc
            _set_credential(db, acc, "oauth", json.dumps(tokens))
        register_secret(tokens["access_token"])
        return str(tokens["access_token"])

    return get


def build_providers(db: Session, acc: EmailAccount) -> tuple[EmailSender, InboxReader | None]:
    if _factory_override is not None:
        return _factory_override(db, acc)
    if acc.status == AccountStatus.DISCONNECTED:
        raise ConnectionTestError("Account is disconnected")
    if not acc.smtp_host or not acc.smtp_port:
        raise ConnectionTestError("SMTP host/port not configured")
    if acc.provider == AccountProvider.SMTP_IMAP:
        smtp_pw = _get_credential(acc, "smtp_password")
        sender: EmailSender = SmtpSender(acc.smtp_host, acc.smtp_port, acc.smtp_security, acc.smtp_username, smtp_pw)
        reader: InboxReader | None = None
        if acc.imap_enabled and acc.imap_host and acc.imap_port:
            imap_pw = _get_credential(acc, "imap_password") or smtp_pw
            reader = ImapReader(acc.imap_host, acc.imap_port, acc.imap_security, acc.imap_username or acc.email, imap_pw)
        return sender, reader
    getter = _oauth_token_getter(db, acc)
    sender = SmtpSender(acc.smtp_host, acc.smtp_port, acc.smtp_security, acc.email, oauth_token=getter)
    reader = ImapReader(acc.imap_host or "", acc.imap_port or 993, "ssl", acc.email, oauth_token=getter)
    return sender, reader


def test_connection(db: Session, account_id: int) -> dict[str, Any]:
    """Real connection test. Status only becomes 'connected' after SMTP auth succeeds."""
    acc = get_account(db, account_id)
    results: dict[str, Any] = {"smtp": None, "imap": None}
    acc.last_test_at = utcnow()
    try:
        sender, reader = build_providers(db, acc)
        results["smtp"] = {"ok": True, "message": sender.test()}
    except ConnectionTestError as exc:
        results["smtp"] = {"ok": False, "message": exc.message}
        acc.status = AccountStatus.AUTH_FAILED if exc.auth else AccountStatus.ERROR
        acc.status_message = exc.message
        acc.sending_enabled = False
        audit.record(db, "account.test_failed", f"Connection test failed for {acc.email}", entity_type="account", entity_id=acc.id)
        return results
    if reader is not None:
        try:
            results["imap"] = {"ok": True, "message": reader.test()}
        except ConnectionTestError as exc:
            results["imap"] = {"ok": False, "message": exc.message}
    acc.status = AccountStatus.CONNECTED
    imap_note = ""
    if results["imap"] and not results["imap"]["ok"]:
        imap_note = f" (inbox sync failing: {results['imap']['message']})"
    acc.status_message = "Connection test passed" + imap_note
    audit.record(db, "account.test_passed", f"Connection test passed for {acc.email}", entity_type="account", entity_id=acc.id)
    return results


def set_sending_enabled(db: Session, account_id: int, enabled: bool) -> EmailAccount:
    acc = get_account(db, account_id)
    if enabled and acc.status != AccountStatus.CONNECTED:
        raise ValidationFailed("Run a successful connection test before enabling sending")
    acc.sending_enabled = enabled
    audit.record(
        db,
        "account.sending_" + ("enabled" if enabled else "disabled"),
        f"Sending {'enabled' if enabled else 'disabled'} for {acc.email}",
        entity_type="account",
        entity_id=acc.id,
    )
    return acc


def mark_auth_failure(db: Session, acc: EmailAccount, message: str) -> None:
    acc.status = AccountStatus.AUTH_FAILED
    acc.status_message = message
    acc.sending_enabled = False
    audit.record(
        db, "account.auth_failed", f"Authentication failed for {acc.email}; sending disabled", entity_type="account", entity_id=acc.id
    )


# --- OAuth flow ------------------------------------------------------------------------------


def oauth_start(db: Session, account_id: int) -> str:
    acc = get_account(db, account_id)
    if acc.provider == AccountProvider.SMTP_IMAP:
        raise ValidationFailed("This account uses SMTP/IMAP passwords, not OAuth")
    if not oauth.is_configured(acc.provider):
        raise ValidationFailed(
            "OAuth client is not configured. Set the client id/secret environment variables (see README).",
            code="oauth_not_configured",
        )
    state = secrets.token_urlsafe(32)
    verifier = oauth.new_code_verifier()
    db.merge(AppSetting(key=f"oauth_state:{state}", value={"account_id": acc.id, "verifier": encrypt_secret(verifier), "ts": time.time()}))
    return oauth.build_authorize_url(acc.provider, state, verifier, acc.email)


def oauth_callback(db: Session, state: str, code: str) -> EmailAccount:
    row = db.get(AppSetting, f"oauth_state:{state}")
    if row is None or time.time() - float(row.value.get("ts", 0)) > 900:
        raise ValidationFailed("OAuth state is invalid or expired; start again")
    acc = get_account(db, int(row.value["account_id"]))
    verifier = decrypt_secret(row.value["verifier"])
    db.delete(row)
    try:
        tokens = oauth.exchange_code(acc.provider, code, verifier)
    except oauth.OAuthError as exc:
        raise ValidationFailed(exc.message) from exc
    if not tokens.get("refresh_token"):
        raise ValidationFailed("Provider did not return a refresh token; revoke the app's access and try again")
    _set_credential(db, acc, "oauth", json.dumps(tokens))
    acc.status = AccountStatus.UNVERIFIED
    acc.status_message = "OAuth authorized — run a connection test"
    audit.record(db, "account.oauth_connected", f"OAuth authorized for {acc.email}", entity_type="account", entity_id=acc.id)
    return acc
