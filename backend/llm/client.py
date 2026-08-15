"""The LLM gateway used by every agent.

Responsibilities:
* route a request across providers and their model chains, so a model that runs
  out of daily quota is skipped instead of failing the batch
* cache every response on disk keyed by prompt hash, making re-runs free
* retry transient errors with backoff, then fail soft and return `None`
* track usage, latency and per-model outcomes for the metrics dashboard

Failing soft matters: agents treat `None` as "no answer" and fall back to
deterministic logic, so a quota wall degrades quality instead of crashing a run.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.config import settings
from backend.llm.providers import (
    GeminiProvider,
    GroqProvider,
    Outcome,
    Provider,
    Reply,
)

logger = logging.getLogger(__name__)


@dataclass
class UsageStats:
    """Aggregate LLM usage, surfaced in the metrics endpoint."""

    calls: int = 0
    cache_hits: int = 0
    failures: int = 0
    total_tokens: int = 0
    total_seconds: float = 0.0
    by_model: Counter = field(default_factory=Counter)
    exhausted: set[str] = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(
        self, *, tokens: int = 0, seconds: float = 0.0, cached: bool = False,
        failed: bool = False, model: str = "",
    ) -> None:
        with self._lock:
            self.calls += 1
            if cached:
                self.cache_hits += 1
            if failed:
                self.failures += 1
            self.total_tokens += tokens
            self.total_seconds += seconds
            if model:
                self.by_model[model] += 1

    def mark_exhausted(self, key: str) -> None:
        with self._lock:
            self.exhausted.add(key)

    def is_exhausted(self, key: str) -> bool:
        with self._lock:
            return key in self.exhausted

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            live = self.calls - self.cache_hits
            return {
                "calls": self.calls,
                "cache_hits": self.cache_hits,
                "live_calls": live,
                "failures": self.failures,
                "total_tokens": self.total_tokens,
                "avg_latency_s": round(self.total_seconds / live, 2) if live else 0.0,
                "cache_hit_rate": round(self.cache_hits / self.calls, 3) if self.calls else 0.0,
                "by_model": dict(self.by_model),
                "exhausted_models": sorted(self.exhausted),
            }


class LLMClient:
    """Multi-provider, quota-aware, cached JSON generator."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache_dir = cache_dir or settings.cache_path
        self._cache_enabled = settings.enable_llm_cache
        if self._cache_enabled:
            self._cache_dir.mkdir(parents=True, exist_ok=True)

        self.stats = UsageStats()
        self._providers: dict[str, Provider] = {}
        if settings.groq_enabled:
            self._providers["groq"] = GroqProvider(settings.groq_api_key)
        if settings.gemini_enabled:
            self._providers["gemini"] = GeminiProvider(settings.gemini_api_key)
        self._order = settings.provider_order

    # -- availability --
    @property
    def available(self) -> bool:
        return bool(self._order)

    @property
    def active_provider(self) -> str:
        return self._order[0] if self._order else "none"

    def describe(self) -> dict[str, Any]:
        return {
            "providers": self._order,
            "chains": {
                name: settings.chain_for(name) for name in self._order
            },
        }

    # -- cache --
    def _cache_key(self, prompt: str, schema: dict[str, Any], tag: str) -> str:
        blob = f"{tag}\x00{prompt}\x00{json.dumps(schema, sort_keys=True)}".encode()
        return hashlib.sha256(blob).hexdigest()[:40]

    def _cache_read(self, key: str) -> dict[str, Any] | None:
        if not self._cache_enabled:
            return None
        path = self._cache_dir / f"{key}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _cache_write(self, key: str, payload: dict[str, Any]) -> None:
        if not self._cache_enabled:
            return
        try:
            (self._cache_dir / f"{key}.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            logger.debug("cache write failed: %s", exc)

    # -- main entry point --
    def generate_json(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        fast: bool = False,
        temperature: float = 0.0,
        system: str | None = None,
    ) -> dict[str, Any] | None:
        """Return parsed JSON, or `None` when every route is exhausted.

        The cache key deliberately ignores which model answered, so a cached
        result is reused even after the model chain has moved on.
        """
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        key = self._cache_key(full_prompt, schema, "fast" if fast else "std")

        cached = self._cache_read(key)
        if cached is not None:
            self.stats.record(cached=True)
            return cached

        if not self.available:
            self.stats.record(failed=True)
            return None

        last_error = "no route attempted"

        for provider_name in self._order:
            provider = self._providers[provider_name]
            if not provider.available:
                continue

            for model in settings.chain_for(provider_name, fast=fast):
                route = f"{provider_name}:{model}"
                if self.stats.is_exhausted(route):
                    continue

                reply, elapsed = self._attempt(
                    provider, model, prompt, schema, system, temperature
                )

                if reply.outcome is Outcome.OK and reply.data is not None:
                    self.stats.record(
                        tokens=reply.tokens, seconds=elapsed, model=route
                    )
                    self._cache_write(key, reply.data)
                    return reply.data

                last_error = f"{route}: {reply.error}"
                if reply.outcome in (Outcome.EXHAUSTED, Outcome.DEAD):
                    self.stats.mark_exhausted(route)
                    logger.warning("route %s disabled: %s", route, reply.error)

        self.stats.record(failed=True)
        logger.warning("all LLM routes failed: %s", last_error)
        return None

    def _attempt(
        self, provider: Provider, model: str, prompt: str,
        schema: dict[str, Any], system: str | None, temperature: float,
    ) -> tuple[Reply, float]:
        """Try one model, retrying only transient failures."""
        started = time.perf_counter()
        reply = Reply(Outcome.RETRY, error="not attempted")

        for attempt in range(settings.llm_max_retries + 1):
            reply = provider.complete(model, prompt, schema, system, temperature)
            if reply.outcome is not Outcome.RETRY:
                break
            if attempt < settings.llm_max_retries:
                time.sleep(min(2 ** attempt, 8))

        return reply, time.perf_counter() - started


_client: LLMClient | None = None
_client_lock = threading.Lock()


def get_client() -> LLMClient:
    """Process-wide singleton so cache, stats and quota state are shared."""
    global _client
    with _client_lock:
        if _client is None:
            _client = LLMClient()
    return _client


def reset_client() -> None:
    """Drop the singleton so configuration changes take effect."""
    global _client
    with _client_lock:
        _client = None


# Backwards-compatible alias: agents are typed against this name.
GeminiClient = LLMClient
