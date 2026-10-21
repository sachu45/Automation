from __future__ import annotations

from enum import StrEnum


class LeadStatus(StrEnum):
    NEW = "new"
    READY = "ready"
    CONTACTED = "contacted"
    REPLIED = "replied"
    INTERESTED = "interested"
    NOT_INTERESTED = "not_interested"
    BOUNCED = "bounced"
    UNSUBSCRIBED = "unsubscribed"
    DO_NOT_CONTACT = "do_not_contact"


# Lead statuses that must never receive outreach
BLOCKED_LEAD_STATUSES = {
    LeadStatus.BOUNCED,
    LeadStatus.UNSUBSCRIBED,
    LeadStatus.DO_NOT_CONTACT,
    LeadStatus.NOT_INTERESTED,
}


class AccountProvider(StrEnum):
    SMTP_IMAP = "smtp_imap"
    GMAIL_OAUTH = "gmail_oauth"
    MICROSOFT_OAUTH = "microsoft_oauth"


class AccountStatus(StrEnum):
    UNVERIFIED = "unverified"  # settings saved, never tested
    CONNECTED = "connected"  # last connection test succeeded
    ERROR = "error"  # last test failed for a non-auth reason
    AUTH_FAILED = "auth_failed"  # credentials rejected / revoked
    DISCONNECTED = "disconnected"  # operator disconnected; credentials removed


class Security(StrEnum):
    SSL = "ssl"
    STARTTLS = "starttls"
    NONE = "none"


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    READY_FOR_REVIEW = "ready_for_review"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class RecipientStatus(StrEnum):
    PENDING = "pending"  # approved, waiting for activation
    ACTIVE = "active"  # in sequence
    COMPLETED = "completed"  # all steps sent
    REPLIED = "replied"
    UNSUBSCRIBED = "unsubscribed"
    BOUNCED = "bounced"
    SUPPRESSED = "suppressed"
    FAILED = "failed"
    STOPPED = "stopped"  # manually / campaign cancelled / max attempts
    NEEDS_REVIEW = "needs_review"  # send outcome unknown after a crash


TERMINAL_RECIPIENT_STATUSES = {
    RecipientStatus.COMPLETED,
    RecipientStatus.REPLIED,
    RecipientStatus.UNSUBSCRIBED,
    RecipientStatus.BOUNCED,
    RecipientStatus.SUPPRESSED,
    RecipientStatus.FAILED,
    RecipientStatus.STOPPED,
}


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NEEDS_REVIEW = "needs_review"


class JobKind(StrEnum):
    SEND_STEP = "send_step"
    SEND_SINGLE = "send_single"
    SYNC_INBOX = "sync_inbox"
    DISCOVERY_RUN = "discovery_run"
    SIGNAL_PROCESS = "signal_process"


class AttemptStatus(StrEnum):
    QUEUED = "queued"
    SENDING = "sending"
    ACCEPTED = "accepted"  # SMTP server accepted (NOT proof of inbox delivery)
    DELIVERED = "delivered"  # only when a provider reports it
    BOUNCED = "bounced"
    FAILED_TEMPORARY = "failed_temporary"
    FAILED_PERMANENT = "failed_permanent"
    BLOCKED = "blocked"  # pre-send check refused (suppressed, limits...)
    UNKNOWN = "unknown"  # worker died mid-send; never auto-resent
    SIMULATED = "simulated"  # dry-run; no SMTP contact


class SuppressionReason(StrEnum):
    UNSUBSCRIBED = "unsubscribed"
    HARD_BOUNCE = "hard_bounce"
    DO_NOT_CONTACT = "do_not_contact"
    INVALID_ADDRESS = "invalid_address"
    MANUAL = "manual"
    COMPLAINT = "complaint"
    OTHER = "other"


class ReplyLabel(StrEnum):
    INTERESTED = "interested"
    NOT_INTERESTED = "not_interested"
    REFERRAL = "referral"
    QUESTION = "question"
    OUT_OF_OFFICE = "out_of_office"
    UNSUBSCRIBE = "unsubscribe"
    AUTOMATED = "automated"
    UNKNOWN = "unknown"


POSITIVE_REPLY_LABELS = {ReplyLabel.INTERESTED, ReplyLabel.REFERRAL}
NON_HUMAN_LABELS = {ReplyLabel.OUT_OF_OFFICE, ReplyLabel.AUTOMATED}


class MessageDirection(StrEnum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"


class TemplateFormat(StrEnum):
    TEXT = "text"
    HTML = "html"
