"""Extraction type registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class ExtractionTypeInfo:
    """Information about an extraction type."""

    name: str
    keywords: list[str]
    description: str


class ExtractionRegistry:
    """Registry of extraction types."""

    def __init__(self):
        self.types = {
            "triples": ExtractionTypeInfo(
                name="triples",
                keywords=["triples", "relations", "relation", "triple", "kg", "knowledge graph"],
                description="Relation triples (subject, relation, object)",
            ),
            "temporal": ExtractionTypeInfo(
                name="temporal",
                keywords=["temporal", "time", "when", "quadruples", "quadruple"],
                description="Temporal quadruples (subject, relation, object, time)",
            ),
            "hyper": ExtractionTypeInfo(
                name="hyper",
                keywords=["hyper", "hyper-relation", "context", "attributes", "attribute"],
                description="Hyper-relations (relation with structured attributes)",
            ),
            "event": ExtractionTypeInfo(
                name="event",
                keywords=["event", "events", "happening", "occurrence", "autoschema", "event graph"],
                description="Event knowledge graph using AutoSchemaKG (3-stage: entity-relation, event-entity, event-relation)",
            ),
        }

    def detect_type(self, query: str) -> str:
        """Detect extraction type from query string.

        Args:
            query: User query

        Returns:
            Extraction type name (triples, temporal, hyper)
        """
        query_lower = query.lower()

        # Collect all matching keywords with their types
        matches = []
        for type_name, type_info in self.types.items():
            for keyword in type_info.keywords:
                if keyword in query_lower:
                    matches.append((len(keyword), type_name, keyword))

        # If matches found, return the type with longest matching keyword
        if matches:
            matches.sort(reverse=True)  # Sort by keyword length (descending)
            return matches[0][1]

        # Default to triples
        return "triples"

    def get_type_info(self, type_name: str) -> ExtractionTypeInfo:
        """Get information about an extraction type.

        Args:
            type_name: Type name

        Returns:
            ExtractionTypeInfo

        Raises:
            ValueError: If type not found
        """
        if type_name not in self.types:
            raise ValueError(f"Unknown extraction type: {type_name}")
        return self.types[type_name]
