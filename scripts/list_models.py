"""Discover which models each configured provider can actually use.

Listing endpoints are not trustworthy on their own - Gemini advertises models
that 404 on call - so every candidate is also smoke-tested with a real
structured request.

Usage:
    python scripts/list_models.py            # list, then probe
    python scripts/list_models.py --no-probe # list only, spends no quota
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings  # noqa: E402
from backend.llm.providers import (  # noqa: E402
    GeminiProvider,
    GroqProvider,
    Outcome,
)

PROBE_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "string"}},
    "required": ["ok"],
}
PROBE_PROMPT = 'Return exactly {"ok":"y"}'

# Groq hosts speech, vision and guard models that cannot do JSON chat.
_SKIP = ("whisper", "tts", "guard", "prompt-guard", "embed")


def groq_models() -> list[str]:
    from groq import Groq

    client = Groq(api_key=settings.groq_api_key)
    names = [m.id for m in client.models.list().data]
    return sorted(n for n in names if not any(s in n.lower() for s in _SKIP))


def gemini_models() -> list[str]:
    from google import genai

    client = genai.Client(api_key=settings.gemini_api_key)
    out: list[str] = []
    for model in client.models.list():
        actions = list(getattr(model, "supported_actions", None) or [])
        if actions and "generateContent" not in actions:
            continue
        name = (model.name or "").removeprefix("models/")
        if name and not any(s in name.lower() for s in _SKIP):
            out.append(name)
    return sorted(out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-probe", action="store_true", help="skip live calls")
    args = parser.parse_args()

    if not settings.has_api_key:
        print("No API key configured. Set GROQ_API_KEY or GEMINI_API_KEY in .env")
        return 1

    usable: dict[str, list[str]] = {}

    for name, enabled, lister, factory in (
        ("groq", settings.groq_enabled, groq_models,
         lambda: GroqProvider(settings.groq_api_key)),
        ("gemini", settings.gemini_enabled, gemini_models,
         lambda: GeminiProvider(settings.gemini_api_key)),
    ):
        if not enabled:
            print(f"\n=== {name.upper()} === (no API key, skipped)")
            continue

        print(f"\n=== {name.upper()} ===")
        try:
            models = lister()
        except Exception as exc:  # noqa: BLE001
            print(f"  could not list models: {type(exc).__name__}: {exc}")
            continue

        if args.no_probe:
            for model in models:
                print(f"  {model}")
            usable[name] = models
            continue

        provider = factory()
        good: list[str] = []
        for model in models:
            reply = provider.complete(model, PROBE_PROMPT, PROBE_SCHEMA, None, 0.0)
            if reply.outcome is Outcome.OK:
                good.append(model)
                print(f"  [ OK ]      {model}")
            elif reply.outcome is Outcome.EXHAUSTED:
                print(f"  [QUOTA]     {model}  {reply.error}")
            elif reply.outcome is Outcome.DEAD:
                print(f"  [DEAD]      {model}  {reply.error}")
            else:
                print(f"  [RETRY]     {model}  {reply.error}")
        usable[name] = good

    print("\n" + "=" * 70)
    print("Suggested .env values")
    print("=" * 70)
    if groq := usable.get("groq"):
        big = [m for m in groq if "70b" in m or "120b" in m or "maverick" in m]
        small = [m for m in groq if "8b" in m or "instant" in m or "20b" in m]
        print(f"GROQ_MODEL={big[0] if big else groq[0]}")
        print(f"GROQ_MODEL_FAST={small[0] if small else groq[0]}")
        print(f"GROQ_MODEL_CHAIN={','.join(groq)}")
    if gem := usable.get("gemini"):
        print(f"GEMINI_MODEL={gem[0]}")
        print(f"GEMINI_MODEL_CHAIN={','.join(gem)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
