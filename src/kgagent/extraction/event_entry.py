"""Event KG extraction using AutoSchemaKG approach."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    TextBlock,
)

from kgagent.extraction.config import ExtractionConfig
from kgagent.extraction.tools.autoschema import extract_autoschema_kg, format_autoschema_result
from kgagent.system.prompts import load_prompt

logger = logging.getLogger(__name__)


class EventExtractionEntry:
    """Entry point for AutoSchemaKG-based event KG extraction."""

    def __init__(self, config: ExtractionConfig):
        self.config = config
        # Load the event extraction prompt
        try:
            self.base_prompt = load_prompt("event_extraction")
        except FileNotFoundError:
            logger.warning("event_extraction.md not found, using default prompt")
            self.base_prompt = "You are an AutoSchemaKG extraction agent."

    def extract(
        self,
        data: str | dict,
        language: str = "en",
    ) -> dict[str, Any]:
        """Synchronous extraction."""
        return asyncio.run(self.extract_async(data, language))

    async def extract_async(
        self,
        data: str | dict,
        language: str = "en",
    ) -> dict[str, Any]:
        """Run AutoSchemaKG three-stage extraction.

        Args:
            data: Input text or dict with 'text' field
            language: Language code ("en" or "zh")

        Returns:
            Extraction result with entity_relation_dict, event_entity_relation_dict, event_relation_dict
        """
        # Extract text
        if isinstance(data, dict):
            text = data.get("text") or data.get("content") or str(data)
        else:
            text = str(data)

        logger.info(f"Starting AutoSchemaKG event extraction: text_length={len(text)}")

        # Get prompts and schemas
        autoschema_config = extract_autoschema_kg(text, language=language)

        # Run three stages sequentially
        results = {}
        for stage in autoschema_config["stages"]:
            logger.info(f"Running stage: {stage}")

            prompt = autoschema_config["prompts"][stage]
            schema = autoschema_config["schemas"][stage]

            result = await self._run_stage(prompt, schema, stage)
            results[stage] = result

        # Format final result
        formatted = format_autoschema_result(
            entity_relations=results.get("entity_relation", []),
            event_entities=results.get("event_entity", []),
            event_relations=results.get("event_relation", [])
        )

        logger.info(f"Event extraction complete: entity_relations={len(formatted.get('entity_relation_dict', []))}, "
                   f"event_entities={len(formatted.get('event_entity_relation_dict', []))}, "
                   f"event_relations={len(formatted.get('event_relation_dict', []))}")
        return formatted

    async def _run_stage(
        self,
        prompt: str,
        schema: dict,
        stage_name: str,
    ) -> list[dict[str, Any]]:
        """Run a single AutoSchemaKG stage.

        Args:
            prompt: Stage-specific prompt
            schema: Expected JSON schema
            stage_name: Stage identifier

        Returns:
            List of extracted items for this stage
        """
        # Use the loaded prompt as system prompt
        system_prompt = self.base_prompt

        # Build options
        options = ClaudeAgentOptions(
            cwd=self.config.work_dir,
            model=self.config.model_name,
            system_prompt=system_prompt,
            tools=[],
            permission_mode=self.config.permission_mode,
            max_turns=self.config.max_turns,
        )

        # Run extraction
        result = None
        async with ClaudeSDKClient(options) as client:
            await client.query(prompt)

            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            result = self._parse_json_array(block.text)
                            if result is not None:
                                break
                if result is not None:
                    break

        if result is None:
            logger.warning(f"Stage {stage_name} returned no valid result, using empty list")
            return []

        return result

    def _parse_json_array(self, text: str) -> list[dict[str, Any]] | None:
        """Parse JSON array from agent response.

        Args:
            text: Response text

        Returns:
            Parsed JSON array or None
        """
        import re

        text = text.strip()

        # Strategy 1: Try to find ```json blocks
        json_blocks = re.findall(r'```(?:json)?\s*\n(.*?)\n```', text, re.DOTALL)
        if json_blocks:
            for block in reversed(json_blocks):
                try:
                    result = json.loads(block.strip())
                    if isinstance(result, list):
                        logger.info(f"Parsed JSON array from code block: {len(result)} items")
                        return result
                except json.JSONDecodeError:
                    continue

        # Strategy 2: Parse entire text
        try:
            result = json.loads(text)
            if isinstance(result, list):
                logger.info(f"Parsed JSON array from full text: {len(result)} items")
                return result
        except json.JSONDecodeError:
            pass

        # Strategy 3: Extract array [...]
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            try:
                result = json.loads(text[start:end + 1])
                if isinstance(result, list):
                    logger.info(f"Parsed JSON array from extraction: {len(result)} items")
                    return result
            except json.JSONDecodeError:
                pass

        logger.warning(f"Could not parse JSON array from response: {text[:200]}...")
        return None
