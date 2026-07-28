"""OpenAI-compatible gateway client with usage and local price accounting."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse

import requests
from requests.exceptions import JSONDecodeError as RequestsJSONDecodeError


DEFAULT_MODEL_PRICING_PER_1M = {
    "gpt-4o": {"prompt": 2.50, "completion": 10.00, "reasoning": 10.00},
    "gpt-4o-mini": {"prompt": 0.15, "completion": 0.60, "reasoning": 0.60},
    "deepseek-v3": {"prompt": 0.27, "completion": 1.10, "reasoning": 1.10},
    "qwen3-235b-a22b": {"prompt": 0.00, "completion": 0.00, "reasoning": 0.00},
}


def normalize_openai_base_url(url: str) -> str:
    s = url.strip().rstrip("/")
    if not s:
        return s
    p = urlparse(s)
    path = (p.path or "").rstrip("/")
    if path == "":
        p = p._replace(path="/v1")
        return urlunparse(p).rstrip("/")
    return s


def get_llm_base_url() -> str:
    raw = (
        os.environ.get("LLM_BASE_URL", "").strip()
        or os.environ.get("OPENAI_BASE_URL", "").strip()
        or os.environ.get("OPENAI_API_BASE", "").strip()
        or os.environ.get("DF_API_URL", "").strip()
    )
    if not raw:
        raise ValueError("LLM_BASE_URL, OPENAI_BASE_URL, OPENAI_API_BASE, or DF_API_URL environment variable is not set")
    return normalize_openai_base_url(raw)


def get_df_api_key() -> str:
    k = os.environ.get("OPENAI_API_KEY", "").strip()
    if k:
        return k
    return os.environ.get("DF_API_KEY", "").strip()


def _sanitize_model_name(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", (model or "").strip()).strip("_").upper()


def _get_pricing(model: str) -> dict[str, float]:
    base = dict(
        DEFAULT_MODEL_PRICING_PER_1M.get(
            model,
            {"prompt": 0.0, "completion": 0.0, "reasoning": 0.0},
        )
    )
    env_prefix = f"LLM_PRICE_{_sanitize_model_name(model)}"
    for field in ("prompt", "completion", "reasoning"):
        env_key = f"{env_prefix}_{field.upper()}_PER_1M"
        if env_key in os.environ:
            try:
                base[field] = float(os.environ[env_key])
            except ValueError:
                pass
    return base


def _usage_from_payload(data: dict[str, Any], model: str) -> dict[str, float]:
    usage_raw = data.get("usage") or {}
    comp_details = usage_raw.get("completion_tokens_details") or {}
    prompt_tokens = int(usage_raw.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage_raw.get("completion_tokens", 0) or 0)
    reasoning_tokens = int(comp_details.get("reasoning_tokens", 0) or 0)
    output_tokens_non_reasoning = max(0, completion_tokens - reasoning_tokens)
    total_tokens = int(usage_raw.get("total_tokens", prompt_tokens + completion_tokens) or 0)
    pricing = _get_pricing(model)
    price_usd = (
        prompt_tokens * pricing["prompt"]
        + output_tokens_non_reasoning * pricing["completion"]
        + reasoning_tokens * pricing["reasoning"]
    ) / 1_000_000.0
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "reasoning_tokens": reasoning_tokens,
        "output_tokens_non_reasoning": output_tokens_non_reasoning,
        "total_tokens": total_tokens,
        "price_usd": round(price_usd, 8),
        "llm_call_count": 1,
    }


def merge_usage(total: dict[str, float], delta: dict[str, float] | None) -> dict[str, float]:
    if not delta:
        return total
    for key in (
        "prompt_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "output_tokens_non_reasoning",
        "total_tokens",
        "llm_call_count",
    ):
        total[key] = int(total.get(key, 0)) + int(delta.get(key, 0))
    total["price_usd"] = round(float(total.get("price_usd", 0.0)) + float(delta.get("price_usd", 0.0)), 8)
    return total


def empty_usage() -> dict[str, float]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "reasoning_tokens": 0,
        "output_tokens_non_reasoning": 0,
        "total_tokens": 0,
        "price_usd": 0.0,
        "llm_call_count": 0,
    }


def summarize_usage_rows(rows: list[dict[str, Any]]) -> dict[str, float]:
    total = empty_usage()
    for row in rows:
        merge_usage(
            total,
            {
                "prompt_tokens": row.get("prompt_tokens", 0),
                "completion_tokens": row.get("completion_tokens", 0),
                "reasoning_tokens": row.get("reasoning_tokens", 0),
                "output_tokens_non_reasoning": row.get("output_tokens_non_reasoning", 0),
                "total_tokens": row.get("total_tokens", 0),
                "price_usd": row.get("price_usd", 0.0),
                "llm_call_count": row.get("llm_call_count", 0),
            },
        )
    return total


def _post_chat(
    payload: dict[str, Any],
    *,
    base_url: Optional[str],
    api_key: Optional[str],
    timeout: float,
) -> dict[str, Any]:
    base = normalize_openai_base_url((base_url or get_llm_base_url()).strip())
    key = (api_key or get_df_api_key()).strip()
    if not key:
        raise ValueError("API key not set: configure DF_API_KEY or OPENAI_API_KEY")
    url = f"{base}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    }
    last_err: Optional[Exception] = None
    max_attempts = 6
    for attempt in range(max_attempts):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if r.status_code in (408, 409, 429, 500, 502, 503, 504):
                last_err = RuntimeError(f"HTTP {r.status_code}: {(r.text or '')[:500]}")
                time.sleep(min(20, 2**attempt))
                continue
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {(r.text or '')[:1200]}")
            raw = r.text or ""
            try:
                return r.json()
            except RequestsJSONDecodeError:
                head = raw.lstrip()[:300].lower()
                html_hint = ""
                if "<!doctype" in head or "<html" in head:
                    html_hint = " Response looks like HTML — check that base_url points to /v1."
                raise RuntimeError(
                    f"Gateway did not return valid JSON. URL={url}.{html_hint} "
                    f"First 800 chars: {raw[:800]!r}"
                ) from None
        except RuntimeError as e:
            last_err = e
            msg = str(e).lower()
            transient = any(
                marker in msg
                for marker in (
                    "timeout",
                    "timed out",
                    "connection",
                    "temporarily unavailable",
                    "temporary failure",
                    "internal server error",
                    "server disconnected",
                    "http 408",
                    "http 409",
                    "http 429",
                    "http 500",
                    "http 502",
                    "http 503",
                    "http 504",
                    "rate limit",
                )
            )
            if transient and attempt < max_attempts - 1:
                time.sleep(min(20, 2**attempt))
                continue
            raise
        except (requests.RequestException, json.JSONDecodeError) as e:
            last_err = e
            if attempt < max_attempts - 1:
                time.sleep(min(20, 2**attempt))
                continue
            raise
    raise last_err if last_err else RuntimeError("LLM request failed after retries")


def call_chat_completions(
    prompt: str,
    model: str = "gpt-4o-mini",
    *,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    max_tokens: int = 256,
    temperature: float = 0.0,
    timeout: float = 120.0,
) -> str:
    result = call_chat_completions_with_usage(
        prompt=prompt,
        model=model,
        base_url=base_url,
        api_key=api_key,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
    )
    return result["content"]


def call_chat_completions_with_usage(
    prompt: str,
    model: str = "gpt-4o-mini",
    *,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    max_tokens: int = 256,
    temperature: float = 0.0,
    timeout: float = 120.0,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    data = _post_chat(payload, base_url=base_url, api_key=api_key, timeout=timeout)
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"Empty choices: {json.dumps(data, ensure_ascii=False)[:800]}")
    msg = choices[0].get("message") or {}
    return {
        "content": (msg.get("content") or "").strip(),
        "usage": _usage_from_payload(data, model),
        "raw": data,
    }
