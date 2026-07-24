"""Core JSON I/O utilities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json(path: str | Path) -> Any:
    """Load JSON from a file path."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(data: Any, path: str | Path) -> None:
    """Save data to JSON file."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def dumps_pretty(data: Any) -> str:
    """Convert data to pretty JSON string."""
    return json.dumps(data, indent=2, ensure_ascii=False)


def json_to_text(data: Any) -> str:
    """Convert JSON data to text intelligently.

    Strategy:
    1. If data has a 'text' field, use it directly
    2. If data has common text fields (content, description, body, message), use them
    3. If data is a list, join items with newlines
    4. If data is a dict, format as "key: value" pairs
    5. Otherwise, convert to JSON string

    Args:
        data: JSON data (dict, list, str, etc.)

    Returns:
        Extracted text content
    """
    # Already a string
    if isinstance(data, str):
        return data

    # Dict with explicit text field
    if isinstance(data, dict):
        # Priority 1: exact 'text' field
        if "text" in data:
            return str(data["text"])

        # Priority 2: title + another text field (combined)
        if "title" in data:
            title = str(data["title"])
            text_fields = ["content", "description", "body", "message", "summary", "abstract"]
            for field in text_fields:
                if field in data:
                    return f"{title}\n\n{data[field]}"
            # Just title alone
            return title

        # Priority 3: common text field names (without title)
        text_fields = ["content", "description", "body", "message", "summary", "abstract"]
        for field in text_fields:
            if field in data:
                return str(data[field])

        # Priority 4: format all fields as "key: value"
        lines = []
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                # Recursively handle nested structures
                nested_text = json_to_text(value)
                lines.append(f"{key}:\n{nested_text}")
            else:
                lines.append(f"{key}: {value}")
        return "\n".join(lines)

    # List of items
    if isinstance(data, list):
        # If list of dicts, convert each
        if data and isinstance(data[0], dict):
            texts = [json_to_text(item) for item in data]
            return "\n\n---\n\n".join(texts)
        # If list of strings, join with newlines
        return "\n".join(str(item) for item in data)

    # Fallback: convert to JSON string
    return dumps_pretty(data)

