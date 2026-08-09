from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kgagent.reasoning.completion import (
    list_kg_completion_methods,
    recommend_kg_completion_methods,
    run_kicgpt_kg_completion_for_agent,
)
from kgagent.core.json_io import dumps_pretty


def list_kg_completion_methods_impl() -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": dumps_pretty(list_kg_completion_methods())}],
        "structuredContent": {"methods": list_kg_completion_methods()},
    }


def recommend_kg_completion_methods_impl(task_type: str, mode: str | None = None) -> dict[str, Any]:
    methods = recommend_kg_completion_methods(task_type, mode=mode)
    return {
        "content": [{"type": "text", "text": dumps_pretty(methods)}],
        "structuredContent": {"methods": methods},
    }


def run_kicgpt_kg_completion_impl(
    input_json: str,
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    parsed = json.loads(input_json)
    result = run_kicgpt_kg_completion_for_agent(parsed, workspace=workspace)
    return {
        "content": [{"type": "text", "text": dumps_pretty(result)}],
        "structuredContent": result,
    }
