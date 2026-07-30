"""Convert KG data to Neo4j CSV format."""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def convert_to_neo4j_csv(
    data: dict | list,
    output_path: str | Path | None = None,
    **kwargs,
) -> dict[str, Any]:
    """Convert KG data to Neo4j CSV format.

    Args:
        data: Input KG data (list of items with 'kg' field)
        output_path: Output directory path (will create separate files for each index)
        **kwargs: Additional options

    Returns:
        Result with output paths and statistics
    """
    if output_path is None:
        output_path = Path("./neo4j_import")
    else:
        output_path = Path(output_path)

    output_path.mkdir(parents=True, exist_ok=True)

    # Process data - save each index separately
    if isinstance(data, list):
        total_nodes = 0
        total_relations = 0
        output_files = []

        for item in data:
            index = item.get("index", 0)
            kg_data = item.get("kg", [])

            # Extract entities and relations for this item
            entities = {}
            relations = []
            _extract_from_kg(kg_data, entities, relations)

            # Skip empty items
            if not entities and not relations:
                continue

            # Write separate files for this index
            nodes_path = output_path / f"index_{index}_nodes.csv"
            relationships_path = output_path / f"index_{index}_relationships.csv"

            _write_nodes_csv(entities, nodes_path)
            _write_relationships_csv(relations, relationships_path)

            total_nodes += len(entities)
            total_relations += len(relations)
            output_files.append({
                "index": index,
                "nodes_file": str(nodes_path),
                "relationships_file": str(relationships_path),
                "nodes": len(entities),
                "relationships": len(relations),
            })

            logger.info(
                f"Index {index}: {len(entities)} nodes, {len(relations)} relationships"
            )

        logger.info(
            f"Converted to Neo4j CSV: {len(output_files)} items, "
            f"{total_nodes} total nodes, {total_relations} total relationships"
        )

        return {
            "format": "neo4j_csv",
            "output_dir": str(output_path),
            "files": output_files,
            "statistics": {
                "items": len(output_files),
                "total_nodes": total_nodes,
                "total_relationships": total_relations,
            },
        }

    elif isinstance(data, dict):
        # Single item - use index 0
        index = data.get("index", 0)
        kg_data = data.get("kg", [])

        entities = {}
        relations = []
        _extract_from_kg(kg_data, entities, relations)

        nodes_path = output_path / f"index_{index}_nodes.csv"
        relationships_path = output_path / f"index_{index}_relationships.csv"

        _write_nodes_csv(entities, nodes_path)
        _write_relationships_csv(relations, relationships_path)

        logger.info(
            f"Converted to Neo4j CSV: {len(entities)} nodes, {len(relations)} relationships"
        )

        return {
            "format": "neo4j_csv",
            "output_dir": str(output_path),
            "nodes_file": str(nodes_path),
            "relationships_file": str(relationships_path),
            "statistics": {
                "nodes": len(entities),
                "relationships": len(relations),
            },
        }


def _extract_from_kg(
    kg_data: list[str],
    entities: dict[str, dict],
    relations: list[tuple],
):
    """Extract entities and relations from KG data.

    Args:
        kg_data: List of KG strings (triples, quadruples, or hyper-relations)
        entities: Output dict of entities
        relations: Output list of relations
    """
    for kg_item in kg_data:
        # Parse tagged format: <subj> X <obj> Y <rel> Z [<attr> value ...]
        parsed = _parse_tagged_format(kg_item)

        if parsed:
            subj = parsed.get("subj")
            obj = parsed.get("obj")
            rel = parsed.get("rel")

            if subj and obj and rel:
                # Add entities
                if subj not in entities:
                    entities[subj] = {"name": subj}

                if obj not in entities:
                    entities[obj] = {"name": obj}

                # Add relation with attributes
                relation = {
                    "start": subj,
                    "end": obj,
                    "type": rel,
                }

                # Add additional attributes (time, location, etc.)
                for key, value in parsed.items():
                    if key not in ["subj", "obj", "rel"]:
                        relation[key] = value

                relations.append(relation)


def _parse_tagged_format(kg_string: str) -> dict[str, str] | None:
    """Parse tagged format KG string.

    Format: <subj> X <obj> Y <rel> Z [<attr> value ...]

    Args:
        kg_string: Tagged KG string

    Returns:
        Dict with parsed fields or None if parsing fails
    """
    result = {}
    parts = kg_string.strip().split("<")

    for part in parts:
        if not part.strip():
            continue

        # Extract tag and value
        if ">" in part:
            tag, value = part.split(">", 1)
            tag = tag.strip()
            value = value.strip()

            if tag and value:
                result[tag] = value

    return result if result else None


def _write_nodes_csv(entities: dict[str, dict], output_path: Path):
    """Write nodes to CSV file.

    Args:
        entities: Dict of entity_name -> properties
        output_path: Output CSV file path
    """
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)

        # Header
        writer.writerow(["entityId:ID", "name:STRING", ":LABEL"])

        # Write entities
        for idx, (entity_name, props) in enumerate(entities.items()):
            writer.writerow([
                idx,  # entityId
                entity_name,  # name
                "Entity",  # label
            ])

    logger.info(f"Wrote {len(entities)} nodes to {output_path}")


def _write_relationships_csv(relations: list[dict], output_path: Path):
    """Write relationships to CSV file.

    Args:
        relations: List of relation dicts
        output_path: Output CSV file path
    """
    # Build entity name to ID mapping
    entity_to_id = {}
    current_id = 0

    for rel in relations:
        start = rel["start"]
        end = rel["end"]

        if start not in entity_to_id:
            entity_to_id[start] = current_id
            current_id += 1

        if end not in entity_to_id:
            entity_to_id[end] = current_id
            current_id += 1

    # Determine all attribute columns
    attribute_keys = set()
    for rel in relations:
        for key in rel.keys():
            if key not in ["start", "end", "type"]:
                attribute_keys.add(key)

    attribute_keys = sorted(attribute_keys)

    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)

        # Header
        header = [":START_ID", ":END_ID", "relation:STRING", ":TYPE"]
        for attr in attribute_keys:
            header.append(f"{attr}:STRING")
        writer.writerow(header)

        # Write relationships
        for rel in relations:
            start_id = entity_to_id[rel["start"]]
            end_id = entity_to_id[rel["end"]]
            rel_type = rel["type"]

            row = [start_id, end_id, rel_type, "RELATION"]

            # Add attributes
            for attr in attribute_keys:
                row.append(rel.get(attr, ""))

            writer.writerow(row)

    logger.info(f"Wrote {len(relations)} relationships to {output_path}")
