"""Benchmark workflow orchestration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kgagent.benchmark.formatter import format_benchmark
from kgagent.benchmark.graph_normalizer import load_and_normalize_kg
from kgagent.benchmark.method_io import save_method_input_jsonl, samples_to_method_input
from kgagent.benchmark.methods.chronoqg_adapter import run_chronoqg
from kgagent.benchmark.methods.prompt_methods import run_prompt_method
from kgagent.benchmark.methods.sgsh_prompt_adapter import run_sgsh_prompt
from kgagent.benchmark.subgraph_sampler import sample_subgraphs
from kgagent.benchmark.temporal_graph_normalizer import load_and_normalize_tkg


async def run_benchmark(
    *,
    data: str,
    graph_type: str = "KG",
    task: str = "KGQA",
    method: str = "sgsh_prompt",
    sample_count: int = 5,
    model: str = "gpt-4o-mini",
    base_url: str | None = None,
    api_key: str | None = None,
    work_dir: str | Path = ".",
    output_dir: str | Path | None = None,
    output_path: str | Path | None = None,
    temperature: float = 0.7,
    parallelism: int = 4,
    seed: int = 42,
) -> dict[str, Any]:
    """Run a complete benchmark generation workflow."""
    workspace = Path(work_dir)
    outputs = Path(output_dir) if output_dir else workspace / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)

    normalized_method = _normalize_method(method)

    normalized_task = _normalize_task(task, graph_type)
    if graph_type.upper() == "TKG":
        loaded = load_and_normalize_tkg(data, workspace)
        if normalized_method == "chronoqg":
            method_result = run_chronoqg(
                loaded["graph"],
                outputs / "chronoqg",
                mode="tc1",
                per_code=sample_count,
                model=model,
                base_url=base_url,
                api_key=api_key,
                parallelism=parallelism,
            )
            final_path = (
                Path(output_path)
                if output_path
                else outputs / f"{normalized_task.lower()}_benchmark_chronoqg.jsonl"
            )
            formatted = format_benchmark(
                method_result["items"],
                benchmark_type=normalized_task,
                output_path=final_path,
            )
            return {
                "benchmark_type": normalized_task,
                "graph_type": "TKG",
                "method": normalized_method,
                "model": model,
                "input_stats": loaded["stats"],
                "sample_count": len(method_result["items"]),
                "method_input_path": None,
                "method_output_path": method_result["output_path"],
                "output_path": str(final_path),
                "stats": formatted["stats"],
            }
        samples = _sample_temporal_items(loaded["graph"], sample_count)
    else:
        loaded = load_and_normalize_kg(data, workspace)
        sampled = sample_subgraphs(loaded["graph"], sample_count=sample_count, seed=seed)
        samples = samples_to_method_input(sampled["samples"], task=normalized_task)

    method_input_path = outputs / "benchmark_method_input.jsonl"
    method_output_path = outputs / f"benchmark_{normalized_method}_raw.jsonl"
    final_path = Path(output_path) if output_path else outputs / f"{normalized_task.lower()}_benchmark_{normalized_method}.jsonl"

    save_method_input_jsonl(samples, method_input_path)
    if normalized_method == "sgsh_prompt":
        method_result = run_sgsh_prompt(
            samples,
            method_output_path,
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            parallelism=parallelism,
        )
    else:
        method_result = run_prompt_method(
            samples,
            method_output_path,
            method=normalized_method,
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
        )
    formatted = format_benchmark(
        method_result["items"],
        benchmark_type=normalized_task,
        output_path=final_path,
    )

    return {
        "benchmark_type": normalized_task,
        "graph_type": "TKG" if graph_type.upper() == "TKG" else "KG",
        "method": normalized_method,
        "model": model,
        "input_stats": loaded["stats"],
        "sample_count": len(samples),
        "method_input_path": str(method_input_path),
        "method_output_path": str(method_output_path),
        "output_path": str(final_path),
        "stats": formatted["stats"],
    }


def _normalize_task(task: str, graph_type: str) -> str:
    task_upper = (task or "KGQA").upper()
    if graph_type.upper() == "TKG" and not task_upper.startswith("TEMPORAL_"):
        return f"temporal_{task_upper}"
    return task_upper


def _normalize_method(method: str) -> str:
    value = (method or "sgsh_prompt").strip().lower()
    mapping = {
        "d": "sgsh_prompt",
        "sgsh": "sgsh_prompt",
        "sgsh_prompt": "sgsh_prompt",
        "sgsh prompt": "sgsh_prompt",
        "a": "role_agent_qg",
        "roleagent": "role_agent_qg",
        "roleagentqg": "role_agent_qg",
        "role_agent_qg": "role_agent_qg",
        "role agent qg": "role_agent_qg",
        "b": "kqg_cot_plus",
        "kqg": "kqg_cot_plus",
        "kqg_cot_plus": "kqg_cot_plus",
        "kqg-cot+": "kqg_cot_plus",
        "c": "r2dqg_prompt",
        "r2dqg": "r2dqg_prompt",
        "r2dqg_prompt": "r2dqg_prompt",
        "chronoqg": "chronoqg",
        "chrono_qg": "chronoqg",
    }
    normalized = mapping.get(value, value)
    if normalized not in {
        "sgsh_prompt",
        "role_agent_qg",
        "kqg_cot_plus",
        "r2dqg_prompt",
        "chronoqg",
    }:
        raise ValueError(f"Unsupported benchmark method: {method}")
    return normalized


def _sample_temporal_items(tkg: dict[str, Any], sample_count: int) -> list[dict[str, Any]]:
    facts = tkg.get("temporal_facts", [])
    items: list[dict[str, Any]] = []
    for idx, fact in enumerate(facts[:sample_count]):
        time_info = fact.get("time", {})
        answer_text = ""
        answer_type = "time"
        if time_info.get("type") == "interval":
            answer_text = f"{time_info.get('start', '')} to {time_info.get('end', '')}"
        else:
            answer_text = time_info.get("value", "")
        items.append(
            {
                "sample_id": f"temporal_sample_{idx:06d}",
                "task": "temporal_KGQG",
                "graph_type": "TKG",
                "temporal_subgraph": {"facts": [fact]},
                "answer": {"text": answer_text, "type": answer_type, "id": fact.get("id")},
                "constraints": {
                    "temporal_question_type": "time",
                    "hop": 1,
                    "difficulty": "easy",
                },
                "source": {
                    "graph_id": tkg.get("graph_id", "tkg_001"),
                    "sample_strategy": "temporal_fact",
                },
            }
        )
    return items


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load JSONL for debugging or tests."""
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows
