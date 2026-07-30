"""Convert Neo4j dump to JSON KG format using Docker."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def convert_from_neo4j_dump(
    input_path: str | Path,
    output_path: str | Path | None = None,
    neo4j_home: str | None = None,
    **kwargs,
) -> dict[str, Any]:
    """Convert Neo4j dump file to JSON KG format using Docker.

    Args:
        input_path: Input Neo4j dump file path (.dump)
        output_path: Output JSON file path
        neo4j_home: Unused (kept for compatibility)
        **kwargs: Additional options

    Returns:
        Result with output path and data
    """
    input_path = Path(input_path)

    if not input_path.exists():
        raise FileNotFoundError(f"Neo4j dump file not found: {input_path}")

    # Check if Docker is available
    if not _check_docker():
        raise RuntimeError(
            "Docker is required to convert Neo4j dump files.\n\n"
            "Please install Docker:\n"
            "  macOS: https://docs.docker.com/desktop/install/mac-install/\n"
            "  Linux: https://docs.docker.com/engine/install/\n"
            "  Windows: https://docs.docker.com/desktop/install/windows-install/\n\n"
            "After installation, make sure Docker is running."
        )

    if output_path is None:
        output_path = input_path.with_suffix('.json')
    else:
        output_path = Path(output_path)

    logger.info(f"Converting Neo4j dump using Docker: {input_path}")

    # Create temporary directories
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        data_dir = temp_path / "data"
        import_dir = temp_path / "import"
        plugins_dir = temp_path / "plugins"
        backups_dir = temp_path / "backups"

        data_dir.mkdir()
        import_dir.mkdir()
        plugins_dir.mkdir()
        backups_dir.mkdir()

        # Copy dump file to backups directory
        dump_file = backups_dir / input_path.name
        shutil.copy(input_path, dump_file)

        # Step 1: Load dump file into database
        logger.info("Step 1/4: Loading dump file...")
        _docker_load_dump(dump_file, data_dir, backups_dir)

        # Step 2: Start temporary Neo4j container (no port mapping)
        logger.info("Step 2/4: Starting temporary Neo4j container...")
        container_name = _docker_start_neo4j(data_dir, import_dir, plugins_dir)

        try:
            # Step 3: Export data using APOC
            logger.info("Step 3/4: Exporting data to JSON...")
            json_file = import_dir / "graph.json"
            _docker_export_json(container_name, json_file)

            # Step 4: Convert APOC JSON to KG format
            logger.info("Step 4/4: Converting to KG format...")
            kg_data = _convert_apoc_json_to_kg(json_file)

        finally:
            # Always cleanup container
            logger.info("Cleaning up Docker container...")
            _docker_cleanup(container_name)

    # Save to output file
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(kg_data, f, indent=2, ensure_ascii=False)

    logger.info(
        f"Converted from Neo4j dump: {len(kg_data.get('nodes', []))} nodes, "
        f"{len(kg_data.get('kg', []))} relations"
    )

    return {
        "format": "json",
        "output_file": str(output_path),
        "data": kg_data,
        "statistics": {
            "nodes": len(kg_data.get('nodes', [])),
            "relations": len(kg_data.get('kg', [])),
        },
    }


def _check_docker() -> bool:
    """Check if Docker is available and running.

    Returns:
        True if Docker is available, False otherwise
    """
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _docker_load_dump(
    dump_file: Path,
    data_dir: Path,
    backups_dir: Path,
) -> None:
    """Load Neo4j dump file using Docker.

    Args:
        dump_file: Path to dump file
        data_dir: Data directory
        backups_dir: Backups directory containing dump
    """
    db_name = dump_file.stem  # e.g., "neo4j" from "neo4j.dump"

    cmd = [
        "docker", "run", "--rm",
        "-v", f"{data_dir}:/data",
        "-v", f"{backups_dir}:/backups",
        "neo4j:latest",
        "neo4j-admin", "database", "load",
        "--from-path=/backups",
        "--overwrite-destination",
        db_name
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=300
        )
        logger.debug(f"Load dump output: {result.stdout}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to load dump: {e.stderr}")
        raise RuntimeError(f"Failed to load Neo4j dump: {e.stderr}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Neo4j dump loading timed out after 5 minutes")


def _docker_start_neo4j(
    data_dir: Path,
    import_dir: Path,
    plugins_dir: Path,
    container_name: str = "neo4j-converter",
) -> str:
    """Start temporary Neo4j container without port mapping.

    Args:
        data_dir: Data directory
        import_dir: Import directory
        plugins_dir: Plugins directory
        container_name: Container name

    Returns:
        Container name
    """
    # Remove existing container if exists
    subprocess.run(
        ["docker", "rm", "-f", container_name],
        capture_output=True,
        timeout=10
    )

    cmd = [
        "docker", "run", "-d",
        "--name", container_name,
        "-e", "NEO4J_AUTH=neo4j/TempPassword123",
        "-e", "NEO4J_PLUGINS=[\"apoc\"]",
        "-e", "NEO4J_apoc_export_file_enabled=true",
        "-e", "NEO4J_apoc_import_file_enabled=true",
        "-v", f"{data_dir}:/data",
        "-v", f"{import_dir}:/var/lib/neo4j/import",
        "-v", f"{plugins_dir}:/plugins",
        "neo4j:latest"
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=30
        )
        container_id = result.stdout.strip()
        logger.debug(f"Started container: {container_id}")

        # Wait for Neo4j to be ready
        _wait_for_neo4j(container_name)

        return container_name

    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to start Neo4j: {e.stderr}")
        raise RuntimeError(f"Failed to start Neo4j container: {e.stderr}")


def _wait_for_neo4j(container_name: str, timeout: int = 60) -> None:
    """Wait for Neo4j to be ready.

    Args:
        container_name: Container name
        timeout: Timeout in seconds
    """
    logger.info("Waiting for Neo4j to start...")
    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            result = subprocess.run(
                ["docker", "exec", container_name,
                 "cypher-shell", "-u", "neo4j", "-p", "TempPassword123",
                 "RETURN 1"],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                logger.info("Neo4j is ready")
                return

        except subprocess.TimeoutExpired:
            pass

        time.sleep(2)

    raise RuntimeError(f"Neo4j did not start within {timeout} seconds")


def _docker_export_json(container_name: str, output_file: Path) -> None:
    """Export Neo4j data to JSON using APOC.

    Args:
        container_name: Container name
        output_file: Output JSON file path (on host)
    """
    # APOC exports to /var/lib/neo4j/import directory by default
    cmd = [
        "docker", "exec", container_name,
        "cypher-shell",
        "-u", "neo4j",
        "-p", "TempPassword123",
        'CALL apoc.export.json.all("graph.json", {useTypes: true})'
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=300
        )
        logger.debug(f"Export output: {result.stdout}")

        # Wait a moment for file to be written
        time.sleep(1)

        if not output_file.exists():
            raise RuntimeError("Export file was not created")

    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to export JSON: {e.stderr}")
        raise RuntimeError(f"Failed to export Neo4j data: {e.stderr}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Neo4j export timed out after 5 minutes")


def _docker_cleanup(container_name: str) -> None:
    """Remove Docker container.

    Args:
        container_name: Container name
    """
    try:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
            timeout=30
        )
        logger.info("Docker container removed")
    except Exception as e:
        logger.warning(f"Failed to cleanup container: {e}")


def _convert_apoc_json_to_kg(json_file: Path) -> dict[str, Any]:
    """Convert APOC JSON export to KG format.

    APOC exports JSONL format (one JSON object per line).
    Each line is either a node or relationship.

    Args:
        json_file: Path to APOC JSON file

    Returns:
        KG format dict
    """
    if not json_file.exists():
        raise FileNotFoundError(f"JSON file not found: {json_file}")

    nodes_map = {}  # id -> node data
    relationships = []

    with open(json_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            try:
                obj = json.loads(line)

                # Check if it's a node or relationship
                if obj.get('type') == 'node':
                    node_id = obj.get('id')
                    props = obj.get('properties', {})
                    labels = obj.get('labels', [])

                    # Get node name from properties
                    name = (
                        props.get('name') or
                        props.get('entityId') or
                        props.get('id') or
                        f"node_{node_id}"
                    )

                    nodes_map[node_id] = {
                        'id': node_id,
                        'name': name,
                        'labels': labels,
                        'properties': props
                    }

                elif obj.get('type') == 'relationship':
                    start_id = obj.get('start', {}).get('id')
                    end_id = obj.get('end', {}).get('id')
                    props = obj.get('properties', {})

                    # Try to get relation type from properties.relation first, then label
                    rel_type = props.get('relation') or obj.get('label', 'RELATED_TO')

                    relationships.append({
                        'start_id': start_id,
                        'end_id': end_id,
                        'type': rel_type,
                        'properties': props
                    })

            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse JSON line: {e}")
                continue

    # Convert to KG format
    kg_items = []
    for rel in relationships:
        start_id = rel['start_id']
        end_id = rel['end_id']

        if start_id not in nodes_map or end_id not in nodes_map:
            continue

        start_name = nodes_map[start_id]['name']
        end_name = nodes_map[end_id]['name']
        rel_type = rel['type']

        # Build KG string in your format
        kg_string = f"<subj> {start_name} <obj> {end_name} <rel> {rel_type}"

        # Add properties as attributes
        for key, value in rel.get('properties', {}).items():
            if key not in ['relation', 'type']:
                kg_string += f" <{key}> {value}"

        kg_items.append(kg_string)

    return {
        "index": 0,
        "nodes": [node['name'] for node in nodes_map.values()],
        "kg": kg_items,
    }
