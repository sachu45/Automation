"""Bounce (DSN) parsing. Only RFC 3464 delivery-status reports with a 5.x.x status are
treated as confirmed hard bounces. Heuristic, non-standard bounces are recorded as
'uncertain' and never auto-suppress."""

from __future__ import annotations

import re
from dataclasses import dataclass
from email.message import Message
from typing import Any

STATUS_RE = re.compile(r"\b([245])\.(\d{1,3})\.(\d{1,3})\b")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


@dataclass
class BounceInfo:
    recipient: str | None
    status: str | None  # e.g. 5.1.1
    action: str | None  # failed | delayed | delivered ...
    diagnostic: str | None
    original_message_id: str | None
    kind: str  # hard | soft | uncertain

    @property
    def is_hard(self) -> bool:
        return self.kind == "hard"


def looks_like_bounce(msg: Message) -> bool:
    ctype = msg.get_content_type()
    if ctype == "multipart/report" and str(msg.get_param("report-type") or "").lower() == "delivery-status":
        return True
    sender = (msg.get("From") or "").lower()
    subject = (msg.get("Subject") or "").lower()
    return ("mailer-daemon" in sender or "postmaster" in sender) and any(
        w in subject for w in ("undeliver", "delivery status", "failure", "returned mail", "not delivered", "delivery has failed")
    )


def _strip_type(value: str | None) -> str | None:
    if not value:
        return None
    return value.split(";", 1)[-1].strip().strip("<>").lower()


def parse(msg: Message) -> BounceInfo | None:
    if not looks_like_bounce(msg):
        return None
    recipient = status = action = diagnostic = original_id = None
    structured = False
    for part in msg.walk():
        ct = part.get_content_type()
        if ct == "message/delivery-status":
            structured = True
            payload = part.get_payload()
            blocks: list[Any] = payload if isinstance(payload, list) else []
            for block in blocks:
                recipient = recipient or _strip_type(block.get("Final-Recipient") or block.get("Original-Recipient"))
                action = action or (block.get("Action") or "").strip().lower() or None
                status = status or (block.get("Status") or "").strip() or None
                diagnostic = diagnostic or (block.get("Diagnostic-Code") or "").strip() or None
        elif ct in ("message/rfc822", "text/rfc822-headers", "message/rfc822-headers"):
            inner: Any = part.get_payload()
            if isinstance(inner, list) and inner:
                original_id = original_id or inner[0].get("Message-ID")
            elif isinstance(inner, str):
                m = re.search(r"^Message-ID:\s*(<[^>]+>)", inner, re.I | re.M)
                if m:
                    original_id = original_id or m.group(1)
    if not original_id:
        # Some MTAs inline the original headers as text
        for part in msg.walk():
            if part.get_content_maintype() == "text":
                try:
                    text = part.get_content()  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    continue
                m = re.search(r"^Message-ID:\s*(<[^>]+>)", str(text), re.I | re.M)
                if m:
                    original_id = m.group(1)
                    break
    if structured and status:
        if status.startswith("5") and (action in (None, "failed")):
            kind = "hard"
        elif status.startswith("4") or action == "delayed":
            kind = "soft"
        else:
            kind = "uncertain"
        return BounceInfo(recipient, status, action, diagnostic, original_id, kind)
    # Unstructured bounce: extract what we can, but never claim certainty.
    body = ""
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            try:
                body += str(part.get_content())  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
    m = STATUS_RE.search(body)
    emails = EMAIL_RE.findall(body)
    return BounceInfo(
        recipient=emails[0].lower() if emails else None,
        status=m.group(0) if m else None,
        action=None,
        diagnostic=body[:300] if body else None,
        original_message_id=original_id,
        kind="uncertain",
    )
