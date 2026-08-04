"""Method I/O utilities for KGQG benchmark generation."""

from __future__ import annotations

import json
from pathlib import Path

from kgagent.benchmark.types import KGQGMethodInputItem


def save_method_input_jsonl(
    items: list[KGQGMethodInputItem],
    output_path: Path,
) -> None:
    """Save method input items to JSONL file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def load_method_input_jsonl(input_path: Path) -> list[KGQGMethodInputItem]:
    """Load method input items from JSONL file."""
    items: list[KGQGMethodInputItem] = []
    with input_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def samples_to_method_input(
    samples: list[KGQGMethodInputItem],
    task: str = "KGQG",
    language: str = "en",
) -> list[KGQGMethodInputItem]:
    """Convert sampled subgraphs to method input format.

    Currently a pass-through, but allows for future transformations.
    """
    # Ensure all items have correct task
    for sample in samples:
        sample["task"] = task
        sample["language"] = language
    return samples
