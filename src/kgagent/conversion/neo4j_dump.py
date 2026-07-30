"""Convert Neo4j dump to JSON KG format."""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def convert_from_neo4j_dump(
    input_path: str | Path,
    output_path: str | Path | None = None,
    neo4j_home: str | None = None,
    **kwargs,
) -> dict[str, Any]:
    """Convert Neo4j dump file to JSON KG format.

    Args:
        input_path: Input Neo4j dump file path (.dump)
        output_path: Output JSON file path
        neo4j_home: Neo4j installation directory (optional, will try to detect)
        **kwargs: Additional options

    Returns:
        Result with output path and data
    """
    input_path = Path(input_path)

    if not input_path.exists():
        raise FileNotFoundError(f"Neo4j dump file not found: {input_path}")

    if output_path is None:
        output_path = input_path.with_suffix('.json')
    else:
        output_path = Path(output_path)

    # Create temp directory for extraction
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_db_path = Path(temp_dir) / "temp_db"

        # Step 1: Restore dump to temporary database
        logger.info(f"Restoring Neo4j dump: {input_path}")
        _restore_neo4j_dump(input_path, temp_db_path, neo4j_home)

        # Step 2: Export data using cypher-shell or neo4j-admin
        logger.info("Exporting data from restored database")
        kg_data = _export_neo4j_data(temp_db_path, neo4j_home)

    # Step 3: Convert to JSON KG format
    result = _convert_neo4j_to_json(kg_data)

    # Save to file
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    logger.info(
        f"Converted from Neo4j dump: {len(result.get('nodes', []))} nodes, "
        f"{len(result.get('kg', []))} relations"
    )

    return {
        "format": "json",
        "output_file": str(output_path),
        "data": result,
        "statistics": {
            "nodes": len(result.get('nodes', [])),
            "relations": len(result.get('kg', [])),
        },
    }


def _restore_neo4j_dump(
    dump_path: Path,
    db_path: Path,
    neo4j_home: str | None,
):
    """Restore Neo4j dump to temporary database.

    Args:
        dump_path: Path to .dump file
        db_path: Target database path
        neo4j_home: Neo4j home directory
    """
    # Find neo4j-admin command
    neo4j_admin = _find_neo4j_command("neo4j-admin", neo4j_home)

    if not neo4j_admin:
        raise RuntimeError(
            "neo4j-admin not found. Please provide neo4j_home parameter or "
            "ensure Neo4j is installed and in PATH"
        )

    # Run neo4j-admin database load
    cmd = [
        neo4j_admin,
        "database", "load",
        "--from-path", str(dump_path.parent),
        "--database", "temp_db",
        "--overwrite-destination"
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
        logger.debug(f"neo4j-admin output: {result.stdout}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to restore dump: {e.stderr}")
        raise RuntimeError(f"Failed to restore Neo4j dump: {e.stderr}")


def _export_neo4j_data(
    db_path: Path,
    neo4j_home: str | None,
) -> dict[str, Any]:
    """Export data from Neo4j database.

    Args:
        db_path: Database path
        neo4j_home: Neo4j home directory

    Returns:
        Dict with nodes and relationships
    """
    # Find cypher-shell command
    cypher_shell = _find_neo4j_command("cypher-shell", neo4j_home)

    if not cypher_shell:
        raise RuntimeError("cypher-shell not found")

    # Export nodes
    nodes_query = "MATCH (n) RETURN id(n) as id, labels(n) as labels, properties(n) as props"
    nodes_result = _run_cypher_query(cypher_shell, nodes_query)

    # Export relationships
    rels_query = """
    MATCH (a)-[r]->(b)
    RETURN id(a) as start_id, id(b) as end_id, type(r) as type, properties(r) as props
    """
    rels_result = _run_cypher_query(cypher_shell, rels_query)

    return {
        "nodes": nodes_result,
        "relationships": rels_result,
    }


def _run_cypher_query(cypher_shell: str, query: str) -> list[dict]:
    """Run Cypher query and return results.

    Args:
        cypher_shell: Path to cypher-shell command
        query: Cypher query

    Returns:
        List of result records
    """
    cmd = [
        cypher_shell,
        "--format", "json",
        "--non-interactive",
        query
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        logger.error(f"Cypher query failed: {e.stderr}")
        return []
    except json.JSONDecodeError:
        logger.error("Failed to parse Cypher query result")
        return []


def _convert_neo4j_to_json(neo4j_data: dict[str, Any]) -> dict[str, Any]:
    """Convert Neo4j export data to JSON KG format.

    Args:
        neo4j_data: Dict with nodes and relationships

    Returns:
        JSON KG format dict
    """
    nodes = neo4j_data.get("nodes", [])
    relationships = neo4j_data.get("relationships", [])

    # Build node ID to name mapping
    node_map = {}
    for node in nodes:
        node_id = node.get("id")
        props = node.get("props", {})
        name = props.get("name") or props.get("entityId") or f"node_{node_id}"
        node_map[node_id] = name

    # Convert relationships to KG format
    kg_items = []
    for rel in relationships:
        start_id = rel.get("start_id")
        end_id = rel.get("end_id")
        rel_type = rel.get("type", "RELATED_TO")
        props = rel.get("props", {})

        if start_id not in node_map or end_id not in node_map:
            continue

        start_name = node_map[start_id]
        end_name = node_map[end_id]

        # Build KG string
        kg_string = f"<subj> {start_name} <obj> {end_name} <rel> {rel_type}"

        # Add properties as attributes
        for key, value in props.items():
            if key not in ['relation', 'type']:
                kg_string += f" <{key}> {value}"

        kg_items.append(kg_string)

    return {
        "index": 0,
        "nodes": list(node_map.values()),
        "kg": kg_items,
    }


def _find_neo4j_command(command: str, neo4j_home: str | None) -> str | None:
    """Find Neo4j command (neo4j-admin or cypher-shell).

    Args:
        command: Command name
        neo4j_home: Neo4j home directory

    Returns:
        Full path to command or None if not found
    """
    # Try neo4j_home first
    if neo4j_home:
        cmd_path = Path(neo4j_home) / "bin" / command
        if cmd_path.exists():
            return str(cmd_path)

    # Try common locations
    common_paths = [
        "/usr/local/bin",
        "/usr/bin",
        "/opt/neo4j/bin",
        Path.home() / "neo4j" / "bin",
    ]

    for path in common_paths:
        cmd_path = Path(path) / command
        if cmd_path.exists():
            return str(cmd_path)

    # Try PATH
    try:
        result = subprocess.run(
            ["which", command],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        pass

    return None
