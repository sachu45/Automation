"""Transparent, rule-based signal categorisation. Every match records the phrase that triggered it."""

from __future__ import annotations

import re
from dataclasses import dataclass

RULES: dict[str, re.Pattern[str]] = {
    "engineering_hiring": re.compile(
        r"\b(hiring|join (our|the) team|we'?re looking for|open (role|position)s?|job opening)\b.{0,80}\b(engineer|developer|devops|sre|data scientist|programmer|full[- ]stack|back[- ]?end|front[- ]?end)s?\b"
        r"|\b(software|senior|staff|lead|python|java|react|node|mobile)\s+(engineer|developer)s?\b",
        re.I | re.S,
    ),
    "hiring": re.compile(
        r"\b(we'?re hiring|we are hiring|now hiring|join (our|the) team|open (roles|positions)|job openings?|careers at|apply now|is hiring|are hiring|recruiting for)\b",
        re.I,
    ),
    "recruiter_announcement": re.compile(
        r"\b(recruiter|talent acquisition|hr (manager|team)|head of people)\b.{0,60}\b(hiring|looking for|role|position)\b", re.I | re.S
    ),
    "saas_launch": re.compile(
        r"\b(launch(ed|es|ing)?|introducing|now available|general availability|beta|new (product|feature|release|version))\b", re.I
    ),
    "product_initiative": re.compile(r"\b(announc(e|es|ed|ing)|partnership|initiative|roadmap|integration with|rolled out)\b", re.I),
    "expansion": re.compile(
        r"\b(expand(s|ed|ing)?|expansion|new office|opens? (an? )?(office|location)|entering|growing our team|scal(e|ing) up)\b", re.I
    ),
    "operational_challenge": re.compile(
        r"\b(outage|backlog|manual process(es)?|struggl(e|ing)|bottleneck|migration|legacy system)\b", re.I
    ),
}


@dataclass
class CategoryMatch:
    category: str
    phrase: str


def classify(text: str) -> list[CategoryMatch]:
    """Return every rule-category that matches, most specific first."""
    out: list[CategoryMatch] = []
    for category, pattern in RULES.items():
        m = pattern.search(text or "")
        if m:
            out.append(CategoryMatch(category, " ".join(m.group(0).split())[:120]))
    return out


def keyword_hits(text: str, keywords: list[str]) -> list[str]:
    low = (text or "").lower()
    return [k for k in keywords if k and re.search(r"(?<!\w)" + re.escape(k.lower()) + r"(?!\w)", low)]
