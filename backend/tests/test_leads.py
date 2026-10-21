"""Scenarios 1-5 plus segmentation, merge, and CSV export safety."""

from __future__ import annotations

import csv
import io

from sqlalchemy import select

from app.models import Lead
from app.services import suppression
from tests.conftest import make_lead


def test_create_lead_successfully(api) -> None:  # 1
    r = api.post("/api/leads", json={"email": "Jane.Roe@Example.com", "first_name": "Jane", "company": "Example Co", "tags": ["Founders"]})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "jane.roe@example.com"
    assert body["company"] == "Example Co"
    assert body["tags"] == ["founders"]
    assert body["status"] == "new"


def test_reject_invalid_email(api) -> None:  # 2
    for bad in ["not-an-email", "a@b", "two@@example.com", ""]:
        r = api.post("/api/leads", json={"email": bad})
        assert r.status_code == 422, bad
        assert r.json()["error"]["code"] == "validation_failed"


def test_duplicate_email_detected(api) -> None:  # 4
    assert api.post("/api/leads", json={"email": "dup@example.com"}).status_code == 201
    r = api.post("/api/leads", json={"email": "DUP@example.com"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "duplicate_email"


CSV = (
    "Email,First Name,Last Name,Company,Website,Title,Favorite Tool\n"
    "ann@alpha.test,Ann,Lee,Alpha,alpha.test,CEO,Notion\n"
    "bob@beta.test,Bob,Ray,Beta,,CTO,\n"
    "bad-email,Bad,Row,Gamma,,,\n"
    "ann@alpha.test,Ann,Again,Alpha,,,\n"
    "blocked@delta.test,Bl,Ocked,Delta,,,\n"
    "existing@acme.test,Changed,Name,NewCo,,,\n"
)


def _upload(api, content: str = CSV):  # type: ignore[no-untyped-def]
    r = api.post("/api/leads/import/upload", files={"file": ("leads.csv", content.encode(), "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()


def test_import_valid_csv_with_mapping_preview_and_report(api, db) -> None:  # 3, 4, 5
    make_lead(db, "existing@acme.test", first_name="Original", company="Acme")
    suppression.suppress(db, email="blocked@delta.test", reason="unsubscribed")
    db.commit()

    up = _upload(api)
    mapping = up["suggested_mapping"]
    assert mapping["Email"] == "email" and mapping["First Name"] == "first_name" and mapping["Website"] == "company_website"
    mapping["Favorite Tool"] = "custom:favorite_tool"

    preview = api.post(f"/api/leads/import/{up['import_id']}/preview", json={"mapping": mapping, "source": "event-2026"}).json()
    s = preview["summary"]
    assert s == {"total_rows": 6, "valid": 2, "invalid": 1, "duplicate_in_file": 1, "existing": 1, "suppressed": 1}

    lst = api.post("/api/lists", json={"name": "Event leads"}).json()
    result = api.post(
        f"/api/leads/import/{up['import_id']}/commit",
        json={"mapping": mapping, "source": "event-2026", "list_id": lst["id"], "existing": "skip"},
    ).json()
    assert result["summary"]["created"] == 2
    db.expire_all()
    ann = db.scalar(select(Lead).where(Lead.email == "ann@alpha.test"))
    assert ann is not None and ann.custom_fields == {"favorite_tool": "Notion"} and ann.source == "event-2026"
    assert db.scalar(select(Lead).where(Lead.email == "blocked@delta.test")) is None  # suppressed: blocked
    existing = db.scalar(select(Lead).where(Lead.email == "existing@acme.test"))
    assert existing.first_name == "Original"  # never silently overwritten
    assert existing.company.name == "Acme"
    lists = api.get("/api/lists").json()
    assert lists[0]["lead_count"] == 3  # 2 new + existing lead added to the list


def test_import_fill_blanks_only(api, db) -> None:
    make_lead(db, "existing@acme.test", first_name="Original", job_title=None)
    up = _upload(api, "email,first_name,title\nexisting@acme.test,Changed,VP Sales\n")
    m = {"email": "email", "first_name": "first_name", "title": "job_title"}
    api.post(f"/api/leads/import/{up['import_id']}/commit", json={"mapping": m, "existing": "fill_blanks"})
    db.expire_all()
    lead = db.scalar(select(Lead).where(Lead.email == "existing@acme.test"))
    assert lead.first_name == "Original" and lead.job_title == "VP Sales"


def test_import_rejects_non_csv_and_requires_email_mapping(api) -> None:
    r = api.post("/api/leads/import/upload", files={"file": ("x.exe", b"MZ\x00\x00", "application/octet-stream")})
    assert r.status_code == 422
    up = _upload(api, "name\nBob\n")
    r = api.post(f"/api/leads/import/{up['import_id']}/preview", json={"mapping": {"name": "full_name"}})
    assert r.status_code == 422


def test_export_protects_against_formula_injection(api, db) -> None:
    make_lead(db, "evil@x.test", first_name='=HYPERLINK("http://x")', notes="+cmd|' /C calc'!A0")
    r = api.post("/api/leads/export", json={})
    assert r.status_code == 200
    rows = list(csv.DictReader(io.StringIO(r.text.lstrip("﻿"))))
    assert rows[0]["first_name"].startswith("'=")
    assert rows[0]["notes"].startswith("'+")


def test_search_filter_paginate(api, db) -> None:
    for i in range(30):
        make_lead(db, f"user{i}@corp{i % 3}.test", first_name=f"User{i}", industry="SaaS" if i % 2 else "Retail")
    r = api.get("/api/leads", params={"industry": "saas", "page_size": 10, "page": 2}).json()
    assert r["total"] == 15 and len(r["items"]) == 5
    r = api.get("/api/leads", params={"q": "user1"}).json()
    assert all("user1" in i["email"] for i in r["items"])


def test_segment_rules_use_real_queries(api, db) -> None:
    make_lead(db, "f1@saas.test", job_title="Founder", industry="SaaS")
    make_lead(db, "f2@saas.test", job_title="Co-Founder & CEO", industry="SaaS")
    make_lead(db, "m1@shop.test", job_title="Marketing Manager", industry="Retail")
    seg = api.post(
        "/api/segments",
        json={
            "name": "SaaS founders",
            "match": "all",
            "rules": [{"field": "job_title", "op": "contains", "value": "founder"}, {"field": "industry", "op": "eq", "value": "saas"}],
        },
    ).json()
    assert seg["lead_count"] == 2
    never = api.post("/api/segments/preview", json={"name": "x", "rules": [{"field": "never_contacted", "op": "is_true"}]}).json()
    assert never["total"] == 3
    dom = api.post(
        "/api/segments/preview", json={"name": "x", "rules": [{"field": "email_domain", "op": "eq", "value": "shop.test"}]}
    ).json()
    assert dom["total"] == 1
    bad = api.post("/api/segments", json={"name": "bad", "rules": [{"field": "nope", "op": "eq", "value": 1}]})
    assert bad.status_code == 422


def test_merge_requires_confirmation_and_fills_blanks(api, db) -> None:
    a = make_lead(db, "john+work@gmail.com", first_name="John", job_title=None)
    b = make_lead(db, "jo.hn@gmail.com", first_name="Johnny", job_title="Head of Ops")
    groups = api.get("/api/leads/duplicates").json()
    assert any({x["id"] for x in g["leads"]} == {a.id, b.id} for g in groups)
    r = api.post("/api/leads/merge", json={"primary_id": a.id, "duplicate_ids": [b.id]})
    assert r.status_code == 422  # explicit confirmation required
    r = api.post("/api/leads/merge", json={"primary_id": a.id, "duplicate_ids": [b.id], "confirm": True})
    assert r.status_code == 200
    merged = r.json()
    assert merged["first_name"] == "John" and merged["job_title"] == "Head of Ops"
    assert api.get(f"/api/leads/{b.id}").status_code == 404


def test_deleting_lead_keeps_suppression(api, db) -> None:
    lead = make_lead(db, "gone@x.test")
    suppression.suppress(db, email=lead.email, reason="unsubscribed", source="reply")
    db.commit()
    assert api.delete(f"/api/leads/{lead.id}").status_code == 200
    assert api.get("/api/suppressions/check", params={"email": "gone@x.test"}).json()["suppressed"] is True
