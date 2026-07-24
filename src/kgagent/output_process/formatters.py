"""Output formatters for different extraction types."""

from __future__ import annotations

from typing import Any


def format_triple_result(result: dict[str, Any]) -> dict[str, Any]:
    """Format relation triples extraction result.

    Args:
        result: Raw extraction result

    Returns:
        Formatted result with standardized structure
    """
    formatted = {
        "type": "triples",
        "entities": result.get("entities", []),
        "relations": [],
    }

    # Handle different relation formats
    relations = result.get("relations") or result.get("triples") or result.get("relation_triples") or []

    for rel in relations:
        if isinstance(rel, (list, tuple)) and len(rel) == 3:
            formatted["relations"].append({
                "subject": rel[0],
                "relation": rel[1],
                "object": rel[2],
            })
        elif isinstance(rel, dict):
            formatted["relations"].append(rel)
        elif isinstance(rel, str):
            # Tagged format: "<subj> X <obj> Y <rel> Z"
            formatted["relations"].append({"raw": rel})

    return formatted


def format_temporal_result(result: dict[str, Any]) -> dict[str, Any]:
    """Format temporal quadruples extraction result.

    Args:
        result: Raw extraction result

    Returns:
        Formatted result with standardized structure
    """
    formatted = {
        "type": "temporal",
        "quadruples": [],
    }

    quadruples = result.get("quadruples", [])

    for quad in quadruples:
        if isinstance(quad, str):
            # Parse tagged format: "<subj> X <obj> Y <rel> Z <time> T"
            parsed = _parse_tagged_quadruple(quad)
            formatted["quadruples"].append(parsed)
        elif isinstance(quad, dict):
            formatted["quadruples"].append(quad)
        elif isinstance(quad, (list, tuple)) and len(quad) == 4:
            formatted["quadruples"].append({
                "subject": quad[0],
                "relation": quad[1],
                "object": quad[2],
                "time": quad[3],
            })

    return formatted


def format_hyper_result(result: dict[str, Any]) -> dict[str, Any]:
    """Format hyper-relations extraction result.

    Args:
        result: Raw extraction result

    Returns:
        Formatted result with standardized structure
    """
    formatted = {
        "type": "hyper",
        "hyper_relations": [],
    }

    hyper_relations = result.get("hyper_relations", [])

    for hyper in hyper_relations:
        if isinstance(hyper, str):
            # Parse tagged format: "<subj> X <obj> Y <rel> Z <attr1> V1 ..."
            parsed = _parse_tagged_hyper(hyper)
            formatted["hyper_relations"].append(parsed)
        elif isinstance(hyper, dict):
            formatted["hyper_relations"].append(hyper)

    return formatted


def format_event_result(result: dict[str, Any]) -> dict[str, Any]:
    """Format event KG (AutoSchemaKG) extraction result.

    Args:
        result: Raw extraction result

    Returns:
        Formatted result with standardized structure
    """
    formatted = {
        "type": "event",
        "entity_relations": result.get("entity_relation_dict", []),
        "event_entities": result.get("event_entity_relation_dict", []),
        "event_relations": result.get("event_relation_dict", []),
    }

    return formatted


def _parse_tagged_quadruple(tagged: str) -> dict[str, Any]:
    """Parse tagged quadruple format.

    Args:
        tagged: Tagged string like "<subj> X <obj> Y <rel> Z <time> T"

    Returns:
        Parsed dict with subject, object, relation, time
    """
    parts = {}
    current_tag = None
    current_value = []

    tokens = tagged.split()
    for token in tokens:
        if token.startswith("<") and token.endswith(">"):
            if current_tag and current_value:
                parts[current_tag] = " ".join(current_value)
            current_tag = token.strip("<>")
            current_value = []
        else:
            current_value.append(token)

    if current_tag and current_value:
        parts[current_tag] = " ".join(current_value)

    return {
        "subject": parts.get("subj", ""),
        "object": parts.get("obj", ""),
        "relation": parts.get("rel", ""),
        "time": parts.get("time", ""),
        "raw": tagged,
    }


def _parse_tagged_hyper(tagged: str) -> dict[str, Any]:
    """Parse tagged hyper-relation format.

    Args:
        tagged: Tagged string like "<subj> X <obj> Y <rel> Z <attr1> V1 ..."

    Returns:
        Parsed dict with subject, object, relation, and attributes
    """
    parts = {}
    current_tag = None
    current_value = []

    tokens = tagged.split()
    for token in tokens:
        if token.startswith("<") and token.endswith(">"):
            if current_tag and current_value:
                parts[current_tag] = " ".join(current_value)
            current_tag = token.strip("<>")
            current_value = []
        else:
            current_value.append(token)

    if current_tag and current_value:
        parts[current_tag] = " ".join(current_value)

    # Separate core triple from attributes
    result = {
        "subject": parts.pop("subj", ""),
        "object": parts.pop("obj", ""),
        "relation": parts.pop("rel", ""),
        "attributes": parts,  # Remaining are attributes
        "raw": tagged,
    }

    return result
