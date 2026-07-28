"""Shared helpers for ChronoQG rewrite and verification.

The release pipeline uses :mod:`eval_benchmark` as the only rewrite and
verification entry point.  This module intentionally contains only small helper
functions reused by that pipeline.
"""

from __future__ import annotations

import json


def _clean_text(text: str) -> str:
    """Normalize a short LLM text response."""
    return text.strip().strip('"').strip()


def _parse_json(text: str) -> dict:
    """Parse strict JSON, accepting a fenced ```json block if present."""
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned.removeprefix("```json").strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```").strip()
    if cleaned.endswith("```"):
        cleaned = cleaned.removesuffix("```").strip()
    return json.loads(cleaned)


def _build_cot_scaffold(record: dict) -> str:
    """Build a question-answering scaffold from a benchmark trace.

    The scaffold exposes the reasoning order without revealing intermediate
    answers or the final answer.  It is used by the verifier to answer the
    rewritten question from the provided support facts.
    """
    seed = record.get("seed", {})
    relation = seed.get("fixed_relation_text", "?")
    if seed.get("seed_mode") == "sr":
        base_question = (
            f'Among all entities, which ones are linked from '
            f'{seed.get("fixed_subject_text", "?")} via "{relation}"?'
        )
    else:
        base_question = (
            f'Among all entities, which ones have the relation "{relation}" '
            f'pointing to {seed.get("fixed_object_text", "?")}?'
        )

    lines = [f"Sub-question 1: {base_question}"]
    for index, entry in enumerate(record.get("agent_trace", []), start=2):
        clause = str(entry.get("output", "")).strip()
        if entry.get("agent") == "forward_router":
            question = (
                "Starting from your answer to the previous sub-question, "
                f"{clause.lower()}?"
            )
        else:
            question = f"From your previous answer, keep only the entity {clause}?"
        lines.append(f"Sub-question {index}: {question}")

    return "\n".join(lines)
