"""Provider-independent email interfaces. Campaign logic only depends on these."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol


@dataclass
class OutgoingMessage:
    from_email: str
    from_name: str
    to_email: str
    subject: str
    text: str
    html: str | None = None
    reply_to: str | None = None
    message_id: str = ""
    in_reply_to: str | None = None
    references: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class SendResult:
    accepted: bool
    smtp_code: int | None = None
    response: str | None = None
    provider_message_id: str | None = None


class SendError(Exception):
    """Classified sending failure.

    permanent:   do not retry (e.g. 5xx mailbox unavailable)
    hard_bounce: the recipient address is definitively invalid -> suppress
    auth:        the account's credentials were rejected -> disable the account
    """

    def __init__(
        self,
        message: str,
        *,
        permanent: bool,
        smtp_code: int | None = None,
        hard_bounce: bool = False,
        auth: bool = False,
    ):
        super().__init__(message)
        self.message = message
        self.permanent = permanent
        self.smtp_code = smtp_code
        self.hard_bounce = hard_bounce
        self.auth = auth


@dataclass
class FetchedMessage:
    uid: int
    raw: bytes
    truncated: bool = False


@dataclass
class FetchResult:
    uidvalidity: int | None
    messages: list[FetchedMessage]
    max_uid: int = 0  # highest UID scanned (including messages skipped as irrelevant)


class EmailSender(Protocol):
    def send(self, message: OutgoingMessage) -> SendResult: ...

    def test(self) -> str: ...


class InboxReader(Protocol):
    def test(self) -> str: ...

    def fetch_since(
        self,
        folder: str,
        uidvalidity: int | None,
        last_uid: int,
        limit: int = 200,
        since: date | None = None,
        want: Callable[[bytes], bool] | None = None,
    ) -> FetchResult: ...


class ConnectionTestError(Exception):
    def __init__(self, message: str, *, auth: bool = False):
        super().__init__(message)
        self.message = message
        self.auth = auth
