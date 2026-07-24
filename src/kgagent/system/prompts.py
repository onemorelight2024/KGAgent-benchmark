"""Centralized prompt management."""

from __future__ import annotations

import importlib.resources
from pathlib import Path


def load_prompt(name: str) -> str:
    """Load a prompt by name.

    Args:
        name: Prompt name (without .md extension)

    Returns:
        Prompt content as string

    Raises:
        FileNotFoundError: If prompt file not found
    """
    # Try to load from package resources (for installed package)
    try:
        files = importlib.resources.files("kgagent")
        prompt_file = files / "prompts" / f"{name}.md"
        return prompt_file.read_text(encoding="utf-8")
    except (FileNotFoundError, AttributeError):
        pass

    # Fallback: try to load from local prompts directory
    prompts_dir = Path(__file__).parent.parent / "prompts"
    prompt_file = prompts_dir / f"{name}.md"

    if not prompt_file.exists():
        raise FileNotFoundError(f"Prompt file not found: {name}.md")

    return prompt_file.read_text(encoding="utf-8")


# Prompt names
SUPERVISOR_PROMPT = "supervisor"
EXTRACTION_PROMPT = "extraction"
