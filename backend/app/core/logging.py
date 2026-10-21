"""Structured logging with secret redaction."""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
from datetime import UTC, datetime

_SECRET_VALUES: set[str] = set()
_lock = threading.Lock()

_PATTERNS = [
    re.compile(r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|authorization)(\"?\s*[:=]\s*\"?)([^\s\"',;&]+)"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9\-._~+/]+=*)"),
    re.compile(r"(?i)(AUTH (?:PLAIN|LOGIN|XOAUTH2)\s+)(\S+)"),
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
]


def register_secret(value: str | None) -> None:
    """Register a concrete secret value so it is masked if it ever reaches a log line."""
    if value and len(value) >= 4:
        with _lock:
            _SECRET_VALUES.add(value)


def redact(text: str) -> str:
    for value in list(_SECRET_VALUES):
        if value in text:
            text = text.replace(value, "***")
    text = _PATTERNS[0].sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
    text = _PATTERNS[1].sub(lambda m: f"{m.group(1)}***", text)
    text = _PATTERNS[2].sub(lambda m: f"{m.group(1)}***", text)
    text = _PATTERNS[3].sub("sk-***", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001  # pragma: no cover
            return True
        record.msg = redact(msg)
        record.args = ()
        if record.exc_info and not record.exc_text:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "ctx", None)
        if isinstance(extra, dict):
            payload.update({k: v for k, v in extra.items()})
        if record.exc_text:
            payload["exc"] = record.exc_text
        return redact(json.dumps(payload, default=str))


def configure_logging(level: str = "INFO", json_logs: bool = False) -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactingFilter())
    handler.setFormatter(JsonFormatter() if json_logs else logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(handler)
    # smtplib/imaplib debug output can contain AUTH lines; keep them quiet.
    logging.getLogger("httpx").setLevel(logging.WARNING)
