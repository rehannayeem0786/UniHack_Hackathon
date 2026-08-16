"""Cost estimation for LLM usage.

Turns the token counts the gateway already tracks into a dollar figure, so the
dashboard can tell the business story: enriching a catalogue costs pennies per
row, and a re-run costs nothing at all because every response is cached.

Estimates use public list prices, blended across input and output tokens (the
gateway tracks the two combined, so a split is not available). Where a model
has no listed price, a conservative mid-tier default is used. Every number
derived here is labelled an estimate wherever it is shown.
"""

from __future__ import annotations

from typing import Any

# USD per 1M tokens, blended input+output, from public list prices. Matched by
# substring of the route key ("provider:model"), most specific first.
RATES_USD_PER_MTOK: tuple[tuple[str, float], ...] = (
    ("llama-3.3-70b", 0.65),  # $0.59 in / $0.79 out per 1M
    ("llama-3.1-8b", 0.06),   # $0.05 in / $0.08 out per 1M
    ("gpt-oss-120b", 0.30),   # $0.15 in / $0.60 out per 1M
    ("gpt-oss-20b", 0.15),    # $0.07 in / $0.30 out per 1M
    ("qwen3", 0.20),          # mid-tier estimate
    ("flash-lite", 0.10),
    ("flash", 0.20),
    ("gemma", 0.15),
)

# Applied when no model name matches: deliberately above the cheapest routes
# so an unknown model never understates the bill.
DEFAULT_RATE_USD_PER_MTOK = 0.30

# Fallback size of one cached exchange, used only when a run had no live calls
# at all and therefore no measured token baseline. 1,200 tokens is typical for
# these JSON extractions (prompt plus structured response); it is a labelled
# assumption, never applied when real token counts exist.
ASSUMED_TOKENS_PER_CACHED_CALL = 1_200


def rate_for(route: str) -> float:
    """Blended USD per 1M tokens for one provider:model route."""
    lowered = route.casefold()
    for needle, rate in RATES_USD_PER_MTOK:
        if needle in lowered:
            return rate
    return DEFAULT_RATE_USD_PER_MTOK


def estimate_cost(usage: dict[str, Any], records: int) -> dict[str, Any]:
    """Estimated USD for one run's LLM usage, plus what the cache saved.

    `usage` is the gateway's per-run snapshot (`calls`, `cache_hits`,
    `live_calls`, `total_tokens`, `by_model`). Tokens are tracked in aggregate
    only, so they are spread across the models that answered in proportion to
    each model's call count, and each share is priced at that model's rate.

    Cache savings are the counterfactual: cached calls consumed no tokens this
    run, so they are priced at the run's average tokens-per-live-call to show
    what the same run would have cost with the cache disabled.
    """
    by_model: dict[str, int] = dict(usage.get("by_model") or {})
    total_tokens = int(usage.get("total_tokens") or 0)
    live_calls = int(usage.get("live_calls") or 0)
    cache_hits = int(usage.get("cache_hits") or 0)

    call_count = sum(by_model.values())
    cost = 0.0
    if total_tokens and call_count:
        for route, calls in by_model.items():
            cost += total_tokens * (calls / call_count) / 1_000_000 * rate_for(route)
    elif total_tokens:
        cost = total_tokens / 1_000_000 * DEFAULT_RATE_USD_PER_MTOK

    # USD per token actually paid this run; the fallback is the default rate.
    rate_per_token = cost / total_tokens if total_tokens else DEFAULT_RATE_USD_PER_MTOK / 1_000_000
    if live_calls:
        avg_tokens_per_call = total_tokens / live_calls
        basis = "blended public list prices; cached calls cost nothing"
    else:
        # No live calls: no measured baseline, so price the cached replay at a
        # documented typical exchange size. Still an estimate, and said so.
        avg_tokens_per_call = ASSUMED_TOKENS_PER_CACHED_CALL if cache_hits else 0.0
        basis = (
            "fully cached run; savings estimated at ~"
            f"{ASSUMED_TOKENS_PER_CACHED_CALL} tokens per replayed call"
        )
    savings = cache_hits * avg_tokens_per_call * rate_per_token

    records = max(int(records), 0)
    return {
        "estimated_usd": round(cost, 4),
        "usd_per_record": round(cost / records, 6) if records else 0.0,
        "live_tokens": total_tokens,
        "live_calls": live_calls,
        "cache_hits": cache_hits,
        "estimated_cache_savings_usd": round(savings, 4),
        "usd_per_record_without_cache": round((cost + savings) / records, 6) if records else 0.0,
        "basis": basis,
    }
