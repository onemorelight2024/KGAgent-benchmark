"""Training-free SGSH prompt adapter."""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from kgagent.benchmark.tools.llm import chat_json

logger = logging.getLogger(__name__)


SKELETON_PROMPTS = {
    "en": """You generate question skeletons for knowledge graph benchmark generation.

Given a support graph and target answer, produce one concise skeleton that
preserves the intended question form while hiding content words with "_".

Rules:
1. Do not reveal the answer.
2. Keep question words, auxiliaries, prepositions, and useful temporal words.
3. Use "_" for entities, relations, and content spans.
4. Match the requested output language.
5. End the skeleton with "?" or "？".
6. Return ONLY valid JSON: {"skeleton": "...", "rationale": "..."}""",
    "zh": """你负责为知识图谱 benchmark 生成问题骨架。

给定支持图和目标答案，生成一个简洁的问题骨架。骨架要保留问题形式，
但用 "_" 隐去实体、关系和其他内容词。

规则：
1. 不要泄露答案。
2. 保留疑问词、助词、介词以及必要的时间表达。
3. 用 "_" 替代实体、关系和内容片段。
4. 输出中文问题骨架。
5. 骨架以 "？" 或 "?" 结尾。
6. 只返回合法 JSON：{"skeleton": "...", "rationale": "..."}""",
}


QUESTION_PROMPTS = {
    "en": """You generate a natural-language question from a knowledge graph support graph.

Use the provided skeleton as a wording constraint. The question must be answerable
from the support graph and must not reveal the target answer.

Rules:
1. Ask about the target answer, not an intermediate entity.
2. Mention only entities, relations, and times supported by the graph.
3. Avoid KG jargon such as "entity", "relation", "triple", "subgraph", and "hop".
4. Produce one fluent question in the requested output language.
5. If the requested language is Chinese, write natural Chinese and end with "？" or "?".
6. Return ONLY valid JSON: {"question": "...", "notes": "..."}""",
    "zh": """你负责根据知识图谱支持图生成自然语言问题。

请把给定骨架作为措辞约束。问题必须能从支持图中回答，且不能泄露目标答案。

规则：
1. 问题要询问目标答案，不要询问中间实体。
2. 只提及支持图中出现的实体、关系和时间。
3. 避免使用“实体”“关系”“三元组”“子图”“跳数”等知识图谱术语。
4. 生成一个自然流畅的中文问题。
5. 问题以 "？" 或 "?" 结尾。
6. 只返回合法 JSON：{"question": "...", "notes": "..."}""",
}


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
    workers = max(1, min(parallelism, len(items))) if items else 1
    logger.info("SGSH batch started: size=%s model=%s parallelism=%s workers=%s", len(items), model, parallelism, workers)

    def _run_one(index: int, item: dict[str, Any]) -> dict[str, Any]:
        try:
            context = _format_item_context(item)
            language = _normalize_language(item.get("language", "en"))
            skeleton_payload = chat_json(
                base_url=base_url,
                api_key=api_key,
                model=skeleton_model,
                system_prompt=SKELETON_PROMPTS[language],
                user_prompt=context,
                temperature=0.2,
            )
            skeleton = _clean_question_like_text(skeleton_payload.get("skeleton", "_ ?"))
            question_payload = chat_json(
                base_url=base_url,
                api_key=api_key,
                model=question_model,
                system_prompt=QUESTION_PROMPTS[language],
                user_prompt=_format_question_user_prompt(context, skeleton, language),
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
            fallback_question = _fallback_question(item)
            return {
                "sample_id": item.get("sample_id", f"item_{index}"),
                "generated_question": fallback_question,
                "answer": item.get("answer", {"text": "", "type": "entity", "id": None}),
                "subgraph": item.get("subgraph") or item.get("temporal_subgraph", {}),
                "constraints": item.get("constraints", {}),
                "source": item.get("source", {}),
                "graph_type": item.get("graph_type", "KG"),
                "method": "sgsh_prompt",
                "metadata": {
                    "fallback_used": True,
                    "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                },
            }

    with ThreadPoolExecutor(max_workers=workers) as pool:
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
        or result.get("metadata", {}).get("fallback_used")
    )
    logger.info("SGSH batch completed: total=%s success=%s errors=%s", len(results), len(results) - errors, errors)
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
    language = _language_label(item.get("language", "en"))
    lines = [
        f"Output language: {language}",
        "",
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


def _format_question_user_prompt(context: str, skeleton: str, language: str) -> str:
    if language == "zh":
        return f"{context}\n\n问题骨架：{skeleton}\n请生成最终问题。"
    return f"{context}\n\nSkeleton: {skeleton}\nGenerate the final question."


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
    if cleaned and not cleaned.endswith(("?", "？")):
        cleaned += "?"
    return cleaned


def _reveals_answer(question: str, answer: dict[str, Any]) -> bool:
    answer_text = str(answer.get("text", "")).strip().lower()
    return bool(answer_text and answer_text in question.lower())


def _fallback_question(item: dict[str, Any]) -> str:
    if item.get("language") == "zh":
        return _fallback_question_zh(item)

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


def _fallback_question_zh(item: dict[str, Any]) -> str:
    answer = item.get("answer", {})
    answer_id = answer.get("id")
    support = item.get("subgraph") or item.get("temporal_subgraph", {})

    if "facts" in support:
        fact = support.get("facts", [{}])[-1]
        return (
            f"{fact.get('subject', '该主体')}在什么时候"
            f"{_relation_text(fact.get('relation', '关联'))}"
            f"{fact.get('object', '该对象')}？"
        )

    nodes = support.get("nodes", [])
    edges = support.get("edges", [])
    node_names = {node.get("id", ""): node.get("name", node.get("id", "")) for node in nodes}
    if not edges:
        return "目标答案是什么？"

    edge = next((edge for edge in reversed(edges) if edge.get("target") == answer_id), edges[-1])
    source = node_names.get(edge.get("source", ""), edge.get("source", "该主体"))
    target = node_names.get(edge.get("target", ""), edge.get("target", "该对象"))
    relation = _relation_text(edge.get("relation", "关联"))

    if edge.get("target") == answer_id:
        return f"{source}的{relation}是什么？"
    return f"谁与{target}存在{relation}关系？"


def _relation_text(relation: str) -> str:
    return str(relation).replace("_", " ")


def _language_label(language: str) -> str:
    return "Chinese" if language == "zh" else "English"


def _normalize_language(language: Any) -> str:
    return "zh" if language == "zh" else "en"
