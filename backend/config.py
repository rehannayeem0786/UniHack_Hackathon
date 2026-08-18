"""Central configuration, loaded from `.env` at the project root.

Every module reads config through `settings` so there is exactly one place
where environment variables are named and defaulted.

Two LLM providers are supported. Groq is the default because its free tier
allows thousands of requests per day, where a free Gemini key allows only 20
per model per day - not enough to enrich a catalogue. Gemini is retained as an
automatic fallback, and each provider has a model chain so that a model which
exhausts its quota mid-run is skipped rather than failing the batch.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _chain(primary: str, extra: str) -> list[str]:
    """Ordered, de-duplicated model chain that always starts with `primary`."""
    return list(dict.fromkeys([primary, *_split(extra)])) if primary else _split(extra)


class Settings(BaseSettings):
    """Runtime configuration for the enrichment engine."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Provider selection ---
    # "groq" or "gemini"; the other is used automatically as a fallback.
    llm_provider: str = Field(default="groq", alias="LLM_PROVIDER")

    # --- Groq ---
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    groq_model: str = Field(default="llama-3.3-70b-versatile", alias="GROQ_MODEL")
    groq_model_fast: str = Field(
        default="llama-3.1-8b-instant", alias="GROQ_MODEL_FAST"
    )
    groq_model_chain: str = Field(default="", alias="GROQ_MODEL_CHAIN")

    # --- Gemini ---
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-3.6-flash", alias="GEMINI_MODEL")
    gemini_model_fast: str = Field(
        default="gemini-3.5-flash-lite", alias="GEMINI_MODEL_FAST"
    )
    gemini_model_chain: str = Field(default="", alias="GEMINI_MODEL_CHAIN")

    # --- Pipeline ---
    max_concurrency: int = Field(default=6, ge=1, le=64, alias="MAX_CONCURRENCY")
    llm_max_retries: int = Field(default=3, ge=0, le=10, alias="LLM_MAX_RETRIES")
    enable_llm_cache: bool = Field(default=True, alias="ENABLE_LLM_CACHE")
    cache_dir: Path = Field(default=Path(".cache"), alias="CACHE_DIR")
    # Records per LLM request. Batching keeps a full run inside free-tier limits.
    batch_size: int = Field(default=8, ge=1, le=40, alias="BATCH_SIZE")

    # --- Manufacturer source retrieval ---
    # Retrieval is on by default because it is the only way the pipeline can
    # state a fact it did not already have. Turn it off for a fully offline run;
    # the cache still serves anything fetched previously.
    web_enabled: bool = Field(default=True, alias="WEB_ENABLED")
    web_cache: bool = Field(default=True, alias="WEB_CACHE")
    web_timeout: float = Field(default=20.0, gt=0, le=120, alias="WEB_TIMEOUT")
    web_concurrency: int = Field(default=8, ge=1, le=32, alias="WEB_CONCURRENCY")
    # Minimum gap between two requests to the same host, and to the search index.
    web_delay_seconds: float = Field(
        default=0.6, ge=0, le=30, alias="WEB_DELAY_SECONDS"
    )
    web_search_delay: float = Field(
        default=1.2, ge=0, le=30, alias="WEB_SEARCH_DELAY"
    )
    web_respect_robots: bool = Field(default=True, alias="WEB_RESPECT_ROBOTS")
    # Documents read per product, and the character budget handed to the model.
    web_max_documents: int = Field(default=3, ge=1, le=10, alias="WEB_MAX_DOCUMENTS")
    # Measured, not guessed: 6,000 characters of manual prose ahead of the
    # attribute template cut attribute fill on retrieved rows by about a third.
    # Specification pairs are prioritised and the overall budget kept tight.
    web_context_chars: int = Field(
        default=3500, ge=500, le=40000, alias="WEB_CONTEXT_CHARS"
    )
    # When the manufacturer publishes nothing about a part, fall back to a small
    # allowlist of reputable third-party sources (standards bodies, government
    # databases, the GS1 registry). E-commerce hosts are never eligible.
    web_third_party_fallback: bool = Field(
        default=True, alias="WEB_THIRD_PARTY_FALLBACK"
    )

    # --- Server ---
    host: str = Field(default="127.0.0.1", alias="HOST")
    port: int = Field(default=8000, ge=1, le=65535, alias="PORT")
    log_level: str = Field(default="info", alias="LOG_LEVEL")

    # --- Derived paths ---
    @property
    def data_dir(self) -> Path:
        return PROJECT_ROOT / "data"

    @property
    def cache_path(self) -> Path:
        path = self.cache_dir
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    # --- Provider helpers ---
    @property
    def groq_enabled(self) -> bool:
        return bool(self.groq_api_key.strip())

    @property
    def gemini_enabled(self) -> bool:
        return bool(self.gemini_api_key.strip())

    @property
    def has_api_key(self) -> bool:
        return self.groq_enabled or self.gemini_enabled

    @property
    def provider_order(self) -> list[str]:
        """Providers to try, primary first, keeping only configured ones."""
        preferred = self.llm_provider.strip().casefold()
        order = ["groq", "gemini"] if preferred != "gemini" else ["gemini", "groq"]
        return [
            name for name in order
            if (name == "groq" and self.groq_enabled)
            or (name == "gemini" and self.gemini_enabled)
        ]

    def chain_for(self, provider: str, *, fast: bool = False) -> list[str]:
        """Ordered model chain for a provider."""
        if provider == "groq":
            primary = self.groq_model_fast if fast else self.groq_model
            return _chain(primary, self.groq_model_chain)
        primary = self.gemini_model_fast if fast else self.gemini_model
        return _chain(primary, self.gemini_model_chain)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor so the `.env` file is parsed only once per process."""
    return Settings()


settings = get_settings()
