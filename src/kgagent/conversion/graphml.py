"""Convert KG data to GraphML format."""

from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def convert_to_graphml(
    data: dict | list,
    output_path: str | Path | None = None,
    **kwargs,
) -> dict[str, Any]:
    """Convert KG data to GraphML format.

    Args:
        data: Input KG data (list of items with 'kg' field or dict)
        output_path: Output directory path (will create separate files for each index)
        **kwargs: Additional options

    Returns:
        Result with output paths and statistics
    """
    if output_path is None:
        output_path = Path("./graphml_output")
    else:
        output_path = Path(output_path)

    # If data is a list, save each item separately by index
    if isinstance(data, list):
        output_path.mkdir(parents=True, exist_ok=True)

        total_nodes = 0
        total_edges = 0
        output_files = []

        for item in data:
            index = item.get("index", 0)
            kg_data = item.get("kg", [])

            # Extract entities and relations for this item
            entities = {}
            relations = []
            _extract_from_kg(kg_data, entities, relations, index)

            # Skip empty items
            if not entities and not relations:
                continue

            # Write separate file for this index
            graphml_path = output_path / f"index_{index}.graphml"
            _write_graphml(entities, relations, graphml_path)

            total_nodes += len(entities)
            total_edges += len(relations)
            output_files.append({
                "index": index,
                "file": str(graphml_path),
                "nodes": len(entities),
                "edges": len(relations),
            })

            logger.info(
                f"Index {index}: {len(entities)} nodes, {len(relations)} edges"
            )

        logger.info(
            f"Converted to GraphML: {len(output_files)} items, "
            f"{total_nodes} total nodes, {total_edges} total edges"
        )

        return {
            "format": "graphml",
            "output_dir": str(output_path),
            "files": output_files,
            "statistics": {
                "items": len(output_files),
                "total_nodes": total_nodes,
                "total_edges": total_edges,
            },
        }

    elif isinstance(data, dict):
        # Single item - use index from data
        index = data.get("index", 0)
        kg_data = data.get("kg", [])

        entities = {}
        relations = []
        _extract_from_kg(kg_data, entities, relations, index)

        # If output_path is a directory, create index file
        if output_path.suffix != '.graphml':
            output_path.mkdir(parents=True, exist_ok=True)
            graphml_path = output_path / f"index_{index}.graphml"
        else:
            graphml_path = output_path

        _write_graphml(entities, relations, graphml_path)

        logger.info(
            f"Converted to GraphML: {len(entities)} nodes, {len(relations)} edges"
        )

        return {
            "format": "graphml",
            "output_file": str(graphml_path),
            "statistics": {
                "nodes": len(entities),
                "edges": len(relations),
            },
        }


def convert_from_graphml(
    input_path: str | Path,
    output_path: str | Path | None = None,
    **kwargs,
) -> dict[str, Any]:
    """Convert GraphML file to JSON KG format.

    Args:
        input_path: Input GraphML file path
        output_path: Output JSON file path
        **kwargs: Additional options

    Returns:
        Result with output path and data
    """
    input_path = Path(input_path)

    if output_path is None:
        output_path = input_path.with_suffix('.json')
    else:
        output_path = Path(output_path)

    # Parse GraphML
    tree = ET.parse(input_path)
    root = tree.getroot()

    # Handle namespace
    ns = {'graphml': 'http://graphml.graphdrawing.org/xmlns'}

    # Extract nodes
    nodes = {}  # id -> name
    for node in root.findall('.//graphml:node', ns):
        node_id = node.get('id')
        name = node_id  # Default to ID

        # Try to find name in data elements
        for data in node.findall('graphml:data', ns):
            if data.get('key') == 'name':
                name = data.text or node_id
                break

        nodes[node_id] = name

    # Extract edges and build KG
    kg_items = []
    for edge in root.findall('.//graphml:edge', ns):
        source_id = edge.get('source')
        target_id = edge.get('target')

        if source_id not in nodes or target_id not in nodes:
            continue

        source_name = nodes[source_id]
        target_name = nodes[target_id]

        # Extract relation type
        relation = "RELATED_TO"  # Default
        attributes = {}

        for data in edge.findall('graphml:data', ns):
            key = data.get('key')
            value = data.text or ""

            if key == 'relation' or key == 'type':
                relation = value
            else:
                attributes[key] = value

        # Build KG string
        kg_string = f"<subj> {source_name} <obj> {target_name} <rel> {relation}"
        for attr_key, attr_value in attributes.items():
            kg_string += f" <{attr_key}> {attr_value}"

        kg_items.append(kg_string)

    # Build output JSON
    result = {
        "index": 0,
        "kg": kg_items,
    }

    # Save to file
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    logger.info(f"Converted from GraphML: {len(nodes)} nodes, {len(kg_items)} relations")

    return {
        "format": "json",
        "output_file": str(output_path),
        "data": result,
        "statistics": {
            "nodes": len(nodes),
            "relations": len(kg_items),
        },
    }


def _extract_from_kg(
    kg_data: list[str],
    entities: dict[str, int],
    relations: list[dict],
    index: int = 0,
):
    """Extract entities and relations from KG data.

    Args:
        kg_data: List of KG strings
        entities: Output dict of entity_name -> id
        relations: Output list of relations
        index: Index of the item
    """
    current_id = len(entities)

    for kg_item in kg_data:
        # Parse tagged format
        parsed = _parse_tagged_format(kg_item)

        if parsed:
            subj = parsed.get("subj")
            obj = parsed.get("obj")
            rel = parsed.get("rel")

            if subj and obj and rel:
                # Add entities if new
                if subj not in entities:
                    entities[subj] = current_id
                    current_id += 1

                if obj not in entities:
                    entities[obj] = current_id
                    current_id += 1

                # Add relation
                relation = {
                    "source": entities[subj],
                    "target": entities[obj],
                    "type": rel,
                    "index": index,
                }

                # Add other attributes
                for key, value in parsed.items():
                    if key not in ["subj", "obj", "rel"]:
                        relation[key] = value

                relations.append(relation)


def _parse_tagged_format(kg_string: str) -> dict[str, str] | None:
    """Parse tagged format KG string."""
    result = {}
    parts = kg_string.strip().split("<")

    for part in parts:
        if not part.strip():
            continue

        if ">" in part:
            tag, value = part.split(">", 1)
            tag = tag.strip()
            value = value.strip()

            if tag and value:
                result[tag] = value

    return result if result else None


def _write_graphml(entities: dict[str, int], relations: list[dict], output_path: Path):
    """Write GraphML XML file.

    Args:
        entities: Dict of entity_name -> id
        relations: List of relation dicts
        output_path: Output file path
    """
    # Create root element
    graphml = ET.Element('graphml', {
        'xmlns': 'http://graphml.graphdrawing.org/xmlns',
        'xmlns:xsi': 'http://www.w3.org/2001/XMLSchema-instance',
        'xsi:schemaLocation': 'http://graphml.graphdrawing.org/xmlns http://graphml.graphdrawing.org/xmlns/1.0/graphml.xsd'
    })

    # Define keys (attributes)
    ET.SubElement(graphml, 'key', {
        'id': 'name',
        'for': 'node',
        'attr.name': 'name',
        'attr.type': 'string'
    })

    ET.SubElement(graphml, 'key', {
        'id': 'relation',
        'for': 'edge',
        'attr.name': 'relation',
        'attr.type': 'string'
    })

    ET.SubElement(graphml, 'key', {
        'id': 'index',
        'for': 'edge',
        'attr.name': 'index',
        'attr.type': 'int'
    })

    # Collect all attribute keys from relations
    attr_keys = set()
    for rel in relations:
        for key in rel.keys():
            if key not in ['source', 'target', 'type', 'index']:
                attr_keys.add(key)

    # Define additional attribute keys
    for attr_key in sorted(attr_keys):
        ET.SubElement(graphml, 'key', {
            'id': attr_key,
            'for': 'edge',
            'attr.name': attr_key,
            'attr.type': 'string'
        })

    # Create graph
    graph = ET.SubElement(graphml, 'graph', {
        'id': 'G',
        'edgedefault': 'directed'
    })

    # Add nodes
    for entity_name, entity_id in entities.items():
        node = ET.SubElement(graph, 'node', {'id': f'n{entity_id}'})
        data = ET.SubElement(node, 'data', {'key': 'name'})
        data.text = entity_name

    # Add edges
    for idx, rel in enumerate(relations):
        edge = ET.SubElement(graph, 'edge', {
            'id': f'e{idx}',
            'source': f'n{rel["source"]}',
            'target': f'n{rel["target"]}'
        })

        # Add relation type
        data = ET.SubElement(edge, 'data', {'key': 'relation'})
        data.text = rel['type']

        # Add index
        data = ET.SubElement(edge, 'data', {'key': 'index'})
        data.text = str(rel.get('index', 0))

        # Add other attributes
        for key, value in rel.items():
            if key not in ['source', 'target', 'type', 'index']:
                data = ET.SubElement(edge, 'data', {'key': key})
                data.text = str(value)

    # Write to file
    tree = ET.ElementTree(graphml)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding='utf-8', xml_declaration=True)

    logger.info(f"Wrote GraphML to {output_path}")
