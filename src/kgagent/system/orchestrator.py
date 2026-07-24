"""Orchestrator for routing extraction tasks."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from kgagent.extraction.config import ExtractionConfig
from kgagent.extraction.kg_entry import ExtractionEntry
from kgagent.extraction.tools.loaders import load_json_file, save_json_file
from kgagent.extraction.tools.validation import validate_result
from kgagent.system.registry import ExtractionRegistry

logger = logging.getLogger(__name__)


async def run_extraction(
    data: str | dict | list,
    extraction_type: str,
    model_name: str,
    work_dir: str | Path,
    permission_mode: str,
    max_turns: int,
    registry: ExtractionRegistry,
    validate: bool = False,
    save_to: str | None = None,
) -> dict[str, Any]:
    """Run extraction task.

    Args:
        data: Input data (file path, inline text, or JSON data)
        extraction_type: Type of extraction (auto, triples, temporal, hyper)
        model_name: Model name
        work_dir: Working directory
        permission_mode: Permission mode
        max_turns: Max turns for agent
        registry: Extraction registry
        validate: Whether to validate result
        save_to: Optional path to save result

    Returns:
        Extraction result
    """
    # Auto-detect extraction type if needed
    if extraction_type == "auto":
        if isinstance(data, str):
            extraction_type = registry.detect_type(data)
        else:
            extraction_type = "triples"  # Default
        logger.info(f"Auto-detected extraction type: {extraction_type}")

    # Load data if it's a file path
    processed_data = data
    if isinstance(data, str):
        # Check if it's a file path (with safety check for long strings)
        try:
            # Only check paths that are reasonable file path lengths
            if len(data) < 500:  # File paths are typically < 500 chars
                path = Path(data)
                if path.exists() and path.is_file():
                    logger.info(f"Loading data from file: {path}")
                    processed_data = load_json_file(path)
        except (OSError, ValueError):
            # Not a valid file path, treat as text data
            pass

    # Create extraction entry
    config = ExtractionConfig(
        model_name=model_name,
        work_dir=str(work_dir),
        permission_mode=permission_mode,
        max_turns=max_turns,
    )
    entry = ExtractionEntry(config)

    # Route based on extraction type
    if extraction_type == "event":
        # Use AutoSchemaKG event extraction
        from kgagent.extraction.event_entry import EventExtractionEntry

        logger.info(f"Running {extraction_type} extraction (AutoSchemaKG)")
        event_entry = EventExtractionEntry(config)
        result = await event_entry.extract_async(processed_data, language="en")
    else:
        # Normal extraction (triples/temporal/hyper)
        logger.info(f"Running {extraction_type} extraction")
        result = await entry.extract_async(processed_data, extraction_type)

    # Validate if requested
    if validate:
        logger.info("Validating result")
        try:
            result = validate_result(result, extraction_type)
            result["_validated"] = True
        except Exception as e:
            logger.warning(f"Validation failed: {e}")
            result["_validation_error"] = str(e)

    # Save if requested
    if save_to:
        logger.info(f"Saving result to {save_to}")
        save_json_file(result, save_to)
        result["_saved_to"] = str(save_to)

    return result
