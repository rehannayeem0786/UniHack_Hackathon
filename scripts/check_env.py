"""Environment doctor: verifies deps, config, datasets and Gemini connectivity.

Usage:  python scripts/check_env.py
"""

from __future__ import annotations

import importlib.metadata as md
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import PROJECT_ROOT, settings  # noqa: E402

OK, WARN, FAIL = "[ OK ]", "[WARN]", "[FAIL]"
failures = 0


def report(status: str, label: str, detail: str = "") -> None:
    global failures
    if status == FAIL:
        failures += 1
    print(f"{status} {label}" + (f"  ->  {detail}" if detail else ""))


def check_packages() -> None:
    print("\n--- Packages ---")
    required = [
        "fastapi", "uvicorn", "pandas", "openpyxl", "XlsxWriter",
        "scikit-learn", "rapidfuzz", "pydantic", "pydantic-settings",
        "python-dotenv", "httpx", "groq", "google-genai",
    ]
    for name in required:
        try:
            report(OK, name, md.version(name))
        except md.PackageNotFoundError:
            report(FAIL, name, "not installed")


def check_config() -> None:
    print("\n--- Configuration ---")
    env_file = PROJECT_ROOT / ".env"
    report(OK if env_file.exists() else FAIL, ".env file", str(env_file))

    for label, key in (
        ("GROQ_API_KEY", settings.groq_api_key),
        ("GEMINI_API_KEY", settings.gemini_api_key),
    ):
        if key.strip():
            report(OK, label, f"set ({len(key)} chars, ...{key[-4:]})")
        else:
            report(WARN, label, "empty")

    order = settings.provider_order
    if order:
        report(OK, "provider order", " -> ".join(order))
        for name in order:
            report(OK, f"  {name} chain", ", ".join(settings.chain_for(name)))
    else:
        report(FAIL, "provider order", "no provider has an API key")

    report(OK, "BATCH_SIZE", str(settings.batch_size))
    report(OK, "MAX_CONCURRENCY", str(settings.max_concurrency))
    report(OK, "LLM cache", f"{settings.enable_llm_cache} @ {settings.cache_path}")


def check_datasets() -> None:
    print("\n--- Datasets ---")
    expected = [
        "Unilog_Input_200_Items.xlsx",
        "Unilog_Output_Delivery_Format.xlsx",
    ]
    for name in expected:
        path = settings.data_dir / name
        if path.exists():
            report(OK, name, f"{path.stat().st_size / 1024:.0f} KB")
        else:
            report(FAIL, name, "missing from data/")


def check_llm() -> None:
    """Live round-trip through the real gateway, so routing is tested too."""
    print("\n--- LLM connectivity ---")
    if not settings.has_api_key:
        report(WARN, "live call", "skipped, no API key configured")
        return

    from backend.llm.client import LLMClient

    client = LLMClient(cache_dir=PROJECT_ROOT / ".cache" / "healthcheck")
    schema = {
        "type": "object",
        "properties": {"status": {"type": "string"}},
        "required": ["status"],
    }
    result = client.generate_json(
        'Reply with {"status":"READY"}', schema,
        system="You return only JSON.",
    )

    if result and "READY" in str(result.get("status", "")).upper():
        stats = client.stats.snapshot()
        used = ", ".join(stats["by_model"]) or "cache"
        report(OK, "live call", f"answered by {used}")
    else:
        report(FAIL, "live call", "no provider returned a valid response")
        print(
            "\n       Fix: run  python scripts/list_models.py\n"
            "       then update the MODEL / MODEL_CHAIN values in .env"
        )

    if exhausted := client.stats.snapshot()["exhausted_models"]:
        report(WARN, "exhausted routes", ", ".join(exhausted))


def main() -> int:
    print("=" * 62)
    print(" UniHack 2026 - Environment Check")
    print("=" * 62)
    print(f"Python      {sys.version.split()[0]}")
    print(f"Interpreter {sys.executable}")
    print(f"Project     {PROJECT_ROOT}")

    check_packages()
    check_config()
    check_datasets()
    check_llm()

    print("\n" + "=" * 62)
    if failures:
        print(f" {failures} check(s) FAILED")
    else:
        print(" Environment ready")
    print("=" * 62)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
