"""Finding the manufacturer's own page for a part number.

There is a distinction here worth stating plainly, because it is the difference
between following the sourcing rules and breaking them:

**A search index is used to locate a URL. It is never used as a source of data.**

Every query is `site:`-restricted to a domain the record is already permitted to
read, every returned link is re-checked against `backend.sourcing.policy`, and
the snippets the search engine returns are discarded — only the URLs survive. The
product data itself is then read from the manufacturer's own page. So the answer
to "where did this attribute come from" is always a first-party URL.

Two discovery routes, tried in order:

1. `site:`-restricted search, which handles the general case.
2. Per-domain URL templates for sites whose product pages are addressable by
   part number. Cheaper and faster when it hits, and it needs no third party at
   all — but every candidate is still fetched and confirmed to mention the part
   number before it is believed.

A candidate is only ever a candidate. `research.py` fetches it and checks the
part number actually appears before any evidence is recorded.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urldefrag, urlparse

from backend.config import settings
from backend.sourcing.policy import allowed, is_blocked, registrable

logger = logging.getLogger(__name__)

# Keyless HTML search endpoints, in measured order of usefulness. Brave is first
# because it is the only one of the five tested that honours a `site:` operator
# and returns the exact product URL; DuckDuckGo's HTML endpoints answer 202 to a
# non-browser client and Mojeek, Startpage and Marginalia returned no on-site
# results for part-number queries. The others stay as fallbacks in case Brave
# rate-limits: a degraded route is better than none.
_SEARCH_ENDPOINTS: tuple[str, ...] = (
    "https://search.brave.com/search?q={query}",
    "https://html.duckduckgo.com/html/?q={query}",
    "https://www.mojeek.com/search?q={query}",
)

# URL shapes that address a product page directly by part number. Every
# candidate is still fetched and confirmed to mention the part number before it
# is believed, so a template that guesses the wrong path simply fails its check
# and costs one request — it can never produce a wrong value.
_URL_TEMPLATES: dict[str, tuple[str, ...]] = {
    "frigidaire.com": ("https://www.frigidaire.com/en/p/owner-center/product-support/{mpn}",),
    "speedqueen.com": (
        "https://speedqueen.com/products/{mpn_lower}/",
        "https://www.speedqueen.com/products/{mpn_lower}/",
    ),
    "alliancelaundry.com": ("https://www.alliancelaundry.com/products/{mpn_lower}",),
    "satco.com": (
        "https://satco.com/products/{mpn_lower}/",
        "https://www.satco.com/products/{mpn_lower}/",
    ),
    "leviton.com": ("https://www.leviton.com/en/products/{mpn_lower}",),
    "milwaukeetool.com": (
        "https://www.milwaukeetool.com/Products/Power-Tools/{mpn}",
        "https://www.milwaukeetool.com/Products/{mpn}",
    ),
    "dewalt.com": (
        "https://www.dewalt.com/product/{mpn_lower}",
        "https://www.dewalt.com/products/{mpn_lower}",
    ),
    "makitatools.com": (
        "https://www.makitatools.com/products/details/{mpn}",
        "https://www.makitatools.com/products/{mpn_lower}",
    ),
    "trex.com": ("https://www.trex.com/products/{mpn_lower}/",),
    "kichler.com": ("https://www.kichler.com/products/{mpn_lower}/",),
    "lithonia.com": ("https://lithonia.acuitybrands.com/products/detail/{mpn_lower}",),
    # Lighting and electrical brands whose catalogues key pages by part number.
    "lighting.philips.com": (
        "https://www.lighting.philips.com/main/professional/p/{mpn}",
        "https://www.lighting.philips.com/consumer/p/{mpn}",
    ),
    "philips.com": ("https://www.usa.lighting.philips.com/p/{mpn}",),
    "signify.com": ("https://www.signify.com/global/our-company/products/{mpn}",),
    "southwire.com": (
        "https://www.southwire.com/ProductCatalog/ProductDetail.aspx?part={mpn}",
        "https://www.southwire.com/products/{mpn_lower}",
    ),
    "hagerco.com": ("https://hagerco.com/us/en/product/{mpn_lower}",),
    "mirka.com": ("https://www.mirka.com/en-us/products/{mpn_lower}",),
    "edgeeyewear.com": ("https://www.edgeeyewear.com/products/{mpn_lower}",),
    "ustape.com": ("https://www.ustape.com/products/{mpn_lower}",),
}

_RESULT_HREF = re.compile(r'<a[^>]+class="[^"]*result(?:__a|-link)[^"]*"[^>]+href="([^"]+)"', re.I)
_ANY_HREF = re.compile(r'href="(https?://[^"]+|//duckduckgo\.com/l/\?[^"]+)"', re.I)


def _unwrap(href: str) -> str:
    """Resolve a DuckDuckGo redirect wrapper down to the real destination."""
    href = href.strip().replace("&amp;", "&")
    if href.startswith("//"):
        href = f"https:{href}"
    parsed = urlparse(href)
    if "duckduckgo.com" in (parsed.hostname or "") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return urldefrag(target)[0] if target else ""
    if parsed.scheme in {"http", "https"}:
        return urldefrag(href)[0]
    return ""


class SearchClient:
    """`site:`-restricted URL discovery, cached on disk like every other call."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or (settings.cache_path / "search")
        if settings.web_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = None
        self._lock = threading.Lock()
        self._last_call = 0.0
        self.queries = 0
        self.cache_hits = 0

    def _http(self):
        with self._lock:
            if self._client is None:
                import httpx

                from backend.sourcing.fetch import USER_AGENT

                self._client = httpx.Client(
                    follow_redirects=True,
                    timeout=settings.web_timeout,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept": "text/html,application/xhtml+xml",
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                )
            return self._client

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    def _cache_file(self, query: str) -> Path:
        digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:40]
        return self.cache_dir / f"{digest}.json"

    def _cached(self, query: str) -> list[str] | None:
        if not settings.web_cache:
            return None
        path = self._cache_file(query)
        if not path.exists():
            return None
        try:
            return list(json.loads(path.read_text(encoding="utf-8")).get("urls", []))
        except (json.JSONDecodeError, OSError):
            return None

    def _store(self, query: str, urls: list[str]) -> None:
        if not settings.web_cache:
            return
        try:
            self._cache_file(query).write_text(
                json.dumps({"query": query, "urls": urls}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass

    def search(self, query: str, permitted: set[str], limit: int = 6) -> list[str]:
        """Return permitted URLs for a query. Snippets are deliberately dropped."""
        cached = self._cached(query)
        if cached is not None:
            self.cache_hits += 1
            return [u for u in cached if allowed(u, permitted)][:limit]

        if not settings.web_enabled:
            return []

        found: list[str] = []
        for template in _SEARCH_ENDPOINTS:
            # One request per second to the index, shared across worker threads.
            with self._lock:
                gap = time.monotonic() - self._last_call
                if gap < settings.web_search_delay:
                    time.sleep(settings.web_search_delay - gap)
                self._last_call = time.monotonic()
            try:
                self.queries += 1
                response = self._http().get(template.format(query=quote_plus(query)))
            except Exception as exc:  # noqa: BLE001 - search is best-effort
                logger.debug("search failed (%s): %s", template, exc)
                continue
            if response.status_code >= 400:
                continue

            body = response.text
            hrefs = _RESULT_HREF.findall(body) or _ANY_HREF.findall(body)
            for href in hrefs:
                url = _unwrap(href)
                if not url or is_blocked(url):
                    continue
                if url not in found:
                    found.append(url)
            if found:
                break

        self._store(query, found)
        return [u for u in found if allowed(u, permitted)][:limit]


_search: SearchClient | None = None
_search_lock = threading.Lock()


def get_search() -> SearchClient:
    global _search
    with _search_lock:
        if _search is None:
            _search = SearchClient()
    return _search


def reset_search() -> None:
    global _search
    with _search_lock:
        if _search is not None:
            _search.close()
        _search = None


def template_candidates(mpn: str, domains: set[str]) -> list[str]:
    """Direct URLs for domains whose product pages are addressable by part number."""
    out: list[str] = []
    for domain in domains:
        for template in _URL_TEMPLATES.get(registrable(domain), ()):
            out.append(
                template.format(
                    mpn=quote_plus(mpn), mpn_lower=quote_plus(mpn.casefold())
                )
            )
    return out


def _rank(urls: list[str], mpn: str) -> list[str]:
    """Put URLs that name the part number first, category pages last.

    A path containing the part number is almost certainly the product's own page.
    A search or category URL might mention it in a listing, which is far weaker,
    so it sinks — the research stage will still verify whatever it opens.
    """
    folded = re.sub(r"[^a-z0-9]+", "", mpn.casefold())

    def key(url: str) -> tuple[int, int, int]:
        low = re.sub(r"[^a-z0-9]+", "", url.casefold())
        has_mpn = 0 if folded and folded in low else 1
        listing = 1 if any(
            k in url.casefold()
            for k in ("/search", "?query", "view-all", "/category", "/catalog", "/collections")
        ) else 0
        return (has_mpn, listing, len(url))

    return sorted(urls, key=key)


def candidates(mpn: str, domains: set[str], *, extra_terms: str = "") -> list[str]:
    """Ordered candidate URLs for this part number, first-party only."""
    if not mpn or not domains:
        return []

    templates = template_candidates(mpn, domains)
    found: list[str] = []
    client = get_search()

    for domain in sorted(domains):
        for query in (
            f'site:{domain} "{mpn}"',
            f"site:{domain} {mpn} {extra_terms}".strip(),
        ):
            for url in client.search(query, domains):
                if url not in found:
                    found.append(url)
            # A part-number-bearing URL on the first query is all we need.
            if any(re.sub(r"[^a-z0-9]+", "", mpn.casefold()) in
                   re.sub(r"[^a-z0-9]+", "", u.casefold()) for u in found):
                break

    ordered = list(dict.fromkeys([*_rank(found, mpn), *templates]))
    return ordered[:10]
