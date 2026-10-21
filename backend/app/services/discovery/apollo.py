"""Apollo.io adapter (optional).

Endpoints verified against docs.apollo.io:
* People API Search  POST /api/v1/mixed_people/api_search — 0 credits, returns NO emails/phones
  (last names are obfuscated; only `has_email` style flags). Max 100 per page.
* People Enrichment  POST /api/v1/people/match — consumes credits (1+ per person; more for phones,
  which we never request). Only used when enrichment is enabled, confirmed and within the daily budget.
Auth: `x-api-key` header. The key never leaves the backend.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.db.base import utcnow
from app.services.discovery.config import DiscoveryConfig
from app.services.discovery.http import request_json
from app.services.discovery.sources import RawSignal, SourceError

BASE = "https://api.apollo.io/api/v1"
SENIORITY_HINTS = {
    "founder": "founder",
    "owner": "owner",
    "ceo": "c_suite",
    "cto": "c_suite",
    "coo": "c_suite",
    "cfo": "c_suite",
    "chief": "c_suite",
    "vp": "vp",
    "vice president": "vp",
    "head": "head",
    "director": "director",
    "manager": "manager",
}


class _Org(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str | None = None
    primary_domain: str | None = None
    website_url: str | None = None
    industry: str | None = None
    estimated_num_employees: int | None = None


class _SearchPerson(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    first_name: str | None = None
    last_name_obfuscated: str | None = None
    last_name: str | None = None
    title: str | None = None
    has_email: bool | None = None
    organization: _Org | None = None


class _SearchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    total_entries: int | None = None
    people: list[_SearchPerson] = Field(default_factory=list)


class _EnrichedPerson(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    email_status: str | None = None
    title: str | None = None
    linkedin_url: str | None = None
    city: str | None = None
    country: str | None = None
    organization: _Org | None = None


class _MatchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    person: _EnrichedPerson | None = None
    match_confidence: str | None = None


class EnrichmentResult(BaseModel):
    email: str | None
    email_status: str | None
    first_name: str | None
    last_name: str | None
    title: str | None
    linkedin_url: str | None
    company_name: str | None
    company_domain: str | None
    industry: str | None
    employees: int | None
    country: str | None
    match_confidence: str | None


class ApolloSource:
    name = "apollo"

    def __init__(self, api_key: str | None, client: httpx.Client | None = None, sleep=None):  # type: ignore[no-untyped-def]
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=20)
        self.sleep = sleep

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise SourceError("Apollo API key is not configured", code="not_configured", permanent=True)
        return {"x-api-key": self.api_key, "Content-Type": "application/json", "Cache-Control": "no-cache"}

    def _call(self, path: str, params: Any) -> dict[str, Any]:
        kw: dict[str, Any] = {"sleep": self.sleep} if self.sleep else {}
        return request_json(self.client, "POST", BASE + path, provider="Apollo", headers=self._headers(), params=params, **kw)

    def status(self) -> dict[str, Any]:
        return {
            "configured": bool(self.api_key),
            "requires_key": True,
            "cost": "People Search: 0 credits (no emails). Enrichment: 1+ credits per person, off unless you enable it.",
        }

    def test(self) -> str:
        data = self._call("/mixed_people/api_search", [("per_page", 1), ("page", 1)])
        try:
            total = _SearchResponse.model_validate(data).total_entries
        except ValidationError as exc:
            raise SourceError("Apollo returned an unexpected response", code="invalid_response", permanent=True) from exc
        return f"Apollo API reachable (People Search, 0 credits). {total if total is not None else 'Unknown'} people match an empty filter."

    def search_params(self, config: DiscoveryConfig, per_page: int) -> list[tuple[str, Any]]:
        params: list[tuple[str, Any]] = [("page", 1), ("per_page", max(1, min(per_page, 100)))]
        for t in config.job_titles or config.buyer_roles:
            params.append(("person_titles[]", t))
        seniorities = {v for t in config.buyer_roles for k, v in SENIORITY_HINTS.items() if k in t}
        for s in sorted(seniorities):
            params.append(("person_seniorities[]", s))
        for loc in config.countries + config.regions:
            params.append(("organization_locations[]", loc))
        for size in config.company_sizes:
            params.append(("organization_num_employees_ranges[]", size))
        for d in config.excluded_domains:
            params.append(("not_organization_websites_list[]", d))
        kw = " ".join(config.keywords[:5] + config.target_industries[:3]).strip()
        if kw:
            params.append(("q_keywords", kw))
        return params

    def discover(self, config: DiscoveryConfig, limit: int) -> list[RawSignal]:
        if not (config.job_titles or config.buyer_roles):
            raise SourceError("Set job titles or buyer roles in Discovery rules to search Apollo", code="needs_targeting", permanent=True)
        params = self.search_params(config, limit)
        data = self._call("/mixed_people/api_search", params)
        try:
            parsed = _SearchResponse.model_validate(data)
        except ValidationError as exc:
            raise SourceError("Apollo returned an unexpected response", code="invalid_response", permanent=True) from exc
        out: list[RawSignal] = []
        retrieved = utcnow().isoformat()
        filters = {k: v for k, v in params if k not in ("page", "per_page")}
        for p in parsed.people[:limit]:
            org = p.organization.name if p.organization else None
            name = " ".join(x for x in [p.first_name, p.last_name or p.last_name_obfuscated] if x) or None
            text = f"Apollo People Search match: {p.title or 'unknown title'} at {org or 'unknown company'}."
            out.append(
                RawSignal(
                    source_provider="apollo",
                    source_type="people_search",
                    dedupe_basis=f"person:{p.id}",
                    title=f"{p.title or 'Contact'} at {org or 'unknown company'}",
                    source_external_id=p.id,
                    category="prospect_match",
                    company_name=org,
                    contact_name=name,
                    contact_title=p.title,
                    contact_external_id=p.id,
                    excerpt=text,
                    evidence=[
                        {
                            "kind": "provider_record",
                            "url": None,
                            "text": text,
                            "retrieved_at": retrieved,
                            "provider": "apollo",
                            "filters": filters,
                            "has_email": p.has_email,
                        }
                    ],
                    attributes={"has_email": p.has_email},
                )
            )
        return out

    def enrich(self, person_id: str) -> EnrichmentResult:
        """Credit-consuming. Callers must check the budget first. Never asks for personal emails or phones."""
        data = self._call("/people/match", [("id", person_id), ("reveal_personal_emails", "false"), ("reveal_phone_number", "false")])
        try:
            m = _MatchResponse.model_validate(data)
        except ValidationError as exc:
            raise SourceError("Apollo enrichment returned an unexpected response", code="invalid_response", permanent=True) from exc
        p = m.person or _EnrichedPerson()
        org = p.organization or _Org()
        domain = (
            org.primary_domain
            or (org.website_url or "").replace("https://", "").replace("http://", "").split("/")[0].removeprefix("www.")
            or None
        )
        return EnrichmentResult(
            email=(p.email or "").lower() or None,
            email_status=p.email_status,
            first_name=p.first_name,
            last_name=p.last_name,
            title=p.title,
            linkedin_url=p.linkedin_url,
            company_name=org.name,
            company_domain=domain,
            industry=org.industry,
            employees=org.estimated_num_employees,
            country=p.country,
            match_confidence=m.match_confidence,
        )
