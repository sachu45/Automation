"""AI-assisted drafting. AI only produces drafts — it can never approve or send anything."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, NotFound, ValidationFailed
from app.db.base import utcnow
from app.models import AiDraft, AiPrompt, AiRequest, Lead
from app.models.enums import ReplyLabel
from app.providers.ai.base import AIProvider, AIProviderError
from app.providers.ai.openai_compat import OpenAICompatibleProvider
from app.providers.ai.template_provider import MockProvider, TemplateProvider
from app.services import audit
from app.services.settings import get_ai_api_key, get_config
from app.services.template_engine import BUILTIN_VARIABLES, TAG_RE

_override: AIProvider | None = None


def set_provider_override(p: AIProvider | None) -> None:
    global _override
    _override = p


# --- Schemas ----------------------------------------------------------------------------


class GenerateInput(BaseModel):
    product_description: str = Field(default="", max_length=3000)
    ideal_customer_profile: str = Field(default="", max_length=2000)
    target_audience: str = Field(default="", max_length=1000)
    reason: str = Field(default="", max_length=1000)
    value_proposition: str = Field(default="", max_length=2000)
    call_to_action: str = Field(default="", max_length=500)
    tone: Literal["friendly", "professional", "direct", "casual"] = "friendly"
    length: Literal["short", "medium", "long"] = "short"
    prospect_context: str = Field(default="", max_length=4000)
    lead_id: int | None = None
    use_variables: bool = True
    segments: list[str] = Field(default_factory=list, max_length=5)


class FollowUp(BaseModel):
    subject: str = ""
    body: str


class Variation(BaseModel):
    segment: str
    body: str


class DraftOutput(BaseModel):
    subject_lines: list[str] = Field(min_length=1, max_length=8)
    opening_lines: list[str] = Field(default_factory=list, max_length=8)
    body: str = Field(min_length=1, max_length=8000)
    short_version: str = Field(default="", max_length=8000)
    long_version: str = Field(default="", max_length=12000)
    follow_ups: list[FollowUp] = Field(default_factory=list, max_length=5)
    segment_variations: list[Variation] = Field(default_factory=list, max_length=5)
    grounding: list[str] = Field(default_factory=list)
    insufficient_context: bool = False
    notes: str = ""

    @field_validator("subject_lines")
    @classmethod
    def _subjects(cls, v: list[str]) -> list[str]:
        out = [" ".join(s.split())[:200] for s in v if s and s.strip()]
        if not out:
            raise ValueError("at least one subject line")
        return out


class ReplyClassOutput(BaseModel):
    label: ReplyLabel
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(default="", max_length=500)


SYSTEM_DRAFT = """You write short, honest, relevant B2B cold emails for a single operator who will review every word before anything is sent.

Hard rules:
- Use ONLY facts provided in the input (operator inputs and the prospect record). Never invent company news, funding, metrics, achievements, mutual connections, customers, case studies, research, or personal details.
- If the prospect context is thin, write a generic but honest opening and set "insufficient_context": true.
- No deceptive subject lines (no fake "Re:"/"Fwd:", no false urgency). No spammy formatting, no ALL CAPS, no excessive links.
- Plain text, no HTML. Keep sentences short. Respect the requested tone and length (short ~60-90 words, medium ~90-140, long ~140-200).
- If "use_variables" is true, write a reusable template using only these placeholders: {{first_name}}, {{last_name}}, {{company}}, {{job_title}}, {{sender_name}}, {{sender_company}}, {{signature}}. You may add a fallback like {{first_name|there}}. Otherwise write for the specific prospect and use no placeholders except {{signature}}.
- End the body with {{signature}} on its own line. Do not write an unsubscribe line (the app adds one).
- In "grounding", list which input fields you relied on.

Respond with a JSON object with keys: subject_lines (3 strings), opening_lines (2-3 strings), body, short_version, long_version, follow_ups (2 objects with subject and body; subject may be empty to reply in-thread), segment_variations (one object {segment, body} per requested segment, else []), grounding (list), insufficient_context (bool), notes (string)."""

SYSTEM_CLASSIFY = """Classify a reply to a cold email. Labels: interested, not_interested, referral, question, out_of_office, unsubscribe, automated, unknown.
Use 'unsubscribe' if the person asks not to be contacted. Use 'unknown' when unsure. Never infer interest from politeness alone.
Respond with JSON: {"label": ..., "confidence": 0..1, "rationale": short reason}."""


# --- Provider resolution ---------------------------------------------------------------------


def resolve_provider(db: Session) -> AIProvider:
    if _override is not None:
        return _override
    s = get_settings()
    cfg = get_config(db)
    name = s.ai_provider if cfg.ai_provider == "env" else cfg.ai_provider
    if name == "openai_compatible":
        key = get_ai_api_key(db) or s.ai_api_key
        return OpenAICompatibleProvider(
            base_url=cfg.ai_base_url or s.ai_base_url,
            api_key=key,
            model=cfg.ai_model or s.ai_model,
            timeout=s.ai_timeout_seconds,
        )
    if name == "gemini":
        from app.providers.ai.gemini import GeminiProvider
        from app.services.discovery.config import get_secret

        return GeminiProvider(
            api_key=get_secret(db, "gemini"),
            model=_gemini_model(cfg.ai_model),
            base_url=s.gemini_base_url,
            timeout=s.ai_timeout_seconds,
            max_input_chars=s.gemini_max_input_chars,
            max_output_tokens=s.gemini_max_output_tokens,
        )
    if name == "mock":
        return MockProvider()
    return TemplateProvider()


def _gemini_model(configured: str) -> str:
    return configured if configured.startswith(("gemini", "models/gemini")) else get_settings().gemini_model


def provider_status(db: Session) -> dict[str, Any]:
    s = get_settings()
    cfg = get_config(db)
    name = s.ai_provider if cfg.ai_provider == "env" else cfg.ai_provider
    if name == "gemini":
        from app.services.discovery.config import get_secret

        has_key = bool(get_secret(db, "gemini"))
        return {
            "provider": name,
            "model": _gemini_model(cfg.ai_model),
            "base_url": s.gemini_base_url,
            "has_api_key": has_key,
            "ready": has_key,
            "ai_enabled": has_key,
        }
    has_key = bool(get_ai_api_key(db) or s.ai_api_key)
    return {
        "provider": name,
        "model": (cfg.ai_model or s.ai_model) if name == "openai_compatible" else None,
        "base_url": (cfg.ai_base_url or s.ai_base_url) if name == "openai_compatible" else None,
        "has_api_key": has_key,
        "ready": name != "openai_compatible" or has_key,
        "ai_enabled": name == "openai_compatible" and has_key,
    }


def _log_request(
    db: Session,
    provider: AIProvider,
    purpose: str,
    started: float,
    *,
    ok: bool,
    model: str | None = None,
    pt: int | None = None,
    ct: int | None = None,
    error: str | None = None,
) -> None:
    db.add(
        AiRequest(
            provider=provider.name,
            model=model,
            purpose=purpose,
            status="ok" if ok else "error",
            latency_ms=int((time.monotonic() - started) * 1000),
            prompt_tokens=pt,
            completion_tokens=ct,
            error=(error or "")[:500] or None,
        )
    )


# --- Grounding checks -----------------------------------------------------------------------------

_NUM_RE = re.compile(r"\b\d[\d,.]*%?|\$\d[\d,.]*")


def grounding_warnings(output: DraftOutput, source_text: str, use_variables: bool) -> list[str]:
    """Flag content that could be fabricated: numbers not present in the inputs, stray placeholders."""
    warnings: list[str] = []
    texts = [output.body, output.short_version, output.long_version, *output.subject_lines, *[f.body for f in output.follow_ups]]
    src = source_text.lower()
    for t in texts:
        for n in _NUM_RE.findall(t or ""):
            if n.lower() not in src:
                warnings.append(f"Number '{n}' does not appear in your inputs — verify it is accurate")
    allowed = BUILTIN_VARIABLES
    for t in texts:
        for m in TAG_RE.finditer(t or ""):
            name = m.group(1).strip()
            if not use_variables and name != "signature":
                warnings.append(f"Placeholder {{{{{name}}}}} left in a personalized draft")
            elif name not in allowed:
                warnings.append(f"Unknown placeholder {{{{{name}}}}} — map it to a custom field or remove it")
    if output.insufficient_context:
        warnings.append("Limited prospect context — the opening is intentionally generic")
    return sorted(set(warnings))


def _lead_facts(db: Session, lead_id: int | None) -> dict[str, Any]:
    if not lead_id:
        return {}
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise NotFound("Lead not found")
    return {
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "job_title": lead.job_title,
        "company": lead.company.name if lead.company else None,
        "company_website": lead.company.website if lead.company else None,
        "industry": lead.industry,
        "country": lead.country,
        "notes": lead.notes,
        "custom_fields": lead.custom_fields or {},
    }


def generate(
    db: Session,
    data: GenerateInput,
    parent_id: int | None = None,
    *,
    purpose: str = "draft",
    system: str | None = None,
    extra_facts: dict[str, Any] | None = None,
    fallback_to_template: bool = False,
    provider: AIProvider | None = None,
) -> AiDraft:
    """Generate a draft. With `fallback_to_template`, AI errors fall back to the deterministic provider."""
    provider = provider or _safe_resolve(db, fallback_to_template)
    lead = _lead_facts(db, data.lead_id)
    inputs = data.model_dump()
    inputs["lead"] = {k: v for k, v in lead.items() if v}
    payload: dict[str, Any] = {
        "operator_inputs": {k: v for k, v in inputs.items() if k not in ("lead", "lead_id")},
        "prospect_record": inputs["lead"],
    }
    if extra_facts:
        payload.update(extra_facts)
    user = json.dumps(payload, ensure_ascii=False)
    fallback_note: str | None = None
    started = time.monotonic()
    try:
        result = provider.complete_json(system=system or SYSTEM_DRAFT, user=user, purpose=purpose, inputs=inputs)
        output = DraftOutput.model_validate(result.data)
    except (AIProviderError, ValidationError) as exc:
        reason = str(exc) if isinstance(exc, AIProviderError) else "schema validation failed"
        _log_request(db, provider, purpose, started, ok=False, error=reason)
        db.commit()
        if not fallback_to_template or provider.name == "template":
            if isinstance(exc, AIProviderError):
                raise AppError(str(exc), code="ai_error", status_code=502) from exc
            raise AppError(
                "The AI response did not match the required structure; try regenerating", code="ai_invalid_output", status_code=502
            ) from exc
        fallback_note = f"AI provider '{provider.name}' failed ({reason[:120]}); used the deterministic template provider instead"
        provider = TemplateProvider()
        started = time.monotonic()
        result = provider.complete_json(system=system or SYSTEM_DRAFT, user=user, purpose=purpose, inputs=inputs)
        output = DraftOutput.model_validate(result.data)
    _log_request(db, provider, purpose, started, ok=True, model=result.model, pt=result.prompt_tokens, ct=result.completion_tokens)
    source_text = user
    warnings = grounding_warnings(output, source_text, data.use_variables)
    if fallback_note:
        warnings.append(fallback_note)
    stored_output = output.model_dump()
    stored_output["warnings"] = warnings
    draft = AiDraft(
        lead_id=data.lead_id,
        parent_id=parent_id,
        provider=provider.name,
        inputs=data.model_dump(),
        output=stored_output,
        subject=output.subject_lines[0],
        body=output.body,
        status="draft",
        label=f"v{1 + _sibling_count(db, parent_id)}" if parent_id else "v1",
    )
    db.add(draft)
    db.flush()
    return draft


def _safe_resolve(db: Session, fallback: bool) -> AIProvider:
    try:
        return resolve_provider(db)
    except AIProviderError:
        if not fallback:
            raise
        return TemplateProvider()


def _sibling_count(db: Session, parent_id: int | None) -> int:
    if parent_id is None:
        return 0
    return len(list(db.scalars(select(AiDraft.id).where((AiDraft.parent_id == parent_id) | (AiDraft.id == parent_id)))))


def regenerate(db: Session, draft_id: int) -> AiDraft:
    d = get_draft(db, draft_id)
    root = d.parent_id or d.id
    return generate(db, GenerateInput.model_validate(d.inputs), parent_id=root)


def get_draft(db: Session, draft_id: int) -> AiDraft:
    d = db.get(AiDraft, draft_id)
    if d is None:
        raise NotFound("Draft not found")
    return d


def update_draft(db: Session, draft_id: int, subject: str | None, body: str | None) -> AiDraft:
    d = get_draft(db, draft_id)
    if subject is not None:
        if "\n" in subject:
            raise ValidationFailed("Subject must be a single line")
        d.subject = subject.strip()
    if body is not None:
        d.body = body
    if d.status == "approved":
        d.status = "draft"  # any edit requires re-approval
        d.approved_at = None
    return d


def approve_draft(db: Session, draft_id: int) -> AiDraft:
    from app.services.template_engine import validate

    d = get_draft(db, draft_id)
    analysis = validate(d.subject, d.body)
    if analysis.errors:
        raise ValidationFailed("Draft has template syntax errors", details=analysis.errors)
    d.status = "approved"
    d.approved_at = utcnow()
    audit.record(db, "ai_draft.approved", f"AI draft {d.id} approved by operator", entity_type="ai_draft", entity_id=d.id)
    return d


def save_prompt(db: Session, name: str, inputs: dict[str, Any]) -> AiPrompt:
    GenerateInput.model_validate(inputs)
    p = db.scalar(select(AiPrompt).where(AiPrompt.name == name))
    if p is None:
        p = AiPrompt(name=name, inputs=inputs)
        db.add(p)
    else:
        p.inputs = inputs
    db.flush()
    return p


def classify_reply(db: Session, subject: str, body: str):  # type: ignore[no-untyped-def]
    """Optional AI classification. Returns a Classification or None. Never triggers any reply."""
    from app.services.classification import Classification

    provider = resolve_provider(db)
    if provider.name == "template":
        return None
    started = time.monotonic()
    try:
        result = provider.complete_json(
            system=SYSTEM_CLASSIFY,
            user=json.dumps({"subject": subject, "body": body[:4000]}),
            purpose="classify_reply",
            inputs={"subject": subject, "body": body},
        )
        out = ReplyClassOutput.model_validate(result.data)
    except (AIProviderError, ValidationError) as exc:
        _log_request(db, provider, "classify_reply", started, ok=False, error=type(exc).__name__)
        return None
    _log_request(db, provider, "classify_reply", started, ok=True, model=result.model, pt=result.prompt_tokens, ct=result.completion_tokens)
    return Classification(out.label, out.confidence, f"AI: {out.rationale}"[:500])


def usage_summary(db: Session, since: datetime | None = None) -> dict[str, Any]:
    from sqlalchemy import func

    stmt = select(
        AiRequest.purpose, AiRequest.status, func.count(), func.sum(AiRequest.prompt_tokens), func.sum(AiRequest.completion_tokens)
    )
    if since:
        stmt = stmt.where(AiRequest.created_at >= since)
    rows = db.execute(stmt.group_by(AiRequest.purpose, AiRequest.status)).all()
    recent_errors = [
        {"at": r.created_at, "purpose": r.purpose, "error": r.error}
        for r in db.scalars(select(AiRequest).where(AiRequest.status == "error").order_by(AiRequest.created_at.desc()).limit(10))
    ]
    return {
        "by_purpose": [
            {"purpose": p, "status": s, "requests": n, "prompt_tokens": pt or 0, "completion_tokens": ct or 0} for p, s, n, pt, ct in rows
        ],
        "recent_errors": recent_errors,
    }
