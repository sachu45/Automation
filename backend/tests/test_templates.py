"""Scenarios 6-8: rendering, missing variables, and the unresolved-placeholder guard."""

from __future__ import annotations

import pytest

from app.services.template_engine import (
    MissingVariablesError,
    TemplateSyntaxError,
    UnresolvedPlaceholderError,
    analyze,
    assert_no_placeholders,
    html_to_text,
    render_email,
    render_string,
)

CTX = {"first_name": "Ann", "company": "Alpha <Inc>", "custom": {"tool": "Notion"}, "sender_name": "Sam"}


def test_render_template_correctly() -> None:  # 6
    out = render_email(
        "Hi {{ first_name }}", "Hello {{first_name}} at {{company}} — you use {{custom.tool}} / {{tool}}.\n{{sender_name}}", "text", CTX
    )
    assert out.subject == "Hi Ann"
    assert out.text == "Hello Ann at Alpha <Inc> — you use Notion / Notion.\nSam"
    assert out.html is None


def test_fallbacks_for_optional_fields() -> None:
    assert render_string("Hi {{last_name|there}}", CTX) == "Hi there"


def test_html_escaping_sanitizing_and_text_alternative() -> None:
    out = render_email(
        "Hi", '<p>Hello {{company}}</p><script>alert(1)</script><p><a href="https://x.test" onclick="x()">link</a></p>', "html", CTX
    )
    assert "Alpha &lt;Inc&gt;" in out.html
    assert "<script>" not in out.html and "onclick" not in out.html
    assert "Alpha <Inc>" in out.text and "link (https://x.test)" in out.text
    assert html_to_text("<p>a</p><ul><li>b</li></ul>") == "a\n\n- b"


def test_detect_missing_variables() -> None:  # 7
    with pytest.raises(MissingVariablesError) as e:
        render_email("Hi {{first_name}}", "About {{job_title}} at {{company}}", "text", {"first_name": "", "company": "X"})
    assert e.value.missing == ["first_name", "job_title"]
    a = analyze("{{first_name}} {{unknown_thing}} {{custom.x}}")
    assert a.unknown == ["unknown_thing"]


def test_syntax_validation() -> None:
    with pytest.raises(TemplateSyntaxError):
        render_string("Hi {{first name}}", CTX)
    assert analyze("Hi {{first_name}").errors
    assert analyze("Hi }} there").errors


def test_unresolved_placeholders_never_sent() -> None:  # 8
    with pytest.raises(UnresolvedPlaceholderError):
        assert_no_placeholders("Hello {{first_name}}")
    # A value containing braces must not sneak a placeholder into the output either
    with pytest.raises(UnresolvedPlaceholderError):
        render_email("Hi", "Hi {{first_name}}", "text", {"first_name": "{{evil}}"})


def test_template_api_versioning_and_preview(api, db) -> None:
    from tests.conftest import make_lead

    lead = make_lead(db, "p@x.test", first_name="Pia", company="PiaCo")
    t = api.post(
        "/api/templates", json={"name": "Intro", "subject": "Hi {{first_name}}", "body": "Hello {{first_name}} from {{sender_name}}"}
    ).json()
    assert t["current"]["version"] == 1
    t2 = api.patch(f"/api/templates/{t['id']}", json={"body": "Hey {{first_name}} at {{company}}"}).json()
    assert t2["current"]["version"] == 2 and t2["version_count"] == 2
    pv = api.post("/api/templates/preview", json={"subject": "Hi {{first_name}}", "body": "x {{job_title}}", "lead_id": lead.id}).json()
    assert pv["subject"] == "Hi Pia" and pv["missing"] == []
    pv = api.post(
        "/api/templates/preview", json={"subject": "Hi {{first_name}}", "body": "x {{custom.missing_one}}", "lead_id": lead.id}
    ).json()
    assert pv["missing"] == ["custom.missing_one"]
    bad = api.post("/api/templates", json={"name": "Bad", "subject": "Hi {{", "body": "x"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "template_syntax"
