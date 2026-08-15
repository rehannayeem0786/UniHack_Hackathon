"""Provider adapters for Groq and Gemini behind one small interface.

Each adapter turns a prompt plus a JSON schema into parsed JSON, and classifies
its own failures so the caller can decide whether to retry the same model, move
to the next model, or give up:

    OK          got valid JSON
    RETRY       transient (5xx, per-minute rate limit, malformed JSON)
    EXHAUSTED   this model has no quota left today - skip it for the run
    DEAD        this model is retired or rejected the request - skip it
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class Outcome(str, Enum):
    OK = "ok"
    RETRY = "retry"
    EXHAUSTED = "exhausted"
    DEAD = "dead"


@dataclass
class Reply:
    outcome: Outcome
    data: dict[str, Any] | None = None
    tokens: int = 0
    error: str = ""


def _extract_json(text: str) -> dict[str, Any] | None:
    """Parse JSON, tolerating markdown fences and surrounding prose."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {"result": parsed}
    except json.JSONDecodeError:
        pass
    # Fall back to the outermost brace pair.
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(cleaned[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


class Provider:
    """Common interface. Subclasses implement `complete`."""

    name = "base"

    @property
    def available(self) -> bool:
        raise NotImplementedError

    def complete(
        self, model: str, prompt: str, schema: dict[str, Any],
        system: str | None, temperature: float,
    ) -> Reply:
        raise NotImplementedError


class GroqProvider(Provider):
    """Groq via its OpenAI-compatible chat completions API.

    Groq guarantees syntactically valid JSON with `response_format=json_object`
    but does not constrain the shape, so the expected schema is included in the
    prompt and the result is validated by the caller.
    """

    name = "groq"

    def __init__(self, api_key: str) -> None:
        self._api_key = (api_key or "").strip()
        self._client: Any = None

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def _ensure(self) -> Any:
        if self._client is None:
            from groq import Groq

            self._client = Groq(api_key=self._api_key, max_retries=0)
        return self._client

    def complete(
        self, model: str, prompt: str, schema: dict[str, Any],
        system: str | None, temperature: float,
    ) -> Reply:
        from groq import APIStatusError

        instruction = (
            (system or "You return only JSON.")
            + "\n\nRespond with a single JSON object matching this schema:\n"
            + json.dumps(schema, indent=2)
            + "\n\nOutput JSON only, with no commentary and no markdown fences."
        )
        try:
            response = self._ensure().chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": instruction},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=temperature,
            )
        except APIStatusError as exc:
            return self._classify(exc)
        except Exception as exc:  # noqa: BLE001 - network / SDK surprises
            return Reply(Outcome.RETRY, error=f"{type(exc).__name__}: {exc}")

        usage = getattr(response, "usage", None)
        tokens = int(getattr(usage, "total_tokens", 0) or 0)
        content = response.choices[0].message.content if response.choices else ""
        data = _extract_json(content or "")
        if data is None:
            return Reply(Outcome.RETRY, tokens=tokens, error="unparseable JSON")
        return Reply(Outcome.OK, data=data, tokens=tokens)

    @staticmethod
    def _classify(exc: Exception) -> Reply:
        text = str(exc)
        status = getattr(exc, "status_code", None)
        if status == 429:
            # Groq reports per-day limits distinctly from per-minute ones.
            if re.search(r"per\s*day|requests per day|RPD|tokens per day|TPD", text, re.I):
                return Reply(Outcome.EXHAUSTED, error="daily quota reached")
            return Reply(Outcome.RETRY, error="rate limited")
        if status in (404, 400):
            return Reply(Outcome.DEAD, error=f"{status}: model unavailable")
        if status and 500 <= status < 600:
            return Reply(Outcome.RETRY, error=f"{status}: server error")
        return Reply(Outcome.RETRY, error=f"{type(exc).__name__}: {text[:120]}")


class GeminiProvider(Provider):
    """Gemini via google-genai, using native schema-constrained decoding."""

    name = "gemini"

    def __init__(self, api_key: str) -> None:
        self._api_key = (api_key or "").strip()
        self._client: Any = None

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def _ensure(self) -> Any:
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def complete(
        self, model: str, prompt: str, schema: dict[str, Any],
        system: str | None, temperature: float,
    ) -> Reply:
        from google.genai import types
        from google.genai.errors import ClientError, ServerError

        contents = f"{system}\n\n{prompt}" if system else prompt
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=temperature,
        )
        try:
            response = self._ensure().models.generate_content(
                model=model, contents=contents, config=config
            )
        except ClientError as exc:
            text = str(exc)
            if "429" in text or "RESOURCE_EXHAUSTED" in text:
                if "PerDay" in text or "per day" in text.lower():
                    return Reply(Outcome.EXHAUSTED, error="daily quota reached")
                return Reply(Outcome.RETRY, error="rate limited")
            if "404" in text or "no longer available" in text:
                return Reply(Outcome.DEAD, error="model retired")
            return Reply(Outcome.DEAD, error=text[:120])
        except ServerError as exc:
            return Reply(Outcome.RETRY, error=f"server error: {exc}")
        except Exception as exc:  # noqa: BLE001
            return Reply(Outcome.RETRY, error=f"{type(exc).__name__}: {exc}")

        usage = getattr(response, "usage_metadata", None)
        tokens = int(getattr(usage, "total_token_count", 0) or 0)
        data = _extract_json(response.text or "")
        if data is None:
            return Reply(Outcome.RETRY, tokens=tokens, error="unparseable JSON")
        return Reply(Outcome.OK, data=data, tokens=tokens)
