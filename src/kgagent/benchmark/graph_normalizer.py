"""Graph normalizer for KG inputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kgagent.benchmark.types import Entity, GraphStats, NormalizedKG, Relation


def load_and_normalize_kg(raw_input: str, workspace: Path) -> dict[str, Any]:
    """Load KG from path or inline JSON and normalize to standard format.

    Args:
        raw_input: File path or inline JSON string
        workspace: Working directory for resolving relative paths

    Returns:
        Dict with 'graph' (NormalizedKG) and 'stats' (GraphStats)
    """
    # Try to parse as path first
    try:
        input_path = Path(raw_input)
        if not input_path.is_absolute():
            input_path = workspace / input_path
        if input_path.exists():
            with open(input_path, encoding="utf-8") as f:
                obj = json.load(f)
            source_path = str(input_path)
        else:
            # Try as inline JSON
            obj = json.loads(raw_input)
            source_path = "inline"
    except (json.JSONDecodeError, OSError):
        # Fallback: try as inline JSON
        obj = json.loads(raw_input)
        source_path = "inline"

    normalized = normalize_kg_obj(obj, source_path)
    stats = compute_kg_stats(normalized)

    return {"graph": normalized, "stats": stats}


def normalize_kg_obj(obj: Any, source_path: str = "unknown") -> NormalizedKG:
    """Normalize various KG formats to standard NormalizedKG.

    Supported formats:
    - {"entities": [...], "relations": [...]}
    - {"nodes": [...], "edges": [...]}
    - {"triples": [[s, r, o], ...]}
    - {"triples": [{"subject": ..., "relation": ..., "object": ...}]}
    - Entity-relation extraction output format
    """
    if not isinstance(obj, dict):
        raise ValueError(f"Expected dict, got {type(obj).__name__}")

    # Detect format
    if "entities" in obj and "relations" in obj:
        return _normalize_entities_relations_format(obj, source_path)
    elif "nodes" in obj and "edges" in obj:
        return _normalize_nodes_edges_format(obj, source_path)
    elif "triples" in obj:
        return _normalize_triples_format(obj, source_path)
    else:
        raise ValueError(f"Unrecognized KG format. Keys: {list(obj.keys())}")


def _normalize_entities_relations_format(obj: dict, source_path: str) -> NormalizedKG:
    """Normalize entities-relations format."""
    entities_raw = obj.get("entities", [])
    relations_raw = obj.get("relations", [])

    entities: list[Entity] = []
    for e in entities_raw:
        if isinstance(e, dict):
            entities.append(Entity(
                id=e.get("id", e.get("name", "")),
                name=e.get("name", e.get("id", "")),
                type=e.get("type", "Entity"),
                aliases=e.get("aliases", []),
                attributes=e.get("attributes", {}),
            ))

    relations: list[Relation] = []
    for r in relations_raw:
        if isinstance(r, dict):
            relations.append(Relation(
                id=r.get("id", f"R{len(relations)}"),
                source=r.get("source", r.get("subject", "")),
                relation=r.get("relation", r.get("predicate", "")),
                target=r.get("target", r.get("object", "")),
                attributes=r.get("attributes", {}),
                evidence=r.get("evidence", []),
            ))

    return NormalizedKG(
        graph_id=obj.get("graph_id", "kg_001"),
        graph_type="KG",
        entities=entities,
        relations=relations,
        metadata={
            "source_path": source_path,
            "normalizer": "entities_relations_v1",
        },
    )


def _normalize_nodes_edges_format(obj: dict, source_path: str) -> NormalizedKG:
    """Normalize nodes-edges format."""
    nodes_raw = obj.get("nodes", [])
    edges_raw = obj.get("edges", [])

    entities: list[Entity] = []
    for n in nodes_raw:
        if isinstance(n, dict):
            entities.append(Entity(
                id=n.get("id", n.get("name", "")),
                name=n.get("name", n.get("label", n.get("id", ""))),
                type=n.get("type", n.get("label", "Entity")),
                aliases=n.get("aliases", []),
                attributes=n.get("properties", n.get("attributes", {})),
            ))

    relations: list[Relation] = []
    for e in edges_raw:
        if isinstance(e, dict):
            relations.append(Relation(
                id=e.get("id", f"R{len(relations)}"),
                source=e.get("source", e.get("from", e.get("subject", ""))),
                relation=e.get("relation", e.get("type", e.get("label", ""))),
                target=e.get("target", e.get("to", e.get("object", ""))),
                attributes=e.get("properties", e.get("attributes", {})),
                evidence=e.get("evidence", []),
            ))

    return NormalizedKG(
        graph_id=obj.get("graph_id", "kg_001"),
        graph_type="KG",
        entities=entities,
        relations=relations,
        metadata={
            "source_path": source_path,
            "normalizer": "nodes_edges_v1",
        },
    )


def _normalize_triples_format(obj: dict, source_path: str) -> NormalizedKG:
    """Normalize triples format (list of lists or list of dicts)."""
    triples_raw = obj.get("triples", [])

    # Collect unique entities
    entity_set: set[str] = set()
    relations: list[Relation] = []

    for idx, triple in enumerate(triples_raw):
        if isinstance(triple, (list, tuple)) and len(triple) >= 3:
            # [subject, relation, object]
            s, r, o = str(triple[0]), str(triple[1]), str(triple[2])
            entity_set.add(s)
            entity_set.add(o)
            relations.append(Relation(
                id=f"R{idx}",
                source=s,
                relation=r,
                target=o,
                attributes={},
                evidence=[],
            ))
        elif isinstance(triple, dict):
            # {"subject": ..., "relation": ..., "object": ...}
            s = str(triple.get("subject", triple.get("s", "")))
            r = str(triple.get("relation", triple.get("predicate", triple.get("r", ""))))
            o = str(triple.get("object", triple.get("o", "")))
            entity_set.add(s)
            entity_set.add(o)
            relations.append(Relation(
                id=triple.get("id", f"R{idx}"),
                source=s,
                relation=r,
                target=o,
                attributes=triple.get("attributes", {}),
                evidence=triple.get("evidence", []),
            ))

    entities: list[Entity] = []
    for e_name in sorted(entity_set):
        entities.append(Entity(
            id=e_name,
            name=e_name,
            type="Entity",
            aliases=[],
            attributes={},
        ))

    return NormalizedKG(
        graph_id=obj.get("graph_id", "kg_001"),
        graph_type="KG",
        entities=entities,
        relations=relations,
        metadata={
            "source_path": source_path,
            "normalizer": "triples_v1",
        },
    )


def compute_kg_stats(kg: NormalizedKG) -> GraphStats:
    """Compute statistics for normalized KG."""
    return GraphStats(
        entity_count=len(kg["entities"]),
        relation_count=len(kg["relations"]),
        format_detected=kg["metadata"].get("normalizer", "unknown"),
    )
