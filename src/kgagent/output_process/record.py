"""Record extraction results to local files."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def clean_value(value: str | Any) -> str:
    """Clean extracted value by removing extra quotes, brackets, etc.

    Args:
        value: Raw value from extraction

    Returns:
        Cleaned string value
    """
    if not isinstance(value, str):
        value = str(value)

    # Remove leading/trailing whitespace
    value = value.strip()

    # Remove leading brackets and quotes (handle combinations like ["text")
    while value and value[0] in ('"', "'", '[', '{', '('):
        value = value[1:].strip()

    # Remove trailing brackets and quotes
    while value and value[-1] in ('"', "'", ']', '}', ')'):
        value = value[:-1].strip()

    # Remove escaped quotes and backslashes
    value = value.replace('\\"', '"').replace("\\'", "'").replace('\\\\', '')

    return value


def is_valid_triple_value(value: str) -> bool:
    """Check if a triple value is valid (not malformed).

    Args:
        value: Value to check

    Returns:
        True if valid, False if malformed
    """
    if not isinstance(value, str):
        return False

    # Check for excessive length (likely a concatenated list)
    if len(value) > 500:
        return False

    # Check for multiple quoted strings separated by commas (sign of malformed data)
    # Pattern: "text1","text2","text3"
    quote_comma_count = value.count('","')
    if quote_comma_count > 3:
        return False

    return True


def format_single_result(
    result: dict[str, Any],
    input_data: str | dict | None = None,
    extraction_type: str = "triples",
) -> dict[str, Any]:
    """Format a single extraction result with tagged format.

    Args:
        result: Raw extraction result
        input_data: Original input data (text or dict)
        extraction_type: Type of extraction

    Returns:
        Formatted result dict with "text" and "kg" fields
    """
    item = {}

    # Add text field
    if input_data:
        if isinstance(input_data, dict):
            text = (
                input_data.get("text") or
                input_data.get("content") or
                input_data.get("description") or
                str(input_data)
            )
            item["text"] = text
        elif isinstance(input_data, str):
            item["text"] = input_data

    # Format KG based on result type
    kg = []
    if "error" in result:
        item["error"] = result["error"]
    else:
        # Extract triples/relations - convert to tagged format
        if "relations" in result:
            for rel in result["relations"]:
                try:
                    if isinstance(rel, (list, tuple)) and len(rel) == 3:
                        s, r, o = rel
                        kg.append(f"<subj> {s} <obj> {o} <rel> {r}")
                except (ValueError, TypeError) as e:
                    # Skip malformed triples
                    continue
        elif "triples" in result:
            for rel in result["triples"]:
                try:
                    if isinstance(rel, (list, tuple)) and len(rel) == 3:
                        s, r, o = rel
                        kg.append(f"<subj> {s} <obj> {o} <rel> {r}")
                except (ValueError, TypeError) as e:
                    continue
        elif "relation_triples" in result:
            for rel in result["relation_triples"]:
                try:
                    if isinstance(rel, (list, tuple)) and len(rel) == 3:
                        s, r, o = rel
                        kg.append(f"<subj> {s} <obj> {o} <rel> {r}")
                except (ValueError, TypeError) as e:
                    continue
        # For temporal quadruples - keep as strings
        elif "quadruples" in result:
            kg = result["quadruples"]
        # For hyper-relations - keep as strings
        elif "hyper_relations" in result:
            kg = result["hyper_relations"]
        # For AutoSchemaKG events - keep full structure
        elif "entity_relation_dict" in result or "event_entity_relation_dict" in result:
            kg = {
                "entity_relations": result.get("entity_relation_dict", []),
                "event_entities": result.get("event_entity_relation_dict", []),
                "event_relations": result.get("event_relation_dict", []),
            }

    item["kg"] = kg
    return item


def record_result(
    result: dict[str, Any],
    input_source: str | Path,
    output_dir: str | Path | None = None,
    suffix: str = "_extracted",
) -> Path:
    """Record extraction result to a JSON file.

    Args:
        result: Extraction result to save
        input_source: Original input file path or identifier
        output_dir: Output directory (default: same as input file)
        suffix: Suffix to add to output filename (default: "_extracted")

    Returns:
        Path to the saved file

    Examples:
        >>> record_result(result, "data/input.json")
        # Saves to: data/input_extracted.json

        >>> record_result(result, "input.json", output_dir="output/")
        # Saves to: output/input_extracted.json
    """
    input_path = Path(input_source)

    # Determine output directory
    if output_dir is None:
        # Save in same directory as input
        out_dir = input_path.parent
    else:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

    # Build output filename
    stem = input_path.stem  # filename without extension
    output_path = out_dir / f"{stem}{suffix}.json"

    # Save result
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    return output_path


def record_batch_results(
    results: list[dict[str, Any]],
    input_source: str | Path,
    input_data: list[dict[str, Any]] | None = None,
    output_dir: str | Path | None = None,
    extraction_type: str = "kg",
) -> Path:
    """Record batch extraction results to a JSON file.

    Args:
        results: List of extraction results
        input_source: Original input file path
        input_data: Original input data (to include text field)
        output_dir: Output directory (default: same as input file)
        extraction_type: Extraction type for filename (default: "kg")

    Returns:
        Path to the saved file

    Examples:
        >>> record_batch_results(results, "data/batch.json", extraction_type="event")
        # Saves to: data/batch_event_kg.json
        >>> record_batch_results(results, "data/batch.json", extraction_type="triples")
        # Saves to: data/batch_triples_kg.json
    """
    input_path = Path(input_source)

    # Determine output directory
    if output_dir is None:
        out_dir = input_path.parent
    else:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

    # Build output filename: original_name_type_kg.json
    stem = input_path.stem
    output_path = out_dir / f"{stem}_{extraction_type}_kg.json"

    # Build formatted results
    formatted_results = []
    for i, result in enumerate(results):
        item = {"index": i}

        # Add text field from original data if available
        if input_data and i < len(input_data):
            original_item = input_data[i]
            if isinstance(original_item, dict):
                # Try common text fields
                text = (
                    original_item.get("text") or
                    original_item.get("content") or
                    original_item.get("description") or
                    original_item.get("body") or
                    str(original_item)
                )
                item["text"] = text
            elif isinstance(original_item, str):
                item["text"] = original_item

        # Format KG based on result type
        kg = []
        if "error" in result:
            # Record error information instead of silently leaving kg empty
            item["error"] = result["error"]
        else:
            # Extract triples/relations - convert to tagged format
            if "relations" in result:
                for rel in result["relations"]:
                    try:
                        if isinstance(rel, (list, tuple)) and len(rel) == 3:
                            s, r, o = rel
                            # Validate triple values
                            if not (is_valid_triple_value(s) and is_valid_triple_value(r) and is_valid_triple_value(o)):
                                continue
                            # Clean values
                            s = clean_value(s)
                            r = clean_value(r)
                            o = clean_value(o)
                            kg.append(f"<subj> {s} <obj> {o} <rel> {r}")
                    except (ValueError, TypeError) as e:
                        # Skip malformed triples
                        continue
            elif "triples" in result:
                for rel in result["triples"]:
                    try:
                        if isinstance(rel, (list, tuple)) and len(rel) == 3:
                            s, r, o = rel
                            # Validate triple values
                            if not (is_valid_triple_value(s) and is_valid_triple_value(r) and is_valid_triple_value(o)):
                                continue
                            # Clean values
                            s = clean_value(s)
                            r = clean_value(r)
                            o = clean_value(o)
                            kg.append(f"<subj> {s} <obj> {o} <rel> {r}")
                    except (ValueError, TypeError) as e:
                        continue
            elif "relation_triples" in result:
                for rel in result["relation_triples"]:
                    try:
                        if isinstance(rel, (list, tuple)) and len(rel) == 3:
                            s, r, o = rel
                            # Validate triple values
                            if not (is_valid_triple_value(s) and is_valid_triple_value(r) and is_valid_triple_value(o)):
                                continue
                            # Clean values
                            s = clean_value(s)
                            r = clean_value(r)
                            o = clean_value(o)
                            kg.append(f"<subj> {s} <obj> {o} <rel> {r}")
                    except (ValueError, TypeError) as e:
                        continue
            # For temporal quadruples - keep as strings
            elif "quadruples" in result:
                kg = result["quadruples"]
            # For hyper-relations - keep as strings
            elif "hyper_relations" in result:
                kg = result["hyper_relations"]
            # For AutoSchemaKG events - keep full structure
            elif "entity_relation_dict" in result or "event_entity_relation_dict" in result:
                kg = {
                    "entity_relations": result.get("entity_relation_dict", []),
                    "event_entities": result.get("event_entity_relation_dict", []),
                    "event_relations": result.get("event_relation_dict", []),
                }

        item["kg"] = kg
        formatted_results.append(item)

    # Save result
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(formatted_results, f, indent=2, ensure_ascii=False)

    return output_path


def record_with_metadata(
    result: dict[str, Any],
    input_source: str | Path,
    extraction_type: str,
    metadata: dict[str, Any] | None = None,
    output_dir: str | Path | None = None,
) -> Path:
    """Record extraction result with metadata.

    Args:
        result: Extraction result
        input_source: Original input file path
        extraction_type: Type of extraction (triples, temporal, hyper)
        metadata: Additional metadata to include
        output_dir: Output directory (default: same as input file)

    Returns:
        Path to the saved file
    """
    input_path = Path(input_source)

    # Build full result with metadata
    full_result = {
        "source": str(input_path),
        "extraction_type": extraction_type,
        "timestamp": datetime.now().isoformat(),
        "result": result,
    }

    if metadata:
        full_result["metadata"] = metadata

    # Determine output directory
    if output_dir is None:
        out_dir = input_path.parent
    else:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

    # Build output filename with type
    stem = input_path.stem
    output_path = out_dir / f"{stem}_{extraction_type}.json"

    # Save result
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(full_result, f, indent=2, ensure_ascii=False)

    return output_path
