"""Generic SMTP sender (password or XOAUTH2)."""

from __future__ import annotations

import smtplib
import ssl
from collections.abc import Callable
from email.message import EmailMessage
from email.utils import formataddr, formatdate

from app.providers.email.base import ConnectionTestError, OutgoingMessage, SendError, SendResult

HARD_BOUNCE_ENHANCED = ("5.1.1", "5.1.2", "5.1.3", "5.1.10", "5.2.1", "5.4.1")
HARD_BOUNCE_TEXT = (
    "user unknown",
    "no such user",
    "does not exist",
    "mailbox unavailable",
    "recipient address rejected",
    "invalid recipient",
    "unknown user",
    "address rejected",
)


def build_mime(msg: OutgoingMessage) -> EmailMessage:
    em = EmailMessage()
    em["From"] = formataddr((msg.from_name, msg.from_email)) if msg.from_name else msg.from_email
    em["To"] = msg.to_email
    em["Subject"] = msg.subject
    em["Date"] = formatdate(localtime=False)
    em["Message-ID"] = msg.message_id
    if msg.reply_to:
        em["Reply-To"] = msg.reply_to
    if msg.in_reply_to:
        em["In-Reply-To"] = msg.in_reply_to
        em["References"] = msg.references or msg.in_reply_to
    for k, v in msg.headers.items():
        em[k] = v
    em.set_content(msg.text)
    if msg.html:
        em.add_alternative(msg.html, subtype="html")
    return em


def classify_smtp_error(code: int | None, text: str) -> SendError:
    lowered = text.lower()
    if code is None:
        return SendError(text, permanent=False)
    if code in (530, 534, 535):
        return SendError(f"Authentication rejected ({code})", permanent=True, smtp_code=code, auth=True)
    if code >= 500:
        hard = any(c in text for c in HARD_BOUNCE_ENHANCED) or (code in (550, 551, 553) and any(t in lowered for t in HARD_BOUNCE_TEXT))
        return SendError(text[:500], permanent=True, smtp_code=code, hard_bounce=hard)
    return SendError(text[:500], permanent=False, smtp_code=code)


class SmtpSender:
    def __init__(
        self,
        host: str,
        port: int,
        security: str,
        username: str | None,
        password: str | None = None,
        oauth_token: Callable[[], str] | None = None,
        timeout: float = 30.0,
    ):
        self.host, self.port, self.security = host, port, security
        self.username, self.password, self.oauth_token = username, password, oauth_token
        self.timeout = timeout

    def _connect(self) -> smtplib.SMTP:
        ctx = ssl.create_default_context()
        if self.security == "ssl":
            conn: smtplib.SMTP = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=ctx)
        else:
            conn = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
            conn.ehlo()
            if self.security == "starttls":
                conn.starttls(context=ctx)
                conn.ehlo()
        if self.oauth_token is not None:
            token = self.oauth_token()
            auth_string = f"user={self.username}\x01auth=Bearer {token}\x01\x01"
            conn.auth("XOAUTH2", lambda challenge=None: auth_string, initial_response_ok=True)
        elif self.username and self.password:
            conn.login(self.username, self.password)
        return conn

    def test(self) -> str:
        try:
            conn = self._connect()
            code, _ = conn.noop()
            conn.quit()
            return f"SMTP connection and authentication OK ({self.host}:{self.port}, NOOP {code})"
        except smtplib.SMTPAuthenticationError as exc:
            raise ConnectionTestError(f"SMTP authentication failed ({exc.smtp_code})", auth=True) from exc
        except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
            raise ConnectionTestError(f"SMTP connection failed: {type(exc).__name__}: {exc}") from exc

    def send(self, message: OutgoingMessage) -> SendResult:
        mime = build_mime(message)
        try:
            conn = self._connect()
        except smtplib.SMTPAuthenticationError as exc:
            raise SendError("SMTP authentication failed", permanent=True, smtp_code=exc.smtp_code, auth=True) from exc
        except (TimeoutError, smtplib.SMTPException, OSError, ssl.SSLError) as exc:
            raise SendError(f"Connection failed: {type(exc).__name__}", permanent=False) from exc
        try:
            refused = conn.send_message(mime, from_addr=message.from_email, to_addrs=[message.to_email])
            if refused:
                code, text = next(iter(refused.values()))
                raise classify_smtp_error(code, text.decode(errors="replace") if isinstance(text, bytes) else str(text))
            return SendResult(accepted=True, smtp_code=250, response="accepted", provider_message_id=None)
        except smtplib.SMTPRecipientsRefused as exc:
            code, raw = next(iter(exc.recipients.values()))
            raise classify_smtp_error(code, raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)) from exc
        except smtplib.SMTPResponseException as exc:
            err = exc.smtp_error.decode(errors="replace") if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error)
            raise classify_smtp_error(exc.smtp_code, err) from exc
        except (TimeoutError, smtplib.SMTPServerDisconnected, OSError) as exc:
            # Outcome uncertain mid-DATA is rare; treat as temporary and rely on idempotency guards.
            raise SendError(f"Connection lost: {type(exc).__name__}", permanent=False) from exc
        finally:
            try:
                conn.quit()
            except Exception:  # noqa: BLE001
                pass
