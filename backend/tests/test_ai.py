"""Scenarios 9-10: mocked AI provider, and operation without any AI key."""

from __future__ import annotations

import json
import logging

import httpx

from app.providers.ai.base import AIResult
from app.providers.ai.openai_compat import OpenAICompatibleProvider
from app.services import ai as ai_svc

INPUTS = {
    "product_description": "a scheduling tool for clinics",
    "reason": "you run several clinics",
    "value_proposition": "We cut no-shows with automatic reminders.",
    "call_to_action": "Would a 15-minute call next week be useful?",
    "tone": "friendly",
}


class RecordingMock:
    name = "mock"

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def complete_json(self, *, system: str, user: str, purpose: str, inputs: dict) -> AIResult:
        self.calls.append({"system": system, "user": user, "purpose": purpose})
        return AIResult(data=self.payload, model="mock-1", prompt_tokens=10, completion_tokens=20)


def test_generate_with_mocked_provider_structured_output(api, db) -> None:  # 9
    mock = RecordingMock(
        {
            "subject_lines": ["Clinic reminders", "Quick idea"],
            "opening_lines": ["Saw you run several clinics."],
            "body": "Hi {{first_name}},\n\nWe cut no-shows by 37%.\n\n{{signature}}",
            "short_version": "Hi {{first_name}}",
            "long_version": "Hi {{first_name}} long",
            "follow_ups": [{"subject": "", "body": "Following up"}],
            "grounding": ["reason"],
            "insufficient_context": False,
        }
    )
    ai_svc.set_provider_override(mock)
    r = api.post("/api/ai/generate", json=INPUTS)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["subject"] == "Clinic reminders" and d["status"] == "draft"
    # The invented "37%" is flagged because it is not in the operator's inputs
    assert any("37%" in w for w in d["output"]["warnings"])
    assert "Never invent" in mock.calls[0]["system"]
    # Drafts must be approved explicitly; AI cannot approve or send
    assert d["approved_at"] is None
    usage = api.get("/api/ai/usage").json()
    assert usage["by_purpose"][0]["requests"] == 1


def test_invalid_ai_output_is_rejected(api) -> None:
    ai_svc.set_provider_override(RecordingMock({"subject_lines": [], "body": ""}))
    r = api.post("/api/ai/generate", json=INPUTS)
    assert r.status_code == 502 and r.json()["error"]["code"] == "ai_invalid_output"


def test_runs_without_ai_key_using_template_mode(api, db) -> None:  # 10
    status = api.get("/api/ai/status").json()
    assert status["provider"] == "template" and status["ai_enabled"] is False and status["ready"] is True
    r = api.post("/api/ai/generate", json={**INPUTS, "prospect_context": ""})
    assert r.status_code == 200
    d = r.json()
    assert "{{first_name|there}}" in d["body"]
    assert d["output"]["insufficient_context"] is True  # honest, generic opening when context is thin
    # Edit, approve, regenerate, compare
    d2 = api.patch(f"/api/ai/drafts/{d['id']}", json={"subject": "Edited subject"}).json()
    assert d2["subject"] == "Edited subject"
    assert api.post(f"/api/ai/drafts/{d['id']}/approve").json()["status"] == "approved"
    regen = api.post(f"/api/ai/drafts/{d['id']}/regenerate").json()
    assert regen["parent_id"] == d["id"]
    versions = api.get(f"/api/ai/drafts/{regen['id']}").json()["versions"]
    assert len(versions) == 2


def test_openai_compatible_adapter_without_network_and_key_redaction(caplog) -> None:
    secret = "sk-test-0123456789abcdefghij"
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        body = {"subject_lines": ["S"], "body": "B"}
        return httpx.Response(
            200, json={"model": "m", "choices": [{"message": {"content": json.dumps(body)}}], "usage": {"prompt_tokens": 3}}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    p = OpenAICompatibleProvider("https://ai.invalid/v1", secret, "m", client=client)
    res = p.complete_json(system="s", user="u", purpose="draft", inputs={})
    assert res.data["body"] == "B" and seen["auth"] == f"Bearer {secret}"
    from app.core.logging import RedactingFilter

    record = logging.LogRecord("x", logging.INFO, __file__, 1, "calling with key %s", (secret,), None)
    RedactingFilter().filter(record)
    assert secret not in record.getMessage()


def test_template_cta_punctuation_not_doubled() -> None:
    from app.providers.ai.template_provider import TemplateProvider

    out = (
        TemplateProvider()
        .complete_json(system="", user="", purpose="draft", inputs={**INPUTS, "call_to_action": "Would a call help?"})
        .data
    )
    assert "help?" in out["body"] and "??" not in out["body"] and "?." not in out["body"]
    out = (
        TemplateProvider().complete_json(system="", user="", purpose="draft", inputs={**INPUTS, "call_to_action": "would a call help"}).data
    )
    assert "Would a call help?" in out["body"]
