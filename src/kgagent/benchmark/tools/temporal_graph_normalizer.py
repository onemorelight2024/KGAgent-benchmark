"""Temporal graph normalizer for TKG inputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kgagent.benchmark.types import Entity, NormalizedTKG, TKGStats, TemporalFact, TimeInfo


def load_and_normalize_tkg(raw_input: str, workspace: Path) -> dict[str, Any]:
    """Load TKG from path or inline JSON and normalize to standard format.

    Args:
        raw_input: File path or inline JSON string
        workspace: Working directory for resolving relative paths

    Returns:
        Dict with 'graph' (NormalizedTKG) and 'stats' (TKGStats)
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

    normalized = normalize_tkg_obj(obj, source_path)
    stats = compute_tkg_stats(normalized)

    return {"graph": normalized, "stats": stats}


def normalize_tkg_obj(obj: Any, source_path: str = "unknown") -> NormalizedTKG:
    """Normalize various TKG formats to standard NormalizedTKG.

    Supported formats:
    - {"quadruples": [...]}
    - {"events": [...]}
    - {"facts": [...]}
    - Temporal quadruple extraction output format
    """
    if not isinstance(obj, dict):
        raise ValueError(f"Expected dict, got {type(obj).__name__}")

    # Detect format
    if "quadruples" in obj:
        return _normalize_quadruples_format(obj, source_path)
    elif "events" in obj or "facts" in obj:
        return _normalize_events_facts_format(obj, source_path)
    elif "temporal_facts" in obj:
        # Already in normalized format
        return _normalize_temporal_facts_format(obj, source_path)
    else:
        raise ValueError(f"Unrecognized TKG format. Keys: {list(obj.keys())}")


def _normalize_quadruples_format(obj: dict, source_path: str) -> NormalizedTKG:
    """Normalize quadruples format."""
    quadruples_raw = obj.get("quadruples", [])

    entity_set: set[str] = set()
    temporal_facts: list[TemporalFact] = []

    for idx, quad in enumerate(quadruples_raw):
        if isinstance(quad, dict):
            subject = str(quad.get("subject", quad.get("s", "")))
            relation = str(quad.get("relation", quad.get("r", quad.get("predicate", ""))))
            obj_val = str(quad.get("object", quad.get("o", "")))
            time_raw = quad.get("time", quad.get("t", {}))

            entity_set.add(subject)
            entity_set.add(obj_val)

            # Normalize time
            time_info = _normalize_time_info(time_raw)

            temporal_facts.append(TemporalFact(
                id=quad.get("id", f"Q{idx}"),
                subject=subject,
                relation=relation,
                object=obj_val,
                time=time_info,
                attributes=quad.get("attributes", {}),
                evidence=quad.get("evidence", []),
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

    return NormalizedTKG(
        graph_id=obj.get("graph_id", "tkg_001"),
        graph_type="TKG",
        entities=entities,
        temporal_facts=temporal_facts,
        metadata={
            "source_path": source_path,
            "normalizer": "quadruples_v1",
        },
    )


def _normalize_events_facts_format(obj: dict, source_path: str) -> NormalizedTKG:
    """Normalize events/facts format."""
    events_raw = obj.get("events", obj.get("facts", []))

    entity_set: set[str] = set()
    temporal_facts: list[TemporalFact] = []

    for idx, event in enumerate(events_raw):
        if isinstance(event, dict):
            subject = str(event.get("subject", event.get("s", event.get("entity", ""))))
            relation = str(event.get("relation", event.get("r", event.get("predicate", event.get("type", "")))))
            obj_val = str(event.get("object", event.get("o", event.get("value", ""))))
            time_raw = event.get("time", event.get("timestamp", event.get("when", {})))

            entity_set.add(subject)
            if obj_val:
                entity_set.add(obj_val)

            # Normalize time
            time_info = _normalize_time_info(time_raw)

            temporal_facts.append(TemporalFact(
                id=event.get("id", f"Q{idx}"),
                subject=subject,
                relation=relation,
                object=obj_val,
                time=time_info,
                attributes=event.get("attributes", {}),
                evidence=event.get("evidence", []),
            ))

    entities: list[Entity] = []
    for e_name in sorted(entity_set):
        if e_name:  # Skip empty strings
            entities.append(Entity(
                id=e_name,
                name=e_name,
                type="Entity",
                aliases=[],
                attributes={},
            ))

    return NormalizedTKG(
        graph_id=obj.get("graph_id", "tkg_001"),
        graph_type="TKG",
        entities=entities,
        temporal_facts=temporal_facts,
        metadata={
            "source_path": source_path,
            "normalizer": "events_facts_v1",
        },
    )


def _normalize_temporal_facts_format(obj: dict, source_path: str) -> NormalizedTKG:
    """Normalize already-normalized temporal_facts format."""
    entities_raw = obj.get("entities", [])
    temporal_facts_raw = obj.get("temporal_facts", [])

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

    temporal_facts: list[TemporalFact] = []
    for tf in temporal_facts_raw:
        if isinstance(tf, dict):
            temporal_facts.append(TemporalFact(
                id=tf.get("id", f"Q{len(temporal_facts)}"),
                subject=tf.get("subject", ""),
                relation=tf.get("relation", ""),
                object=tf.get("object", ""),
                time=tf.get("time", TimeInfo(type="point", value="")),
                attributes=tf.get("attributes", {}),
                evidence=tf.get("evidence", []),
            ))

    return NormalizedTKG(
        graph_id=obj.get("graph_id", "tkg_001"),
        graph_type="TKG",
        entities=entities,
        temporal_facts=temporal_facts,
        metadata={
            "source_path": source_path,
            "normalizer": "temporal_facts_v1",
        },
    )


def _normalize_time_info(time_raw: Any) -> TimeInfo:
    """Normalize time information to TimeInfo format."""
    if isinstance(time_raw, dict):
        time_type = time_raw.get("type", "point")
        if time_type == "interval":
            return TimeInfo(
                type="interval",
                value=time_raw.get("value", ""),
                start=time_raw.get("start"),
                end=time_raw.get("end"),
            )
        else:
            return TimeInfo(
                type="point",
                value=time_raw.get("value", time_raw.get("point", "")),
                start=None,
                end=None,
            )
    elif isinstance(time_raw, str):
        # Simple string time
        return TimeInfo(
            type="point",
            value=time_raw,
            start=None,
            end=None,
        )
    else:
        return TimeInfo(
            type="point",
            value="",
            start=None,
            end=None,
        )


def compute_tkg_stats(tkg: NormalizedTKG) -> TKGStats:
    """Compute statistics for normalized TKG."""
    time_type_distribution: dict[str, int] = {}
    for fact in tkg["temporal_facts"]:
        time_type = fact["time"]["type"]
        time_type_distribution[time_type] = time_type_distribution.get(time_type, 0) + 1

    return TKGStats(
        entity_count=len(tkg["entities"]),
        temporal_fact_count=len(tkg["temporal_facts"]),
        time_type_distribution=time_type_distribution,
    )
