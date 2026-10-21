"""Signal-source provider interface plus the key-free sources: RSS/Atom feeds, company websites,
and manual (user-submitted) signals. External APIs live in apollo.py and search.py."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

from defusedxml import ElementTree as SafeET  # XML from the internet: no entity expansion / XXE

from app.db.base import utcnow
from app.services.discovery.classifier import classify, keyword_hits
from app.services.discovery.config import DiscoveryConfig
from app.services.discovery.fetcher import FEED_TYPES, HTML_TYPES, FetchBlocked, FetchFailed, SafeFetcher, canonical_url, domain_of


@dataclass
class RawSignal:
    source_provider: str
    source_type: str
    dedupe_basis: str  # provider-specific stable identity (guid / url+hash / external id)
    title: str | None = None
    source_url: str | None = None
    source_external_id: str | None = None
    category: str | None = None
    company_name: str | None = None
    company_domain: str | None = None
    contact_name: str | None = None
    contact_title: str | None = None
    contact_external_id: str | None = None
    published_at: datetime | None = None
    excerpt: str | None = None
    content_hash: str | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)  # provider facts (industry, size, location...)

    @property
    def dedupe_key(self) -> str:
        return hashlib.sha256(f"{self.source_provider}|{self.dedupe_basis}".encode()).hexdigest()


class SourceError(Exception):
    def __init__(self, message: str, *, code: str = "source_error", permanent: bool = False):
        super().__init__(message)
        self.message = message
        self.code = code
        self.permanent = permanent


class SignalSource(Protocol):
    name: str

    def status(self) -> dict[str, Any]: ...

    def discover(self, config: DiscoveryConfig, limit: int) -> list[RawSignal]: ...


def _clip(text: str | None, n: int) -> str | None:
    if not text:
        return None
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"


def snippet_around(text: str, phrase: str, width: int = 400) -> str:
    i = text.lower().find(phrase.lower()) if phrase else -1
    if i < 0:
        return _clip(text, width) or ""
    start = max(0, i - width // 3)
    return _clip(("…" if start else "") + text[start : start + width], width + 2) or ""


def matches_targets(text: str, config: DiscoveryConfig) -> tuple[bool, str | None, list[str]]:
    """A cheap pre-filter: excluded keywords drop the item; otherwise it needs a category or keyword hit."""
    if keyword_hits(text, config.excluded_keywords):
        return False, None, []
    cats = [c for c in classify(text) if c.category in config.signal_categories]
    hits = keyword_hits(text, config.keywords)
    if not cats and not hits:
        return False, None, hits
    category = cats[0].category if cats else "custom_keyword"
    return True, category, hits


# --- RSS / Atom ---------------------------------------------------------------------------------

_ATOM = "{http://www.w3.org/2005/Atom}"


def _strip_html(s: str | None) -> str:
    return re.sub(r"<[^>]+>", " ", s or "")


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return min(dt.astimezone(UTC), utcnow())


def parse_feed(raw: bytes) -> list[dict[str, Any]]:
    try:
        root = SafeET.fromstring(raw)
    except Exception as exc:  # noqa: BLE001 - includes defusedxml security errors
        raise SourceError(f"Feed could not be parsed: {type(exc).__name__}", code="invalid_feed", permanent=True) from exc
    items: list[dict[str, Any]] = []
    for it in root.iter("item"):  # RSS 2.0
        items.append(
            {
                "title": (it.findtext("title") or "").strip(),
                "link": (it.findtext("link") or "").strip(),
                "guid": (it.findtext("guid") or "").strip(),
                "summary": _strip_html(it.findtext("description")),
                "published": _parse_date(it.findtext("pubDate")),
            }
        )
    for it in root.iter(f"{_ATOM}entry"):  # Atom
        link = ""
        for ln in it.findall(f"{_ATOM}link"):
            if ln.get("rel") in (None, "alternate"):
                link = ln.get("href") or ""
                break
        items.append(
            {
                "title": (it.findtext(f"{_ATOM}title") or "").strip(),
                "link": link.strip(),
                "guid": (it.findtext(f"{_ATOM}id") or "").strip(),
                "summary": _strip_html(it.findtext(f"{_ATOM}summary") or it.findtext(f"{_ATOM}content")),
                "published": _parse_date(it.findtext(f"{_ATOM}published") or it.findtext(f"{_ATOM}updated")),
            }
        )
    return items


class RssSource:
    name = "rss"

    def __init__(self, fetcher: SafeFetcher):
        self.fetcher = fetcher

    def status(self) -> dict[str, Any]:
        return {"configured": True, "requires_key": False, "cost": "free (public feeds you choose)"}

    def discover(self, config: DiscoveryConfig, limit: int) -> list[RawSignal]:
        out: list[RawSignal] = []
        errors: list[str] = []
        for feed in config.feeds:
            if len(out) >= limit:
                break
            try:
                page = self.fetcher.fetch(feed.url, allow=FEED_TYPES + HTML_TYPES, use_cache=False)
            except (FetchBlocked, FetchFailed) as exc:
                errors.append(f"{feed.url}: {exc}")
                continue
            if page.not_modified or not page.raw:
                continue
            for item in parse_feed(page.raw):
                if len(out) >= limit:
                    break
                text = f"{item['title']}\n{item['summary']}"
                ok, category, hits = matches_targets(text, config)
                if feed.category and ok:
                    category = feed.category
                if not ok:
                    continue
                link = canonical_url(item["link"]) if item["link"].startswith(("http://", "https://")) else None
                domain = feed.company_domain or (domain_of(link) if link else None)
                retrieved = utcnow().isoformat()
                excerpt = _clip(item["summary"], config.max_evidence_chars) or _clip(item["title"], 300)
                out.append(
                    RawSignal(
                        source_provider="rss",
                        source_type="feed_item",
                        dedupe_basis=item["guid"] or link or hashlib.sha256(text.encode()).hexdigest(),
                        title=_clip(item["title"], 500),
                        source_url=link,
                        source_external_id=_clip(item["guid"], 255),
                        category=category,
                        company_name=feed.company_name,
                        company_domain=domain,
                        published_at=item["published"],
                        excerpt=excerpt,
                        content_hash=hashlib.sha256(text.encode()).hexdigest(),
                        evidence=[
                            {
                                "kind": "feed_item",
                                "url": link or feed.url,
                                "feed": feed.url,
                                "text": excerpt,
                                "retrieved_at": retrieved,
                                "provider": "rss",
                                "matched_keywords": hits,
                            }
                        ],
                    )
                )
        if errors and not out and len(errors) == len(config.feeds):
            raise SourceError("; ".join(errors)[:500], code="all_feeds_failed")
        return out


# --- Company websites --------------------------------------------------------------------------------

INTERESTING_PATH = re.compile(
    r"/(careers?|jobs?|join|work-with-us|about|news|press|blog|newsroom|announcements?|company|contact)(/|$|\?)", re.I
)


def relevant_links(links: list[str], domain: str, max_n: int) -> list[str]:
    out = []
    for link in links:
        host = domain_of(link)
        if host != domain:
            continue
        if INTERESTING_PATH.search(urlsplit(link).path + "/"):
            out.append(canonical_url(link))
    return list(dict.fromkeys(out))[:max_n]


class WebsiteSource:
    name = "website"

    def __init__(self, fetcher: SafeFetcher):
        self.fetcher = fetcher

    def status(self) -> dict[str, Any]:
        return {"configured": True, "requires_key": False, "cost": "free (official company pages; robots.txt respected)"}

    def company_pages(self, domain: str, max_pages: int) -> list[Any]:
        """Homepage + linked About/Careers/News/Contact pages on the same domain (bounded)."""
        pages = []
        home = self.fetcher.fetch(f"https://{domain}/")
        pages.append(home)
        for link in relevant_links(home.links, domain, max_pages - 1):
            try:
                pages.append(self.fetcher.fetch(link))
            except (FetchBlocked, FetchFailed):
                continue
        return pages

    def discover(self, config: DiscoveryConfig, limit: int) -> list[RawSignal]:
        out: list[RawSignal] = []
        for domain in config.watch_domains:
            if len(out) >= limit:
                break
            if domain in config.excluded_domains:
                continue
            try:
                pages = self.company_pages(domain, config.max_pages_per_domain)
            except (FetchBlocked, FetchFailed):
                continue
            company = (pages[0].title.split("|")[-1].split("–")[-1].split("-")[-1].strip() if pages and pages[0].title else None) or domain
            for page in pages:
                if len(out) >= limit:
                    break
                text = f"{page.title}\n{page.text}"
                ok, category, hits = matches_targets(text, config)
                if not ok:
                    continue
                phrase = hits[0] if hits else next((c.phrase for c in classify(text) if c.category == category), "")
                excerpt = snippet_around(page.text, phrase, min(config.max_evidence_chars, 600))
                out.append(
                    RawSignal(
                        source_provider="website",
                        source_type="web_page",
                        dedupe_basis=f"{page.final_url}|{page.content_hash}",
                        title=_clip(page.title, 500) or page.final_url,
                        source_url=page.final_url,
                        category=category,
                        company_name=_clip(company, 255),
                        company_domain=domain,
                        excerpt=excerpt,
                        content_hash=page.content_hash,
                        evidence=[
                            {
                                "kind": "web_page",
                                "url": page.final_url,
                                "text": excerpt,
                                "retrieved_at": utcnow().isoformat(),
                                "provider": "website",
                                "matched_keywords": hits,
                                "matched_phrase": phrase,
                            }
                        ],
                    )
                )
        return out


# --- Manual / user-submitted (incl. LinkedIn posts the user pastes) -------------------------------------


def is_linkedin(url: str | None) -> bool:
    return bool(url) and (domain_of(url or "") or "").endswith("linkedin.com")


def manual_signal(
    *,
    url: str | None,
    text: str | None,
    title: str | None,
    company_name: str | None,
    company_domain: str | None,
    contact_name: str | None,
    contact_title: str | None,
    category: str | None,
    published_at: datetime | None,
    fetcher: SafeFetcher | None,
    max_chars: int,
) -> RawSignal:
    """Build a signal from what the user supplied. LinkedIn is never fetched — the user must paste the text."""
    source_type = "linkedin_post" if is_linkedin(url) else "user_submitted"
    evidence_text = (text or "").strip()
    page_title = title
    final_url = canonical_url(url) if url else None
    if not evidence_text and url:
        if is_linkedin(url):
            raise SourceError(
                "LinkedIn content is not fetched automatically. Paste the post text you are authorised to use.",
                code="linkedin_text_required",
                permanent=True,
            )
        if fetcher is None:
            raise SourceError("No text supplied", code="text_required", permanent=True)
        try:
            page = fetcher.fetch(url)
        except FetchBlocked as exc:
            raise SourceError(
                f"Could not fetch this URL: {exc}. Paste the relevant text instead.", code="fetch_blocked", permanent=True
            ) from exc
        except FetchFailed as exc:
            raise SourceError(f"Could not fetch this URL: {exc}", code="fetch_failed") from exc
        evidence_text, page_title, final_url = page.text, page_title or page.title, page.final_url
    if not evidence_text:
        raise SourceError("Supply the signal text or a fetchable URL", code="text_required", permanent=True)
    cats = classify(f"{page_title or ''}\n{evidence_text}")
    excerpt = _clip(evidence_text, max_chars)
    basis = final_url or hashlib.sha256(evidence_text.encode()).hexdigest()
    return RawSignal(
        source_provider="manual",
        source_type=source_type,
        dedupe_basis=f"{basis}|{hashlib.sha256(evidence_text.encode()).hexdigest()}",
        title=_clip(page_title, 500) or _clip(evidence_text, 120),
        source_url=final_url,
        category=category or (cats[0].category if cats else "custom_keyword"),
        company_name=company_name,
        company_domain=(company_domain or (domain_of(final_url) if final_url and not is_linkedin(final_url) else None)),
        contact_name=contact_name,
        contact_title=contact_title,
        published_at=published_at,
        excerpt=excerpt,
        content_hash=hashlib.sha256(evidence_text.encode()).hexdigest(),
        evidence=[
            {
                "kind": source_type,
                "url": final_url,
                "text": excerpt,
                "retrieved_at": utcnow().isoformat(),
                "provider": "manual",
                "supplied_by_user": bool(text),
            }
        ],
    )
