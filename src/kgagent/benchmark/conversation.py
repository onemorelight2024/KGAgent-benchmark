"""Agent-driven benchmark conversation."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, TextBlock

from kgagent.benchmark.chat_flow import BenchmarkChatFlow
from kgagent.system.prompts import load_prompt


@dataclass
class BenchmarkConversation:
    """Benchmark conversation driven by an LLM prompt."""

    workspace: Path
    sdk_model: str
    benchmark_model: str = "gpt-4o-mini"

    def __post_init__(self) -> None:
        self.flow = BenchmarkChatFlow(workspace=self.workspace, model=self.benchmark_model)
        self.active = False

    def state(self) -> dict[str, Any]:
        """Return current benchmark state."""
        return self.flow.state()

    async def start(self, user_input: str) -> tuple[str, dict[str, Any] | None]:
        """Start the benchmark conversation."""
        self.active = True
        return await self.handle(user_input)

    async def handle(self, user_input: str) -> tuple[str, dict[str, Any] | None]:
        """Handle one benchmark user turn."""
        llm_result = await self._ask_agent(user_input)
        updates = llm_result.get("updates") if isinstance(llm_result, dict) else None
        self.flow.apply_update(updates)
        self.flow._parse_any(user_input)

        message, params = self.flow.handle("")
        reply = str(llm_result.get("reply") or message) if isinstance(llm_result, dict) else message

        if params is None:
            missing = self.flow._next_missing()
            if missing:
                reply = self.flow._question_for(missing)
            return reply, None

        self.active = False
        return reply, params

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
        async with ClaudeSDKClient(options) as client:
            await client.query(prompt)
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            parsed = _parse_json(block.text)
                            if parsed is not None:
                                return parsed
        return {"reply": "", "updates": {}, "ready_to_run": False}


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
