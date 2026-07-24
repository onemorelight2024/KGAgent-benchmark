"""Validation utilities for extraction results (simplified from record-agent)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def validate_triples(result: dict[str, Any]) -> dict[str, Any]:
    """Validate relation triples format.

    Expected format:
    {
        "entities": ["entity1", "entity2", ...],
        "relations": [["subj", "rel", "obj"], ...]
    }
    or
    {
        "triples": [["subj", "rel", "obj"], ...]
    }
    """
    if "triples" in result:
        triples = result["triples"]
        if not isinstance(triples, list):
            raise ValueError("triples must be a list")
        for triple in triples:
            if not isinstance(triple, (list, dict)):
                raise ValueError(f"Invalid triple format: {triple}")
            if isinstance(triple, list) and len(triple) != 3:
                raise ValueError(f"Triple must have 3 elements: {triple}")
        logger.info(f"Validated {len(triples)} triples")
        return result

    if "relations" in result:
        relations = result["relations"]
        if not isinstance(relations, list):
            raise ValueError("relations must be a list")
        logger.info(f"Validated {len(relations)} relations")
        return result

    raise ValueError("Result must contain 'triples' or 'relations' field")


def validate_temporal(result: dict[str, Any]) -> dict[str, Any]:
    """Validate temporal quadruples format.

    Expected format:
    {
        "quadruples": ["<subj> X <obj> Y <rel> Z <time> T", ...]
    }
    """
    if "quadruples" not in result:
        raise ValueError("Result must contain 'quadruples' field")

    quadruples = result["quadruples"]
    if not isinstance(quadruples, list):
        raise ValueError("quadruples must be a list")

    for quad in quadruples:
        if not isinstance(quad, str):
            raise ValueError(f"Quadruple must be a string: {quad}")
        # Basic check for tagged format
        if "<subj>" not in quad or "<obj>" not in quad or "<rel>" not in quad:
            logger.warning(f"Quadruple missing required tags: {quad}")

    logger.info(f"Validated {len(quadruples)} temporal quadruples")
    return result


def validate_hyper(result: dict[str, Any]) -> dict[str, Any]:
    """Validate hyper-relations format.

    Expected format:
    {
        "hyper_relations": ["<subj> X <obj> Y <rel> Z <attr> V", ...]
    }
    """
    if "hyper_relations" not in result:
        raise ValueError("Result must contain 'hyper_relations' field")

    hyper_relations = result["hyper_relations"]
    if not isinstance(hyper_relations, list):
        raise ValueError("hyper_relations must be a list")

    for hr in hyper_relations:
        if not isinstance(hr, str):
            raise ValueError(f"Hyper-relation must be a string: {hr}")
        # Basic check for tagged format
        if "<subj>" not in hr or "<obj>" not in hr or "<rel>" not in hr:
            logger.warning(f"Hyper-relation missing required tags: {hr}")

    logger.info(f"Validated {len(hyper_relations)} hyper-relations")
    return result


def validate_result(result: dict[str, Any], extraction_type: str) -> dict[str, Any]:
    """Dispatch to appropriate validator.

    Args:
        result: Extraction result to validate
        extraction_type: Type of extraction (triples, temporal, hyper)

    Returns:
        Validated result

    Raises:
        ValueError: If validation fails
    """
    validators = {
        "triples": validate_triples,
        "temporal": validate_temporal,
        "hyper": validate_hyper,
    }

    validator = validators.get(extraction_type)
    if validator is None:
        raise ValueError(f"Unknown extraction type: {extraction_type}")

    return validator(result)
