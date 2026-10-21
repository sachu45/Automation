"""Constrained, SSRF-safe fetcher for public pages and feeds. Not a general-purpose scraper.

* http/https only, default ports only, no credentials in URLs
* every hostname (including each redirect hop) must resolve only to public IPs; the
  connected peer address is checked again before the body is read (DNS-rebinding guard)
* robots.txt is honoured (RFC 9309: 4xx = allowed, 5xx/unreachable = disallowed)
* response size, content-type, redirect count and per-domain request rate are bounded
* conditional requests (ETag / Last-Modified) with a small DB cache
* fetched text is untrusted data — it is only stored/quoted, never executed or obeyed
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import utcnow
from app.models import FetchedPage

log = logging.getLogger("app.discovery.fetch")

HTML_TYPES = ("text/html", "application/xhtml+xml")
FEED_TYPES = ("application/rss+xml", "application/atom+xml", "application/xml", "text/xml", "application/feed+json")
TEXT_TYPES = ("text/plain",)
MAX_REDIRECTS = 3
MAX_TEXT_CHARS = 20_000

Resolver = Callable[[str], list[str]]


class FetchBlocked(Exception):
    """The URL is not allowed (scheme, port, private address, robots.txt...). Permanent."""


class FetchFailed(Exception):
    """Network/HTTP failure. May be temporary."""

    def __init__(self, message: str, temporary: bool = True):
        super().__init__(message)
        self.temporary = temporary


def default_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return list({str(info[4][0]) for info in infos})


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%")[0])
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().rstrip(".")
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    # drop fragments and common tracking parameters
    query = "&".join(q for q in parts.query.split("&") if q and not re.match(r"^(utm_[a-z]+|fbclid|gclid|mc_[a-z]+|ref)=", q, re.I))
    return urlunsplit((scheme, host, path, query, ""))


def domain_of(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    return host or None


def validate_url(url: str, resolver: Resolver = default_resolver) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise FetchBlocked("Only http and https URLs are allowed")
    if parts.username or parts.password:
        raise FetchBlocked("URLs with embedded credentials are not allowed")
    host = parts.hostname
    if not host:
        raise FetchBlocked("URL has no host")
    if parts.port not in (None, 80, 443):
        raise FetchBlocked("Only the default ports 80/443 are allowed")
    try:
        ipaddress.ip_address(host)
        literal = True
    except ValueError:
        literal = False
    if literal:
        if not _is_public(host):
            raise FetchBlocked("Private, loopback or reserved addresses are not allowed")
        return url
    if host.endswith((".local", ".internal", ".localhost", ".lan", ".home.arpa")) or host == "localhost":
        raise FetchBlocked("Internal hostnames are not allowed")
    try:
        ips = resolver(host)
    except OSError as exc:
        raise FetchFailed(f"DNS lookup failed for {host}", temporary=True) from exc
    if not ips:
        raise FetchFailed(f"{host} did not resolve", temporary=True)
    if not all(_is_public(ip) for ip in ips):
        raise FetchBlocked(f"{host} resolves to a private or reserved address")
    return url


class _Extractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "object", "head"}

    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.parts: list[str] = []
        self.links: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self._skip += 1
        if tag == "a":
            href = dict(attrs).get("href")
            if href and not href.startswith(("mailto:", "tel:", "javascript:", "#")):
                self.links.append(urljoin(self.base, href))
        if tag in ("p", "div", "li", "br", "h1", "h2", "h3", "h4", "section", "article", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def extract_html(html: str, base: str) -> tuple[str, str, list[str]]:
    p = _Extractor(base)
    try:
        p.feed(html)
    except Exception:  # noqa: BLE001 - malformed HTML: keep what we parsed
        pass
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.parts))
    text = re.sub(r"\n\s*\n+", "\n", text).strip()[:MAX_TEXT_CHARS]
    links = list(dict.fromkeys(link for link in p.links if link.startswith(("http://", "https://"))))[:300]
    return " ".join(p.title.split())[:500], text, links


@dataclass
class PageResult:
    url: str
    final_url: str
    status: int
    content_type: str
    title: str = ""
    text: str = ""
    links: list[str] = field(default_factory=list)
    raw: bytes = b""
    content_hash: str = ""
    not_modified: bool = False
    from_cache: bool = False


class SafeFetcher:
    def __init__(
        self,
        db: Session,
        *,
        client: httpx.Client | None = None,
        resolver: Resolver = default_resolver,
        respect_robots: bool = True,
        domain_delay: float | None = None,
        cache_ttl: timedelta = timedelta(hours=12),
        verify_peer: bool = True,
    ) -> None:
        s = get_settings()
        self.db = db
        self.client = client or httpx.Client(timeout=s.discovery_request_timeout_seconds, follow_redirects=False)
        self.resolver = resolver
        self.respect_robots = respect_robots
        self.domain_delay = s.discovery_domain_delay_seconds if domain_delay is None else domain_delay
        self.max_bytes = s.discovery_max_page_bytes
        self.user_agent = s.discovery_user_agent
        self.cache_ttl = cache_ttl
        self.verify_peer = verify_peer
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last_hit: dict[str, float] = {}
        self.requests_made = 0

    # -- public ---------------------------------------------------------------------------------

    def fetch(self, url: str, *, allow: tuple[str, ...] = HTML_TYPES + TEXT_TYPES, use_cache: bool = True) -> PageResult:
        url = canonical_url(url)
        validate_url(url, self.resolver)
        if self.respect_robots and not self._robots_allowed(url):
            raise FetchBlocked("Disallowed by robots.txt")
        cached = self._cached(url) if use_cache else None
        if cached is not None and cached.fetched_at > utcnow() - self.cache_ttl and cached.status_code == 200:
            return PageResult(
                url=url,
                final_url=cached.final_url or url,
                status=200,
                content_type=cached.content_type or "",
                title=cached.title or "",
                text=cached.text or "",
                links=list(cached.links or []),
                content_hash=cached.content_hash or "",
                from_cache=True,
            )
        headers = {"User-Agent": self.user_agent, "Accept": ", ".join(allow)}
        if cached is not None and cached.etag:
            headers["If-None-Match"] = cached.etag
        if cached is not None and cached.last_modified:
            headers["If-Modified-Since"] = cached.last_modified
        resp_url, status, ctype, body, resp_headers = self._get(url, headers, allow)
        if status == 304 and cached is not None:
            cached.fetched_at = utcnow()
            return PageResult(
                url=url,
                final_url=cached.final_url or url,
                status=304,
                content_type=cached.content_type or "",
                title=cached.title or "",
                text=cached.text or "",
                links=list(cached.links or []),
                content_hash=cached.content_hash or "",
                not_modified=True,
                from_cache=True,
            )
        result = PageResult(url=url, final_url=resp_url, status=status, content_type=ctype, raw=body)
        result.content_hash = hashlib.sha256(body).hexdigest()
        if ctype.startswith(HTML_TYPES):
            result.title, result.text, result.links = extract_html(body.decode(_charset(ctype), errors="replace"), resp_url)
        elif ctype.startswith(TEXT_TYPES):
            result.text = body.decode(_charset(ctype), errors="replace")[:MAX_TEXT_CHARS]
        self._store(url, result, resp_headers)
        return result

    # -- internals ------------------------------------------------------------------------------

    def _throttle(self, host: str) -> None:
        last = self._last_hit.get(host)
        if last is not None and self.domain_delay > 0:
            wait = self.domain_delay - (time.monotonic() - last)
            if wait > 0:
                time.sleep(min(wait, 10))
        self._last_hit[host] = time.monotonic()

    def _get(self, url: str, headers: dict[str, str], allow: tuple[str, ...]) -> tuple[str, int, str, bytes, httpx.Headers]:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            host = urlsplit(current).hostname or ""
            self._throttle(host)
            self.requests_made += 1
            try:
                with self.client.stream("GET", current, headers=headers) as resp:
                    self._check_peer(resp)
                    if resp.status_code in (301, 302, 303, 307, 308):
                        location = resp.headers.get("location")
                        if not location:
                            raise FetchFailed("Redirect without location", temporary=False)
                        current = canonical_url(urljoin(current, location))
                        validate_url(current, self.resolver)  # every hop is re-validated
                        continue
                    if resp.status_code == 304:
                        return current, 304, "", b"", resp.headers
                    if resp.status_code == 429 or resp.status_code >= 500:
                        raise FetchFailed(f"HTTP {resp.status_code}", temporary=True)
                    if resp.status_code >= 400:
                        raise FetchFailed(f"HTTP {resp.status_code}", temporary=False)
                    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                    if not ctype.startswith(allow):
                        raise FetchBlocked(f"Content type not allowed: {ctype or 'unknown'}")
                    declared = resp.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > self.max_bytes:
                        raise FetchBlocked("Response too large")
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in resp.iter_bytes():
                        size += len(chunk)
                        if size > self.max_bytes:
                            raise FetchBlocked("Response too large")
                        chunks.append(chunk)
                    return current, resp.status_code, resp.headers.get("content-type", ctype), b"".join(chunks), resp.headers
            except httpx.TimeoutException as exc:
                raise FetchFailed("Request timed out", temporary=True) from exc
            except httpx.HTTPError as exc:
                raise FetchFailed(f"Request failed: {type(exc).__name__}", temporary=True) from exc
        raise FetchBlocked("Too many redirects")

    def _check_peer(self, resp: httpx.Response) -> None:
        if not self.verify_peer:
            return
        stream = resp.extensions.get("network_stream")
        if stream is None:
            return
        addr = stream.get_extra_info("server_addr")
        if addr and not _is_public(str(addr[0])):
            raise FetchBlocked("Connected to a private address (possible DNS rebinding)")

    def _robots_allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(origin)
        rp = self._robots[origin]
        return True if rp is None else rp.can_fetch(self.user_agent, url)

    def _load_robots(self, origin: str) -> RobotFileParser | None:
        robots_url = origin + "/robots.txt"
        rp = RobotFileParser()
        try:
            validate_url(robots_url, self.resolver)
            _, status, _, body, _ = self._get(robots_url, {"User-Agent": self.user_agent}, ("text/", "application/octet-stream", ""))
        except FetchFailed as exc:
            if not exc.temporary:  # 4xx: no usable robots.txt => allowed
                return None
            rp.parse(["User-agent: *", "Disallow: /"])  # unreachable/5xx => be conservative
            return rp
        except FetchBlocked:
            rp.parse(["User-agent: *", "Disallow: /"])
            return rp
        rp.parse(body[:500_000].decode("utf-8", errors="replace").splitlines())
        return rp

    def _cached(self, url: str) -> FetchedPage | None:
        return self.db.scalar(select(FetchedPage).where(FetchedPage.url_hash == _hash(url)))

    def _store(self, url: str, result: PageResult, headers: httpx.Headers) -> None:
        if not result.content_type.startswith(HTML_TYPES + TEXT_TYPES + FEED_TYPES):
            return
        row = self._cached(url)
        if row is None:
            row = FetchedPage(url_hash=_hash(url), url=url[:1000])
            self.db.add(row)
        row.final_url = result.final_url[:1000]
        row.status_code = result.status
        row.content_type = result.content_type[:100]
        row.title = result.title[:500]
        row.text = result.text or None
        row.links = result.links
        row.content_hash = result.content_hash
        row.etag = (headers.get("etag") or "")[:255] or None
        row.last_modified = (headers.get("last-modified") or "")[:100] or None
        row.error = None
        row.fetched_at = utcnow()
        self.db.flush()


def _hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def _charset(ctype: str) -> str:
    m = re.search(r"charset=([\w-]+)", ctype, re.I)
    return m.group(1) if m else "utf-8"
