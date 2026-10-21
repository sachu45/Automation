"""CSV import: upload -> field mapping -> validated preview -> commit.

Existing leads are never overwritten. With `existing="fill_blanks"` only empty fields
are filled; the default `existing="skip"` leaves them untouched. Suppressed addresses
are blocked and reported.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFound, ValidationFailed
from app.models import CsvImport, Lead, LeadList
from app.schemas.leads import LeadCreate
from app.services import audit, suppression
from app.services import leads as lead_svc

MAX_ROWS = 50_000
MAX_COLUMNS = 100

TARGET_FIELDS = [
    "email",
    "first_name",
    "last_name",
    "full_name",
    "job_title",
    "company",
    "company_website",
    "linkedin_url",
    "industry",
    "country",
    "timezone",
    "source",
    "notes",
    "tags",
]

SYNONYMS: dict[str, list[str]] = {
    "email": ["email", "email address", "e-mail", "mail", "work email", "emailaddress"],
    "first_name": ["first name", "firstname", "first", "given name"],
    "last_name": ["last name", "lastname", "last", "surname", "family name"],
    "full_name": ["name", "full name", "fullname", "contact name"],
    "job_title": ["title", "job title", "position", "role", "jobtitle"],
    "company": ["company", "company name", "organization", "organisation", "account", "employer"],
    "company_website": ["website", "company website", "domain", "url", "company url", "web"],
    "linkedin_url": ["linkedin", "linkedin url", "linkedin profile", "linkedin_url"],
    "industry": ["industry", "sector", "vertical"],
    "country": ["country", "location country"],
    "timezone": ["timezone", "time zone", "tz"],
    "source": ["source", "lead source"],
    "notes": ["notes", "note", "personalization", "comments"],
    "tags": ["tags", "tag", "labels"],
}


def _norm(h: str) -> str:
    return re.sub(r"[\s_\-]+", " ", h.strip().lower())


def suggest_mapping(headers: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for h in headers:
        n = _norm(h)
        target = "ignore"
        for field, names in SYNONYMS.items():
            if field not in used and (n in names or n == field.replace("_", " ")):
                target = field
                used.add(field)
                break
        mapping[h] = target
    return mapping


def _decode(content: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValidationFailed("Could not decode the file; please save it as UTF-8 CSV")


def parse_upload(db: Session, filename: str, content: bytes, max_bytes: int) -> CsvImport:
    if not filename.lower().endswith((".csv", ".txt")):
        raise ValidationFailed("Only .csv files are accepted")
    if len(content) > max_bytes:
        raise ValidationFailed(f"File too large (max {max_bytes // 1_000_000} MB)")
    if b"\x00" in content[:4096]:
        raise ValidationFailed("File looks binary, not CSV")
    text = _decode(content)
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    try:
        rows = [r for r in reader if any(c.strip() for c in r)]
    except csv.Error as exc:
        raise ValidationFailed(f"Malformed CSV: {exc}") from exc
    if not rows:
        raise ValidationFailed("The file is empty")
    headers = [h.strip() for h in rows[0]]
    if len(headers) > MAX_COLUMNS:
        raise ValidationFailed(f"Too many columns (max {MAX_COLUMNS})")
    if any(not h for h in headers):
        raise ValidationFailed("Every column needs a header")
    if len(set(headers)) != len(headers):
        raise ValidationFailed("Column headers must be unique")
    body = rows[1:]
    if len(body) > MAX_ROWS:
        raise ValidationFailed(f"Too many rows (max {MAX_ROWS})")
    imp = CsvImport(filename=filename[:255], headers=headers, rows=body, row_count=len(body))
    imp.mapping = suggest_mapping(headers)
    db.add(imp)
    db.flush()
    return imp


def _validate_mapping(headers: list[str], mapping: dict[str, str]) -> None:
    targets = [t for t in mapping.values() if t != "ignore"]
    unknown_headers = set(mapping) - set(headers)
    if unknown_headers:
        raise ValidationFailed(f"Mapping references unknown columns: {sorted(unknown_headers)}")
    for t in targets:
        if t not in TARGET_FIELDS and not re.match(r"^custom:[a-z0-9_]{1,60}$", t):
            raise ValidationFailed(f"Invalid mapping target: {t}")
    plain = [t for t in targets if not t.startswith("custom:")]
    if len(plain) != len(set(plain)):
        raise ValidationFailed("Each field can only be mapped once")
    if "email" not in targets:
        raise ValidationFailed("An email column must be mapped")


def _row_to_payload(
    headers: list[str], row: list[str], mapping: dict[str, str], extra_tags: list[str], source: str | None
) -> dict[str, Any]:
    data: dict[str, Any] = {"custom_fields": {}, "tags": list(extra_tags)}
    for idx, header in enumerate(headers):
        target = mapping.get(header, "ignore")
        if target == "ignore":
            continue
        value = row[idx].strip() if idx < len(row) else ""
        if not value:
            continue
        if target.startswith("custom:"):
            data["custom_fields"][target.split(":", 1)[1]] = value[:1000]
        elif target == "tags":
            data["tags"].extend(t for t in re.split(r"[;,|]", value) if t.strip())
        else:
            data[target] = value
    if source and not data.get("source"):
        data["source"] = source
    return data


def analyze(
    db: Session,
    imp: CsvImport,
    mapping: dict[str, str],
    *,
    source: str | None = None,
    tags: list[str] | None = None,
    sample: int = 50,
) -> dict[str, Any]:
    _validate_mapping(imp.headers, mapping)
    rows = imp.rows or []
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    payloads: list[tuple[int, dict[str, Any]]] = []
    for i, row in enumerate(rows, start=2):  # header is line 1
        payload = _row_to_payload(imp.headers, row, mapping, tags or [], source)
        try:
            parsed = LeadCreate.model_validate(payload)
        except ValidationError as exc:
            msgs = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
            results.append({"line": i, "status": "invalid", "email": payload.get("email"), "errors": msgs})
            continue
        if parsed.email in seen:
            results.append({"line": i, "status": "duplicate_in_file", "email": parsed.email, "errors": []})
            continue
        seen.add(parsed.email)
        payloads.append((i, parsed.model_dump()))
        results.append({"line": i, "status": "valid", "email": parsed.email, "errors": []})

    emails = [p["email"] for _, p in payloads]
    existing = set()
    for chunk_start in range(0, len(emails), 500):
        chunk = emails[chunk_start : chunk_start + 500]
        existing |= set(db.scalars(select(Lead.email).where(Lead.email.in_(chunk))))
    suppressed = suppression.suppressed_set(db, emails)
    for r in results:
        if r["status"] != "valid":
            continue
        if r["email"] in suppressed:
            r["status"] = "suppressed"
        elif r["email"] in existing:
            r["status"] = "existing"
    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    summary = {
        "total_rows": len(rows),
        "valid": counts.get("valid", 0),
        "invalid": counts.get("invalid", 0),
        "duplicate_in_file": counts.get("duplicate_in_file", 0),
        "existing": counts.get("existing", 0),
        "suppressed": counts.get("suppressed", 0),
    }
    problems = [r for r in results if r["status"] != "valid"]
    return {
        "summary": summary,
        "sample": results[:sample],
        "problems": problems[:500],
        "_payloads": {p["email"]: p for _, p in payloads},
        "_results": results,
    }


def get_import(db: Session, import_id: int) -> CsvImport:
    imp = db.get(CsvImport, import_id)
    if imp is None:
        raise NotFound("Import not found")
    if imp.status in {"committed", "discarded"}:
        raise ValidationFailed(f"This import was already {imp.status}")
    return imp


def preview(db: Session, import_id: int, mapping: dict[str, str], source: str | None, tags: list[str]) -> dict[str, Any]:
    imp = get_import(db, import_id)
    result = analyze(db, imp, mapping, source=source, tags=tags)
    imp.mapping = mapping
    imp.status = "previewed"
    imp.summary = result["summary"]
    return {k: v for k, v in result.items() if not k.startswith("_")}


def commit(
    db: Session,
    import_id: int,
    mapping: dict[str, str],
    *,
    source: str | None,
    tags: list[str],
    list_id: int | None,
    existing_mode: str = "skip",
) -> dict[str, Any]:
    if existing_mode not in {"skip", "fill_blanks"}:
        raise ValidationFailed("existing must be 'skip' or 'fill_blanks'")
    imp = get_import(db, import_id)
    if list_id is not None and db.get(LeadList, list_id) is None:
        raise NotFound("List not found")
    result = analyze(db, imp, mapping, source=source, tags=tags)
    payloads = result["_payloads"]
    created_ids: list[int] = []
    updated = 0
    list_ids: list[int] = []
    for r in result["_results"]:
        if r["status"] == "valid":
            lead = lead_svc.create_lead(db, LeadCreate.model_validate(payloads[r["email"]]), audit_it=False)
            created_ids.append(lead.id)
            list_ids.append(lead.id)
        elif r["status"] == "existing":
            found = lead_svc.find_by_email(db, r["email"])
            if found is None:
                continue
            lead = found
            if existing_mode == "fill_blanks":
                if _fill_blanks(db, lead, payloads[r["email"]]):
                    updated += 1
            list_ids.append(lead.id)
    added_to_list = lead_svc.add_to_list(db, list_id, list_ids) if list_id else 0
    summary = dict(result["summary"])
    summary.update({"created": len(created_ids), "updated_blanks": updated, "added_to_list": added_to_list})
    imp.status = "committed"
    imp.summary = summary
    imp.mapping = mapping
    imp.list_id = list_id
    imp.source_label = source
    imp.rows = None  # purge raw data once imported
    audit.record(
        db,
        "import.committed",
        f"CSV import '{imp.filename}': {len(created_ids)} created, {summary['suppressed']} suppressed blocked",
        entity_type="csv_import",
        entity_id=imp.id,
        details=summary,
    )
    return {"summary": summary, "problems": result["problems"]}


def _fill_blanks(db: Session, lead: Lead, payload: dict[str, Any]) -> bool:
    changed = False
    for f in ["first_name", "last_name", "full_name", "job_title", "linkedin_url", "industry", "country", "timezone", "notes"]:
        if not getattr(lead, f) and payload.get(f):
            setattr(lead, f, payload[f])
            changed = True
    if not lead.company and payload.get("company"):
        lead.company = lead_svc.get_or_create_company(db, payload["company"], payload.get("company_website"))
        changed = True
    custom = dict(lead.custom_fields or {})
    for k, v in (payload.get("custom_fields") or {}).items():
        if k not in custom or custom[k] in (None, ""):
            custom[k] = v
            changed = True
    lead.custom_fields = custom
    return changed


def discard(db: Session, import_id: int) -> None:
    imp = get_import(db, import_id)
    imp.status = "discarded"
    imp.rows = None
