"""Web-search provider abstraction.

Google's Custom Search JSON API is closed to new customers and is scheduled to shut down on
2027-01-01 (Google suggests Vertex AI Search, which searches your own content, not the public web).
The adapter below therefore only works for projects that already have access. It is optional;
discovery runs fine without it using feeds, company sites and manual signals.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.db.base import utcnow
from app.services.discovery.config import DiscoveryConfig
from app.services.discovery.fetcher import canonical_url, domain_of
from app.services.discovery.http import request_json
from app.services.discovery.sources import RawSignal, SourceError, matches_targets

CSE_URL = "https://www.googleapis.com/customsearch/v1"


class _Item(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str = ""
    link: str
    snippet: str = ""
    displayLink: str | None = None  # noqa: N815 - API field name


class _CseResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    items: list[_Item] = Field(default_factory=list)


class GoogleCseSource:
    name = "google_cse"

    def __init__(self, api_key: str | None, engine_id: str | None, client: httpx.Client | None = None, sleep=None):  # type: ignore[no-untyped-def]
        self.api_key = api_key
        self.engine_id = engine_id
        self.client = client or httpx.Client(timeout=15)
        self.sleep = sleep

    def status(self) -> dict[str, Any]:
        return {
            "configured": bool(self.api_key and self.engine_id),
            "requires_key": True,
            "cost": "Google Custom Search JSON API: closed to new customers, shuts down 2027-01-01. Existing projects only; quota per your Google Cloud plan.",
        }

    def _query(self, q: str, num: int) -> _CseResponse:
        if not (self.api_key and self.engine_id):
            raise SourceError("Google Custom Search key or engine ID is not configured", code="not_configured", permanent=True)
        kw: dict[str, Any] = {"sleep": self.sleep} if self.sleep else {}
        # Google requires the key as a query parameter; it is never logged (httpx logging is disabled).
        data = request_json(
            self.client,
            "GET",
            CSE_URL,
            provider="Google Custom Search",
            params={"key": self.api_key, "cx": self.engine_id, "q": q, "num": max(1, min(num, 10))},
            **kw,
        )
        try:
            return _CseResponse.model_validate(data)
        except ValidationError as exc:
            raise SourceError("Google returned an unexpected response", code="invalid_response", permanent=True) from exc

    def test(self) -> str:
        res = self._query("careers", 1)
        return f"Google Custom Search reachable ({len(res.items)} result for a test query)."

    def discover(self, config: DiscoveryConfig, limit: int) -> list[RawSignal]:
        queries = config.search_queries or [" ".join(config.keywords[:3])]
        queries = [q for q in queries if q.strip()]
        if not queries:
            raise SourceError("Add search queries or keywords in Discovery rules", code="needs_targeting", permanent=True)
        out: list[RawSignal] = []
        for q in queries:
            if len(out) >= limit:
                break
            for item in self._query(q, min(10, limit - len(out))).items:
                link = canonical_url(item.link)
                domain = domain_of(link)
                if domain in config.excluded_domains:
                    continue
                ok, category, hits = matches_targets(f"{item.title}\n{item.snippet}", config)
                if not ok:
                    continue
                out.append(
                    RawSignal(
                        source_provider="google_cse",
                        source_type="search_result",
                        dedupe_basis=link,
                        title=item.title[:500],
                        source_url=link,
                        category=category,
                        company_domain=domain,
                        excerpt=item.snippet[: config.max_evidence_chars],
                        evidence=[
                            {
                                "kind": "search_result",
                                "url": link,
                                "text": item.snippet,
                                "query": q,
                                "retrieved_at": utcnow().isoformat(),
                                "provider": "google_cse",
                                "matched_keywords": hits,
                            }
                        ],
                    )
                )
        return out
