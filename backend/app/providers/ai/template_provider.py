"""Deterministic, non-AI drafting. Uses only what the operator supplied — never invents facts.

Also used (as `MockProvider`) in tests, where a fixed, predictable output is needed.
"""

from __future__ import annotations

from typing import Any

from app.providers.ai.base import AIResult

TONE_GREETING = {
    "friendly": "Hi",
    "casual": "Hey",
    "professional": "Hello",
    "direct": "Hi",
}


def _sentence(text: str) -> str:
    t = (text or "").strip().rstrip(".")
    if not t:
        return ""
    end = "" if t[-1] in "?!" else "."
    return f"{t[0].upper()}{t[1:]}{end}"


def _lower_first(text: str) -> str:
    t = (text or "").strip().rstrip(".")
    return t[0].lower() + t[1:] if t else ""


class TemplateProvider:
    name = "template"

    def complete_json(self, *, system: str, user: str, purpose: str, inputs: dict[str, Any]) -> AIResult:
        if purpose == "classify_reply":
            from app.services.classification import classify_text

            c = classify_text(inputs.get("subject", ""), inputs.get("body", ""))
            return AIResult(data={"label": str(c.label), "confidence": c.confidence, "rationale": c.rationale}, model="rules")
        return AIResult(data=self._draft(inputs), model="deterministic-template")

    def _draft(self, i: dict[str, Any]) -> dict[str, Any]:
        lead = i.get("lead") or {}
        use_vars = i.get("use_variables", True)
        first = "{{first_name|there}}" if use_vars else (lead.get("first_name") or "there")
        company = "{{company|your team}}" if use_vars else (lead.get("company") or "your team")
        greeting = TONE_GREETING.get(i.get("tone", "friendly"), "Hi")
        reason = _lower_first(i.get("reason", ""))
        value = _sentence(i.get("value_proposition", ""))
        product = _lower_first(i.get("product_description", ""))
        cta = _sentence(i.get("call_to_action", "") or "Would a short call next week be useful")
        if not cta.endswith("?") and cta.lower().startswith(("would", "could", "are", "is", "do", "can")):
            cta = cta.rstrip(".") + "?"
        context = (i.get("prospect_context") or "").strip()
        insufficient = not context and not lead.get("notes")

        if context:
            opening = f"I'm reaching out because {reason}." if reason else "I wanted to reach out briefly."
            opening_alt = f"Based on what you shared — {context[:140].rstrip('.')} — I thought this might be relevant."
        else:
            opening = f"I'm reaching out because {reason}." if reason else "I'll keep this short."
            opening_alt = f"I work on {product} and thought it might be relevant to {company}." if product else opening

        body_mid = value or (f"We help teams with {product}." if product else "")
        sig = "{{signature}}"
        body = "\n\n".join(p for p in [f"{greeting} {first},", opening, body_mid, cta, sig] if p)
        short = "\n\n".join(p for p in [f"{greeting} {first},", opening, cta, sig] if p)
        long_parts = [f"{greeting} {first},", opening]
        if product:
            long_parts.append(f"For context: we work on {product}.")
        if body_mid:
            long_parts.append(body_mid)
        audience = (i.get("target_audience") or "").strip()
        if audience:
            long_parts.append(f"We typically work with {_lower_first(audience)}.")
        long_parts += [cta, sig]
        topic = (i.get("reason") or i.get("product_description") or "a quick question").strip().rstrip(".")
        topic_short = topic[:60]
        return {
            "subject_lines": [f"Quick question for {company}", f"{topic_short[:1].upper()}{topic_short[1:]}", f"Idea for {company}"],
            "opening_lines": [opening, opening_alt],
            "body": body,
            "short_version": short,
            "long_version": "\n\n".join(p for p in long_parts if p),
            "follow_ups": [
                {"subject": "", "body": f"{greeting} {first},\n\nJust following up on my note below in case it got buried. {cta}\n\n{sig}"},
                {
                    "subject": "",
                    "body": f"{greeting} {first},\n\nI don't want to clutter your inbox, so this will be my last note. If it's ever useful, just reply and I'll pick it up.\n\n{sig}",
                },
            ],
            "segment_variations": [
                {"segment": seg, "body": body.replace(cta, f"I thought this might be relevant for {seg}. {cta}", 1)}
                for seg in (i.get("segments") or [])[:5]
            ],
            "grounding": [k for k in ("reason", "value_proposition", "product_description", "prospect_context") if i.get(k)],
            "insufficient_context": insufficient,
            "notes": "Generated deterministically from your inputs (no AI). Edit before approving."
            + (" Add prospect context for more specific openings." if insufficient else ""),
        }


class MockProvider(TemplateProvider):
    name = "mock"
