"""A polite, cached HTTP fetcher for first-party manufacturer sources.

Design mirrors `backend/llm/client.py` on purpose, because the failure modes are
the same: an external service that is slow, rate-limited and sometimes absent.

* **Cache first.** Every retrieval is written to `.cache/web/` keyed by URL hash.
  A second run costs nothing and a demo replays with the network unplugged.
* **Fail soft.** A timeout, a 403 bot wall, a 404 or a missing dependency returns
  `None`. The research stage treats that as "no evidence" and the pipeline
  continues with the deterministic path, exactly as it does when the LLM is
  exhausted.
* **Polite.** `robots.txt` is honoured per host and cached, requests to one host
  are spaced by a minimum interval, only rate limits and gateway errors are
  retried (once), redirects are re-checked against the sourcing policy, and the
  response size is capped so a 200 MB catalogue PDF cannot exhaust memory.

**What gets cached is the extracted document, not the raw HTML.** Parsing happens
here rather than downstream for one concrete reason: a manufacturer product page
is routinely 1-3 MB of application shell wrapping 6 KB of product data. Caching
the raw markup meant either storing megabytes per URL or truncating mid-document
and losing the specification block entirely — which is precisely the bug this
layout removes. The cache now holds title, prose, specification pairs and
document links: small, diffable, and exactly what the pipeline consumes.

The policy check is applied twice: before the request, and again on the final URL
after redirects. A distributor that a manufacturer redirects to is still a
distributor.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import urllib.robotparser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.config import settings
from backend.sourcing.extract import parse_html
from backend.sourcing.policy import allowed, host_of, is_blocked

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (compatible; UniHackProductIntelligence/1.0; "
    "+enrichment research bot; contact: hackathon participant)"
)

# Hard ceiling on what we will pull over the wire for one URL.
MAX_BYTES = 8_000_000
# Extracted text kept per document. Ample for a full specification page; a
# 60-page installation manual is truncated rather than stored whole.
MAX_TEXT = 200_000
# Cache-entry format version. Bumping this invalidates older entries rather than
# silently reading a payload written by a different extractor.
CACHE_VERSION = 3


@dataclass
class Document:
    """One retrieved and extracted source."""

    url: str
    status: int
    kind: str  # html | pdf | other
    title: str = ""
    text: str = ""
    tables: dict[str, str] = field(default_factory=dict)
    links: list[tuple[str, str]] = field(default_factory=list)
    from_cache: bool = False

    @property
    def usable(self) -> bool:
        return self.status < 400 and bool(self.text or self.tables)


@dataclass
class FetchStats:
    """Counters surfaced in the metrics endpoint so retrieval is auditable."""

    requests: int = 0
    cache_hits: int = 0
    failures: int = 0
    blocked: int = 0
    robots_denied: int = 0
    bytes_down: int = 0
    seconds: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(
        self,
        *,
        cached: bool = False,
        failed: bool = False,
        blocked: bool = False,
        robots: bool = False,
        size: int = 0,
        seconds: float = 0.0,
    ) -> None:
        with self._lock:
            self.requests += 1
            self.cache_hits += int(cached)
            self.failures += int(failed)
            self.blocked += int(blocked)
            self.robots_denied += int(robots)
            self.bytes_down += size
            self.seconds += seconds

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            live = self.requests - self.cache_hits
            return {
                "requests": self.requests,
                "cache_hits": self.cache_hits,
                "live_requests": live,
                "failures": self.failures,
                "blocked_by_policy": self.blocked,
                "robots_denied": self.robots_denied,
                "megabytes": round(self.bytes_down / 1_000_000, 2),
                "avg_latency_s": round(self.seconds / live, 2) if live else 0.0,
                "cache_hit_rate": (
                    round(self.cache_hits / self.requests, 3) if self.requests else 0.0
                ),
            }


def _pdf_text(payload: bytes) -> str:
    """Extract text from a PDF, or return empty when it is an image scan."""
    try:
        import io

        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - dependency is pinned
        logger.debug("pypdf not installed; skipping PDF extraction")
        return ""
    # Manufacturer PDFs are frequently malformed ("EOF marker not found" and
    # similar). pypdf recovers from these but shouts about it on stderr, which
    # buries the progress output of a run. The condition is already handled, so
    # the warning is demoted rather than printed.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        reader = PdfReader(io.BytesIO(payload))
        return "\n".join((page.extract_text() or "") for page in reader.pages[:12])
    except Exception as exc:  # noqa: BLE001 - a malformed PDF is not fatal
        logger.debug("pdf extraction failed: %s", exc)
        return ""


class WebFetcher:
    """Cached, robots-aware fetcher restricted to first-party sources."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or (settings.cache_path / "web")
        self.enabled = settings.web_enabled
        if settings.web_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.stats = FetchStats()

        self._client: Any = None
        self._client_lock = threading.Lock()
        self._robots: dict[str, Any] = {}
        self._robots_lock = threading.Lock()
        self._last_hit: dict[str, float] = {}
        self._pace_lock = threading.Lock()

    # -- plumbing ----------------------------------------------------------
    def _http(self) -> Any:
        """Lazily build one shared client so connections are pooled."""
        with self._client_lock:
            if self._client is None:
                import httpx

                self._client = httpx.Client(
                    follow_redirects=True,
                    timeout=httpx.Timeout(settings.web_timeout),
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept": "text/html,application/xhtml+xml,application/pdf,*/*",
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                    limits=httpx.Limits(max_connections=settings.web_concurrency),
                )
            return self._client

    def close(self) -> None:
        with self._client_lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:40]
        return self.cache_dir / f"{digest}.json"

    def _cache_read(self, url: str) -> Document | None:
        if not settings.web_cache:
            return None
        path = self._cache_path(url)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if int(payload.get("version", 1)) != CACHE_VERSION:
            return None
        return Document(
            url=payload.get("final_url", url),
            status=int(payload.get("status", 0)),
            kind=payload.get("kind", "other"),
            title=payload.get("title", ""),
            text=payload.get("text", ""),
            tables=dict(payload.get("tables") or {}),
            links=[tuple(pair) for pair in payload.get("links") or []],
            from_cache=True,
        )

    def _cache_write(self, url: str, doc: Document) -> None:
        if not settings.web_cache:
            return
        try:
            self._cache_path(url).write_text(
                json.dumps(
                    {
                        "version": CACHE_VERSION,
                        "requested_url": url,
                        "final_url": doc.url,
                        "status": doc.status,
                        "kind": doc.kind,
                        "title": doc.title,
                        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "text": doc.text[:MAX_TEXT],
                        "tables": doc.tables,
                        "links": [list(pair) for pair in doc.links[:60]],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.debug("web cache write failed: %s", exc)

    def _robots_ok(self, url: str) -> bool:
        """Honour robots.txt, treating an unreachable file as permission."""
        if not settings.web_respect_robots:
            return True
        host = host_of(url)
        if not host:
            return False
        with self._robots_lock:
            parser = self._robots.get(host)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            try:
                response = self._http().get(f"https://{host}/robots.txt")
                if response.status_code < 400:
                    parser.parse(response.text.splitlines())
                else:
                    parser.allow_all = True
            except Exception:  # noqa: BLE001 - absent robots.txt means allowed
                parser.allow_all = True
            with self._robots_lock:
                self._robots[host] = parser
        try:
            return bool(parser.can_fetch(USER_AGENT, url))
        except Exception:  # noqa: BLE001
            return True

    def _pace(self, url: str) -> None:
        """Space requests to a single host by the configured minimum interval."""
        host = host_of(url)
        delay = settings.web_delay_seconds
        if not host or delay <= 0:
            return
        while True:
            with self._pace_lock:
                now = time.monotonic()
                earliest = self._last_hit.get(host, 0.0) + delay
                if now >= earliest:
                    self._last_hit[host] = now
                    return
                wait = earliest - now
            time.sleep(min(wait, delay))

    # -- public API --------------------------------------------------------
    def document(self, url: str, permitted: set[str]) -> Document | None:
        """Fetch and extract one first-party URL, or `None` for any refusal.

        `permitted` is the set of registrable domains this record may read.
        """
        if not url:
            return None

        cached = self._cache_read(url)
        if cached is not None:
            self.stats.record(cached=True)
            # A cached entry is re-checked: the policy may have tightened since
            # it was written.
            if not allowed(cached.url, permitted):
                self.stats.record(blocked=True)
                return None
            return cached if cached.usable else None

        if not self.enabled:
            return None
        if not allowed(url, permitted):
            self.stats.record(blocked=True)
            logger.debug("refused by sourcing policy: %s", url)
            return None
        if not self._robots_ok(url):
            self.stats.record(robots=True)
            logger.debug("refused by robots.txt: %s", url)
            return None

        started = time.perf_counter()
        response = None
        # One retry, and only for a rate limit or a transient gateway error. A
        # 403 is a bot wall and a 404 is a wrong guess; neither improves on a
        # second attempt, and hammering a manufacturer's site is not acceptable.
        for attempt in range(2):
            self._pace(url)
            try:
                response = self._http().get(url)
            except Exception as exc:  # noqa: BLE001 - network problems are expected
                logger.debug("fetch failed %s: %s", url, exc)
                self.stats.record(failed=True, seconds=time.perf_counter() - started)
                return None
            if response.status_code not in (429, 502, 503, 504) or attempt:
                break
            time.sleep(min(float(response.headers.get("retry-after", 2) or 2), 5.0))

        if response is None:
            self.stats.record(failed=True, seconds=time.perf_counter() - started)
            return None

        elapsed = time.perf_counter() - started
        final_url = str(response.url)

        # Re-check after redirects: a manufacturer may bounce us to a reseller.
        if is_blocked(final_url) or not allowed(final_url, permitted):
            self.stats.record(blocked=True, seconds=elapsed)
            logger.debug("redirect left permitted domains: %s -> %s", url, final_url)
            return None

        payload = response.content[:MAX_BYTES]
        content_type = response.headers.get("content-type", "").casefold()
        self.stats.record(
            failed=response.status_code >= 400, size=len(payload), seconds=elapsed
        )

        doc = Document(url=final_url, status=response.status_code, kind="other")

        if "pdf" in content_type or final_url.lower().split("?")[0].endswith(".pdf"):
            doc.kind = "pdf"
            doc.text = _pdf_text(payload)[:MAX_TEXT]
            doc.title = final_url.rsplit("/", 1)[-1]
        elif "html" in content_type or "xml" in content_type or not content_type:
            doc.kind = "html"
            # Decoded and parsed in full: extraction shrinks megabytes of
            # application shell down to the product data worth keeping.
            html = payload.decode(response.encoding or "utf-8", errors="replace")
            title, text, tables, links = parse_html(html, final_url)
            doc.title, doc.text, doc.tables, doc.links = (
                title,
                text[:MAX_TEXT],
                tables,
                links,
            )

        self._cache_write(url, doc)
        return doc if doc.usable else None


_fetcher: WebFetcher | None = None
_fetcher_lock = threading.Lock()


def get_fetcher() -> WebFetcher:
    """Process-wide singleton so the cache, pacing and stats are shared."""
    global _fetcher
    with _fetcher_lock:
        if _fetcher is None:
            _fetcher = WebFetcher()
    return _fetcher


def reset_fetcher() -> None:
    global _fetcher
    with _fetcher_lock:
        if _fetcher is not None:
            _fetcher.close()
        _fetcher = None
