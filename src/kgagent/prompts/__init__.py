"""Prompt templates."""

from __future__ import annotations

from importlib.resources import files


def load_prompt(name: str) -> str:
    """Load a prompt template by name."""
    prompt_file = files("kgagent.prompts") / name
    return prompt_file.read_text(encoding="utf-8")
