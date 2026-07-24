"""Core validators for knowledge graphs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from jsonschema import validate, ValidationError
    JSONSCHEMA_AVAILABLE = True
except ImportError:
    JSONSCHEMA_AVAILABLE = False


# Load schemas once at module level
_SCHEMAS = {}

def _load_schema(schema_name: str) -> dict:
    """Load a JSON schema file."""
    if schema_name not in _SCHEMAS:
        schema_dir = Path(__file__).parent.parent / "schemas"
        schema_path = schema_dir / f"{schema_name}.schema.json"
        with open(schema_path, encoding="utf-8") as f:
            _SCHEMAS[schema_name] = json.load(f)
    return _SCHEMAS[schema_name]


def validate_triple_graph(result: Any) -> dict[str, Any]:
    """Validate a relation triple graph structure.

    Expected format:
    {
        "entities": ["entity1", "entity2"],
        "relations": [
            ["subject", "relation", "object"],
            ["subject", "relation", "object"]
        ]
    }
    """
    errors = []

    if not isinstance(result, dict):
        return {"valid": False, "errors": ["Result must be a dictionary"]}

    # Try JSON Schema validation first (if available)
    if JSONSCHEMA_AVAILABLE:
        try:
            schema = _load_schema("relation_triples")
            validate(instance=result, schema=schema)
        except ValidationError as e:
            errors.append(f"Schema validation failed: {e.message}")
        except FileNotFoundError:
            # Schema file not found, fall back to manual validation
            pass

    # Manual validation (always run as additional check)
    # Check entities
    entities = result.get("entities")
    if entities is not None and not isinstance(entities, list):
        errors.append("'entities' field must be a list")

    # Check relations
    relations = result.get("relations")
    if not isinstance(relations, list):
        errors.append("'relations' field must be a list")
        return {"valid": False, "errors": errors}

    for i, rel in enumerate(relations):
        if not isinstance(rel, list):
            errors.append(f"Relation {i} must be a list")
            continue
        if len(rel) != 3:
            errors.append(f"Relation {i} must have exactly 3 elements [subject, relation, object]")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "relation_count": len(relations),
    }


def validate_temporal_quadruples(result: Any) -> dict[str, Any]:
    """Validate temporal quadruples structure.

    Expected format:
    {
        "quadruples": [
            "<subj> Alice <obj> Acme <rel> joined <time> 2020"
        ]
    }
    """
    errors = []

    if not isinstance(result, dict):
        return {"valid": False, "errors": ["Result must be a dictionary"]}

    # Try JSON Schema validation first (if available)
    if JSONSCHEMA_AVAILABLE:
        try:
            schema = _load_schema("temporal_quadruples")
            validate(instance=result, schema=schema)
        except ValidationError as e:
            errors.append(f"Schema validation failed: {e.message}")
        except FileNotFoundError:
            pass

    # Manual validation
    quadruples = result.get("quadruples")
    if not isinstance(quadruples, list):
        errors.append("'quadruples' field must be a list")
        return {"valid": False, "errors": errors}

    for i, quad in enumerate(quadruples):
        if not isinstance(quad, str):
            errors.append(f"Quadruple {i} must be a string")
            continue

        # Check for required tags
        required_tags = ["<subj>", "<obj>", "<rel>", "<time>"]
        for tag in required_tags:
            if tag not in quad:
                errors.append(f"Quadruple {i} missing required tag '{tag}'")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "quadruple_count": len(quadruples),
    }


def validate_hyper_relations(result: Any) -> dict[str, Any]:
    """Validate hyper-relation graph structure.

    Expected format:
    {
        "hyper_relations": [
            "<subj> Beyoncé <obj> Album <rel> Released <time> 2003 <location> New York"
        ]
    }
    """
    errors = []

    if not isinstance(result, dict):
        return {"valid": False, "errors": ["Result must be a dictionary"]}

    # Try JSON Schema validation first (if available)
    if JSONSCHEMA_AVAILABLE:
        try:
            schema = _load_schema("hyper_relations")
            validate(instance=result, schema=schema)
        except ValidationError as e:
            errors.append(f"Schema validation failed: {e.message}")
        except FileNotFoundError:
            pass

    # Manual validation
    hyper_relations = result.get("hyper_relations")
    if not isinstance(hyper_relations, list):
        errors.append("'hyper_relations' field must be a list")
        return {"valid": False, "errors": errors}

    for i, hr in enumerate(hyper_relations):
        if not isinstance(hr, str):
            errors.append(f"Hyper-relation {i} must be a string")
            continue

        # Check for required tags
        required_tags = ["<subj>", "<obj>", "<rel>"]
        for tag in required_tags:
            if tag not in hr:
                errors.append(f"Hyper-relation {i} missing required tag '{tag}'")

        # Check for forbidden placeholder attribute names
        forbidden = ["<attribute1>", "<attribute2>", "<attr1>", "<property1>"]
        for forbidden_tag in forbidden:
            if forbidden_tag in hr:
                errors.append(f"Hyper-relation {i} uses forbidden placeholder '{forbidden_tag}'")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "hyper_relation_count": len(hyper_relations),
    }


def validate_event_graph(result: Any) -> dict[str, Any]:
    """Validate AutoSchemaKG event graph structure.

    Expected format:
    {
        "entity_relation_dict": [
            {"Head": "...", "Relation": "...", "Tail": "..."}
        ],
        "event_entity_relation_dict": [
            {"Event": "...", "Entity": ["..."]}
        ],
        "event_relation_dict": [
            {"Head": "...", "Relation": "...", "Tail": "..."}
        ]
    }
    """
    errors = []

    if not isinstance(result, dict):
        return {"valid": False, "errors": ["Result must be a dictionary"]}

    # Try JSON Schema validation first (if available)
    if JSONSCHEMA_AVAILABLE:
        try:
            schema = _load_schema("autoschema_kg")
            validate(instance=result, schema=schema)
        except ValidationError as e:
            errors.append(f"Schema validation failed: {e.message}")
        except FileNotFoundError:
            pass

    # Manual validation
    # Check entity_relation_dict
    entity_relations = result.get("entity_relation_dict")
    if not isinstance(entity_relations, list):
        errors.append("'entity_relation_dict' field must be a list")
    else:
        for i, rel in enumerate(entity_relations):
            if not isinstance(rel, dict):
                errors.append(f"Entity relation {i} must be a dict")
                continue
            if "Head" not in rel or "Relation" not in rel or "Tail" not in rel:
                errors.append(f"Entity relation {i} missing required fields (Head, Relation, Tail)")

    # Check event_entity_relation_dict
    event_entities = result.get("event_entity_relation_dict")
    if not isinstance(event_entities, list):
        errors.append("'event_entity_relation_dict' field must be a list")
    else:
        for i, ev in enumerate(event_entities):
            if not isinstance(ev, dict):
                errors.append(f"Event-entity {i} must be a dict")
                continue
            if "Event" not in ev or "Entity" not in ev:
                errors.append(f"Event-entity {i} missing required fields (Event, Entity)")
            elif not isinstance(ev.get("Entity"), list):
                errors.append(f"Event-entity {i} 'Entity' field must be a list")

    # Check event_relation_dict
    event_relations = result.get("event_relation_dict")
    if not isinstance(event_relations, list):
        errors.append("'event_relation_dict' field must be a list")
    else:
        for i, rel in enumerate(event_relations):
            if not isinstance(rel, dict):
                errors.append(f"Event relation {i} must be a dict")
                continue
            if "Head" not in rel or "Relation" not in rel or "Tail" not in rel:
                errors.append(f"Event relation {i} missing required fields (Head, Relation, Tail)")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "entity_relation_count": len(entity_relations) if isinstance(entity_relations, list) else 0,
        "event_entity_count": len(event_entities) if isinstance(event_entities, list) else 0,
        "event_relation_count": len(event_relations) if isinstance(event_relations, list) else 0,
    }
