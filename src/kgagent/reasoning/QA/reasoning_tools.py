from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kgagent.reasoning import (
    list_reasoning_methods,
    recommend_reasoning_methods,
    run_graphrag_qa_for_agent,
    run_rag_anything_qa_for_agent,
    run_rag_anything_qa_for_agent_async,
)
from kgagent.core.json_io import dumps_pretty


def format_reasoning_dependency_hint(error: Exception | str) -> str:
    message = str(error)
    lowered = message.lower()

    install_lines = [
        "Reasoning dependencies are not ready.",
        'Install them with: `pip install -e ".[reasoning]"`',
        "Or: `pip install -r requirements-reasoning.txt`",
        "See also: `docs/REASONING.md`",
    ]

    if "rag-anything is not installed" in lowered or "no module named 'raganything'" in lowered:
        return "\n".join(
            [
                "RAG-Anything is not installed.",
                *install_lines[1:],
                f"Original error: {message}",
            ]
        )

    if "graphrag is not installed" in lowered or "no module named 'graphrag'" in lowered:
        return "\n".join(
            [
                "GraphRAG is not installed.",
                *install_lines[1:],
                f"Original error: {message}",
            ]
        )

    if "no module named 'lightrag'" in lowered or "no module named 'sentence_transformers'" in lowered:
        return "\n".join([*install_lines, f"Original error: {message}"])

    if "reasoning" in lowered and "not installed" in lowered:
        return "\n".join([*install_lines, f"Original error: {message}"])

    return message


def list_reasoning_methods_impl() -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": dumps_pretty(list_reasoning_methods())}],
    }


def recommend_reasoning_methods_impl(task_type: str, input_path: str | None = None) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": dumps_pretty(recommend_reasoning_methods(task_type, input_path=input_path)),
            }
        ],
    }


def run_rag_anything_qa_impl(input_json: str, workspace: str | Path | None = None) -> dict[str, Any]:
    parsed = json.loads(input_json)
    result = run_rag_anything_qa_for_agent(parsed, workspace=workspace)
    return {
        "content": [{"type": "text", "text": dumps_pretty(result)}],
        "structuredContent": result,
    }


async def run_rag_anything_qa_async_impl(
    input_json: str,
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    parsed = json.loads(input_json)
    result = await run_rag_anything_qa_for_agent_async(parsed, workspace=workspace)
    return {
        "content": [{"type": "text", "text": dumps_pretty(result)}],
        "structuredContent": result,
    }


def run_graphrag_qa_impl(input_json: str, workspace: str | Path | None = None) -> dict[str, Any]:
    parsed = json.loads(input_json)
    result = run_graphrag_qa_for_agent(parsed, workspace=workspace)
    return {
        "content": [{"type": "text", "text": dumps_pretty(result)}],
        "structuredContent": result,
    }

