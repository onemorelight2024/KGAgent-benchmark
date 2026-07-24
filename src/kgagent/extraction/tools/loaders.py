"""Data loading utilities for extraction."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def load_json_file(path: str | Path) -> dict[str, Any] | list[Any]:
    """Load JSON data from file.

    Args:
        path: File path

    Returns:
        Loaded JSON data

    Raises:
        FileNotFoundError: If file doesn't exist
        json.JSONDecodeError: If file is not valid JSON
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    logger.info(f"Loaded JSON from {path}: {len(str(data))} bytes")
    return data


def save_json_file(data: Any, path: str | Path) -> None:
    """Save data to JSON file.

    Args:
        data: Data to save
        path: Output file path
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    logger.info(f"Saved JSON to {path}: {len(str(data))} bytes")
