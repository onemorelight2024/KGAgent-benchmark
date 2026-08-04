"""Agent-driven benchmark conversation."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, TextBlock

from kgagent.benchmark.agents.chat_flow import BenchmarkChatFlow
from kgagent.benchmark.tools.llm import resolve_model
from kgagent.core.language import detect_language
from kgagent.system.prompts import load_prompt


@dataclass
class BenchmarkConversation:
    """Benchmark conversation driven by an LLM prompt."""

    workspace: Path
    sdk_model: str
    benchmark_model: str = "gpt-5.4"
    reply_language: str = "zh"

    def __post_init__(self) -> None:
        self.flow = BenchmarkChatFlow(workspace=self.workspace, model=resolve_model(self.benchmark_model))
        self.active = False

    def state(self) -> dict[str, Any]:
        """Return current benchmark state."""
        return self.flow.state()

    def current_reply_language(self) -> str:
        """Return the current user-facing reply language."""
        return self.reply_language

    async def start(self, user_input: str) -> tuple[str, dict[str, Any] | None]:
        """Start the benchmark conversation."""
        self.active = True
        return await self.handle(user_input)

    async def handle(self, user_input: str) -> tuple[str, dict[str, Any] | None]:
        """Handle one benchmark user turn."""
        self._update_reply_language(user_input)
        llm_result = await self._ask_agent(user_input)
        updates = llm_result.get("updates") if isinstance(llm_result, dict) else None
        self.flow.apply_update(updates)
        self.flow._parse_any(user_input)

        message, params = self.flow.handle("")
        llm_reply = str(llm_result.get("reply") or "").strip() if isinstance(llm_result, dict) else ""
        reply = llm_reply or message

        if params is None:
            missing = self.flow._next_missing()
            if missing and not llm_reply:
                reply = self.flow._question_for(missing, reply_language=self.reply_language)
            return reply, None

        self.active = False
        reply = self.flow._summary(reply_language=self.reply_language)
        return reply, params

    def _update_reply_language(self, user_input: str) -> None:
        stripped = user_input.strip()
        if not stripped:
            return
        lowered = stripped.lower()
        if "中文" in stripped or "chinese" in lowered:
            self.reply_language = "zh"
            return
        if "英文" in stripped or "english" in lowered:
            self.reply_language = "en"
            return
        if detect_language(stripped) == "zh":
            self.reply_language = "zh"
            return
        words = re.findall(r"[A-Za-z][A-Za-z']+", stripped)
        has_lowercase = any(any(ch.islower() for ch in word) for word in words)
        looks_like_parameter = (
            stripped.upper() in {"A", "B", "C", "D", "SGSH"}
            or bool(re.fullmatch(r"(?:/|\./|[A-Za-z0-9_.\-]+/)[A-Za-z0-9_./~\-]+\.(?:jsonl?|md)", stripped))
            or bool(re.fullmatch(r"[A-Za-z0-9_.\-]+\.(?:jsonl?|md)", stripped))
            or "sk-" in lowered
            or "gpt-" in lowered
            or bool(re.fullmatch(r"\d{1,5}", stripped))
        )
        if len(words) >= 2 and has_lowercase and not looks_like_parameter:
            self.reply_language = "en"

    async def _ask_agent(self, user_input: str) -> dict[str, Any]:
        prompt = (
            "Current state:\n"
            f"{json.dumps(self.flow.state(), ensure_ascii=False, indent=2)}\n\n"
            "Latest user message:\n"
            f"{user_input}"
        )
        options = ClaudeAgentOptions(
            cwd=str(self.workspace),
            model=self.sdk_model,
            system_prompt=load_prompt("benchmark"),
            tools=[],
            permission_mode="auto",
            max_turns=1,
        )
        raw_texts: list[str] = []
        async with ClaudeSDKClient(options) as client:
            await client.query(prompt)
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            raw_texts.append(block.text)
                            parsed = _parse_json(block.text)
                            if parsed is not None:
                                return parsed
        raw_preview = "\n".join(raw_texts).strip()[:500] or "<empty response>"
        raise RuntimeError(f"benchmark conversation agent returned non-JSON response: {raw_preview}")


def _parse_json(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").strip()
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None
