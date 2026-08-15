"""Benchmark candidate models on a realistic extraction task.

Measures latency, token use, and whether the model respects a constrained
value list - which matters more than raw quality for this pipeline.

Usage:  python scripts/bench_models.py
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings  # noqa: E402
from backend.llm.providers import GeminiProvider, GroqProvider, Outcome  # noqa: E402

SCHEMA = {
    "type": "object",
    "properties": {
        "product_name": {"type": "string"},
        "box_type": {"type": "string"},
        "material": {"type": "string"},
        "length": {"type": "string"},
        "uom": {"type": "string"},
    },
    "required": ["product_name", "box_type", "material", "length", "uom"],
}

SYSTEM = (
    "You extract structured attributes for industrial product catalogues. "
    "SS/SST=Stainless Steel, SQ=Square, GALV=Galvanized, RCPT=Receptacle."
)

PROMPT = """Product: 'G1941UPC 4" Sq Cover Sw/Outlet'
Brand: Southwire

Extract:
- product_name (Title Case, generic item type)
- box_type   [allowed: Square, Round, Octagon, Rectangular]
- material   [allowed: Steel, Aluminum, Plastic, Stainless Steel]
- length     (number only, fractions not decimals)
- uom        [allowed: in, ft, mm]
"""

# A correct answer must respect the allowed lists.
EXPECTED = {"box_type": "Square", "material": "Steel", "length": "4", "uom": "in"}

GROQ_CANDIDATES = [
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-20b",
    "qwen/qwen3.6-27b",
    "llama-3.1-8b-instant",
]
GEMINI_CANDIDATES = ["gemini-3.6-flash", "gemini-3.5-flash-lite"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=1, help="repeats per model")
    args = parser.parse_args()

    jobs: list[tuple[str, object, str]] = []
    if settings.groq_enabled:
        provider = GroqProvider(settings.groq_api_key)
        jobs += [("groq", provider, m) for m in GROQ_CANDIDATES]
    if settings.gemini_enabled:
        provider = GeminiProvider(settings.gemini_api_key)
        jobs += [("gemini", provider, m) for m in GEMINI_CANDIDATES]

    if not jobs:
        print("No provider configured.")
        return 1

    print(f"{'MODEL':<34}{'SEC':>7}{'TOK':>7}{'LOV':>6}  RESULT")
    print("-" * 96)

    for label, provider, model in jobs:
        best_seconds = float("inf")
        reply = None
        for _ in range(args.runs):
            started = time.perf_counter()
            reply = provider.complete(model, PROMPT, SCHEMA, SYSTEM, 0.0)
            best_seconds = min(best_seconds, time.perf_counter() - started)

        name = f"{label}:{model}"
        if reply is None or reply.outcome is not Outcome.OK or not reply.data:
            note = reply.error if reply else "no reply"
            print(f"{name:<34}{'-':>7}{'-':>7}{'-':>6}  FAILED: {note[:40]}")
            continue

        data = reply.data
        hits = sum(1 for k, v in EXPECTED.items() if str(data.get(k, "")).strip() == v)
        compact = " ".join(f"{k}={data.get(k)}" for k in ("product_name", "box_type", "material"))
        print(
            f"{name:<34}{best_seconds:>7.2f}{reply.tokens:>7}"
            f"{hits}/{len(EXPECTED):>4}  {compact[:46]}"
        )

    print("\nLOV = fields that respected the allowed-value list (higher is better).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
