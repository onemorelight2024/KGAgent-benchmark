"""LLM-based benchmark intent parsing."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    TextBlock,
)

logger = logging.getLogger(__name__)


BENCHMARK_INTENT_PROMPT = """You parse benchmark workflow messages for KGAgent.

Return ONLY valid JSON. Do not explain.

Current task: update benchmark parameters from the user's latest message.

Fields:
{
  "graph_type": "KG" | "TKG" | null,
  "task": "KGQA" | "KGQG" | null,
  "input_path": string | null,
  "sample_count": integer | null,
  "method": "role_agent_qg" | "sgsh_prompt" | "chronoqg" | null,
  "base_url": string | null,
  "api_key": string | null,
  "model": string | null,
  "config_path": string | null,
  "run_id": string | null,
  "resume": boolean | null,
  "batch_size": integer | null
}

Rules:
- "1", "普通", "静态", "static" means graph_type KG.
- "2", "时序", "temporal", "TKG" means graph_type TKG.
- KGQA/KGQG are task names.
- A means sgsh_prompt, B means role_agent_qg.
- C or ChronoQG means chronoqg.
- "选择最合适的方法" means sgsh_prompt.
- "断点", "续跑", "resume" means resume=true.
- "不续跑", "重新跑", "fresh" means resume=false.
- Extract file paths even when embedded in Chinese text, e.g. "在examples/a.json" -> "examples/a.json".
- If the user says a config file contains the API settings, put that path in config_path.
- Never invent API keys or URLs.
"""


class BenchmarkIntentEntry:
    """Benchmark-specific structured intent parser."""

    def __init__(
        self,
        *,
        model_name: str,
        work_dir: str | Path,
        permission_mode: str = "auto",
    ):
        self.model_name = model_name
        self.work_dir = str(work_dir)
        self.permission_mode = permission_mode

    async def parse_async(self, user_input: str, state: dict[str, Any]) -> dict[str, Any] | None:
        """Parse benchmark parameters from one user turn."""
        prompt = (
            "Current benchmark state:\n"
            f"{json.dumps(state, ensure_ascii=False, indent=2)}\n\n"
            "Latest user message:\n"
            f"{user_input}\n\n"
            "Return the JSON update."
        )
        options = ClaudeAgentOptions(
            cwd=self.work_dir,
            model=self.model_name,
            system_prompt=BENCHMARK_INTENT_PROMPT,
            tools=[],
            permission_mode=self.permission_mode,
            max_turns=1,
        )

        async with ClaudeSDKClient(options) as client:
            await client.query(prompt)
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            parsed = _parse_json_object(block.text)
                            if parsed is not None:
                                return parsed
        return None

    def parse(self, user_input: str, state: dict[str, Any]) -> dict[str, Any] | None:
        """Sync wrapper."""
        return asyncio.run(self.parse_async(user_input, state))


def _parse_json_object(text: str) -> dict[str, Any] | None:
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

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            logger.warning("Could not parse benchmark intent JSON")
    return None
