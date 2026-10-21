"""A deliberately small, non-executing template language.

    {{ first_name }}               -> value, or error in strict mode if empty
    {{ first_name | there }}       -> value, or the literal fallback "there"
    {{ custom.favorite_tool }}     -> lead.custom_fields["favorite_tool"]
    {{ favorite_tool }}            -> also looks in custom fields when not a built-in

No expressions, loops, or attribute access on objects: templates cannot execute code.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

import nh3

TAG_RE = re.compile(r"\{\{\s*([^{}|]*?)\s*(?:\|\s*([^{}]*?)\s*)?\}\}")
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)?$")

LEAD_VARIABLES = {
    "first_name",
    "last_name",
    "full_name",
    "email",
    "job_title",
    "company",
    "company_website",
    "industry",
    "country",
}
SENDER_VARIABLES = {
    "sender_name",
    "sender_email",
    "sender_company",
    "sender_website",
    "signature",
    "mailing_address",
}
SYSTEM_VARIABLES = {"unsubscribe_url", "opt_out_text"}
BUILTIN_VARIABLES = LEAD_VARIABLES | SENDER_VARIABLES | SYSTEM_VARIABLES

ALLOWED_TAGS = {"p", "br", "a", "strong", "b", "em", "i", "u", "ul", "ol", "li", "blockquote", "span", "div"}
ALLOWED_ATTRS = {"a": {"href", "title"}}


class TemplateSyntaxError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class MissingVariablesError(ValueError):
    def __init__(self, missing: list[str]):
        super().__init__("Missing values for: " + ", ".join(missing))
        self.missing = missing


class UnresolvedPlaceholderError(ValueError):
    pass


@dataclass
class TemplateAnalysis:
    variables: list[str] = field(default_factory=list)
    with_fallback: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)  # resolved from custom fields at send time
    errors: list[str] = field(default_factory=list)


def analyze(text: str) -> TemplateAnalysis:
    result = TemplateAnalysis()
    seen: set[str] = set()
    for m in TAG_RE.finditer(text or ""):
        name = m.group(1).strip()
        if not NAME_RE.match(name):
            result.errors.append(f"Invalid variable name: '{name}'")
            continue
        if name not in seen:
            seen.add(name)
            result.variables.append(name)
            if m.group(2) is not None:
                result.with_fallback.append(name)
            base = name.split(".", 1)[0]
            if base != "custom" and name not in BUILTIN_VARIABLES:
                result.unknown.append(name)
    leftover = TAG_RE.sub("", text or "")
    if "{{" in leftover or "}}" in leftover:
        result.errors.append("Unbalanced or malformed '{{ }}' placeholder")
    return result


def validate(subject: str, body: str) -> TemplateAnalysis:
    a_s = analyze(subject)
    a_b = analyze(body)
    if "\n" in subject or "\r" in subject:
        a_s.errors.append("Subject must be a single line")
    merged = TemplateAnalysis(
        variables=list(dict.fromkeys(a_s.variables + a_b.variables)),
        with_fallback=list(dict.fromkeys(a_s.with_fallback + a_b.with_fallback)),
        unknown=list(dict.fromkeys(a_s.unknown + a_b.unknown)),
        errors=[f"Subject: {e}" for e in a_s.errors] + [f"Body: {e}" for e in a_b.errors],
    )
    return merged


def _lookup(name: str, context: dict[str, Any]) -> str:
    if name.startswith("custom."):
        value = (context.get("custom") or {}).get(name.split(".", 1)[1])
    elif name in context:
        value = context.get(name)
    else:
        value = (context.get("custom") or {}).get(name)
    if value is None:
        return ""
    return str(value).strip()


def render_string(text: str, context: dict[str, Any], *, escape_html: bool = False, strict: bool = True) -> str:
    out, missing = render_collect(text, context, escape_html=escape_html)
    if strict and missing:
        raise MissingVariablesError(missing)
    return out


def render_collect(text: str, context: dict[str, Any], *, escape_html: bool = False) -> tuple[str, list[str]]:
    """Render, returning (output, sorted missing variable names). Raises only on syntax errors."""
    analysis = analyze(text)
    if analysis.errors:
        raise TemplateSyntaxError(analysis.errors)
    missing: list[str] = []

    def _sub(m: re.Match[str]) -> str:
        name = m.group(1).strip()
        fallback = m.group(2)
        value = _lookup(name, context)
        if not value:
            if fallback is not None:
                value = fallback
            else:
                missing.append(name)
                return ""
        return html.escape(value) if escape_html else value

    out = TAG_RE.sub(_sub, text or "")
    return out, sorted(set(missing))


def assert_no_placeholders(*parts: str) -> None:
    for p in parts:
        if "{{" in (p or "") or "}}" in (p or ""):
            raise UnresolvedPlaceholderError("Rendered message still contains '{{' or '}}' — refusing to send")


def sanitize_html(body: str) -> str:
    return nh3.clean(body, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, link_rel="noopener noreferrer")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._href: str | None = None
        self._list_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self.parts.append("\n")
        elif tag in {"p", "div", "blockquote"}:
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag == "a":
            self._href = dict(attrs).get("href")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "div", "blockquote", "ul", "ol"}:
            self.parts.append("\n")
        elif tag == "a" and self._href:
            self.parts.append(f" ({self._href})")
            self._href = None

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(body: str) -> str:
    parser = _TextExtractor()
    parser.feed(body)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def text_to_html(text: str) -> str:
    paragraphs = [p for p in re.split(r"\n\s*\n", text.strip()) if p]
    return "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in paragraphs)


@dataclass
class RenderedEmail:
    subject: str
    text: str
    html: str | None


def render_email(
    subject: str,
    body: str,
    fmt: str,
    context: dict[str, Any],
    *,
    strict: bool = True,
    footer_text: str | None = None,
) -> RenderedEmail:
    """Render subject+body. `fmt` is 'text' or 'html'. Always produces a plain-text part."""
    rendered_subject, missing_s = render_collect(subject, context)
    rendered_subject = " ".join(rendered_subject.split())  # no header injection / stray whitespace
    rendered_body, missing_b = render_collect(body, context, escape_html=fmt == "html")
    missing = sorted(set(missing_s) | set(missing_b))
    if strict and missing:
        raise MissingVariablesError(missing)
    if fmt == "html":
        rendered_html = sanitize_html(rendered_body)
        text = html_to_text(rendered_html)
        if footer_text:
            text = f"{text}\n\n--\n{footer_text}"
            rendered_html += f'<p style="color:#666;font-size:12px">--<br>{html.escape(footer_text).replace(chr(10), "<br>")}</p>'
    else:
        text = rendered_body.strip()
        if footer_text:
            text = f"{text}\n\n--\n{footer_text}"
        rendered_html = None
    if strict:
        assert_no_placeholders(rendered_subject, text, rendered_html or "")
    return RenderedEmail(subject=rendered_subject, text=text, html=rendered_html)


def build_context(lead: Any, sender: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    company = getattr(lead, "company", None)
    full_name = lead.full_name or " ".join(p for p in [lead.first_name, lead.last_name] if p) or None
    ctx: dict[str, Any] = {
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "full_name": full_name,
        "email": lead.email,
        "job_title": lead.job_title,
        "company": company.name if company else None,
        "company_website": company.website if company else None,
        "industry": lead.industry,
        "country": lead.country,
        "custom": dict(lead.custom_fields or {}),
    }
    ctx.update(sender)
    if extra:
        ctx.update(extra)
    return ctx


SAMPLE_LEAD_CONTEXT: dict[str, Any] = {
    "first_name": "Alex",
    "last_name": "Rivera",
    "full_name": "Alex Rivera",
    "email": "alex@example.com",
    "job_title": "Head of Marketing",
    "company": "Example Co",
    "company_website": "https://example.com",
    "industry": "Software",
    "country": "United States",
    "custom": {},
}
