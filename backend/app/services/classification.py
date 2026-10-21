"""Rule-based reply classification. Results are suggestions for human review, not facts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from email.message import Message

from app.models.enums import ReplyLabel


@dataclass
class Classification:
    label: ReplyLabel
    confidence: float
    rationale: str


AUTO_SUBJECT = re.compile(
    r"^(auto(matic)?[ -]?(reply|response)|out of (the )?office|ooo\b|away from|abwesenheit|r[ée]ponse automatique|"
    r"respuesta autom[áa]tica|on (annual )?leave|on vacation|vacation reply)",
    re.I,
)
OOO_BODY = re.compile(
    r"\b(out of (the )?office|on (annual |parental |maternity |paternity )?leave|on vacation|away until|back on|return(ing)? on|limited access to (my )?email)\b",
    re.I,
)

RULES: list[tuple[ReplyLabel, re.Pattern[str], float]] = [
    (
        ReplyLabel.UNSUBSCRIBE,
        re.compile(
            r"\b(unsubscribe|remove me|take me off|stop (emailing|contacting|sending)|do not (email|contact)|don'?t (email|contact) me|opt[ -]?out)\b",
            re.I,
        ),
        0.85,
    ),
    (
        ReplyLabel.NOT_INTERESTED,
        re.compile(
            r"\b(not interested|no thanks|no thank you|not a (good )?fit|not (right )?now|we('re| are) (all )?set|pass on this|not looking)\b",
            re.I,
        ),
        0.7,
    ),
    (
        ReplyLabel.REFERRAL,
        re.compile(
            r"\b(reach out to|contact (my colleague|our)|(the )?right person (is|would be)|cc'?ing|looping in|forwarded (this|your email) to|speak (with|to) (my|our))\b",
            re.I,
        ),
        0.6,
    ),
    (
        ReplyLabel.INTERESTED,
        re.compile(
            r"\b(interested|sounds (good|great|interesting)|let'?s (talk|chat|connect|set up|schedule)|happy to (chat|talk|connect)|book a (call|time|meeting)|free (on|next)|send (me )?(more )?(info|details)|tell me more|calendar)\b",
            re.I,
        ),
        0.6,
    ),
]


def strip_quoted(text: str) -> str:
    """Remove quoted history so we classify only what the person wrote."""
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith(">"):
            continue
        if re.match(r"^On .+wrote:\s*$", line) or re.match(r"^-{2,}\s*Original Message", line, re.I) or line.strip() == "--":
            break
        if re.match(r"^(From|Sent|De|Von):\s", line) and lines:
            break
        lines.append(line)
    return "\n".join(lines).strip()


def is_automated(msg: Message) -> tuple[bool, ReplyLabel | None]:
    auto_submitted = (msg.get("Auto-Submitted") or "").lower()
    precedence = (msg.get("Precedence") or "").lower()
    subject = msg.get("Subject") or ""
    x_auto = msg.get("X-Autoreply") or msg.get("X-Autorespond") or msg.get("X-Auto-Response-Suppress")
    if AUTO_SUBJECT.search(subject.strip()):
        return True, ReplyLabel.OUT_OF_OFFICE
    if auto_submitted.startswith("auto-replied"):
        return True, ReplyLabel.OUT_OF_OFFICE
    if auto_submitted and auto_submitted != "no":
        return True, ReplyLabel.AUTOMATED
    if x_auto and str(x_auto).lower() not in ("", "no"):
        return True, ReplyLabel.OUT_OF_OFFICE
    if precedence in ("bulk", "junk", "list", "auto_reply"):
        return True, ReplyLabel.AUTOMATED
    return False, None


def classify_text(subject: str, body: str) -> Classification:
    text = strip_quoted(body or "")
    haystack = f"{subject}\n{text}"
    if OOO_BODY.search(haystack) and len(text) < 1500:
        return Classification(ReplyLabel.OUT_OF_OFFICE, 0.6, "Out-of-office wording")
    for label, pattern, conf in RULES:
        m = pattern.search(text)
        if m:
            return Classification(label, conf, f"Matched phrase: '{m.group(0)}'")
    if "?" in text:
        return Classification(ReplyLabel.QUESTION, 0.4, "Contains a question")
    return Classification(ReplyLabel.UNKNOWN, 0.0, "No rule matched — needs manual review")
