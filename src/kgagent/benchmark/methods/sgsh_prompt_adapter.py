"""Training-free SGSH prompt adapter."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from kgagent.benchmark.llm import chat_json


SKELETON_PROMPT = """You generate question skeletons for knowledge graph benchmark generation.

Given a support graph and target answer, produce one concise skeleton that
preserves the intended question form while hiding content words with "_".

Rules:
1. Do not reveal the answer.
2. Keep question words, auxiliaries, prepositions, and useful temporal words.
3. Use "_" for entities, relations, and content spans.
4. End the skeleton with "?".
5. Return ONLY valid JSON: {"skeleton": "...", "rationale": "..."}"""


QUESTION_PROMPT = """You generate a natural-language question from a knowledge graph support graph.

Use the provided skeleton as a wording constraint. The question must be answerable
from the support graph and must not reveal the target answer.

Rules:
1. Ask about the target answer, not an intermediate entity.
2. Mention only entities, relations, and times supported by the graph.
3. Avoid KG jargon such as "entity", "relation", "triple", "subgraph", and "hop".
4. Produce one fluent question ending with "?".
5. Return ONLY valid JSON: {"question": "...", "notes": "..."}"""


def run_sgsh_prompt(
    items: list[dict[str, Any]],
    output_jsonl: Path,
    *,
    model: str,
    base_url: str | None,
    api_key: str | None,
    skeleton_model: str | None = None,
    question_model: str | None = None,
    temperature: float = 0.7,
    parallelism: int = 4,
) -> dict[str, Any]:
    """Run the SGSH prompt method on benchmark method input items."""
    skeleton_model = skeleton_model or model
    question_model = question_model or model
    results: list[dict[str, Any]] = [{} for _ in items]

    def _run_one(index: int, item: dict[str, Any]) -> dict[str, Any]:
        try:
            context = _format_item_context(item)
            skeleton_payload = chat_json(
                base_url=base_url,
                api_key=api_key,
                model=skeleton_model,
                system_prompt=SKELETON_PROMPT,
                user_prompt=context,
                temperature=0.2,
            )
            skeleton = _clean_question_like_text(skeleton_payload.get("skeleton", "_ ?"))
            question_payload = chat_json(
                base_url=base_url,
                api_key=api_key,
                model=question_model,
                system_prompt=QUESTION_PROMPT,
                user_prompt=f"{context}\n\nSkeleton: {skeleton}\nGenerate the final question.",
                temperature=temperature,
            )
            question = _clean_question_like_text(question_payload.get("question", ""))
            if not question or _reveals_answer(question, item.get("answer", {})):
                question = _fallback_question(item)

            return {
                "sample_id": item["sample_id"],
                "generated_question": question,
                "answer": item["answer"],
                "subgraph": item.get("subgraph") or item.get("temporal_subgraph", {}),
                "constraints": item.get("constraints", {}),
                "source": item.get("source", {}),
                "graph_type": item.get("graph_type", "KG"),
                "method": "sgsh_prompt",
                "metadata": {
                    "skeleton": skeleton,
                    "skeleton_model": skeleton_model,
                    "question_model": question_model,
                    "skeleton_rationale": skeleton_payload.get("rationale", ""),
                    "question_notes": question_payload.get("notes", ""),
                },
            }
        except Exception as exc:
            return {
                "sample_id": item.get("sample_id", f"item_{index}"),
                "generated_question": f"[error: {type(exc).__name__}: {str(exc)[:300]}]",
                "answer": item.get("answer", {"text": "", "type": "entity", "id": None}),
                "subgraph": item.get("subgraph") or item.get("temporal_subgraph", {}),
                "constraints": item.get("constraints", {}),
                "source": item.get("source", {}),
                "graph_type": item.get("graph_type", "KG"),
                "method": "sgsh_prompt",
                "metadata": {"error": str(exc)},
            }

    with ThreadPoolExecutor(max_workers=max(1, parallelism)) as pool:
        futures = {pool.submit(_run_one, idx, item): idx for idx, item in enumerate(items)}
        for future in as_completed(futures):
            results[futures[future]] = future.result()

    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with output_jsonl.open("w", encoding="utf-8") as f:
        for result in results:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")

    errors = sum(
        1 for result in results
        if str(result.get("generated_question", "")).startswith("[error:")
    )
    return {
        "items": results,
        "output_path": str(output_jsonl),
        "method": "sgsh_prompt",
        "stats": {"total": len(results), "success": len(results) - errors, "errors": errors},
    }


def _format_item_context(item: dict[str, Any]) -> str:
    support = item.get("subgraph") or item.get("temporal_subgraph", {})
    answer = item.get("answer", {})
    constraints = item.get("constraints", {})
    lines = [
        "Support graph:",
        _format_support_graph(support),
        "",
        f"Target answer: {answer.get('text', '')}",
        f"Answer type: {answer.get('type', 'entity')}",
        f"Hop count: {constraints.get('hop', 1)}",
        f"Difficulty: {constraints.get('difficulty', 'medium')}",
    ]
    temporal_type = constraints.get("temporal_question_type")
    if temporal_type:
        lines.append(f"Temporal question type: {temporal_type}")
    return "\n".join(lines)


def _format_support_graph(support: dict[str, Any]) -> str:
    if "facts" in support:
        lines = ["Temporal facts:"]
        for fact in support.get("facts", []):
            time = fact.get("time", {})
            if time.get("type") == "interval":
                time_text = f"{time.get('start', '')} to {time.get('end', '')}"
            else:
                time_text = time.get("value", "")
            lines.append(
                f"- {fact.get('subject', '')} -- {fact.get('relation', '')} "
                f"--> {fact.get('object', '')} @ {time_text}"
            )
        return "\n".join(lines)

    nodes = support.get("nodes", [])
    edges = support.get("edges", [])
    node_names = {node.get("id", ""): node.get("name", node.get("id", "")) for node in nodes}
    lines: list[str] = []
    if nodes:
        lines.append("Nodes:")
        for node in nodes:
            label = node.get("name", node.get("id", ""))
            node_type = node.get("type", "")
            lines.append(f"- {label}" + (f" ({node_type})" if node_type else ""))
    if edges:
        lines.append("Edges:")
        for edge in edges:
            source = node_names.get(edge.get("source", ""), edge.get("source", ""))
            target = node_names.get(edge.get("target", ""), edge.get("target", ""))
            lines.append(f"- {source} -- {edge.get('relation', '')} --> {target}")
    return "\n".join(lines) if lines else "(empty graph)"


def _clean_question_like_text(text: str) -> str:
    cleaned = " ".join(str(text).strip().split())
    if cleaned and not cleaned.endswith("?"):
        cleaned += "?"
    return cleaned


def _reveals_answer(question: str, answer: dict[str, Any]) -> bool:
    answer_text = str(answer.get("text", "")).strip().lower()
    return bool(answer_text and answer_text in question.lower())


def _fallback_question(item: dict[str, Any]) -> str:
    answer = item.get("answer", {})
    answer_id = answer.get("id")
    support = item.get("subgraph") or item.get("temporal_subgraph", {})

    if "facts" in support:
        fact = support.get("facts", [{}])[-1]
        return (
            f"When did {fact.get('subject', 'the subject')} "
            f"{_relation_text(fact.get('relation', 'relate to'))} "
            f"{fact.get('object', 'the object')}?"
        )

    nodes = support.get("nodes", [])
    edges = support.get("edges", [])
    node_names = {node.get("id", ""): node.get("name", node.get("id", "")) for node in nodes}
    if not edges:
        return "What is the target answer?"

    edge = next((edge for edge in reversed(edges) if edge.get("target") == answer_id), edges[-1])
    source = node_names.get(edge.get("source", ""), edge.get("source", "the source"))
    target = node_names.get(edge.get("target", ""), edge.get("target", "the target"))
    relation = edge.get("relation", "")

    if edge.get("target") == answer_id:
        return _question_for_target_answer(source, relation)
    return _question_for_source_answer(relation, target)


def _question_for_target_answer(source: str, relation: str) -> str:
    rel = relation.lower()
    if rel == "founded":
        return f"What organization did {source} found?"
    if rel == "works_at":
        return f"Where does {source} work?"
    if rel == "invested_in":
        return f"What organization did {source} invest in?"
    if rel == "located_in":
        return f"Where is {source} located?"
    if rel == "member_of":
        return f"Which organization is {source} a member of?"
    if rel == "president_of":
        return f"Which country did {source} serve as president of?"
    return f"What is the object of {source}'s {_relation_text(relation)}?"


def _question_for_source_answer(relation: str, target: str) -> str:
    rel = relation.lower()
    if rel == "founded":
        return f"Who founded {target}?"
    if rel == "works_at":
        return f"Who works at {target}?"
    if rel == "invested_in":
        return f"Who invested in {target}?"
    if rel == "located_in":
        return f"What is located in {target}?"
    return f"Who is associated with {target} through {_relation_text(relation)}?"


def _relation_text(relation: str) -> str:
    return str(relation).replace("_", " ")
