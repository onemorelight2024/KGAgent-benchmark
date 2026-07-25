"""Three-stage extraction pipeline for knowledge graph extraction."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    TextBlock,
)

from kgagent.extraction.config import ExtractionConfig


class ThreeStageExtractor:
    """Three-stage extraction pipeline for triples, temporal, and hyper-relations.

    Stages:
    1. Entity Extraction - Extract all entities from text
    2. Relation Extraction - Extract relations between entities
    3. Post-Processing - Merge synonyms and quality control
    """

    def __init__(self, config: ExtractionConfig):
        """Initialize the three-stage extractor.

        Args:
            config: Extraction configuration
        """
        self.config = config
        self.work_dir = Path(config.work_dir)
        self.tmp_dir = self.work_dir / "tmp"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

        # Load prompts
        prompt_dir = Path(__file__).parent.parent / "prompts"
        self.stage1_prompt = (prompt_dir / "stage1_entity_extraction.md").read_text()
        self.stage2_prompt = (prompt_dir / "stage2_relation_extraction.md").read_text()
        self.stage3_prompt = (prompt_dir / "stage3_postprocessing.md").read_text()

    async def extract_async(
        self,
        data: str | dict | Any,
        extraction_type: str = "triples",
    ) -> dict[str, Any]:
        """Run three-stage extraction pipeline.

        Args:
            data: Input text or dict
            extraction_type: Type of extraction (triples, temporal, hyper)

        Returns:
            Final extraction result
        """
        # Generate unique session ID for temp files
        session_id = str(uuid.uuid4())[:8]

        # Convert data to text
        text = self._prepare_text(data)

        try:
            # Stage 1: Extract entities
            entities_result = await self._stage1_extract_entities(text, session_id)
            entities = entities_result.get("entities", [])

            if not entities:
                return self._empty_result(extraction_type)

            # Stage 2: Extract relations
            relations_result = await self._stage2_extract_relations(
                text, entities, extraction_type, session_id
            )

            # Stage 3: Post-processing
            final_result = await self._stage3_postprocess(
                entities, relations_result, extraction_type, session_id
            )

            return final_result

        finally:
            # Clean up temp files
            self._cleanup_temp_files(session_id)

    def _prepare_text(self, data: str | dict | Any) -> str:
        """Convert input data to text string.

        Args:
            data: Input data

        Returns:
            Text string
        """
        if isinstance(data, str):
            return data
        elif isinstance(data, dict):
            # Try common text fields
            for field in ["text", "content", "description", "body", "message"]:
                if field in data:
                    return str(data[field])
            # Convert dict to text
            return "\n".join(f"{k}: {v}" for k, v in data.items())
        else:
            return str(data)

    async def _stage1_extract_entities(
        self, text: str, session_id: str
    ) -> dict[str, Any]:
        """Stage 1: Extract entities from text.

        Args:
            text: Input text
            session_id: Session ID for temp files

        Returns:
            Dict with entities list
        """
        print(f"  [Stage 1/3] Extracting entities...")

        # Build options
        options = ClaudeAgentOptions(
            cwd=str(self.work_dir),
            model=self.config.model_name,
            system_prompt=self.stage1_prompt,
            tools=[],
            permission_mode=self.config.permission_mode,
            max_turns=self.config.max_turns,
        )

        # Run extraction
        user_prompt = f"Extract all entities from this text:\n\n{text}"
        result = None

        async with ClaudeSDKClient(options) as client:
            await client.query(user_prompt)

            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            result = self._parse_json_response(block.text)
                            if result is not None:
                                break
                if result is not None:
                    break

        if result is None:
            result = {"entities": []}

        # Save to temp file
        temp_file = self.tmp_dir / f"{session_id}_stage1_entities.json"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        print(f"    ✓ Extracted {len(result.get('entities', []))} entities")

        return result

    async def _stage2_extract_relations(
        self,
        text: str,
        entities: list[str],
        extraction_type: str,
        session_id: str,
    ) -> dict[str, Any]:
        """Stage 2: Extract relations between entities.

        Args:
            text: Input text
            entities: List of entities from stage 1
            extraction_type: Type of extraction
            session_id: Session ID for temp files

        Returns:
            Dict with relations/quadruples/hyper_relations
        """
        print(f"  [Stage 2/3] Extracting relations ({extraction_type})...")

        # Build options
        options = ClaudeAgentOptions(
            cwd=str(self.work_dir),
            model=self.config.model_name,
            system_prompt=self.stage2_prompt,
            tools=[],
            permission_mode=self.config.permission_mode,
            max_turns=self.config.max_turns,
        )

        # Build user prompt
        entities_json = json.dumps(entities, ensure_ascii=False)
        user_prompt = f"""Extract {extraction_type} from this text using the provided entities.

**Entities:**
{entities_json}

**Text:**
{text}

**Extraction Type:** {extraction_type}
"""

        result = None

        async with ClaudeSDKClient(options) as client:
            await client.query(user_prompt)

            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            result = self._parse_json_response(block.text)
                            if result is not None:
                                break
                if result is not None:
                    break

        if result is None:
            result = self._empty_result(extraction_type)

        # Save to temp file
        temp_file = self.tmp_dir / f"{session_id}_stage2_relations.json"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        # Count results
        count = 0
        if extraction_type == "triples":
            count = len(result.get("relations", []))
        elif extraction_type == "temporal":
            count = len(result.get("quadruples", []))
        elif extraction_type == "hyper":
            count = len(result.get("hyper_relations", []))

        print(f"    ✓ Extracted {count} relations")

        return result

    async def _stage3_postprocess(
        self,
        entities: list[str],
        relations_result: dict[str, Any],
        extraction_type: str,
        session_id: str,
    ) -> dict[str, Any]:
        """Stage 3: Post-processing - merge synonyms and quality control.

        Args:
            entities: List of entities
            relations_result: Relations from stage 2
            extraction_type: Type of extraction
            session_id: Session ID for temp files

        Returns:
            Final processed result
        """
        print(f"  [Stage 3/3] Post-processing (merging & quality control)...")

        # Build options
        options = ClaudeAgentOptions(
            cwd=str(self.work_dir),
            model=self.config.model_name,
            system_prompt=self.stage3_prompt,
            tools=[],
            permission_mode=self.config.permission_mode,
            max_turns=self.config.max_turns,
        )

        # Build user prompt
        input_data = {
            "entities": entities,
            "extraction_type": extraction_type,
        }
        input_data.update(relations_result)

        input_json = json.dumps(input_data, indent=2, ensure_ascii=False)

        user_prompt = f"""Perform post-processing on these extracted relations.

Tasks:
1. Merge synonymous entities
2. Merge synonymous relations
3. Quality control - filter out invalid/malformed triples

**Input Data:**
{input_json}

**Extraction Type:** {extraction_type}
"""

        result = None

        async with ClaudeSDKClient(options) as client:
            await client.query(user_prompt)

            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            result = self._parse_json_response(block.text)
                            if result is not None:
                                break
                if result is not None:
                    break

        if result is None:
            # If post-processing fails, return original result
            result = relations_result
            result["entities"] = entities

        # Save to temp file
        temp_file = self.tmp_dir / f"{session_id}_stage3_final.json"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        # Show stats
        stats = result.get("stats", {})
        if stats:
            removed = stats.get("removed_count", 0)
            final_count = stats.get("filtered_relation_count", stats.get("filtered_count", 0))
            print(f"    ✓ Final: {final_count} relations (removed {removed} invalid)")
        else:
            # No stats, just show result count
            count = 0
            if extraction_type == "triples":
                count = len(result.get("relations", []))
            elif extraction_type == "temporal":
                count = len(result.get("quadruples", []))
            elif extraction_type == "hyper":
                count = len(result.get("hyper_relations", []))
            print(f"    ✓ Final: {count} relations")

        return result

    def _parse_json_response(self, content: str) -> dict[str, Any]:
        """Parse JSON from agent response.

        Args:
            content: Response content

        Returns:
            Parsed JSON dict
        """
        # Try to parse as JSON
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # Try to extract JSON from markdown or text
        content = content.strip()

        # Remove markdown code blocks
        if content.startswith("```"):
            lines = content.split("\n")
            # Skip first line (```json or ```) and last line (```)
            if len(lines) > 2:
                content = "\n".join(lines[1:-1])
            content = content.strip()

            # Try parsing the cleaned content
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                pass

        # Find JSON object
        start = content.find("{")
        end = content.rfind("}") + 1

        if start != -1 and end > start:
            json_str = content[start:end]
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass

        # Failed to parse - return None instead of error dict
        print(f"Warning: Failed to parse JSON from response: {content[:200]}...")
        return None

    def _empty_result(self, extraction_type: str) -> dict[str, Any]:
        """Generate empty result for given extraction type.

        Args:
            extraction_type: Type of extraction

        Returns:
            Empty result dict
        """
        if extraction_type == "triples":
            return {"entities": [], "relations": []}
        elif extraction_type == "temporal":
            return {"quadruples": []}
        elif extraction_type == "hyper":
            return {"hyper_relations": []}
        else:
            return {}

    def _cleanup_temp_files(self, session_id: str):
        """Clean up temporary files for a session.

        Args:
            session_id: Session ID
        """
        pattern = f"{session_id}_*.json"
        for temp_file in self.tmp_dir.glob(pattern):
            try:
                temp_file.unlink()
            except Exception:
                pass
