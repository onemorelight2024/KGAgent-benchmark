"""Claude SDK LLM client with lightweight usage accounting."""

from __future__ import annotations

from typing import Any, Optional

from kgagent.benchmark.tools.llm import chat_text


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


def call_chat_completions(
    prompt: str,
    model: str = "gpt-5.4",
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
    model: str = "gpt-5.4",
    *,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    max_tokens: int = 256,
    temperature: float = 0.0,
    timeout: float = 120.0,
) -> dict[str, Any]:
    content = chat_text(
        model=model,
        system_prompt="You are a precise temporal knowledge graph benchmark assistant.",
        user_prompt=prompt,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return {
        "content": content.strip(),
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "reasoning_tokens": 0,
            "output_tokens_non_reasoning": 0,
            "total_tokens": 0,
            "price_usd": 0.0,
            "llm_call_count": 1,
        },
        "raw": {"backend": "claude_sdk", "model": model},
    }
