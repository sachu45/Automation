"""Transparent, explainable qualification.

Hard exclusions are checked first and can never be overridden by a model. The score is a
weighted average of component scores in [0, 1]; components whose target list is empty are
left out of the denominator. Every component records its reason and supporting evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import DiscoverySignal, Lead
from app.models.enums import BLOCKED_LEAD_STATUSES
from app.services import suppression
from app.services.discovery.classifier import keyword_hits
from app.services.discovery.config import DiscoveryConfig


@dataclass
class Qualification:
    status: str  # qualified | review_required | rejected
    score: float
    components: dict[str, Any] = field(default_factory=dict)
    reasons: list[dict[str, Any]] = field(default_factory=list)


def _contains_any(value: str | None, targets: list[str]) -> str | None:
    low = (value or "").lower()
    return next((t for t in targets if t and t in low), None)


def hard_exclusions(db: Session, sig: DiscoverySignal, config: DiscoveryConfig, lead: Lead | None, contact_email: str | None) -> list[str]:
    reasons: list[str] = []
    domain = (sig.company_domain or "").lower()
    if domain and (domain in config.excluded_domains or any(domain.endswith("." + d) for d in config.excluded_domains)):
        reasons.append(f"Company domain {domain} is excluded")
    if sig.company_name and _contains_any(sig.company_name, config.excluded_companies):
        reasons.append(f"Company '{sig.company_name}' is on the exclusion list (customer/competitor)")
    text = f"{sig.title or ''}\n{sig.excerpt or ''}"
    bad = keyword_hits(text, config.excluded_keywords)
    if bad:
        reasons.append(f"Contains excluded keyword(s): {', '.join(bad)}")
    email = contact_email or (lead.email if lead else None)
    if email and suppression.is_suppressed(db, email):
        reasons.append("Contact is on the suppression list")
    elif domain and suppression.is_suppressed(db, f"x@{domain}"):
        reasons.append(f"Domain {domain} is on the suppression list")
    if lead is not None:
        if lead.status in BLOCKED_LEAD_STATUSES:
            reasons.append(f"Lead status is {lead.status}")
        if (
            config.skip_if_contacted_within_days
            and lead.last_contacted_at
            and lead.last_contacted_at >= utcnow() - timedelta(days=config.skip_if_contacted_within_days)
        ):
            reasons.append(f"Lead was already contacted within {config.skip_if_contacted_within_days} days")
    return reasons


def score(sig: DiscoverySignal, config: DiscoveryConfig, attrs: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    w = config.weights
    comps: dict[str, Any] = {}
    text = f"{sig.title or ''}\n{sig.excerpt or ''}"

    def put(name: str, weight: float, value: float | None, reason: str, evidence: str | None = None) -> None:
        comps[name] = {"weight": weight, "score": None if value is None else round(value, 3), "reason": reason, "evidence": evidence}

    # Industry
    industry = attrs.get("industry")
    if config.target_industries:
        hit = _contains_any(industry, config.target_industries) or next(iter(keyword_hits(text, config.target_industries)), None)
        if hit:
            put("industry", w.industry, 1.0, f"Matches target industry '{hit}'", industry or hit)
        elif industry:
            put("industry", w.industry, 0.0, f"Industry '{industry}' is not a target", industry)
        else:
            put("industry", w.industry, 0.4, "Industry unknown (not enough data)")
    # Geography
    if config.countries or config.regions:
        place = " ".join(str(attrs.get(k) or "") for k in ("country", "region", "city"))
        targets = config.countries + config.regions
        hit = _contains_any(place, targets) or next(iter(keyword_hits(text, targets)), None)
        if hit:
            put("geography", w.geography, 1.0, f"Located in target area '{hit}'", place.strip() or hit)
        elif place.strip():
            put("geography", w.geography, 0.0, f"Location '{place.strip()}' is outside the target area", place.strip())
        else:
            put("geography", w.geography, 0.4, "Location unknown")
    # Company size
    employees = attrs.get("employees")
    if config.company_sizes:
        if isinstance(employees, int):
            ok = False
            for rng in config.company_sizes:
                lo, _, hi = rng.partition(",")
                try:
                    ok = ok or (int(lo) <= employees <= int(hi or 10**9))
                except ValueError:
                    continue
            put(
                "company_size",
                w.company_size,
                1.0 if ok else 0.0,
                f"{employees} employees {'within' if ok else 'outside'} target sizes",
                str(employees),
            )
        else:
            put("company_size", w.company_size, 0.4, "Company size unknown")
    # Signal relevance
    kw = keyword_hits(text, config.keywords)
    cat_ok = sig.category in config.signal_categories
    sig_score = (0.7 if cat_ok else 0.2) + min(0.3, 0.15 * len(kw))
    put(
        "signal",
        w.signal,
        min(1.0, sig_score),
        f"Category '{sig.category}' {'is' if cat_ok else 'is not'} a target" + (f"; keywords: {', '.join(kw)}" if kw else ""),
        (sig.title or "")[:200],
    )
    # Buyer persona
    roles = config.buyer_roles + config.job_titles
    if roles:
        title = sig.contact_title or attrs.get("contact_title")
        hit = _contains_any(title, roles)
        if hit:
            put("persona", w.persona, 1.0, f"Contact title matches '{hit}'", title)
        elif title:
            put("persona", w.persona, 0.2, f"Contact title '{title}' is not a target role", title)
        else:
            put("persona", w.persona, 0.4, "No named contact yet")
    # Evidence strength & recency
    ev = 0.0
    parts = []
    if sig.excerpt and len(sig.excerpt) >= 80:
        ev += 0.4
        parts.append("substantive excerpt")
    if sig.source_url:
        ev += 0.2
        parts.append("source URL")
    if sig.published_at:
        age = (utcnow() - sig.published_at).days
        ev += 0.4 if age <= 30 else 0.25 if age <= 90 else 0.05
        parts.append(f"published {age} day(s) ago")
    else:
        ev += 0.15
        parts.append("publication date unknown")
    put("evidence", w.evidence, min(1.0, ev), ", ".join(parts))

    total_w = sum(c["weight"] for c in comps.values() if c["score"] is not None)
    if not total_w:
        return 0.0, comps
    total = sum(c["weight"] * c["score"] for c in comps.values() if c["score"] is not None) / total_w * 100
    return round(total, 1), comps


def qualify(
    db: Session, sig: DiscoverySignal, config: DiscoveryConfig, *, lead: Lead | None, contact_email: str | None, attrs: dict[str, Any]
) -> Qualification:
    excl = hard_exclusions(db, sig, config, lead, contact_email)
    if excl:
        return Qualification("rejected", 0.0, {}, [{"type": "exclusion", "reason": r} for r in excl])
    total, comps = score(sig, config, attrs)
    reasons = [{"type": name, "reason": c["reason"], "evidence": c["evidence"], "score": c["score"]} for name, c in comps.items()]
    if total >= config.min_relevance_score:
        status = "qualified"
    elif total >= config.review_threshold:
        status = "review_required"
        reasons.append({"type": "threshold", "reason": f"Score {total} is below {config.min_relevance_score}; needs your review"})
    else:
        status = "rejected"
        reasons.append({"type": "threshold", "reason": f"Score {total} is below the review threshold {config.review_threshold}"})
    return Qualification(status, total, comps, reasons)


def duplicate_of(db: Session, sig: DiscoverySignal) -> DiscoverySignal | None:
    """Same company + same content already processed (e.g. found by two providers)."""
    if not sig.content_hash:
        return None
    return db.scalar(
        select(DiscoverySignal).where(
            DiscoverySignal.id != sig.id,
            DiscoverySignal.content_hash == sig.content_hash,
            DiscoverySignal.processing_status.in_(["qualified", "drafted", "review_required", "draft_pending"]),
        )
    )
