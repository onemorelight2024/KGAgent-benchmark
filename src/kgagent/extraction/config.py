"""Extraction configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExtractionConfig:
    """Configuration for extraction route."""

    model_name: str
    work_dir: str
    permission_mode: str = "bypassPermissions"
    max_turns: int = 10
