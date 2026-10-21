"""IMAP incremental reader (read-only; uses BODY.PEEK so messages are not marked as read)."""

from __future__ import annotations

import imaplib
import re
import ssl
from collections.abc import Callable
from datetime import date

from app.providers.email.base import ConnectionTestError, FetchedMessage, FetchResult

MAX_MESSAGE_BYTES = 2_000_000


class ImapReader:
    def __init__(
        self,
        host: str,
        port: int,
        security: str,
        username: str,
        password: str | None = None,
        oauth_token: Callable[[], str] | None = None,
        timeout: float = 30.0,
    ):
        self.host, self.port, self.security = host, port, security
        self.username, self.password, self.oauth_token = username, password, oauth_token
        self.timeout = timeout

    def _connect(self) -> imaplib.IMAP4:
        ctx = ssl.create_default_context()
        if self.security == "ssl":
            conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=ctx, timeout=self.timeout)
        else:
            conn = imaplib.IMAP4(self.host, self.port, timeout=self.timeout)
            if self.security == "starttls":
                conn.starttls(ssl_context=ctx)
        if self.oauth_token is not None:
            token = self.oauth_token()
            auth = f"user={self.username}\x01auth=Bearer {token}\x01\x01".encode()
            conn.authenticate("XOAUTH2", lambda _: auth)
        else:
            conn.login(self.username, self.password or "")
        return conn

    def test(self) -> str:
        try:
            conn = self._connect()
            typ, _ = conn.select("INBOX", readonly=True)
            conn.logout()
            if typ != "OK":
                raise ConnectionTestError("IMAP connected but INBOX could not be opened")
            return f"IMAP connection and authentication OK ({self.host}:{self.port})"
        except imaplib.IMAP4.error as exc:
            text = str(exc)
            raise ConnectionTestError(
                f"IMAP authentication failed: {text[:200]}", auth="auth" in text.lower() or "login" in text.lower()
            ) from exc
        except (OSError, ssl.SSLError) as exc:
            raise ConnectionTestError(f"IMAP connection failed: {type(exc).__name__}: {exc}") from exc

    def fetch_since(
        self,
        folder: str,
        uidvalidity: int | None,
        last_uid: int,
        limit: int = 200,
        since: date | None = None,
        want: Callable[[bytes], bool] | None = None,
    ) -> FetchResult:
        """Incremental fetch. Headers are fetched in batches; full bodies only for messages `want` accepts.

        On a first sync (`last_uid == 0`) `since` bounds the search so old mailbox history is never scanned.
        """
        conn = self._connect()
        try:
            typ, _ = conn.select(_quote(folder), readonly=True)
            if typ != "OK":
                raise ConnectionTestError(f"Cannot open folder {folder}")
            uv_resp = conn.response("UIDVALIDITY")[1]
            current_uv = int(uv_resp[0]) if uv_resp and uv_resp[0] else None
            start = last_uid + 1
            if uidvalidity is not None and current_uv is not None and current_uv != uidvalidity:
                start = 1  # mailbox was rebuilt; resync (dedupe by Message-ID protects us)
            if start == 1 and since is not None:
                typ, data = conn.uid("SEARCH", f"SINCE {since.strftime('%d-%b-%Y')}")
            else:
                typ, data = conn.uid("SEARCH", f"UID {start}:*")
            uids = sorted(int(u) for u in (data[0] or b"").split() if int(u) >= start)[:limit]
            messages: list[FetchedMessage] = []
            for i in range(0, len(uids), 50):
                batch = uids[i : i + 50]
                typ, payload = conn.uid("FETCH", ",".join(map(str, batch)), "(RFC822.SIZE BODY.PEEK[HEADER])")
                for uid, size, headers in _parse_header_batch(payload):
                    if want is not None and not want(headers):
                        continue
                    if size > MAX_MESSAGE_BYTES:
                        messages.append(FetchedMessage(uid=uid, raw=headers, truncated=True))
                        continue
                    typ, full = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
                    raw = _extract_bytes(full)
                    if raw is not None:
                        messages.append(FetchedMessage(uid=uid, raw=raw))
            return FetchResult(uidvalidity=current_uv, messages=messages, max_uid=max(uids) if uids else last_uid)
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass


def _parse_header_batch(data: list) -> list[tuple[int, int, bytes]]:  # type: ignore[type-arg]
    out = []
    for item in data or []:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            meta = item[0] if isinstance(item[0], bytes) else b""
            uid_m = re.search(rb"UID (\d+)", meta)
            size_m = re.search(rb"RFC822\.SIZE (\d+)", meta)
            if uid_m:
                out.append((int(uid_m.group(1)), int(size_m.group(1)) if size_m else 0, item[1]))
    return out


def _quote(folder: str) -> str:
    return f'"{folder}"' if " " in folder and not folder.startswith('"') else folder


def _parse_size(data: list) -> int:  # type: ignore[type-arg]
    for item in data or []:
        if isinstance(item, bytes):
            m = re.search(rb"RFC822\.SIZE (\d+)", item)
            if m:
                return int(m.group(1))
    return 0


def _extract_bytes(data: list) -> bytes | None:  # type: ignore[type-arg]
    for item in data or []:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    return None
