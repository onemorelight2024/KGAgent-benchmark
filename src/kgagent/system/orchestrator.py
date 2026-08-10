"""Orchestrator for routing extraction tasks."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from kgagent.extraction.config import ExtractionConfig
from kgagent.extraction.kg_entry import ExtractionEntry
from kgagent.extraction.tools.loaders import load_json_file, save_json_file
from kgagent.extraction.tools.validation import validate_result
from kgagent.extraction.document_processor import preprocess_document
from kgagent.core.validators import (
    validate_kg_completion_result,
    validate_qa_reasoning_result,
)
from kgagent.system.registry import ExtractionRegistry
from kgagent.core.language import detect_language
from kgagent.conversion import ConversionEntry

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
    chunk_size: int = 2000,
    overlap: int = 200,
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
        chunk_size: Maximum characters per chunk (for document preprocessing)
        overlap: Overlap between chunks (for document preprocessing)

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

    # Document preprocessing: check if input is a file that needs preprocessing
    processed_data = data
    preprocessing_result = None

    if isinstance(data, str):
        # Check if it's a file path (with safety check for long strings)
        try:
            # Only check paths that are reasonable file path lengths
            if len(data) < 500:  # File paths are typically < 500 chars
                path = Path(data)
                if path.exists() and path.is_file():
                    file_ext = path.suffix.lower()

                    # Check if file needs preprocessing
                    if file_ext in [".json", ".pdf", ".md", ".txt", ".jsonl"]:
                        logger.info(f"Preprocessing document: {path}")
                        preprocessing_result = await preprocess_document(
                            path,
                            chunk_size=chunk_size,
                            overlap=overlap
                        )

                        if not preprocessing_result.get("success"):
                            # If preprocessing fails, return the error
                            error_msg = preprocessing_result.get("error", "Preprocessing failed")
                            logger.error(f"Preprocessing failed: {error_msg}")
                            return {
                                "success": False,
                                "error": error_msg,
                                "preprocessing_result": preprocessing_result,
                            }

                        # Use chunked data for extraction
                        processed_data = preprocessing_result.get("chunks", [])
                        logger.info(f"Document preprocessed into {len(processed_data)} chunks")

                    # For other file types, try loading as JSON
                    elif file_ext == ".json":
                        logger.info(f"Loading data from JSON file: {path}")
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

    # Detect language from input data
    if isinstance(processed_data, str):
        language = detect_language(processed_data)
    elif isinstance(processed_data, dict):
        # Try to extract text from dict
        text = (
            processed_data.get("text") or
            processed_data.get("content") or
            processed_data.get("description") or
            str(processed_data)
        )
        language = detect_language(text)
    else:
        language = "en"

    logger.info(f"Detected language: {language}")

    # Route based on extraction type
    if extraction_type == "event":
        # Use AutoSchemaKG event extraction
        from kgagent.extraction.event_entry import EventExtractionEntry

        logger.info(f"Running {extraction_type} extraction (AutoSchemaKG)")
        event_entry = EventExtractionEntry(config)
        result = await event_entry.extract_async(processed_data, language=language)
    else:
        # Normal extraction (triples/temporal/hyper)
        logger.info(f"Running {extraction_type} extraction")
        result = await entry.extract_async(
            processed_data,
            extraction_type,
            save_to=save_to  # Pass save_to for batch resume support
        )

    # Validate if requested
    if validate:
        logger.info("Validating result")
        try:
            result = validate_result(result, extraction_type)
            result["_validated"] = True
        except Exception as e:
            logger.warning(f"Validation failed: {e}")
            result["_validation_error"] = str(e)

    # Add preprocessing metadata if document was preprocessed
    if preprocessing_result:
        result["_preprocessing"] = {
            "file_type": preprocessing_result.get("file_type"),
            "pdf_type": preprocessing_result.get("pdf_type"),
            "chunks_count": len(preprocessing_result.get("chunks", [])),
            "original_file": preprocessing_result.get("original_file"),
        }

    # Save if requested (batch processing with resume already saves, so skip for list results)
    if save_to and isinstance(result, dict):
        logger.info(f"Saving result to {save_to}")
        save_json_file(result, save_to)
        result["_saved_to"] = str(save_to)
    elif save_to and isinstance(result, list):
        # Batch processing with resume already saved the file
        logger.info(f"Result already saved to {save_to} during batch processing")

    return result


async def run_conversion(
    source: str,
    target_format: str,
    output_path: str | None = None,
    last_result: dict | list | None = None,
) -> dict[str, Any]:
    """Run format conversion task.

    Args:
        source: Source data ("last" or "file:<path>")
        target_format: Target format (neo4j_csv, rdf, graphml, json)
        output_path: Optional output path
        last_result: Last extraction result for "last" source

    Returns:
        Conversion result with output paths and statistics
    """
    logger.info(f"Running conversion: source={source}, format={target_format}")

    # Resolve source data
    if source == "last":
        if last_result is None:
            raise ValueError("No previous extraction result available")
        input_data = last_result
    elif source.startswith("file:"):
        file_path = source[5:]  # Remove "file:" prefix
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Source file not found: {file_path}")

        # Check if source is an external format that needs importing first
        suffix = path.suffix.lower()
        if suffix in ['.graphml', '.xml', '.dump'] and target_format == 'json':
            # This is actually an import operation (external format -> JSON)
            logger.info(f"Detected import operation from {suffix} to JSON")
            converter = ConversionEntry()
            result = converter.convert_from(
                input_path=path,
                output_path=output_path,
            )
            logger.info(f"Import complete: {result.get('output_file')}")
            return result

        # Otherwise load as JSON for conversion
        input_data = load_json_file(path)
    else:
        raise ValueError(f"Invalid source: {source}")

    # Run conversion
    converter = ConversionEntry()
    result = converter.convert(
        input_data=input_data,
        output_format=target_format,
        output_path=output_path,
    )

    logger.info(f"Conversion complete: {result.get('output_dir', result.get('output_file'))}")


async def run_reasoning(
    data: str | dict | list,
    task_type: str,
    model_name: str,
    work_dir: str | Path,
    permission_mode: str,
    max_turns: int,
    validate: bool = False,
    save_to: str | None = None,
) -> dict[str, Any]:
    """Run a reasoning task."""
    from kgagent.reasoning.reasoning_entry import ReasoningEntry

    logger.info("Orchestrator routing reasoning task: task_type=%s", task_type)
    config = ExtractionConfig(
        model_name=model_name,
        work_dir=str(work_dir),
        permission_mode=permission_mode,
        max_turns=max_turns,
    )
    entry = ReasoningEntry(config)
    result = await entry.reason_async(data, task_type)
    logger.info("Orchestrator finished reasoning task: task_type=%s", task_type)

    if validate:
        validators = {
            "qa": validate_qa_reasoning_result,
            "completion": validate_kg_completion_result,
        }
        validator = validators.get(task_type)
        if validator is not None:
            validation = validator(result)
            result["_validated"] = validation["valid"]
            if validation["errors"]:
                result["_validation_errors"] = validation["errors"]

    if save_to:
        logger.info(f"Saving reasoning result to {save_to}")
        save_json_file(result, save_to)
        result["_saved_to"] = str(save_to)
    return result
