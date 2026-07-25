"""Entry point for the extraction route."""

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

from kgagent.extraction.agents.extractor import build_extraction_agent
from kgagent.extraction.config import ExtractionConfig
from kgagent.extraction.three_stage import ThreeStageExtractor

logger = logging.getLogger(__name__)


class ExtractionEntry:
    """Entry point for knowledge graph extraction route."""

    def __init__(self, config: ExtractionConfig):
        self.config = config
        self.agent = build_extraction_agent()

        # Initialize three-stage extractor
        self.three_stage_extractor = ThreeStageExtractor(config)

        # Enable three-stage mode by default for triples, temporal, and hyper
        self.use_three_stage = True

    def extract(
        self,
        data: str | dict | list,
        extraction_type: str,
    ) -> dict[str, Any]:
        """Synchronous extraction."""
        return asyncio.run(self.extract_async(data, extraction_type))

    async def extract_async(
        self,
        data: str | dict | list,
        extraction_type: str,
    ) -> dict[str, Any]:
        """Run extraction agent on the data.

        Args:
            data: Input data (text, dict, or list)
            extraction_type: Type of extraction (triples, temporal, hyper)

        Returns:
            Extraction result as dict
        """
        logger.info(
            f"Starting extraction: type={extraction_type}, "
            f"data_type={type(data).__name__}"
        )

        # Use three-stage extraction for triples, temporal, and hyper
        if self.use_three_stage and extraction_type in ("triples", "temporal", "hyper"):
            logger.info("Using three-stage extraction pipeline")
            return await self.three_stage_extractor.extract_async(data, extraction_type)

        # Fall back to original single-stage extraction
        logger.info("Using single-stage extraction")
        return await self._single_stage_extract(data, extraction_type)

    async def _single_stage_extract(
        self,
        data: str | dict | list,
        extraction_type: str,
    ) -> dict[str, Any]:
        """Original single-stage extraction method.

        Args:
            data: Input data
            extraction_type: Type of extraction

        Returns:
            Extraction result
        """
        # Build prompt
        prompt = self._build_prompt(data, extraction_type)

        # Build options
        options = ClaudeAgentOptions(
            cwd=self.config.work_dir,
            model=self.config.model_name,
            system_prompt=self.agent.prompt,
            tools=[],  # Extraction agent doesn't need tools
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
                            result = self._parse_result(block.text)
                            if result is not None:
                                break
                if result is not None:
                    break

        if result is None:
            raise ValueError("Extraction agent returned no valid result")

        logger.info(f"Extraction complete: {len(str(result))} bytes")
        return result

    def _build_prompt(
        self,
        data: str | dict | list,
        extraction_type: str,
    ) -> str:
        """Build extraction prompt.

        Args:
            data: Input data
            extraction_type: Type of extraction

        Returns:
            Prompt string
        """
        type_map = {
            "triples": "relation triples",
            "temporal": "temporal quadruples",
            "hyper": "hyper-relations",
        }

        extraction_name = type_map.get(extraction_type, extraction_type)

        # Format data
        if isinstance(data, str):
            data_str = data
        else:
            data_str = json.dumps(data, indent=2, ensure_ascii=False)

        prompt = f"""Extract {extraction_name} from the following data:

{data_str}

CRITICAL: Your response must be ONLY valid JSON. Start with {{ and end with }}. No explanatory text, no markdown blocks, no preamble."""

        return prompt

    def _parse_result(self, text: str) -> dict[str, Any] | None:
        """Parse JSON result from agent response.

        Args:
            text: Response text

        Returns:
            Parsed JSON or None if not valid
        """
        import re

        text = text.strip()

        # Strategy 1: Try to find the final/complete JSON block
        # Look for ```json ... ``` blocks
        json_blocks = re.findall(r'```(?:json)?\s*\n(.*?)\n```', text, re.DOTALL)

        if json_blocks:
            # Try to parse each block, starting from the last one (most likely complete)
            for block in reversed(json_blocks):
                try:
                    result = json.loads(block.strip())
                    # Check if it's a complete result (has expected fields)
                    if isinstance(result, dict) and (
                        'triples' in result or
                        'relations' in result or
                        'quadruples' in result or
                        'hyper_relations' in result or
                        'entities' in result  # Accept partial results too
                    ):
                        logger.info(f"Parsed JSON from code block")
                        return result
                except json.JSONDecodeError:
                    continue

        # Strategy 2: Try to parse entire text as JSON
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Strategy 3: Find JSON object in text (between first { and last })
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                result = json.loads(text[start : end + 1])
                if isinstance(result, dict):
                    logger.info(f"Parsed JSON from text extraction")
                    return result
            except json.JSONDecodeError:
                pass

        # Strategy 4: Find JSON array
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass

        # Strategy 5: Parse structured text format (fallback for badly formatted responses)
        # Try to extract triples/quadruples/hyper-relations from markdown lists
        result = self._parse_structured_text(text)
        if result:
            logger.info(f"Parsed result from structured text")
            return result

        logger.warning(f"Could not parse JSON from response: {text[:200]}...")
        return None

    def _parse_structured_text(self, text: str) -> dict[str, Any] | None:
        """Parse structured text format (markdown lists, tuples, etc.).

        Args:
            text: Response text

        Returns:
            Parsed result or None
        """
        import re

        result = {}

        # Try to parse relation triples: (subject, relation, object) or [subject, relation, object]
        triple_patterns = [
            r'\*\*\((.*?),\s*(.*?),\s*(.*?)\)\*\*',  # **(Alice, works_at, Acme)**
            r'\((.*?),\s*(.*?),\s*(.*?)\)',          # (Alice, works_at, Acme)
            r'\[(.*?),\s*(.*?),\s*(.*?)\]',          # [Alice, works_at, Acme]
        ]

        for pattern in triple_patterns:
            matches = re.findall(pattern, text)
            if matches:
                result['relations'] = [[s.strip(), r.strip(), o.strip()] for s, r, o in matches]
                # Extract entities
                entities = set()
                for s, r, o in matches:
                    entities.add(s.strip())
                    entities.add(o.strip())
                result['entities'] = sorted(list(entities))
                return result

        # Try to parse temporal quadruples: (subject, relation, object, time)
        quad_patterns = [
            r'\((.*?),\s*(.*?),\s*(.*?),\s*(.*?)\)',  # (Bob, met, Carol, 2024-01-15)
        ]

        for pattern in quad_patterns:
            matches = re.findall(pattern, text)
            if matches and len(matches[0]) == 4:
                result['quadruples'] = [
                    f"<subj> {s.strip()} <obj> {o.strip()} <rel> {r.strip()} <time> {t.strip()}"
                    for s, r, o, t in matches
                ]
                return result

        return None
