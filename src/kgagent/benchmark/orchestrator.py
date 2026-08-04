"""Benchmark workflow orchestration."""

from __future__ import annotations

import json
import logging
import hashlib
import re
from pathlib import Path
from typing import Any

from kgagent.benchmark.tools.formatter import format_benchmark
from kgagent.benchmark.tools.graph_normalizer import load_and_normalize_kg
from kgagent.benchmark.tools.language import detect_graph_language
from kgagent.benchmark.tools.method_io import save_method_input_jsonl, samples_to_method_input
from kgagent.benchmark.tools.llm import resolve_model
from kgagent.benchmark.methods.chronoqg_adapter import run_chronoqg
from kgagent.benchmark.methods.prompt_methods import run_prompt_method
from kgagent.benchmark.methods.sgsh_prompt_adapter import run_sgsh_prompt
from kgagent.benchmark.tools.run_state import (
    BenchmarkRunState,
    append_jsonl,
    completed_by_sample_id,
    iter_batches,
    load_jsonl as load_run_jsonl,
    write_jsonl,
)
from kgagent.benchmark.tools.subgraph_sampler import sample_subgraphs
from kgagent.benchmark.tools.temporal_graph_normalizer import load_and_normalize_tkg

logger = logging.getLogger(__name__)
ENABLED_KG_METHODS = {"sgsh_prompt", "role_agent_qg"}
ENABLED_TKG_METHODS = {"chronoqg"}
DEFAULT_MAX_BATCH_SIZE = 50


async def run_benchmark(
    *,
    data: str,
    graph_type: str = "KG",
    task: str = "KGQA",
    method: str | None = None,
    sample_count: int = 5,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    work_dir: str | Path = ".",
    output_dir: str | Path | None = None,
    output_path: str | Path | None = None,
    temperature: float = 0.7,
    parallelism: int = 4,
    seed: int = 42,
    run_id: str | None = None,
    resume: bool = True,
    batch_size: int | None = None,
    language: str | None = "auto",
) -> dict[str, Any]:
    """Run a complete benchmark generation workflow."""
    workspace = Path(work_dir)
    outputs = Path(output_dir) if output_dir else workspace / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)

    resolved_model = resolve_model(model)
    normalized_task = _normalize_task(task, graph_type)
    normalized_graph_type = "TKG" if graph_type.upper() == "TKG" else "KG"
    normalized_method = _normalize_method(method, normalized_graph_type)
    run_id = run_id or _default_run_id(
        data=data,
        workspace=workspace,
        task=normalized_task,
        graph_type=normalized_graph_type,
        method=normalized_method,
    )
    run_dir = outputs / "benchmark_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    state_path = run_dir / "run_state.json"
    samples_path = run_dir / "samples.jsonl"
    method_input_path = run_dir / "method_input.jsonl"
    method_output_path = run_dir / "method_output.jsonl"
    final_path = Path(output_path) if output_path else run_dir / "final.jsonl"
    effective_batch_size = max(1, min(batch_size if batch_size is not None else sample_count, DEFAULT_MAX_BATCH_SIZE))

    if resume and state_path.exists():
        state = BenchmarkRunState.load(state_path)
        if state.sample_count != sample_count:
            raise ValueError(
                "Existing benchmark checkpoint uses sample_count="
                f"{state.sample_count}, but this request asks for sample_count={sample_count}. "
                "Use the same sample count to resume, use a different input file/run_id, "
                "or run fresh without resume."
            )
        logger.info("Resuming benchmark run: run_id=%s method_done=%s", run_id, state.progress.get("method_done", 0))
    else:
        state = BenchmarkRunState(
            run_id=run_id,
            task=normalized_task,
            graph_type=normalized_graph_type,
            method=normalized_method,
            model=resolved_model,
            sample_count=sample_count,
        )
        if method_output_path.exists():
            method_output_path.unlink()
        state.save(state_path)

    logger.info(
        "Benchmark started: run_id=%s task=%s graph_type=%s method=%s model=%s sample_count=%s batch_size=%s",
        run_id,
        normalized_task,
        normalized_graph_type,
        normalized_method,
        resolved_model,
        sample_count,
        effective_batch_size,
    )

    if graph_type.upper() == "TKG":
        loaded, samples = _load_or_sample_tkg(data, workspace, sample_count, samples_path, state)
    else:
        loaded, samples = _load_or_sample_kg(data, workspace, sample_count, seed, samples_path, state)
    normalized_language = _resolve_benchmark_language(language, loaded["graph"])
    state.save(state_path)

    if normalized_method == "chronoqg":
        method_inputs = samples_to_method_input(samples, task=normalized_task, language=normalized_language)
        save_method_input_jsonl(method_inputs, method_input_path)
        state.stages["build_method_input"] = "done"
        state.progress["method_input"] = len(method_inputs)
        state.save(state_path)
        method_result = _run_chronoqg_method(
            tkg=loaded["graph"],
            method_output_path=method_output_path,
            run_dir=run_dir,
            model=resolved_model,
            base_url=base_url,
            api_key=api_key,
            parallelism=parallelism,
            sample_count=sample_count,
            resume=resume,
            language=normalized_language,
            state=state,
            state_path=state_path,
        )
        samples = method_result["items"]
    else:
        samples = samples_to_method_input(samples, task=normalized_task, language=normalized_language)
        save_method_input_jsonl(samples, method_input_path)
        state.stages["build_method_input"] = "done"
        state.progress["method_input"] = len(samples)
        state.save(state_path)

        method_result = _run_method_in_batches(
            samples=samples,
            method=normalized_method,
            method_output_path=method_output_path,
            run_dir=run_dir,
            model=resolved_model,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            parallelism=parallelism,
            batch_size=effective_batch_size,
            resume=resume,
            state=state,
            state_path=state_path,
        )

    state.stages["format_benchmark"] = "running"
    state.save(state_path)
    logger.info("Formatting benchmark: run_id=%s output=%s", run_id, final_path)
    formatted = format_benchmark(
        method_result["items"],
        benchmark_type=normalized_task,
        output_path=final_path,
    )
    state.stages["format_benchmark"] = "done"
    state.progress["formatted"] = formatted["stats"]["total"]
    state.status = "done"
    state.save(state_path)
    logger.info(
        "Benchmark completed: run_id=%s total=%s valid=%s output=%s",
        run_id,
        formatted["stats"]["total"],
        formatted["stats"]["valid"],
        final_path,
    )

    return {
        "benchmark_type": normalized_task,
        "graph_type": normalized_graph_type,
        "method": normalized_method,
        "model": resolved_model,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "run_state_path": str(state_path),
        "batch_size": effective_batch_size,
        "language": normalized_language,
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


def _normalize_method(method: str | None, graph_type: str) -> str:
    if method is None or not str(method).strip():
        return "chronoqg" if graph_type == "TKG" else "sgsh_prompt"
    value = str(method).strip().lower()
    mapping = {
        "d": "sgsh_prompt",
        "sgsh": "sgsh_prompt",
        "sgsh_prompt": "sgsh_prompt",
        "sgsh prompt": "sgsh_prompt",
        "a": "sgsh_prompt",
        "b": "role_agent_qg",
        "roleagent": "role_agent_qg",
        "roleagentqg": "role_agent_qg",
        "role_agent_qg": "role_agent_qg",
        "role agent qg": "role_agent_qg",
        "c": "chronoqg",
        "chrono": "chronoqg",
        "chronoqg": "chronoqg",
        "chrono_qg": "chronoqg",
        "chrono qg": "chronoqg",
    }
    normalized = mapping.get(value, value)
    enabled = ENABLED_TKG_METHODS if graph_type == "TKG" else ENABLED_KG_METHODS
    if normalized not in enabled:
        raise ValueError(
            f"Unsupported {graph_type} benchmark method in this release: {method}. "
            f"Enabled methods: {', '.join(sorted(enabled))}"
        )
    return normalized


def _resolve_benchmark_language(language: str | None, graph: Any) -> str:
    if language in ("zh", "en"):
        return language
    return detect_graph_language(graph)


def _default_run_id(
    *,
    data: str,
    workspace: Path,
    task: str,
    graph_type: str,
    method: str,
) -> str:
    """Build a stable run id so repeated runs can discover checkpoints."""
    source = _stable_data_source(data, workspace)
    digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]
    stem = _safe_run_id_stem(source)
    return f"{task.lower()}_{graph_type.lower()}_{method}_{stem}_{digest}"


def _stable_data_source(data: str, workspace: Path) -> str:
    path = Path(data)
    resolved = path if path.is_absolute() else workspace / path
    if resolved.exists():
        return str(resolved.resolve())
    return data.strip()


def _safe_run_id_stem(source: str) -> str:
    if source.startswith("{") or source.startswith("["):
        return "inline_json"
    stem = Path(source).stem or "input"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._-")
    return safe[:48] or "input"


def _load_or_sample_kg(
    data: str,
    workspace: Path,
    sample_count: int,
    seed: int,
    samples_path: Path,
    state: BenchmarkRunState,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    logger.info("Loading KG graph")
    loaded = load_and_normalize_kg(data, workspace)
    state.stages["load_graph"] = "done"
    if samples_path.exists() and state.stages.get("sample_subgraphs") == "done":
        samples = load_run_jsonl(samples_path)
        logger.info("Loaded existing KG samples: count=%s", len(samples))
    else:
        state.stages["sample_subgraphs"] = "running"
        sampled = sample_subgraphs(loaded["graph"], sample_count=sample_count, seed=seed)
        samples = sampled["samples"]
        write_jsonl(samples_path, samples)
        state.stages["sample_subgraphs"] = "done"
        logger.info("Sampled KG subgraphs: count=%s warnings=%s", len(samples), sampled.get("warnings", []))
    state.progress["sampled"] = len(samples)
    return loaded, samples


def _load_or_sample_tkg(
    data: str,
    workspace: Path,
    sample_count: int,
    samples_path: Path,
    state: BenchmarkRunState,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    logger.info("Loading TKG graph")
    loaded = load_and_normalize_tkg(data, workspace)
    state.stages["load_graph"] = "done"
    if samples_path.exists() and state.stages.get("sample_subgraphs") == "done":
        samples = load_run_jsonl(samples_path)
        logger.info("Loaded existing TKG samples: count=%s", len(samples))
    else:
        state.stages["sample_subgraphs"] = "running"
        samples = _sample_temporal_items(loaded["graph"], sample_count)
        write_jsonl(samples_path, samples)
        state.stages["sample_subgraphs"] = "done"
        logger.info("Sampled TKG facts: count=%s", len(samples))
    state.progress["sampled"] = len(samples)
    return loaded, samples


def _run_method_in_batches(
    *,
    samples: list[dict[str, Any]],
    method: str,
    method_output_path: Path,
    run_dir: Path,
    model: str,
    base_url: str | None,
    api_key: str | None,
    temperature: float,
    parallelism: int,
    batch_size: int,
    resume: bool,
    state: BenchmarkRunState,
    state_path: Path,
) -> dict[str, Any]:
    completed = completed_by_sample_id(method_output_path) if resume else {}
    pending = [item for item in samples if item.get("sample_id") not in completed]
    batches = iter_batches(pending, batch_size)
    state.stages["run_method"] = "running"
    state.progress["method_done"] = len(completed)
    state.progress["batch_total"] = len(batches)
    state.progress["batch_done"] = 0
    state.save(state_path)
    logger.info(
        "Running benchmark method: method=%s total=%s completed=%s pending=%s batches=%s",
        method,
        len(samples),
        len(completed),
        len(pending),
        len(batches),
    )

    for batch_index, batch in enumerate(batches, start=1):
        batch_path = run_dir / f"method_batch_{batch_index:04d}.jsonl"
        logger.info("Running method batch: method=%s batch=%s/%s size=%s", method, batch_index, len(batches), len(batch))
        batch_result = _run_one_method_batch(
            method=method,
            batch=batch,
            output_path=batch_path,
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            parallelism=parallelism,
        )
        append_jsonl(method_output_path, batch_result["items"])
        for row in batch_result["items"]:
            sample_id = row.get("sample_id")
            if sample_id:
                completed[str(sample_id)] = row
        state.progress["method_done"] = len(completed)
        state.progress["batch_done"] = batch_index
        state.save(state_path)

    ordered_outputs = [completed[item["sample_id"]] for item in samples if item.get("sample_id") in completed]
    write_jsonl(method_output_path, ordered_outputs)
    state.stages["run_method"] = "done"
    state.progress["method_done"] = len(ordered_outputs)
    state.save(state_path)
    return {
        "items": ordered_outputs,
        "output_path": str(method_output_path),
        "method": method,
        "stats": {
            "total": len(ordered_outputs),
            "success": len(ordered_outputs),
            "errors": sum(1 for row in ordered_outputs if str(row.get("generated_question", "")).startswith("[error:")),
        },
    }


def _run_chronoqg_method(
    *,
    tkg: dict[str, Any],
    method_output_path: Path,
    run_dir: Path,
    model: str,
    base_url: str | None,
    api_key: str | None,
    parallelism: int,
    sample_count: int,
    resume: bool,
    language: str,
    state: BenchmarkRunState,
    state_path: Path,
) -> dict[str, Any]:
    state.stages["run_method"] = "running"
    state.save(state_path)
    if resume and method_output_path.exists():
        existing = load_run_jsonl(method_output_path)
        if existing:
            logger.info("Loaded existing ChronoQG outputs: count=%s", len(existing))
            state.stages["run_method"] = "done"
            state.progress["method_done"] = len(existing)
            state.save(state_path)
            return {
                "items": existing,
                "output_path": str(method_output_path),
                "method": "chronoqg",
                "stats": {"total": len(existing), "success": len(existing), "errors": 0},
            }

    logger.info("Running ChronoQG method: sample_count=%s model=%s", sample_count, model)
    result = run_chronoqg(
        tkg,
        run_dir / "chronoqg",
        per_code=max(1, sample_count),
        model=model,
        base_url=base_url,
        api_key=api_key,
        parallelism=parallelism,
        language=language,
        resume=resume,
    )
    items = result["items"][:sample_count]
    for item in items:
        item["language"] = language
        item.setdefault("metadata", {})["language"] = language
    write_jsonl(method_output_path, items)
    state.stages["run_method"] = "done"
    state.progress["method_done"] = len(items)
    state.progress["batch_total"] = 1
    state.progress["batch_done"] = 1
    state.save(state_path)
    return {
        "items": items,
        "output_path": str(method_output_path),
        "method": "chronoqg",
        "stats": {
            **result.get("stats", {}),
            "total": len(items),
            "success": len(items),
            "errors": 0,
        },
    }


def _run_one_method_batch(
    *,
    method: str,
    batch: list[dict[str, Any]],
    output_path: Path,
    model: str,
    base_url: str | None,
    api_key: str | None,
    temperature: float,
    parallelism: int,
) -> dict[str, Any]:
    if method == "sgsh_prompt":
        return run_sgsh_prompt(
            batch,
            output_path,
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            parallelism=parallelism,
        )
    return run_prompt_method(
        batch,
        output_path,
        method=method,
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
    )


def _sample_temporal_items(tkg: dict[str, Any], sample_count: int) -> list[dict[str, Any]]:
    facts = tkg.get("temporal_facts", [])
    entities_by_id = {
        entity.get("id"): entity
        for entity in tkg.get("entities", [])
        if entity.get("id")
    }
    items: list[dict[str, Any]] = []
    for idx, fact in enumerate(facts[:sample_count]):
        time_info = fact.get("time", {})
        answer_text = ""
        answer_type = "time"
        if time_info.get("type") == "interval":
            answer_text = f"{time_info.get('start', '')} to {time_info.get('end', '')}"
        else:
            answer_text = time_info.get("value", "")
        subject = str(fact.get("subject", ""))
        obj = str(fact.get("object", ""))
        temporal_subgraph = {
            "nodes": [
                _temporal_node(subject, entities_by_id),
                _temporal_node(obj, entities_by_id),
            ],
            "edges": [
                {
                    "id": fact.get("id"),
                    "source": subject,
                    "relation": fact.get("relation", ""),
                    "target": obj,
                    "time": time_info,
                }
            ],
            "facts": [fact],
        }
        items.append(
            {
                "sample_id": f"temporal_sample_{idx:06d}",
                "task": "temporal_KGQG",
                "graph_type": "TKG",
                "temporal_subgraph": temporal_subgraph,
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


def _temporal_node(entity_id: str, entities_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    entity = entities_by_id.get(entity_id, {})
    return {
        "id": entity_id,
        "name": entity.get("name", entity_id),
        "type": entity.get("type", "Entity"),
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load JSONL for debugging or tests."""
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows
