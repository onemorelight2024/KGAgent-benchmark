"""Language detection helpers for benchmark graphs."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from kgagent.core.language import detect_language


TEXT_KEYS = {
    "name",
    "label",
    "title",
    "description",
    "text",
    "type",
    "relation",
    "predicate",
    "event",
    "question",
    "answer",
}


def detect_graph_language(graph: dict[str, Any] | list[Any] | None) -> str:
    """Detect the natural language used by a KG/TKG graph."""
    snippets: list[str] = []
    _collect_graph_text(graph, snippets)
    text = " ".join(snippets)
    return detect_language(text) if text.strip() else "en"


def _collect_graph_text(value: Any, snippets: list[str], key: str | None = None) -> None:
    if is_dataclass(value):
        _collect_graph_text(asdict(value), snippets, key)
        return
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            _collect_graph_text(child_value, snippets, str(child_key).lower())
        return
    if isinstance(value, list):
        for item in value:
            _collect_graph_text(item, snippets, key)
        return
    if not isinstance(value, str):
        return

    stripped = value.strip()
    if not stripped:
        return
    if key in TEXT_KEYS or detect_language(stripped) == "zh":
        snippets.append(stripped)
