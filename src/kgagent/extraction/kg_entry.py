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
from kgagent.core.batch import is_batch_input, process_batch_with_resume

logger = logging.getLogger(__name__)


class ExtractionEntry:
    """Entry point for knowledge graph extraction route."""

    def __init__(self, config: ExtractionConfig):
        self.config = config
        self.agent = build_extraction_agent()

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
        save_to: str | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Run extraction agent on the data.

        Args:
            data: Input data (text, dict, or list)
            extraction_type: Type of extraction (triples, temporal, hyper)
            save_to: Optional path to save results (for batch with resume)

        Returns:
            Extraction result as dict or list (for batch)
        """
        logger.info(
            f"Starting extraction: type={extraction_type}, "
            f"data_type={type(data).__name__}"
        )

        # Check if input is a batch
        if is_batch_input(data):
            logger.info(f"Batch mode detected: {len(data)} items")
            return await self._batch_extract(data, extraction_type, save_to)

        # Single-stage extraction
        logger.info("Using single-stage extraction")
        return await self._single_stage_extract(data, extraction_type)

    async def _batch_extract(
        self,
        data_list: list,
        extraction_type: str,
        save_to: str | None = None,
    ) -> list[dict[str, Any]]:
        """Process batch of items with concurrency control and resume support.

        Args:
            data_list: List of data items
            extraction_type: Type of extraction
            save_to: Optional path to save results (enables resume support)

        Returns:
            List of results with index, text, and kg fields
        """
        from kgagent.core.batch import process_batch_with_resume, process_batch
        from pathlib import Path

        async def process_single_item(item: Any) -> dict[str, Any]:
            """Process a single item from the batch."""
            # Extract text from item
            if isinstance(item, dict):
                text = (
                    item.get("text") or
                    item.get("content") or
                    item.get("description") or
                    str(item)
                )
            else:
                text = str(item)

            # Extract from this single item
            extraction_result = await self._single_stage_extract(text, extraction_type)
            return extraction_result

        def format_result(result: dict, item: Any, index: int) -> dict[str, Any]:
            """Format result for output."""
            # DEBUG: Log what we received
            logger.debug(f"format_result called for index {index}")
            logger.debug(f"  result type: {type(result)}")
            logger.debug(f"  result keys: {list(result.keys()) if isinstance(result, dict) else 'N/A'}")

            # Extract text
            if isinstance(item, dict):
                text = (
                    item.get("text") or
                    item.get("content") or
                    item.get("description") or
                    str(item)
                )
            else:
                text = str(item)

            # Build result item
            result_item = {
                "index": index,
                "text": text,
            }

            # Copy original fields if dict
            if isinstance(item, dict):
                for key, value in item.items():
                    if key not in result_item:
                        result_item[key] = value

            # Add kg field based on extraction type
            if extraction_type == "triples":
                kg_value = result.get("triple", [])
                logger.debug(f"  extraction_type=triples, kg length: {len(kg_value)}")
                result_item["kg"] = kg_value
            elif extraction_type == "temporal":
                result_item["kg"] = result.get("quadruples", [])
            elif extraction_type == "hyper":
                result_item["kg"] = result.get("hyper_relations", [])
            else:
                result_item["kg"] = []

            return result_item

        # If save_to is provided, use process_batch_with_resume for resume support
        if save_to:
            logger.info(f"Using batch processing with resume support (output: {save_to})")

            await process_batch_with_resume(
                items=data_list,
                process_fn=process_single_item,
                output_path=save_to,
                max_concurrent=3,
                format_fn=format_result,
            )

            # Load the saved results and return
            import json
            with open(save_to, "r", encoding="utf-8") as f:
                results = json.load(f)

            logger.info(f"Batch extraction complete: {len(results)} items")
            return results

        # Otherwise use regular process_batch (no resume)
        logger.info(f"Starting batch processing with max_concurrent=3")
        batch_result = await process_batch(
            items=data_list,
            process_fn=process_single_item,
            max_concurrent=3,
            return_errors=True,
        )

        # Format results
        results = []
        successful_results = batch_result.get("batch_results", [])
        errors = batch_result.get("batch_errors", [])

        # Add successful results
        for item_result in successful_results:
            index = item_result["index"]
            extraction_result = item_result["result"]
            original_item = data_list[index]
            formatted = format_result(extraction_result, original_item, index)
            results.append(formatted)

        # Add error results (with empty kg)
        for error_item in errors:
            index = error_item["index"]
            original_item = data_list[index]

            if isinstance(original_item, dict):
                text = original_item.get("text") or original_item.get("content") or str(original_item)
            else:
                text = str(original_item)

            result_item = {
                "index": index,
                "text": text,
                "kg": [],
                "error": error_item["error"]
            }

            if isinstance(original_item, dict):
                for key, value in original_item.items():
                    if key not in result_item:
                        result_item[key] = value

            results.append(result_item)

        # Sort by index to maintain order
        results.sort(key=lambda x: x["index"])

        summary = batch_result.get("summary", {})
        logger.info(
            f"Batch extraction complete: {summary.get('successful', 0)} successful, "
            f"{summary.get('failed', 0)} failed out of {summary.get('total', 0)} items"
        )

        return results

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
            logger.error("Extraction agent returned no valid result")
            logger.error(f"Last response text (first 1000 chars): {text[:1000]}")
            raise ValueError("Extraction agent returned no valid result")

        logger.info(f"Extraction complete: {len(str(result))} bytes")
        logger.info(f"Result structure: keys={list(result.keys()) if isinstance(result, dict) else 'not a dict'}")
        if isinstance(result, dict) and extraction_type == 'triples':
            triple_count = len(result.get('triple', []))
            logger.info(f"Triple count: {triple_count}")
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

        # DEBUG: Log the raw response
        logger.debug(f"Raw LLM response (first 500 chars): {text[:500]}")

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
                        'triple' in result or
                        'triples' in result or
                        'relations' in result or
                        'quadruples' in result or
                        'hyper_relations' in result or
                        'entities' in result  # Accept partial results too
                    ):
                        logger.info(f"Parsed JSON from code block")
                        logger.info(f"Result keys: {list(result.keys())}")
                        logger.info(f"Result preview: {str(result)[:200]}")

                        # Normalize relations/triples field
                        result = self._normalize_result(result)
                        return result
                except json.JSONDecodeError:
                    continue

        # Strategy 2: Try to parse entire text as JSON
        try:
            result = json.loads(text)
            return self._normalize_result(result) if isinstance(result, dict) else result
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
                    return self._normalize_result(result)
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

    def _normalize_result(self, result: dict[str, Any]) -> dict[str, Any]:
        """Normalize result to standard format.

        Convert 'relations' to 'triple', 'triples' to 'triple', etc.

        Args:
            result: Raw result dict

        Returns:
            Normalized result dict
        """
        # Handle relations field (legacy format from _parse_structured_text)
        if 'relations' in result and 'triple' not in result:
            relations = result.pop('relations')
            # Convert to tagged format
            if isinstance(relations, list) and relations:
                # Check format of first item
                if isinstance(relations[0], list) and len(relations[0]) >= 3:
                    # [[subj, rel, obj], ...] format
                    result['triple'] = [
                        f"<subj> {r[0]} <obj> {r[2]} <rel> {r[1]}"
                        for r in relations
                    ]
                elif isinstance(relations[0], str):
                    # Already in tagged format or malformed
                    # Try to keep only valid ones
                    valid_triples = []
                    for r in relations:
                        if '<subj>' in r and '<obj>' in r and '<rel>' in r:
                            valid_triples.append(r)
                    result['triple'] = valid_triples

        # Handle triples field (should be triple)
        if 'triples' in result and 'triple' not in result:
            result['triple'] = result.pop('triples')

        # Remove entities field if it's malformed (not a simple list of strings)
        if 'entities' in result:
            entities = result['entities']
            if isinstance(entities, list) and entities:
                # Check if all items are simple strings
                if not all(isinstance(e, str) and '<subj>' not in e for e in entities):
                    # Malformed entities, remove it
                    result.pop('entities')

        return result

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
                # Convert to standard tagged format
                result['triple'] = [
                    f"<subj> {s.strip()} <obj> {o.strip()} <rel> {r.strip()}"
                    for s, r, o in matches
                ]
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
