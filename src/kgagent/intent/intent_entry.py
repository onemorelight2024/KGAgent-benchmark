"""Intent classification entry point."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    TextBlock,
)

from kgagent.extraction.config import ExtractionConfig
from kgagent.intent.agents.classifier import build_intent_agent

logger = logging.getLogger(__name__)


class IntentEntry:
    """Entry point for intent classification."""

    def __init__(self, config: ExtractionConfig):
        self.config = config
        self.agent = build_intent_agent()

    def parse_intent(self, user_input: str) -> dict[str, Any]:
        """Synchronous intent parsing."""
        return asyncio.run(self.parse_intent_async(user_input))

    async def parse_intent_async(self, user_input: str) -> dict[str, Any]:
        """Parse user intent from natural language input.

        Args:
            user_input: User's natural language input

        Returns:
            Intent classification result with format:
            {
                "intent": "chat" | "extract" | "help" | "command",
                "confidence": float,
                "parameters": {...} or None,  # for extract intent
                "explanation": str,
                "response": str  # for chat intent
            }
        """
        logger.info(f"Parsing intent from: {user_input[:50]}...")

        # Quick checks for obvious cases (skip LLM call)
        if user_input.startswith(":"):
            command = user_input[1:].strip()
            return {
                "intent": "command",
                "confidence": 1.0,
                "command": command,
                "explanation": "系统命令",
            }

        # Build prompt
        prompt = f"Classify the intent of this user input:\n\n{user_input}"

        # Build options
        options = ClaudeAgentOptions(
            cwd=self.config.work_dir,
            model=self.config.model_name,
            system_prompt=self.agent.prompt,
            tools=[],
            permission_mode=self.config.permission_mode,
            max_turns=1,  # Intent classification is single-turn
        )

        # Run classification
        result = None
        async with ClaudeSDKClient(options) as client:
            await client.query(prompt)

            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            result = self._parse_json_result(block.text)
                            if result is not None:
                                break
                if result is not None:
                    break

        if result is None:
            logger.warning("Intent classification failed, defaulting to chat")
            return {
                "intent": "chat",
                "confidence": 0.5,
                "response": "抱歉，我没有理解你的意思。你可以：\n1. 直接输入文本让我抽取知识图谱\n2. 输入 :help 查看帮助\n3. 随意和我聊天",
            }

        logger.info(f"Intent classified: {result['intent']} (confidence: {result.get('confidence', 0)})")
        return result

    def _parse_json_result(self, text: str) -> dict[str, Any] | None:
        """Parse JSON result from agent response.

        Args:
            text: Response text

        Returns:
            Parsed JSON or None if not valid
        """
        import re

        text = text.strip()

        # Strategy 1: Try to find ```json blocks
        json_blocks = re.findall(r'```(?:json)?\s*\n(.*?)\n```', text, re.DOTALL)
        if json_blocks:
            for block in reversed(json_blocks):
                try:
                    result = json.loads(block.strip())
                    if isinstance(result, dict) and "intent" in result:
                        logger.info("Parsed JSON from code block")
                        return result
                except json.JSONDecodeError:
                    continue

        # Strategy 2: Parse entire text as JSON
        try:
            result = json.loads(text)
            if isinstance(result, dict) and "intent" in result:
                return result
        except json.JSONDecodeError:
            pass

        # Strategy 3: Find JSON object in text (between first { and last })
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                result = json.loads(text[start : end + 1])
                if isinstance(result, dict) and "intent" in result:
                    logger.info("Parsed JSON from text extraction")
                    return result
            except json.JSONDecodeError:
                pass

        logger.warning(f"Could not parse JSON from response: {text[:200]}...")
        return None
