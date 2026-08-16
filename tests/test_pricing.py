"""Cost estimation stays honest: proportional per-model pricing, cache savings."""

from backend.llm.pricing import estimate_cost, rate_for


def test_rate_lookup_matches_model_substrings() -> None:
    assert rate_for("groq:llama-3.3-70b-versatile") == 0.65
    assert rate_for("groq:llama-3.1-8b-instant") == 0.06
    assert rate_for("gemini:gemini-3.5-flash-lite") == 0.10
    # Unknown routes fall back to the conservative default rather than 0.
    assert rate_for("groq:some-new-model") == 0.30


def test_estimate_cost_prices_tokens_at_model_rate() -> None:
    usage = {
        "by_model": {"groq:llama-3.3-70b-versatile": 10},
        "total_tokens": 1_000_000,
        "live_calls": 10,
        "cache_hits": 0,
    }
    cost = estimate_cost(usage, records=100)
    assert cost["estimated_usd"] == 0.65
    assert cost["usd_per_record"] == 0.0065
    assert cost["estimated_cache_savings_usd"] == 0.0


def test_estimate_cost_splits_tokens_across_models() -> None:
    usage = {
        # Equal call counts: half the tokens priced at each route.
        "by_model": {"groq:llama-3.3-70b-versatile": 5, "groq:llama-3.1-8b-instant": 5},
        "total_tokens": 1_000_000,
        "live_calls": 10,
        "cache_hits": 0,
    }
    cost = estimate_cost(usage, records=10)
    assert abs(cost["estimated_usd"] - (0.325 + 0.03)) < 1e-9


def test_cache_hits_are_priced_as_savings_not_cost() -> None:
    usage = {
        "by_model": {"groq:llama-3.1-8b-instant": 5},
        "total_tokens": 50_000,
        "live_calls": 5,
        "cache_hits": 95,
    }
    cost = estimate_cost(usage, records=10)
    # 95 cached calls x 10k average tokens x $0.06/1M = $0.057 saved.
    assert abs(cost["estimated_cache_savings_usd"] - 0.057) < 1e-9
    assert cost["cache_hits"] == 95
    assert cost["usd_per_record_without_cache"] > cost["usd_per_record"]


def test_empty_usage_is_free() -> None:
    cost = estimate_cost({"by_model": {}, "total_tokens": 0, "live_calls": 0, "cache_hits": 0}, records=4)
    assert cost["estimated_usd"] == 0.0
    assert cost["estimated_cache_savings_usd"] == 0.0
    assert cost["usd_per_record"] == 0.0


def test_fully_cached_run_estimates_savings_from_typical_call_size() -> None:
    # No live calls means no measured baseline; savings fall back to the
    # documented typical exchange size at the default rate, and say so.
    cost = estimate_cost(
        {"by_model": {}, "total_tokens": 0, "live_calls": 0, "cache_hits": 10}, records=2
    )
    # 10 calls x 1,200 tokens x $0.30/1M = $0.0036
    assert abs(cost["estimated_cache_savings_usd"] - 0.0036) < 1e-9
    assert cost["estimated_usd"] == 0.0
    assert "fully cached" in cost["basis"]
