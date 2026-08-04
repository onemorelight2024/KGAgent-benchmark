"""Claude SDK chat helpers used by benchmark adapters."""

from __future__ import annotations

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, TextBlock


def mask_secret(secret: str | None) -> str:
    """Mask a secret for user-facing output."""
    if not secret:
        return ""
    if len(secret) <= 10:
        return "*" * len(secret)
    return f"{secret[:3]}...{secret[-4:]}"


def resolve_model(model: str | None = None) -> str:
    """Resolve the benchmark LLM model."""
    return (
        model
        or os.getenv("KG_BENCHMARK_MODEL")
        or os.getenv("KG_MODEL")
        or os.getenv("OPENAI_MODEL")
        or "gpt-5.4"
    )


def resolve_base_url(base_url: str | None = None) -> str:
    """Resolve legacy benchmark base URL from arguments or environment."""
    return (base_url or os.getenv("KG_API_URL") or "").rstrip("/")


def resolve_api_key(api_key: str | None = None) -> str:
    """Resolve API key from arguments or environment."""
    return api_key or os.getenv("KG_API_KEY") or ""


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
    """Call Claude Agent SDK and parse a JSON object.

    `base_url` and `api_key` are accepted for backward-compatible method
    signatures. Claude SDK routing is configured through ANTHROPIC_* / CCR.
    """
    content = chat_text(
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return parse_json_object(content)


def chat_text(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.7,
    max_tokens: int = 600,
) -> str:
    """Call Claude Agent SDK and return raw assistant text."""
    return _run_sync(
        _chat_text_async(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    )


async def _chat_text_async(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
) -> str:
    options = ClaudeAgentOptions(
        cwd=os.getcwd(),
        model=model,
        system_prompt=system_prompt,
        tools=[],
        permission_mode="auto",
        max_turns=1,
    )
    texts: list[str] = []
    async with ClaudeSDKClient(options) as client:
        await client.query(user_prompt)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        texts.append(block.text)
    content = "\n".join(texts).strip()
    if not content:
        raise RuntimeError("Claude SDK returned empty response")
    return content


def _run_sync(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro)).result()


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
