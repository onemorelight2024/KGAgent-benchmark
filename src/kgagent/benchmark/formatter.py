"""Benchmark formatter and validators."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kgagent.benchmark.types import BenchmarkItem, BenchmarkMetadata, QualityInfo, Reasoning


def format_benchmark(
    method_outputs: list[dict],
    benchmark_type: str = "KGQG",
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Format method outputs into final benchmark format.

    Args:
        method_outputs: List of method output dicts
        benchmark_type: Type of benchmark (KGQG, KGQA, etc.)
        output_path: Optional path to save formatted benchmark

    Returns:
        Dict with 'benchmark' (list of BenchmarkItem) and 'stats'
    """
    benchmark: list[BenchmarkItem] = []

    for idx, output in enumerate(method_outputs):
        # Extract fields from method output
        sample_id = output.get("sample_id", output.get("benchmark_id", f"benchmark_{idx:06d}"))
        question = output.get("generated_question", output.get("question", ""))
        answer = output.get("answer", {})
        method = output.get("method", "unknown")

        # Get supporting graph (may need to reconstruct from original sample)
        supporting_graph = output.get("subgraph", output.get("supporting_graph", {"nodes": [], "edges": []}))
        graph_type = output.get("graph_type", "KG")

        # Extract reasoning info
        hop = output.get("hop", output.get("constraints", {}).get("hop", 1))
        difficulty = output.get("difficulty", output.get("constraints", {}).get("difficulty", "medium"))

        # Create benchmark item
        item = BenchmarkItem(
            id=sample_id,
            benchmark_type=benchmark_type,
            graph_type=graph_type,
            question=question,
            answer=answer,
            supporting_graph=supporting_graph,
            reasoning=Reasoning(
                hop=hop,
                difficulty=difficulty,
                chain=[],
            ),
            metadata=BenchmarkMetadata(
                method=method,
                source_graph=output.get("source", {}).get("graph_id", "unknown"),
                created_by="KGAgent",
                version="0.1.0",
            ),
            quality=QualityInfo(
                valid=True,
                warnings=[],
            ),
        )

        benchmark.append(item)

    # Validate all items
    for item in benchmark:
        validation = validate_benchmark_item(item)
        item["quality"]["valid"] = validation["valid"]
        item["quality"]["warnings"] = validation["warnings"]

    # Save if path provided
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as f:
            for item in benchmark:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    # Compute stats
    stats = {
        "total": len(benchmark),
        "valid": sum(1 for item in benchmark if item["quality"]["valid"]),
        "invalid": sum(1 for item in benchmark if not item["quality"]["valid"]),
        "hop_distribution": _compute_distribution(benchmark, lambda x: str(x["reasoning"]["hop"])),
        "difficulty_distribution": _compute_distribution(benchmark, lambda x: x["reasoning"]["difficulty"]),
        "method_distribution": _compute_distribution(benchmark, lambda x: x["metadata"]["method"]),
    }

    return {
        "benchmark": benchmark,
        "stats": stats,
        "output_path": str(output_path) if output_path else None,
    }


def validate_benchmark_item(item: BenchmarkItem) -> dict[str, Any]:
    """Validate a single benchmark item.

    Returns:
        Dict with 'valid' (bool) and 'warnings' (list)
    """
    warnings: list[str] = []
    question = item.get("question", "")

    # Check required fields
    if not item.get("id"):
        warnings.append("Missing id")

    if not item.get("question") or not isinstance(item["question"], str):
        warnings.append("Missing or invalid question")
    elif len(item["question"].strip()) == 0:
        warnings.append("Empty question")
    elif item["question"].startswith("[error:"):
        warnings.append("Question generation failed")

    if not item.get("answer"):
        warnings.append("Missing answer")
    elif not item["answer"].get("text"):
        warnings.append("Empty answer text")
    elif question and str(item["answer"].get("text", "")).strip().lower() in question.lower():
        warnings.append("Question reveals answer")

    if not item.get("supporting_graph"):
        warnings.append("Missing supporting_graph")
    elif isinstance(item["supporting_graph"], dict):
        if item.get("graph_type") == "TKG":
            facts = item["supporting_graph"].get("facts", [])
            if len(facts) == 0:
                warnings.append("Empty temporal supporting facts")
        else:
            nodes = item["supporting_graph"].get("nodes", [])
            edges = item["supporting_graph"].get("edges", [])
            if len(nodes) == 0:
                warnings.append("Empty supporting graph nodes")
            if len(edges) == 0:
                warnings.append("Empty supporting graph edges")

    # Check question quality
    if question and not question.strip().endswith("?"):
        warnings.append("Question does not end with '?'")

    kg_jargon = ("knowledge graph", "entity", "relation", "triple", "subgraph", "hop")
    if any(term in question.lower() for term in kg_jargon):
        warnings.append("Question contains KG jargon")

    valid = len(warnings) == 0

    return {
        "valid": valid,
        "warnings": warnings,
    }


def validate_benchmark(
    benchmark: list[BenchmarkItem],
) -> dict[str, Any]:
    """Validate entire benchmark.

    Returns:
        Dict with validation results and summary
    """
    all_valid = True
    all_warnings: list[str] = []
    id_set: set[str] = set()
    duplicate_ids: list[str] = []

    for item in benchmark:
        # Validate individual item
        validation = validate_benchmark_item(item)
        if not validation["valid"]:
            all_valid = False
        all_warnings.extend(validation["warnings"])

        # Check for duplicate IDs
        item_id = item.get("id", "")
        if item_id in id_set:
            duplicate_ids.append(item_id)
            all_valid = False
        else:
            id_set.add(item_id)

    if duplicate_ids:
        all_warnings.append(f"Duplicate IDs found: {', '.join(duplicate_ids)}")

    return {
        "valid": all_valid,
        "total_items": len(benchmark),
        "valid_items": sum(1 for item in benchmark if item["quality"]["valid"]),
        "warnings": all_warnings,
        "warning_count": len(all_warnings),
    }


def _compute_distribution(benchmark: list[BenchmarkItem], key_fn) -> dict[str, int]:
    """Compute distribution for a given key function."""
    dist: dict[str, int] = {}
    for item in benchmark:
        try:
            key = key_fn(item)
            dist[key] = dist.get(key, 0) + 1
        except (KeyError, TypeError):
            pass
    return dist
