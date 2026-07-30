"""Entry point for format conversion."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ConversionEntry:
    """Main entry point for format conversion operations."""

    SUPPORTED_FORMATS = {
        "neo4j": ["neo4j_csv", "neo4j", "csv"],
        "rdf": ["rdf", "turtle", "ttl", "nt", "n3"],
        "graphml": ["graphml", "xml"],
        "json": ["json"],
        "networkx": ["networkx", "nx", "pickle"],
    }

    SUPPORTED_IMPORT_FORMATS = {
        "neo4j_dump": [".dump"],
        "graphml": [".graphml", ".xml"],
    }

    def __init__(self):
        """Initialize conversion entry."""
        pass

    def convert(
        self,
        input_data: str | Path | dict | list,
        output_format: str,
        output_path: str | Path | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Convert KG data to specified format.

        Args:
            input_data: Input data (file path, dict, or list)
            output_format: Target format (neo4j_csv, rdf, graphml, etc.)
            output_path: Output file/directory path
            **kwargs: Additional format-specific options

        Returns:
            Conversion result with paths and statistics
        """
        # Normalize format name
        target_format = self._normalize_format(output_format)

        if target_format is None:
            raise ValueError(
                f"Unsupported format: {output_format}. "
                f"Supported formats: {', '.join(self._get_all_formats())}"
            )

        logger.info(f"Converting to format: {target_format}")

        # Load input data if it's a file path
        data = self._load_input(input_data)

        # Route to appropriate converter
        if target_format == "neo4j":
            from kgagent.conversion.neo4j_csv import convert_to_neo4j_csv
            return convert_to_neo4j_csv(data, output_path, **kwargs)

        elif target_format == "rdf":
            from kgagent.conversion.rdf import convert_to_rdf
            return convert_to_rdf(data, output_path, **kwargs)

        elif target_format == "graphml":
            from kgagent.conversion.graphml import convert_to_graphml
            return convert_to_graphml(data, output_path, **kwargs)

        elif target_format == "json":
            # Already in JSON format, just save if needed
            if output_path:
                import json
                output_path = Path(output_path)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                return {"format": "json", "output": str(output_path)}
            return {"format": "json", "data": data}

        else:
            raise NotImplementedError(f"Format {target_format} not yet implemented")

    def convert_from(
        self,
        input_path: str | Path,
        output_path: str | Path | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Convert from external format to JSON KG format.

        Args:
            input_path: Input file path (.dump, .graphml, etc.)
            output_path: Output JSON file path
            **kwargs: Additional format-specific options (e.g., neo4j_home)

        Returns:
            Conversion result with paths and statistics
        """
        input_path = Path(input_path)

        if not input_path.exists():
            raise FileNotFoundError(f"Input file not found: {input_path}")

        # Detect format from file extension
        suffix = input_path.suffix.lower()

        if suffix == ".dump":
            logger.info("Detected Neo4j dump file")
            from kgagent.conversion.neo4j_dump import convert_from_neo4j_dump
            return convert_from_neo4j_dump(input_path, output_path, **kwargs)

        elif suffix in [".graphml", ".xml"]:
            logger.info("Detected GraphML file")
            from kgagent.conversion.graphml import convert_from_graphml
            return convert_from_graphml(input_path, output_path, **kwargs)

        else:
            raise ValueError(
                f"Unsupported import format: {suffix}. "
                f"Supported: .dump (Neo4j), .graphml/.xml (GraphML)"
            )

    def _normalize_format(self, format_name: str) -> str | None:
        """Normalize format name to standard format.

        Args:
            format_name: User-provided format name

        Returns:
            Normalized format name or None if not supported
        """
        format_name = format_name.lower().strip()

        for standard_name, aliases in self.SUPPORTED_FORMATS.items():
            if format_name in aliases:
                return standard_name

        return None

    def _get_all_formats(self) -> list[str]:
        """Get list of all supported format names."""
        formats = []
        for aliases in self.SUPPORTED_FORMATS.values():
            formats.extend(aliases)
        return formats

    def _load_input(self, input_data: str | Path | dict | list) -> dict | list:
        """Load input data from file or return as-is.

        Args:
            input_data: Input data

        Returns:
            Loaded data as dict or list
        """
        if isinstance(input_data, (dict, list)):
            return input_data

        # Try to load from file
        input_path = Path(input_data)
        if input_path.exists() and input_path.is_file():
            import json

            logger.info(f"Loading input from {input_path}")
            with open(input_path, "r", encoding="utf-8") as f:
                return json.load(f)

        raise ValueError(f"Invalid input: {input_data}")
