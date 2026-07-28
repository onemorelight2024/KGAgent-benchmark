"""Prompt-based adapters for static benchmark generation methods."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kgagent.benchmark.llm import chat_json
from kgagent.benchmark.methods.sgsh_prompt_adapter import _fallback_question, _reveals_answer


METHOD_SPECS: dict[str, dict[str, str]] = {
    "role_agent_qg": {
        "name": "RoleAgentQG",
        "system": """You are a collaborative editorial team for KG benchmark question generation.
Act as editor-in-chief, writer, content editor, and copy editor in one concise pass.
Generate one natural question that is answerable from the support graph.
Do not reveal the answer. Do not use KG jargon. Return ONLY JSON:
{"question": "...", "review_notes": "..."}""",
    },
    "kqg_cot_plus": {
        "name": "KQG-CoT+",
        "system": """You generate KG benchmark questions using concise hidden reasoning.
First infer the reasoning path from the support graph, then write one final natural question.
The final question must be answerable from the support graph and must not reveal the answer.
Do not expose chain-of-thought; put only a short rationale. Return ONLY JSON:
{"question": "...", "rationale": "..."}""",
    },
    "r2dqg_prompt": {
        "name": "R2DQG",
        "system": """You generate diverse KG benchmark questions with a draft-and-refine process.
Create a question template internally, draft a question, then correct semantic drift.
The final question must preserve relation direction, be answerable from the support graph,
and must not reveal the answer. Return ONLY JSON:
{"question": "...", "template_style": "...", "revision": "..."}""",
    },
}


def run_prompt_method(
    items: list[dict[str, Any]],
    output_jsonl: Path,
    *,
    method: str,
    model: str,
    base_url: str | None,
    api_key: str | None,
    temperature: float = 0.7,
) -> dict[str, Any]:
    """Run a prompt-only method adapter."""
    if method not in METHOD_SPECS:
        raise ValueError(f"Unsupported prompt method: {method}")

    spec = METHOD_SPECS[method]
    results: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        try:
            payload = chat_json(
                base_url=base_url,
                api_key=api_key,
                model=model,
                system_prompt=spec["system"],
                user_prompt=_format_prompt(item, spec["name"]),
                temperature=temperature,
                max_tokens=700,
            )
            question = str(payload.get("question", "")).strip()
            if not question or _reveals_answer(question, item.get("answer", {})):
                question = _fallback_question(item)
            results.append(_build_output(item, question, method, payload))
        except Exception as exc:
            fallback = _fallback_question(item)
            results.append(
                _build_output(
                    item,
                    fallback,
                    method,
                    {"adapter_error": f"{type(exc).__name__}: {str(exc)[:300]}"},
                    index=index,
                )
            )

    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with output_jsonl.open("w", encoding="utf-8") as f:
        for result in results:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")

    return {
        "items": results,
        "output_path": str(output_jsonl),
        "method": method,
        "stats": {
            "total": len(results),
            "success": len(results),
            "errors": sum(1 for r in results if "adapter_error" in r.get("metadata", {})),
        },
    }


def _build_output(
    item: dict[str, Any],
    question: str,
    method: str,
    metadata: dict[str, Any],
    index: int = 0,
) -> dict[str, Any]:
    return {
        "sample_id": item.get("sample_id", f"item_{index}"),
        "generated_question": question,
        "answer": item.get("answer", {}),
        "subgraph": item.get("subgraph") or item.get("temporal_subgraph", {}),
        "constraints": item.get("constraints", {}),
        "source": item.get("source", {}),
        "graph_type": item.get("graph_type", "KG"),
        "method": method,
        "metadata": metadata,
    }


def _format_prompt(item: dict[str, Any], method_name: str) -> str:
    answer = item.get("answer", {})
    constraints = item.get("constraints", {})
    return "\n".join(
        [
            f"Method: {method_name}",
            f"Task: {item.get('task', 'KGQA')}",
            "Support graph:",
            _format_support(item.get("subgraph") or item.get("temporal_subgraph", {})),
            "",
            f"Target answer: {answer.get('text', '')}",
            f"Answer type: {answer.get('type', 'entity')}",
            f"Difficulty: {constraints.get('difficulty', 'medium')}",
            f"Hop count: {constraints.get('hop', 1)}",
        ]
    )


def _format_support(support: dict[str, Any]) -> str:
    if "facts" in support:
        lines = []
        for fact in support.get("facts", []):
            time = fact.get("time", {})
            time_text = time.get("value") or f"{time.get('start', '')} to {time.get('end', '')}"
            lines.append(
                f"- {fact.get('subject', '')} -- {fact.get('relation', '')} "
                f"--> {fact.get('object', '')} @ {time_text}"
            )
        return "\n".join(lines)

    nodes = support.get("nodes", [])
    edges = support.get("edges", [])
    names = {node.get("id", ""): node.get("name", node.get("id", "")) for node in nodes}
    lines = []
    for edge in edges:
        source = names.get(edge.get("source", ""), edge.get("source", ""))
        target = names.get(edge.get("target", ""), edge.get("target", ""))
        lines.append(f"- {source} -- {edge.get('relation', '')} --> {target}")
    return "\n".join(lines)
