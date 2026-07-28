"""ChronoQG adapter for temporal benchmark generation."""

from __future__ import annotations

import json
import os
import shutil
import sys
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any


CHRONOQG_PATH = Path(__file__).parent / "chronoqg"
CHRONOQG_MODULE_PATH = CHRONOQG_PATH / "chrono_qg"


def run_chronoqg(
    tkg: dict[str, Any],
    output_dir: Path,
    *,
    mode: str = "tc1",
    time_granularity: str = "auto",
    per_code: int = 5,
    model: str = "gpt-4o-mini",
    base_url: str | None = None,
    api_key: str | None = None,
    parallelism: int = 4,
) -> dict[str, Any]:
    """Run ChronoQG against a normalized TKG."""
    from kgagent.benchmark.chronoqg_converter import (
        convert_tkg_to_chronoqg_format,
        estimate_time_granularity,
    )

    if str(CHRONOQG_MODULE_PATH) not in sys.path:
        sys.path.insert(0, str(CHRONOQG_MODULE_PATH))
    if str(CHRONOQG_PATH) not in sys.path:
        sys.path.insert(0, str(CHRONOQG_PATH))

    if time_granularity == "auto":
        time_granularity = estimate_time_granularity(tkg)

    output_dir.mkdir(parents=True, exist_ok=True)
    input_dir = output_dir / "chronoqg_input"
    chronoqg_output_dir = output_dir / "chronoqg_output"
    chronoqg_output_dir.mkdir(parents=True, exist_ok=True)

    file_paths = convert_tkg_to_chronoqg_format(
        tkg=tkg,
        output_dir=input_dir,
        time_granularity=time_granularity,
    )

    config_path = output_dir / "chronoqg_config.json"
    config = {
        "kg_path": str(file_paths["kg_file"]),
        "entity_map_path": str(file_paths["entity_map"]),
        "relation_map_path": str(file_paths["relation_map"]),
        "relation_type_tsv": None,
        "output_dir": str(chronoqg_output_dir),
        "time_granularity": time_granularity,
        "per_code": per_code,
        "rewrite_model": model,
        "answer_model": model,
        "judge_model": model,
        "parallelism": parallelism,
    }
    if base_url:
        config["api_base_url"] = base_url
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")

    from main import _run_build, _run_sample, _run_verify
    from tkgqg_config import PipelineConfig

    cfg = PipelineConfig.load(config_path)
    old_openai_key = os.environ.get("OPENAI_API_KEY")
    old_llm_base = os.environ.get("LLM_BASE_URL")
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key
    if base_url:
        os.environ["LLM_BASE_URL"] = base_url

    run_log = StringIO()
    try:
        with redirect_stdout(run_log), redirect_stderr(run_log):
            _run_sample(cfg)
            _run_build(cfg)
            verified_dir = _run_verify(cfg, tc_mode=mode)
    finally:
        if old_openai_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = old_openai_key
        if old_llm_base is None:
            os.environ.pop("LLM_BASE_URL", None)
        else:
            os.environ["LLM_BASE_URL"] = old_llm_base

    verified_path = verified_dir / "dataset.jsonl"
    fallback_preverify = False
    if _count_jsonl(verified_path) == 0:
        verified_path = chronoqg_output_dir / "benchmark" / f"benchmark_{mode}.jsonl"
        fallback_preverify = True
    final_output = output_dir / f"chronoqg_benchmark_{mode}.jsonl"
    shutil.copy(verified_path, final_output)
    log_path = output_dir / f"chronoqg_{mode}_run.log"
    log_path.write_text(run_log.getvalue(), encoding="utf-8")

    rows = _load_jsonl(final_output)
    return {
        "items": [
            _convert_chronoqg_row(row, index, tkg, fallback_preverify=fallback_preverify)
            for index, row in enumerate(rows)
        ],
        "output_path": str(final_output),
        "method": "chronoqg",
        "stats": {
            "total": len(rows),
            "success": len(rows),
            "errors": 0,
            "time_granularity": time_granularity,
            "mode": mode,
            "chronoqg_log": str(log_path),
            "fallback_preverify": fallback_preverify,
        },
    }


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def _convert_chronoqg_row(
    row: dict[str, Any],
    index: int,
    tkg: dict[str, Any],
    *,
    fallback_preverify: bool = False,
) -> dict[str, Any]:
    question = (
        row.get("question")
        or row.get("rewritten_question")
        or row.get("natural_question")
        or row.get("final_question")
        or row.get("raw_question")
        or row.get("gold_question")
        or row.get("original_question_en")
        or row.get("query")
        or ""
    )
    answer_text = row.get("answer") or row.get("gold_answer") or row.get("final_answer") or ""
    if isinstance(answer_text, dict):
        answer_text = answer_text.get("text", "")
    facts = (
        row.get("facts")
        or row.get("subgraph_facts")
        or row.get("participating_events")
        or row.get("subgraph")
    )
    if not isinstance(facts, list) or not facts:
        facts = tkg.get("temporal_facts", [])[:1]
    return {
        "sample_id": str(row.get("id") or row.get("benchmark_id") or f"chronoqg_{index:06d}"),
        "generated_question": _naturalize_question(str(question)),
        "answer": {"text": str(answer_text), "type": _answer_type(row), "id": None},
        "subgraph": {"facts": facts},
        "constraints": {
            "temporal_question_type": row.get("tc_code") or row.get("template_id") or "temporal",
            "hop": 1,
            "difficulty": row.get("difficulty", "medium"),
        },
        "source": {
            "graph_id": tkg.get("graph_id", "tkg_001"),
            "sample_strategy": "chronoqg",
        },
        "graph_type": "TKG",
        "method": "chronoqg",
        "metadata": {**row, "chronoqg_fallback": "preverify" if fallback_preverify else "verified"},
    }


def _naturalize_question(question: str) -> str:
    cleaned = " ".join(question.strip().split())
    cleaned = cleaned.replace("Which entity", "Who")
    cleaned = cleaned.replace("which entity", "who")
    cleaned = cleaned.replace(" has the relation ", " has the connection ")
    cleaned = cleaned.replace(" relation ", " connection ")
    cleaned = cleaned.replace('"president_of"', "president_of")
    cleaned = cleaned.replace('"member_of"', "member_of")
    cleaned = cleaned.replace(" period period", " period")
    cleaned = cleaned.replace("the the", "the")
    if cleaned and not cleaned.endswith("?"):
        cleaned += "?"
    return cleaned


def _answer_type(row: dict[str, Any]) -> str:
    answer = row.get("answer") or row.get("gold_answer") or row.get("final_answer")
    if isinstance(answer, dict) and answer.get("id"):
        return "entity"
    return "time" if row.get("focus_allen_code") else "entity"
