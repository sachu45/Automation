"""Compile segment rules into SQLAlchemy filters (real queries, no cached counts)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, exists, func, not_, or_, select, true
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailed
from app.db.base import utcnow
from app.models import Company, Lead, ListMembership, Segment, Tag, lead_tags

TEXT_FIELDS = {
    "email": Lead.email,
    "first_name": Lead.first_name,
    "last_name": Lead.last_name,
    "job_title": Lead.job_title,
    "industry": Lead.industry,
    "country": Lead.country,
    "source": Lead.source,
    "status": Lead.status,
}
DATE_FIELDS = {
    "created_at": Lead.created_at,
    "last_contacted_at": Lead.last_contacted_at,
    "last_replied_at": Lead.last_replied_at,
}

FIELD_CATALOG: list[dict[str, Any]] = (
    [{"field": f, "type": "text"} for f in TEXT_FIELDS]
    + [{"field": "company", "type": "text"}, {"field": "email_domain", "type": "text"}]
    + [{"field": f, "type": "date"} for f in DATE_FIELDS]
    + [
        {"field": "tag", "type": "tag"},
        {"field": "list", "type": "list"},
        {"field": "never_contacted", "type": "bool"},
        {"field": "has_replied", "type": "bool"},
        {"field": "custom.<key>", "type": "text"},
    ]
)

TEXT_OPS = {"eq", "neq", "contains", "not_contains", "starts_with", "in", "not_in", "is_empty", "is_not_empty"}
DATE_OPS = {"before", "after", "within_days", "older_than_days", "is_empty", "is_not_empty"}


def _text_cond(col: Any, op: str, value: Any) -> ColumnElement[bool]:
    lc = func.lower(func.coalesce(col, ""))
    if op in {"is_empty", "is_not_empty"}:
        cond = or_(col.is_(None), col == "")
        return cond if op == "is_empty" else not_(cond)
    if op in {"in", "not_in"}:
        vals = value if isinstance(value, list) else [v.strip() for v in str(value or "").split(",")]
        vals = [str(v).lower() for v in vals if str(v).strip()]
        if not vals:
            raise ValidationFailed(f"Operator '{op}' needs at least one value")
        cond = lc.in_(vals)
        return cond if op == "in" else not_(cond)
    v = str(value or "").lower()
    if op == "eq":
        return lc == v
    if op == "neq":
        return lc != v
    if op == "contains":
        return lc.contains(v, autoescape=True)
    if op == "not_contains":
        return not_(lc.contains(v, autoescape=True))
    if op == "starts_with":
        return lc.startswith(v, autoescape=True)
    raise ValidationFailed(f"Unsupported text operator: {op}")


def _date_cond(col: Any, op: str, value: Any) -> ColumnElement[bool]:
    if op == "is_empty":
        return col.is_(None)
    if op == "is_not_empty":
        return col.is_not(None)
    if op in {"within_days", "older_than_days"}:
        try:
            days = int(value)
        except (TypeError, ValueError) as exc:
            raise ValidationFailed("Day count must be a number") from exc
        cutoff = utcnow() - timedelta(days=days)
        return col >= cutoff if op == "within_days" else col < cutoff
    if op in {"before", "after"}:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError as exc:
            raise ValidationFailed("Date must be ISO formatted (YYYY-MM-DD)") from exc
        if dt.tzinfo is None:
            from datetime import UTC

            dt = dt.replace(tzinfo=UTC)
        return col < dt if op == "before" else col >= dt
    raise ValidationFailed(f"Unsupported date operator: {op}")


def rule_condition(rule: dict[str, Any]) -> ColumnElement[bool]:
    field = str(rule.get("field", ""))
    op = str(rule.get("op", ""))
    value = rule.get("value")
    if field in TEXT_FIELDS:
        return _text_cond(TEXT_FIELDS[field], op, value)
    if field in DATE_FIELDS:
        return _date_cond(DATE_FIELDS[field], op, value)
    if field == "company":
        sub = select(Company.id).where(_text_cond(Company.name, op if op != "is_empty" else "eq", value))
        if op == "is_empty":
            return Lead.company_id.is_(None)
        if op == "is_not_empty":
            return Lead.company_id.is_not(None)
        if op in {"neq", "not_contains", "not_in"}:
            # Negative ops: leads with no company also match
            positive = {"neq": "eq", "not_contains": "contains", "not_in": "in"}[op]
            sub = select(Company.id).where(_text_cond(Company.name, positive, value))
            return or_(Lead.company_id.is_(None), Lead.company_id.not_in(sub))
        return Lead.company_id.in_(sub)
    if field == "email_domain":
        # Emails are stored lowercase; LIKE keeps this portable across SQLite and PostgreSQL.
        vals = value if isinstance(value, list) else [v.strip() for v in str(value or "").split(",")]
        vals = [str(v).lower().lstrip("@") for v in vals if str(v).strip()]
        if not vals:
            raise ValidationFailed("email_domain rule needs a value")
        if op in {"eq", "in"}:
            return or_(*[Lead.email.like(f"%@{v}") for v in vals])
        if op in {"neq", "not_in"}:
            return and_(*[not_(Lead.email.like(f"%@{v}")) for v in vals])
        if op == "contains":
            return Lead.email.like(f"%@%{vals[0]}%")
        raise ValidationFailed(f"Unsupported email_domain operator: {op}")
    if field == "tag":
        tag_exists = exists(
            select(1)
            .select_from(lead_tags.join(Tag, Tag.id == lead_tags.c.tag_id))
            .where(lead_tags.c.lead_id == Lead.id, func.lower(Tag.name) == str(value or "").lower())
        )
        if op in {"has", "eq"}:
            return tag_exists
        if op in {"not_has", "neq"}:
            return not_(tag_exists)
        raise ValidationFailed("Tag operator must be 'has' or 'not_has'")
    if field == "list":
        try:
            list_id = int(str(value))
        except (TypeError, ValueError) as exc:
            raise ValidationFailed("List rule needs a list id") from exc
        member = exists(select(1).where(ListMembership.lead_id == Lead.id, ListMembership.list_id == list_id))
        if op in {"in", "eq", "has"}:
            return member
        if op in {"not_in", "neq", "not_has"}:
            return not_(member)
        raise ValidationFailed("List operator must be 'in' or 'not_in'")
    if field == "never_contacted":
        cond = Lead.last_contacted_at.is_(None)
        return cond if _truthy(value, op) else not_(cond)
    if field == "has_replied":
        cond = Lead.last_replied_at.is_not(None)
        return cond if _truthy(value, op) else not_(cond)
    if field.startswith("custom."):
        key = field.split(".", 1)[1]
        if not key.replace("_", "").isalnum():
            raise ValidationFailed("Invalid custom field key")
        return _text_cond(Lead.custom_fields[key].as_string(), op, value)
    raise ValidationFailed(f"Unknown segment field: {field}")


def _truthy(value: Any, op: str) -> bool:
    if op == "is_false":
        return False
    if op == "is_true":
        return True
    return str(value).lower() in {"1", "true", "yes"}


def segment_condition(match: str, rules: list[dict[str, Any]]) -> ColumnElement[bool]:
    if not rules:
        return true()
    conds = [rule_condition(r) for r in rules]
    return and_(*conds) if match == "all" else or_(*conds)


def validate_rules(match: str, rules: list[dict[str, Any]]) -> None:
    if match not in {"all", "any"}:
        raise ValidationFailed("match must be 'all' or 'any'")
    segment_condition(match, rules)  # raises on invalid rule


def segment_lead_ids_query(segment: Segment):  # type: ignore[no-untyped-def]
    return select(Lead.id).where(segment_condition(segment.match, segment.rules))


def count_segment(db: Session, segment: Segment) -> int:
    return db.scalar(select(func.count()).select_from(Lead).where(segment_condition(segment.match, segment.rules))) or 0


def preview_rules(db: Session, match: str, rules: list[dict[str, Any]], limit: int = 20) -> tuple[int, list[Lead]]:
    cond = segment_condition(match, rules)
    total = db.scalar(select(func.count()).select_from(Lead).where(cond)) or 0
    leads = list(db.scalars(select(Lead).where(cond).order_by(Lead.created_at.desc()).limit(limit)))
    return total, leads
