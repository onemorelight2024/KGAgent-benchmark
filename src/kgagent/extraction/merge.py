"""Merge and disambiguate KG results from multiple chunks."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


def _parse_kg_item(kg_item: str) -> dict[str, str] | None:
    """Parse a KG item in tagged format.

    Args:
        kg_item: KG string like "<subj> X <obj> Y <rel> Z <time> T"

    Returns:
        Dict with parsed fields or None if invalid
    """
    parts = {}
    current_tag = None
    current_value = []

    tokens = kg_item.split()
    for token in tokens:
        if token.startswith("<") and token.endswith(">"):
            # Save previous tag
            if current_tag and current_value:
                parts[current_tag] = " ".join(current_value)
            # Start new tag
            current_tag = token.strip("<>")
            current_value = []
        elif current_tag:
            current_value.append(token)

    # Save last tag
    if current_tag and current_value:
        parts[current_tag] = " ".join(current_value)

    return parts if parts else None


def _reconstruct_kg_item(parts: dict[str, str]) -> str:
    """Reconstruct KG item from parsed parts.

    Args:
        parts: Dict with parsed fields

    Returns:
        KG string in tagged format
    """
    result = []

    # Standard order: subj, obj, rel, then others
    for tag in ["subj", "obj", "rel"]:
        if tag in parts:
            result.append(f"<{tag}> {parts[tag]}")

    # Add remaining tags
    for tag, value in parts.items():
        if tag not in ["subj", "obj", "rel"]:
            result.append(f"<{tag}> {value}")

    return " ".join(result)


def _string_similarity(s1: str, s2: str) -> float:
    """Calculate string similarity using SequenceMatcher.

    Args:
        s1: First string
        s2: Second string

    Returns:
        Similarity score between 0 and 1
    """
    return SequenceMatcher(None, s1.lower(), s2.lower()).ratio()


def _extract_entities_and_relations(kg_items: list[str]) -> tuple[set[str], set[str]]:
    """Extract unique entities and relations from KG items.

    Args:
        kg_items: List of KG strings

    Returns:
        Tuple of (entities, relations)
    """
    entities = set()
    relations = set()

    for item in kg_items:
        parts = _parse_kg_item(item)
        if parts:
            if "subj" in parts:
                entities.add(parts["subj"])
            if "obj" in parts:
                entities.add(parts["obj"])
            if "rel" in parts:
                relations.add(parts["rel"])

    return entities, relations


def _find_similar_candidates(
    items: set[str],
    threshold: float = 0.85
) -> list[list[str]]:
    """Find groups of similar items based on string similarity.

    Args:
        items: Set of items to compare
        threshold: Similarity threshold (0-1)

    Returns:
        List of groups, each containing similar items
    """
    items_list = list(items)
    groups = []
    used = set()

    for i, item1 in enumerate(items_list):
        if item1 in used:
            continue

        group = [item1]
        used.add(item1)

        for j, item2 in enumerate(items_list[i+1:], start=i+1):
            if item2 in used:
                continue

            similarity = _string_similarity(item1, item2)
            if similarity >= threshold:
                group.append(item2)
                used.add(item2)

        # Only keep groups with multiple items
        if len(group) > 1:
            groups.append(group)

    return groups


async def _disambiguate_with_llm(
    candidates: list[str],
    item_type: str,
    llm_client: Any
) -> dict[str, str]:
    """Use LLM to disambiguate similar items.

    Args:
        candidates: List of similar items
        item_type: Type of items ("entity" or "relation")
        llm_client: LLM client (KGAgentSystem or similar)

    Returns:
        Mapping from original names to canonical names
    """
    prompt = f"""You are a knowledge graph expert. Given these similar {item_type} names, determine which ones refer to the same thing and provide a canonical name for each group.

{item_type.capitalize()} names:
{json.dumps(candidates, indent=2)}

Instructions:
1. Group {item_type}s that refer to the same concept
2. For each group, choose the most clear and concise canonical name
3. Return a JSON object mapping each original name to its canonical name

Example output format:
{{
  "original_name_1": "canonical_name",
  "original_name_2": "canonical_name",
  "original_name_3": "different_canonical_name"
}}

Return ONLY the JSON object, no explanation."""

    try:
        # Use extract_async with triples type to get JSON response
        response = await llm_client.extract_async(
            data=prompt,
            extraction_type="triples"
        )

        # Try to extract mapping from response
        if isinstance(response, dict):
            # Look for a mapping-like structure
            mapping = {}

            # Try direct mapping
            if all(isinstance(k, str) and isinstance(v, str) for k, v in response.items()):
                mapping = response

            # Try nested structure
            elif "mapping" in response:
                mapping = response["mapping"]
            elif "result" in response:
                mapping = response["result"]

            # Validate mapping
            if mapping and all(c in mapping for c in candidates):
                logger.info(f"LLM disambiguation: {len(mapping)} items mapped")
                return mapping

        # Fallback: no disambiguation
        logger.warning(f"Could not parse LLM response, using original names")
        return {c: c for c in candidates}

    except Exception as e:
        logger.error(f"LLM disambiguation failed: {e}")
        # Fallback: use first item as canonical
        canonical = candidates[0]
        return {c: canonical for c in candidates}


async def _disambiguate_items(
    items: set[str],
    item_type: str,
    llm_client: Any,
    similarity_threshold: float = 0.85
) -> dict[str, str]:
    """Disambiguate items using similarity + LLM.

    Args:
        items: Set of items to disambiguate
        item_type: Type of items ("entity" or "relation")
        llm_client: LLM client
        similarity_threshold: Similarity threshold for candidates

    Returns:
        Mapping from original names to canonical names
    """
    logger.info(f"Disambiguating {len(items)} {item_type}s...")

    # Find similar candidates
    candidate_groups = _find_similar_candidates(items, similarity_threshold)

    if not candidate_groups:
        logger.info(f"No similar {item_type}s found, skipping disambiguation")
        return {item: item for item in items}

    logger.info(f"Found {len(candidate_groups)} groups of similar {item_type}s")

    # Disambiguate each group with LLM
    mapping = {}

    for group in candidate_groups:
        logger.debug(f"Disambiguating group: {group}")
        group_mapping = await _disambiguate_with_llm(group, item_type, llm_client)
        mapping.update(group_mapping)

    # Add identity mapping for non-ambiguous items
    for item in items:
        if item not in mapping:
            mapping[item] = item

    # Count changes
    changes = sum(1 for k, v in mapping.items() if k != v)
    logger.info(f"Disambiguation complete: {changes} {item_type}s renamed")

    return mapping


def _apply_mapping(kg_items: list[str], entity_map: dict[str, str], relation_map: dict[str, str]) -> list[str]:
    """Apply entity and relation mappings to KG items.

    Args:
        kg_items: List of KG strings
        entity_map: Mapping from original to canonical entity names
        relation_map: Mapping from original to canonical relation names

    Returns:
        List of updated KG strings
    """
    updated_items = []

    for item in kg_items:
        parts = _parse_kg_item(item)
        if not parts:
            updated_items.append(item)
            continue

        # Apply entity mappings
        if "subj" in parts:
            parts["subj"] = entity_map.get(parts["subj"], parts["subj"])
        if "obj" in parts:
            parts["obj"] = entity_map.get(parts["obj"], parts["obj"])

        # Apply relation mapping
        if "rel" in parts:
            parts["rel"] = relation_map.get(parts["rel"], parts["rel"])

        updated_items.append(_reconstruct_kg_item(parts))

    return updated_items


async def merge_kg_chunks(
    kg_file: str | Path,
    extraction_type: str,
    llm_client: Any,
    output_file: str | Path | None = None,
) -> dict[str, Any]:
    """Merge KG results from multiple chunks into a single graph.

    For triples, temporal, and hyper types: disambiguate entities and relations, then merge.
    For event type: simple merge without disambiguation.

    Args:
        kg_file: Path to the chunk-level KG JSON file (e.g., *_triples_kg.json)
        extraction_type: Type of extraction (triples, temporal, hyper, event)
        llm_client: LLM client for disambiguation (KGAgentSystem instance)
        output_file: Output file path (default: *_kg_all.json)

    Returns:
        Result dict with merged KG and statistics
    """
    kg_file = Path(kg_file)

    if not kg_file.exists():
        raise FileNotFoundError(f"KG file not found: {kg_file}")

    # Read chunk-level results
    with open(kg_file, "r", encoding="utf-8") as f:
        chunks = json.load(f)

    logger.info(f"Merging {len(chunks)} chunks from {kg_file.name}")

    # Determine output file
    if output_file is None:
        # Replace _kg.json with _kg_all.json, handling various patterns
        name = kg_file.name
        if name.endswith("_kg.json"):
            output_file = kg_file.parent / name.replace("_kg.json", "_kg_all.json")
        elif "_kg" in name and name.endswith(".json"):
            # Handle cases like _kg_test.json -> _kg_test_all.json
            output_file = kg_file.parent / name.replace(".json", "_all.json")
        else:
            # Fallback: append _all before .json
            output_file = kg_file.parent / name.replace(".json", "_all.json")
    else:
        output_file = Path(output_file)

    # Handle event type differently (simple merge)
    if extraction_type == "event":
        logger.info("Event type: performing simple merge without disambiguation")

        merged_result = {
            "extraction_type": extraction_type,
            "source_file": str(kg_file),
            "chunks": len(chunks),
            "data": chunks,  # Keep all chunk data as-is
        }

        # Save merged result
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(merged_result, f, indent=2, ensure_ascii=False)

        logger.info(f"Merged event KG saved to {output_file}")

        return {
            "extraction_type": extraction_type,
            "output_file": str(output_file),
            "chunks": len(chunks),
            "disambiguation": False,
        }

    # For triples, temporal, hyper: extract all KG items
    all_kg_items = []
    for chunk in chunks:
        kg_items = chunk.get("kg", [])
        all_kg_items.extend(kg_items)

    logger.info(f"Total KG items before merge: {len(all_kg_items)}")

    # Extract entities and relations
    entities, relations = _extract_entities_and_relations(all_kg_items)
    logger.info(f"Found {len(entities)} unique entities, {len(relations)} unique relations")

    # Disambiguate entities and relations
    entity_map = await _disambiguate_items(entities, "entity", llm_client)
    relation_map = await _disambiguate_items(relations, "relation", llm_client)

    # Apply mappings
    merged_kg_items = _apply_mapping(all_kg_items, entity_map, relation_map)

    # Remove duplicates
    unique_kg_items = list(set(merged_kg_items))
    logger.info(f"Merged KG items: {len(unique_kg_items)} (removed {len(merged_kg_items) - len(unique_kg_items)} duplicates)")

    # Count final entities and relations
    final_entities, final_relations = _extract_entities_and_relations(unique_kg_items)

    # Build result
    merged_result = {
        "extraction_type": extraction_type,
        "source_file": str(kg_file),
        "chunks": len(chunks),
        "kg": unique_kg_items,
        "statistics": {
            "total_items": len(unique_kg_items),
            "entities": len(final_entities),
            "relations": len(final_relations),
            "entities_merged": len(entities) - len(final_entities),
            "relations_merged": len(relations) - len(final_relations),
        },
    }

    # Save merged result
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(merged_result, f, indent=2, ensure_ascii=False)

    logger.info(f"Merged KG saved to {output_file}")
    logger.info(f"  - Entities: {len(entities)} → {len(final_entities)} (merged {len(entities) - len(final_entities)})")
    logger.info(f"  - Relations: {len(relations)} → {len(final_relations)} (merged {len(relations) - len(final_relations)})")

    return {
        "extraction_type": extraction_type,
        "output_file": str(output_file),
        "chunks": len(chunks),
        "total_items": len(unique_kg_items),
        "entities": len(final_entities),
        "relations": len(final_relations),
        "entities_merged": len(entities) - len(final_entities),
        "relations_merged": len(relations) - len(final_relations),
        "disambiguation": True,
    }
