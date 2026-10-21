"""Discovery settings (stored in app_settings) and backend-only provider secrets."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ValidationFailed
from app.core.logging import register_secret
from app.core.security import decrypt_secret, encrypt_secret
from app.db.base import utcnow
from app.models import AppSetting
from app.services import audit

SIGNAL_CATEGORIES = [
    "hiring",
    "engineering_hiring",
    "recruiter_announcement",
    "saas_launch",
    "product_initiative",
    "expansion",
    "operational_challenge",
    "custom_keyword",
    "prospect_match",
]

PROVIDERS = ["rss", "website", "apollo", "google_cse", "manual"]
SECRET_PROVIDERS = {"apollo": "apollo_api_key", "google_cse": "google_search_api_key", "gemini": "gemini_api_key"}


def _clean_list(v: list[str], lower: bool = True, max_len: int = 200) -> list[str]:
    out: list[str] = []
    for item in v:
        item = " ".join(str(item).split())[:max_len]
        if lower:
            item = item.lower()
        if item and item not in out:
            out.append(item)
    return out[:200]


class FeedSource(BaseModel):
    url: str = Field(max_length=1000)
    category: str | None = None  # optional fixed category for every item
    company_name: str | None = Field(default=None, max_length=255)
    company_domain: str | None = Field(default=None, max_length=255)

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        v = v.strip()
        if not re.match(r"^https?://[^\s/]+\.[^\s]+$", v, re.I):
            raise ValueError("Feed URL must be an http(s) URL")
        return v


class Weights(BaseModel):
    industry: float = Field(default=15, ge=0, le=100)
    geography: float = Field(default=10, ge=0, le=100)
    company_size: float = Field(default=10, ge=0, le=100)
    signal: float = Field(default=30, ge=0, le=100)
    persona: float = Field(default=20, ge=0, le=100)
    evidence: float = Field(default=15, ge=0, le=100)


class Offer(BaseModel):
    product_description: str = Field(default="", max_length=2000)
    value_proposition: str = Field(default="", max_length=2000)
    call_to_action: str = Field(default="", max_length=500)
    target_audience: str = Field(default="", max_length=500)
    tone: Literal["friendly", "professional", "direct", "casual"] = "professional"
    length: Literal["short", "medium", "long"] = "short"


class DiscoveryConfig(BaseModel):
    enabled: bool = False  # scheduled discovery (global pause when false)
    test_mode: bool = True  # no credit-consuming calls, no external AI; drafts labelled test
    draft_generation_enabled: bool = True

    # Targeting
    target_industries: list[str] = Field(default_factory=list)
    company_sizes: list[str] = Field(default_factory=list)  # Apollo-style ranges "1,10", "11,50"
    countries: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)  # regions or cities
    job_titles: list[str] = Field(default_factory=list)
    buyer_roles: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)
    signal_categories: list[str] = Field(
        default_factory=lambda: ["hiring", "engineering_hiring", "saas_launch", "product_initiative", "expansion"]
    )
    excluded_domains: list[str] = Field(default_factory=list)
    excluded_companies: list[str] = Field(default_factory=list)  # existing customers, competitors

    # Volume & thresholds
    max_results_per_run: int = Field(default=25, ge=1, le=200)
    interval_minutes: int = Field(default=360, ge=15, le=10_080)
    daily_limit: int = Field(default=100, ge=1, le=2000)
    min_relevance_score: float = Field(default=60, ge=0, le=100)
    review_threshold: float = Field(default=40, ge=0, le=100)
    skip_if_contacted_within_days: int = Field(default=90, ge=0, le=3650)
    weights: Weights = Field(default_factory=Weights)

    # Sources
    enabled_sources: list[str] = Field(default_factory=lambda: ["rss", "website", "manual"])
    feeds: list[FeedSource] = Field(default_factory=list)
    watch_domains: list[str] = Field(default_factory=list)  # company sites to check for careers/news signals
    max_pages_per_domain: int = Field(default=5, ge=1, le=20)
    search_queries: list[str] = Field(default_factory=list)  # for google_cse

    # Apollo enrichment (credit-consuming): off by default and capped
    enrichment_enabled: bool = False
    enrichment_confirmed: bool = False
    enrichment_daily_credit_budget: int = Field(default=0, ge=0, le=10_000)

    # AI drafting limits
    ai_daily_limit: int = Field(default=50, ge=0, le=5000)
    ai_per_run_limit: int = Field(default=20, ge=0, le=500)
    max_evidence_chars: int = Field(default=1500, ge=200, le=8000)

    offer: Offer = Field(default_factory=Offer)
    evidence_retention_days: int = Field(default=180, ge=0)

    @field_validator(
        "target_industries",
        "company_sizes",
        "countries",
        "regions",
        "job_titles",
        "buyer_roles",
        "keywords",
        "excluded_keywords",
        "excluded_companies",
        "search_queries",
    )
    @classmethod
    def _lists(cls, v: list[str]) -> list[str]:
        return _clean_list(v)

    @field_validator("excluded_domains", "watch_domains")
    @classmethod
    def _domains(cls, v: list[str]) -> list[str]:
        out = []
        for d in _clean_list(v):
            d = re.sub(r"^https?://", "", d).split("/")[0].removeprefix("www.").lstrip("@")
            if not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", d):
                raise ValueError(f"Invalid domain: {d}")
            out.append(d)
        return out

    @field_validator("signal_categories")
    @classmethod
    def _cats(cls, v: list[str]) -> list[str]:
        bad = [c for c in v if c not in SIGNAL_CATEGORIES]
        if bad:
            raise ValueError(f"Unknown signal categories: {bad}")
        return list(dict.fromkeys(v))

    @field_validator("enabled_sources")
    @classmethod
    def _sources(cls, v: list[str]) -> list[str]:
        bad = [p for p in v if p not in PROVIDERS]
        if bad:
            raise ValueError(f"Unknown sources: {bad}")
        return list(dict.fromkeys(v))

    @model_validator(mode="after")
    def _thresholds(self) -> DiscoveryConfig:
        if self.review_threshold > self.min_relevance_score:
            raise ValueError("Review threshold must not exceed the minimum relevance score")
        if self.enrichment_enabled and not self.enrichment_confirmed:
            raise ValueError("Credit-consuming enrichment requires explicit confirmation")
        return self


_KEY = "discovery_config"


def get_config(db: Session) -> DiscoveryConfig:
    row = db.get(AppSetting, _KEY)
    if row is None:
        s = get_settings()
        return DiscoveryConfig(max_pages_per_domain=min(max(s.discovery_max_pages_per_domain, 1), 20))
    return DiscoveryConfig.model_validate(row.value)


def update_config(db: Session, patch: dict[str, Any], *, confirm: bool = False) -> DiscoveryConfig:
    old = get_config(db)
    merged = old.model_dump(mode="json")
    merged.update(patch)
    # Turning on scheduled discovery or credit-consuming enrichment needs an explicit confirmation flag.
    turning_on = [k for k in ("enabled", "enrichment_enabled") if merged.get(k) and not getattr(old, k)]
    if turning_on and not confirm:
        raise ValidationFailed("Enabling this requires confirmation", code="confirmation_required", details={"fields": turning_on})
    if "enrichment_enabled" in turning_on:
        merged["enrichment_confirmed"] = True
    if not merged.get("enrichment_enabled"):
        merged["enrichment_confirmed"] = False
    try:
        new = DiscoveryConfig.model_validate(merged)
    except ValidationError as exc:
        raise ValidationFailed(
            "Invalid discovery settings", details=[{"loc": [str(p) for p in e["loc"]], "msg": e["msg"]} for e in exc.errors()]
        ) from exc
    row = db.get(AppSetting, _KEY)
    value = new.model_dump(mode="json")
    if row is None:
        db.add(AppSetting(key=_KEY, value=value))
    else:
        row.value = value
        row.updated_at = utcnow()
    changed = sorted(k for k in patch if old.model_dump(mode="json").get(k) != value.get(k))
    if changed:
        audit.record(db, "discovery.settings_updated", f"Discovery settings changed: {', '.join(changed)}", details={"fields": changed})
    return new


# --- Secrets ----------------------------------------------------------------------------------


def get_secret(db: Session, provider: str) -> str | None:
    """Stored (encrypted) key wins over the environment variable. Never returned to the frontend."""
    env_attr = SECRET_PROVIDERS[provider]
    row = db.get(AppSetting, f"secret:{provider}")
    value = decrypt_secret(row.value) if row is not None and row.value else getattr(get_settings(), env_attr, "")
    if value:
        register_secret(value)
    return value or None


def secret_source(db: Session, provider: str) -> str | None:
    row = db.get(AppSetting, f"secret:{provider}")
    if row is not None and row.value:
        return "stored"
    return "environment" if getattr(get_settings(), SECRET_PROVIDERS[provider], "") else None


def set_secret(db: Session, provider: str, value: str | None) -> None:
    if provider not in SECRET_PROVIDERS:
        raise ValidationFailed("Unknown provider")
    value = (value or "").strip()
    if value and (len(value) < 8 or len(value) > 500 or any(c.isspace() for c in value)):
        raise ValidationFailed("That doesn't look like a valid API key")
    row = db.get(AppSetting, f"secret:{provider}")
    stored = encrypt_secret(value) if value else None
    if row is None:
        db.add(AppSetting(key=f"secret:{provider}", value=stored))
    else:
        row.value = stored
        row.updated_at = utcnow()
    if value:
        register_secret(value)
    audit.record(db, "discovery.provider_key", f"{provider} API key {'updated' if value else 'removed'}")
