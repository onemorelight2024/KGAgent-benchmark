"""Small OpenAI-compatible chat client used by benchmark adapters."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any


def mask_secret(secret: str | None) -> str:
    """Mask a secret for user-facing output."""
    if not secret:
        return ""
    if len(secret) <= 10:
        return "*" * len(secret)
    return f"{secret[:3]}...{secret[-4:]}"


def resolve_base_url(base_url: str | None = None) -> str:
    """Resolve OpenAI-compatible base URL from arguments or environment."""
    return (
        base_url
        or os.getenv("OPENAI_BASE_URL")
        or os.getenv("OPENAI_API_BASE")
        or os.getenv("LLM_BASE_URL")
        or os.getenv("DF_API_URL")
        or ""
    ).rstrip("/")


def resolve_api_key(api_key: str | None = None) -> str:
    """Resolve API key from arguments or environment."""
    return api_key or os.getenv("OPENAI_API_KEY") or os.getenv("DF_API_KEY") or ""


def chat_json(
    *,
    base_url: str | None,
    api_key: str | None,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.7,
    max_tokens: int = 600,
) -> dict[str, Any]:
    """Call an OpenAI-compatible chat completion endpoint and parse JSON."""
    endpoint = resolve_base_url(base_url)
    key = resolve_api_key(api_key)
    if not endpoint:
        raise ValueError("Missing base_url / OPENAI_BASE_URL / LLM_BASE_URL / DF_API_URL")
    if not key:
        raise ValueError("Missing api_key / OPENAI_API_KEY / DF_API_KEY")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{endpoint}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                data = json.loads(response.read().decode("utf-8"))
            content = data["choices"][0]["message"].get("content") or "{}"
            return parse_json_object(content)
        except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(0.5 * attempt)
    raise RuntimeError(f"LLM call failed: {last_error}")


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from a model response."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").strip()
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()

    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        parsed = json.loads(text[start : end + 1])
        return parsed if isinstance(parsed, dict) else {}
    return {}
