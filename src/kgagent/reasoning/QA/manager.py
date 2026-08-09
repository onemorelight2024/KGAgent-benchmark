from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import hashlib
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

from kgagent.reasoning.QA.methods import REASONING_METHODS

_LOCAL_EMBEDDING_MODELS: dict[tuple[str, str | None], Any] = {}
_MANIFEST_NAME = ".kgagent_reasoning_manifest.json"
_GRAPHRAG_TEXT_EXTENSIONS = {".txt", ".md", ".markdown"}
_GRAPHRAG_STRUCTURED_DOC_EXTENSIONS = {".json", ".jsonl", ".parquet"}
_GRAPHRAG_SUPPORTED_EXTENSIONS = _GRAPHRAG_TEXT_EXTENSIONS | _GRAPHRAG_STRUCTURED_DOC_EXTENSIONS
_RAG_ANYTHING_KGAGENT_PARSER_NAME = "kgagent_mineru"
_RAG_ANYTHING_KGAGENT_PARSER_REGISTERED = False

def list_reasoning_methods() -> list[dict[str, Any]]:
    return [dict(method) for method in REASONING_METHODS]

def recommend_reasoning_methods(task_type: str, input_path: str | None = None) -> list[dict[str, Any]]:
    input_hint = _input_type_hint(input_path) if input_path else None
    candidates = [
        dict(method)
        for method in REASONING_METHODS
        if task_type in method["task_types"]
    ]
    if input_hint is None:
        return [
            {
                **method,
                "recommended": False,
                "recommendation_reason": "No input path was provided, so method choice depends on the user's stated modality and preference.",
            }
            for method in candidates
        ]

    ranked = sorted(candidates, key=lambda method: _reasoning_recommendation_rank(method["name"], input_hint))
    return [
        {
            **method,
            "recommended": _reasoning_recommendation_rank(method["name"], input_hint) == 0,
            "recommendation_reason": _reasoning_recommendation_reason(method["name"], input_hint),
        }
        for method in ranked
    ]


def _normalize_qa_job_input(input_data: Mapping[str, Any]) -> dict[str, Any]:
    method_options = input_data.get("method_options", {})
    if not isinstance(method_options, Mapping):
        method_options = {}

    input_paths = _normalize_input_paths(input_data.get("input_paths"), input_data.get("input_path"))
    input_path = input_paths[0] if len(input_paths) == 1 else ""
    input_kind = str(input_data.get("input_kind") or _infer_input_kind_from_path(input_path)).strip().lower()
    questions_path = str(input_data.get("questions_path") or "").strip()
    question = str(input_data.get("question") or "").strip()
    save_kg_json = bool(
        input_data.get("save_kg_json")
        or input_data.get("export_kg_json")
        or method_options.get("save_kg_json")
        or method_options.get("export_kg_json")
    )
    kg_output_path = str(
        input_data.get("kg_output_path")
        or input_data.get("graph_output_path")
        or method_options.get("kg_output_path")
        or method_options.get("graph_output_path")
        or ""
    ).strip()

    return {
        **dict(input_data),
        "task_type": "qa",
        "method": str(input_data.get("method") or "auto").strip().lower() or "auto",
        "input_path": input_path,
        "input_paths": [str(path) for path in input_paths],
        "input_kind": input_kind,
        "question": question,
        "questions_path": questions_path,
        "working_dir": str(input_data.get("working_dir") or "").strip(),
        "reuse_existing": bool(input_data.get("reuse_existing", True)),
        "build_if_missing": bool(input_data.get("build_if_missing", True)),
        "save_kg_json": save_kg_json,
        "kg_output_path": kg_output_path,
        "method_options": dict(method_options),
    }


def _ensure_reasoning_dependencies(method_name: str) -> None:
    requirements_path = Path(__file__).with_name("requirements-reasoning.txt")
    requirements_hint = (
        f" You can also install the shared QA dependencies from `{requirements_path}`."
    )

    if method_name == "graphrag":
        executable = shutil.which("graphrag")
        if executable is None:
            raise RuntimeError(
                "GraphRAG is not installed or its CLI is not on PATH. "
                "Install it with `pip install graphrag` and make sure the `graphrag` command is available."
                + requirements_hint
            )
        return

    if method_name == "rag_anything":
        missing_modules: list[str] = []
        for module_name in ["raganything", "lightrag"]:
            if importlib.util.find_spec(module_name) is None:
                missing_modules.append(module_name)
        if missing_modules:
            raise RuntimeError(
                "RAG-Anything dependencies are missing: "
                f"{', '.join(missing_modules)}. "
                "Install them with `pip install raganything lightrag-hku` "
                "or `pip install raganything[all]` for broader document support."
                + requirements_hint
            )
        return


def run_rag_anything_qa_for_agent(input_data: Mapping[str, Any], workspace: str | Path | None = None) -> dict[str, Any]:
    _ensure_reasoning_dependencies("rag_anything")
    normalized_input = _normalize_qa_job_input(input_data)
    batch_result = _maybe_run_qa_batch(normalized_input, workspace=workspace, method_name="rag_anything")
    if batch_result is not None:
        return batch_result
    return asyncio.run(_run_rag_anything_qa(normalized_input, workspace=workspace))


def run_graphrag_qa_for_agent(
    input_data: Mapping[str, Any],
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    _ensure_reasoning_dependencies("graphrag")
    normalized_input = _normalize_qa_job_input(input_data)
    batch_result = _maybe_run_qa_batch(normalized_input, workspace=workspace, method_name="graphrag")
    if batch_result is not None:
        return batch_result
    question = _resolve_question_text(normalized_input, workspace=workspace)
    source_input_paths = _resolve_input_sources(normalized_input, workspace)
    source_input_path = source_input_paths[0]
    reuse_existing = bool(normalized_input.get("reuse_existing", True))
    build_if_missing = bool(normalized_input.get("build_if_missing", True))
    method_options = normalized_input.get("method_options", {})
    if not isinstance(method_options, Mapping):
        method_options = {}

    working_dir = _resolve_working_dir(
        normalized_input.get("working_dir"),
        input_path=source_input_path,
        input_paths=source_input_paths,
        workspace=workspace,
        method_name="graphrag",
    )
    had_existing_storage = _has_graphrag_storage(working_dir)
    prepared_input_path = _prepare_reasoning_input_bundle(
        input_paths=source_input_paths,
        working_dir=working_dir,
        workspace=workspace,
        method_options=method_options,
    )
    if not (had_existing_storage and reuse_existing):
        prepared_input_path = _prepare_graphrag_input(
            input_path=prepared_input_path,
        input_kind=_infer_input_kind_from_path(str(prepared_input_path)),
        working_dir=working_dir,
        workspace=workspace,
        method_options=method_options,
        )
    built_index = False
    input_type = "text"
    _write_reasoning_manifest(
        working_dir,
        method="graphrag",
        input_type=input_type,
        input_path=prepared_input_path,
        question=question,
        status="ready" if had_existing_storage and reuse_existing else "building",
        method_options=method_options,
        paths={
            "source_input_path": str(source_input_path),
            "source_input_paths": json.dumps([str(path) for path in source_input_paths], ensure_ascii=False),
            "normalized_input_path": str(prepared_input_path),
        },
    )

    if had_existing_storage and reuse_existing:
        pass
    elif build_if_missing:
        _run_graphrag_command(
            [
                "init",
                "-r",
                str(working_dir),
                "-f",
            ],
            cwd=working_dir,
            method_options=method_options,
        )
        _write_graphrag_env(working_dir, method_options=method_options)
        _patch_graphrag_settings(working_dir, method_options=method_options)
        _prepare_graphrag_workspace(
            input_path=prepared_input_path,
            working_dir=working_dir,
            method_options=method_options,
        )
        _run_graphrag_command(
            ["index", "-r", str(working_dir), "-m", str(method_options.get("index_method", "standard"))],
            cwd=working_dir,
            method_options=method_options,
        )
        _write_graphrag_ready_marker(working_dir, input_path=input_path)
        built_index = True
        _write_reasoning_manifest(
            working_dir,
            method="graphrag",
                input_type=input_type,
                input_path=prepared_input_path,
                question=question,
                status="ready",
                method_options=method_options,
                paths={
                    "source_input_path": str(source_input_path),
                    "source_input_paths": json.dumps([str(path) for path in source_input_paths], ensure_ascii=False),
                    "normalized_input_path": str(prepared_input_path),
                },
            )
    else:
        raise ValueError(
            f"No existing GraphRAG storage found at {working_dir}; "
            "set build_if_missing=true or provide an existing working_dir."
        )

    query_method = str(method_options.get("query_method", "global"))
    query_result = _run_graphrag_command(
        ["query", "-q", question, "-r", str(working_dir), "-m", query_method],
        cwd=working_dir,
        method_options=method_options,
    )

    kg_output_path = _maybe_export_method_kg_json(
        method_name="graphrag",
        input_data=normalized_input,
        method_options=method_options,
        input_path=input_path,
        working_dir=working_dir,
    )
    manifest_paths = {"kg_output_path": str(kg_output_path)} if kg_output_path else None
    manifest_summary = {"kg_exported": bool(kg_output_path)}
    if kg_output_path:
        _write_reasoning_manifest(
            working_dir,
            method="graphrag",
            input_type=input_type,
            input_path=prepared_input_path,
            question=question,
            status="ready",
            method_options=method_options,
            paths={
                "source_input_path": str(input_path),
                "normalized_input_path": str(prepared_input_path),
                **(manifest_paths or {}),
            },
            summary=manifest_summary,
        )

    return {
        "task_type": "qa",
        "method": "graphrag",
        "input_path": str(prepared_input_path),
        "input_paths": [str(path) for path in source_input_paths],
        "input_kind": _infer_input_kind_from_path(str(prepared_input_path)),
        "input_type": input_type,
        "answer": _clean_graphrag_answer(query_result.stdout),
        "evidence": [],
        "working_dir": str(working_dir),
        "storage_id": _storage_id(working_dir),
        "reused_existing": bool(had_existing_storage and reuse_existing and not built_index),
        "built_index": built_index,
        "status": "success",
        "error": "",
        "kg_exported": bool(kg_output_path),
        "kg_output_path": str(kg_output_path) if kg_output_path else "",
        "summary": {
            "evidence_count": 0,
            "kg_exported": bool(kg_output_path),
        },
    }

async def run_rag_anything_qa_for_agent_async(
    input_data: Mapping[str, Any],
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    _ensure_reasoning_dependencies("rag_anything")
    normalized_input = _normalize_qa_job_input(input_data)
    batch_result = _maybe_run_qa_batch(normalized_input, workspace=workspace, method_name="rag_anything")
    if batch_result is not None:
        return batch_result
    return await _run_rag_anything_qa(normalized_input, workspace=workspace)


async def _run_rag_anything_qa(
    input_data: Mapping[str, Any],
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    try:
        from lightrag import LightRAG
        from lightrag.llm.openai import openai_complete_if_cache, openai_embed
        from lightrag.utils import EmbeddingFunc
        from raganything import RAGAnything, RAGAnythingConfig
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "RAG-Anything is not installed. Install it with `pip install raganything` "
            "or `pip install raganything[all]` for broader document support."
        ) from exc

    normalized_input = _normalize_qa_job_input(input_data)
    question = _resolve_question_text(normalized_input, workspace=workspace)
    source_input_paths = _resolve_input_sources(normalized_input, workspace)
    input_path = source_input_paths[0]
    reuse_existing = bool(normalized_input.get("reuse_existing", True))
    build_if_missing = bool(normalized_input.get("build_if_missing", True))
    method_options = normalized_input.get("method_options", {})
    if not isinstance(method_options, Mapping):
        method_options = {}

    working_dir = _resolve_working_dir(
        normalized_input.get("working_dir"),
        input_path=input_path,
        input_paths=source_input_paths,
        workspace=workspace,
        method_name="rag_anything",
    )
    output_dir = Path(method_options.get("output_dir") or working_dir / "parsed").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    had_existing_storage = _has_existing_storage(working_dir)
    built_index = False

    if had_existing_storage and not reuse_existing and build_if_missing:
        _clear_reasoning_working_dir(working_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        had_existing_storage = False

    prepared_input_path = _prepare_reasoning_input_bundle(
        input_paths=source_input_paths,
        working_dir=working_dir,
        workspace=workspace,
        method_options=method_options,
    )
    auto_text_ingest = _should_auto_text_ingest_for_rag_anything(prepared_input_path)
    input_type = _rag_anything_input_type(prepared_input_path)
    _write_reasoning_manifest(
        working_dir=working_dir,
        method="rag_anything",
        input_type=input_type,
        input_path=prepared_input_path,
        question=question,
        status="ready" if had_existing_storage and reuse_existing else "building",
        method_options=method_options,
        paths={
            "parsed_dir": str(output_dir),
            "source_input_path": str(input_path),
            "source_input_paths": json.dumps([str(path) for path in source_input_paths], ensure_ascii=False),
            "normalized_input_path": str(prepared_input_path),
        },
    )

    rag: Any | None = None
    try:
        with _third_party_log_scope(method_options):
            rag = _build_rag_anything(
                RAGAnything=RAGAnything,
                RAGAnythingConfig=RAGAnythingConfig,
                EmbeddingFunc=EmbeddingFunc,
                LightRAG=LightRAG,
                openai_complete_if_cache=openai_complete_if_cache,
                openai_embed=openai_embed,
                working_dir=working_dir,
                method_options=method_options,
            )

        text_only = bool(method_options.get("text_only", False))
        direct_text_ingest = bool(
            text_only
            or auto_text_ingest
            or prepared_input_path.suffix.lower() in {".txt", ".md", ".markdown", ".json", ".jsonl", ".parquet"}
        )
        recovered_after_build = False
        with _third_party_log_scope(method_options):
            await _initialize_lightrag_if_available(rag)

        if had_existing_storage and reuse_existing:
            pass
        elif build_if_missing:
            recovered_from_parsed = False
            try:
                if direct_text_ingest:
                    with _third_party_log_scope(method_options):
                        await _insert_text_file_content(
                            rag=rag,
                            input_path=prepared_input_path,
                            method_options=method_options,
                            question=question,
                        )
                else:
                    with _third_party_log_scope(method_options):
                        await rag.process_document_complete(
                            file_path=str(prepared_input_path),
                            output_dir=str(output_dir),
                            parse_method=str(method_options.get("parse_method", "auto")),
                            **_rag_anything_parser_kwargs(method_options),
                        )
            except Exception as exc:
                recovery_error: Exception | None = None
                if not direct_text_ingest:
                    try:
                        with _third_party_log_scope(method_options):
                            recovered_from_parsed = await _recover_rag_anything_from_parsed_output(
                                rag=rag,
                                input_path=prepared_input_path,
                                output_dir=output_dir,
                                working_dir=working_dir,
                                method_options=method_options,
                            )
                    except Exception as recovery_exc:
                        recovery_error = recovery_exc
                if not recovered_from_parsed and not direct_text_ingest:
                    await _finalize_rag_anything_instance(rag)
                    rag = None
                    try:
                        text_fallback_rag = await _rebuild_rag_anything_from_parsed_text(
                            RAGAnything=RAGAnything,
                            RAGAnythingConfig=RAGAnythingConfig,
                            EmbeddingFunc=EmbeddingFunc,
                            LightRAG=LightRAG,
                            openai_complete_if_cache=openai_complete_if_cache,
                            openai_embed=openai_embed,
                            input_path=prepared_input_path,
                            output_dir=output_dir,
                            working_dir=working_dir,
                            method_options=method_options,
                        )
                        if text_fallback_rag is not None:
                            rag = text_fallback_rag
                            recovered_from_parsed = True
                    except Exception as text_fallback_exc:
                        if recovery_error is None:
                            recovery_error = text_fallback_exc
                        else:
                            recovery_error = RuntimeError(f"{recovery_error} | Parsed-text fallback failed: {text_fallback_exc}")
                if recovered_from_parsed:
                    built_index = True
                else:
                    detail = _rag_anything_failure_detail(
                        exc=exc,
                        output_dir=output_dir,
                        working_dir=working_dir,
                    )
                    if recovery_error is not None:
                        detail = (
                            f"{detail} | Recovery from parsed output failed: "
                            f"{_rag_anything_failure_detail(exc=recovery_error, output_dir=output_dir, working_dir=working_dir)}"
                        )
                    _write_reasoning_manifest(
                        working_dir=working_dir,
                        method="rag_anything",
                        input_type=input_type,
                        input_path=input_path,
                        question=question,
                        status="failed",
                        method_options=method_options,
                        paths={"parsed_dir": str(output_dir)},
                    )
                    raise RuntimeError(f"RAG-Anything failed while building document storage: {detail}") from exc
            if not _has_rag_anything_content(working_dir) and not direct_text_ingest:
                try:
                    with _third_party_log_scope(method_options):
                        recovered_after_build = await _recover_rag_anything_from_parsed_output(
                            rag=rag,
                            input_path=prepared_input_path,
                            output_dir=output_dir,
                            working_dir=working_dir,
                            method_options=method_options,
                        )
                except Exception:
                    recovered_after_build = False
                if not recovered_after_build:
                    await _finalize_rag_anything_instance(rag)
                    rag = None
                    text_fallback_rag = await _rebuild_rag_anything_from_parsed_text(
                        RAGAnything=RAGAnything,
                        RAGAnythingConfig=RAGAnythingConfig,
                        EmbeddingFunc=EmbeddingFunc,
                        LightRAG=LightRAG,
                        openai_complete_if_cache=openai_complete_if_cache,
                        openai_embed=openai_embed,
                        input_path=prepared_input_path,
                        output_dir=output_dir,
                        working_dir=working_dir,
                        method_options=method_options,
                    )
                    if text_fallback_rag is not None:
                        rag = text_fallback_rag
                        recovered_after_build = True
                if recovered_after_build:
                    built_index = True
            if not _has_rag_anything_content(working_dir):
                _write_reasoning_manifest(
                    working_dir=working_dir,
                    method="rag_anything",
                    input_type=input_type,
                    input_path=prepared_input_path,
                    question=question,
                    status="failed",
                    method_options=method_options,
                    paths={
                        "parsed_dir": str(output_dir),
                        "source_input_path": str(input_path),
                        "source_input_paths": json.dumps([str(path) for path in source_input_paths], ensure_ascii=False),
                        "normalized_input_path": str(prepared_input_path),
                    },
                )
                raise RuntimeError(
                    "RAG-Anything did not build a usable LightRAG storage. "
                    "The document parser likely failed before indexing; if MinerU tried to download models, "
                    "pre-download the MinerU/HuggingFace assets or use a text input with method_options.text_only=true."
                )
            _write_ready_marker(working_dir, input_path=prepared_input_path)
            built_index = True
            _write_reasoning_manifest(
                working_dir=working_dir,
                method="rag_anything",
                input_type=input_type,
                input_path=prepared_input_path,
                question=question,
                status="ready",
                method_options=method_options,
                paths={
                    "parsed_dir": str(output_dir),
                    "source_input_path": str(input_path),
                    "source_input_paths": json.dumps([str(path) for path in source_input_paths], ensure_ascii=False),
                    "normalized_input_path": str(prepared_input_path),
                },
            )
        else:
            raise ValueError(
                f"No existing RAG-Anything storage found at {working_dir}; "
                "set build_if_missing=true or provide an existing working_dir."
            )

        query_mode = str(method_options.get("mode", "hybrid"))
        vlm_enhanced = method_options.get("vlm_enhanced", False)
        with _third_party_log_scope(method_options):
            answer = await _query_rag_anything_with_fallbacks(
                rag=rag,
                question=question,
                preferred_mode=query_mode,
                vlm_enhanced=vlm_enhanced,
            )
        if answer is None and not direct_text_ingest:
            await _finalize_rag_anything_instance(rag)
            rag = None
            text_fallback = await _try_rag_anything_parsed_text_fallback(
                RAGAnything=RAGAnything,
                RAGAnythingConfig=RAGAnythingConfig,
                EmbeddingFunc=EmbeddingFunc,
                LightRAG=LightRAG,
                openai_complete_if_cache=openai_complete_if_cache,
                openai_embed=openai_embed,
                question=question,
                input_path=input_path,
                output_dir=output_dir,
                working_dir=working_dir,
                method_options=method_options,
                preferred_mode=query_mode,
                vlm_enhanced=vlm_enhanced,
            )
            if text_fallback is not None:
                rag, answer = text_fallback
        if answer is None:
            raise RuntimeError(
                "RAG-Anything returned None. The existing LightRAG storage may be empty or "
                "incompatible; rerun with reuse_existing=false or use a fresh working_dir."
            )

        kg_output_path = _maybe_export_method_kg_json(
            method_name="rag_anything",
            input_data=normalized_input,
            method_options=method_options,
            input_path=input_path,
            working_dir=working_dir,
        )
        if kg_output_path:
            _write_reasoning_manifest(
                working_dir=working_dir,
                method="rag_anything",
                input_type=input_type,
                input_path=input_path,
                question=question,
                status="ready",
                method_options=method_options,
                paths={
                    "parsed_dir": str(output_dir),
                    "kg_output_path": str(kg_output_path),
                },
                summary={"kg_exported": True},
            )

        return {
            "task_type": "qa",
            "method": "rag_anything",
            "input_path": str(input_path),
            "input_paths": [str(path) for path in source_input_paths],
            "input_kind": str(normalized_input.get("input_kind") or ""),
            "input_type": input_type,
            "answer": str(answer),
            "evidence": [],
            "working_dir": str(working_dir),
            "storage_id": _storage_id(working_dir),
            "reused_existing": bool(had_existing_storage and reuse_existing and not built_index),
            "built_index": built_index,
            "status": "success",
            "error": "",
            "kg_exported": bool(kg_output_path),
            "kg_output_path": str(kg_output_path) if kg_output_path else "",
            "summary": {
                "evidence_count": 0,
                "kg_exported": bool(kg_output_path),
            },
        }
    finally:
        await _finalize_rag_anything_instance(rag)


async def _insert_text_file_content(
    *,
    rag: Any,
    input_path: Path,
    method_options: Mapping[str, Any],
    question: str | None = None,
) -> None:
    text = _load_rag_anything_text_content(
        input_path=input_path,
        method_options=method_options,
        question=question,
    )
    if not text.strip():
        raise ValueError(f"input text content is empty: {input_path}")

    await _initialize_lightrag_if_available(rag)
    lightrag_instance = getattr(rag, "lightrag", None)
    if lightrag_instance is not None:
        if hasattr(lightrag_instance, "ainsert"):
            await lightrag_instance.ainsert(text)
            return
        if hasattr(lightrag_instance, "insert"):
            result = lightrag_instance.insert(text)
            if hasattr(result, "__await__"):
                await result
            return

    await rag.insert_content_list(
        content_list=[
            {
                "type": "text",
                "text": text,
                "page_idx": 0,
            }
        ],
        file_path=str(input_path),
        split_by_character=method_options.get("split_by_character"),
        split_by_character_only=bool(method_options.get("split_by_character_only", False)),
        doc_id=str(method_options.get("doc_id") or input_path.stem),
        display_stats=bool(method_options.get("display_stats", False)),
    )


def _load_rag_anything_text_content(
    *,
    input_path: Path,
    method_options: Mapping[str, Any],
    question: str | None = None,
) -> str:
    suffix = input_path.suffix.lower()
    encoding = str(method_options.get("encoding", "utf-8"))

    if suffix in {".txt", ".md", ".markdown"}:
        return input_path.read_text(encoding=encoding)

    if suffix in _GRAPHRAG_STRUCTURED_DOC_EXTENSIONS:
        return _structured_document_to_text(input_path, method_options=method_options)

    raise ValueError(
        "RAG-Anything direct text ingestion supports .txt, .md, .json, .jsonl, and .parquet inputs."
    )


def _rag_anything_failure_detail(
    *,
    exc: BaseException,
    output_dir: Path,
    working_dir: Path,
) -> str:
    parts: list[str] = [_flatten_exception_messages(exc)]

    diagnostics: list[str] = []
    content_list_path = _find_rag_anything_parsed_content_list(output_dir)
    if content_list_path is not None:
        diagnostics.append(f"parsed_content_list={content_list_path.name}")
    fallback_text_path = output_dir / f"{working_dir.stem.rsplit('_', 1)[0]}_parsed_fallback.txt"
    if fallback_text_path.exists():
        diagnostics.append("parsed_text_fallback=present")

    existing_storage = []
    for name in [
        "kv_store_full_docs.json",
        "kv_store_doc_status.json",
        "kv_store_text_chunks.json",
        "vdb_chunks.json",
        "vdb_entities.json",
        "vdb_relationships.json",
        "graph_chunk_entity_relation.graphml",
    ]:
        path = working_dir / name
        if path.exists() and path.stat().st_size > 0:
            existing_storage.append(name)
    if existing_storage:
        diagnostics.append("storage_files=" + ",".join(existing_storage))

    quota_hint = _detect_quota_hint(exc)
    if quota_hint:
        diagnostics.append(quota_hint)

    if diagnostics:
        parts.append("diagnostics: " + "; ".join(diagnostics))
    return " | ".join(part for part in parts if part)


def _flatten_exception_messages(exc: BaseException) -> str:
    messages: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = str(current).strip()
        if text and text not in messages:
            messages.append(text)
        next_exc = current.__cause__ or current.__context__
        current = next_exc if isinstance(next_exc, BaseException) else None
    return " | ".join(messages) if messages else exc.__class__.__name__


def _detect_quota_hint(exc: BaseException) -> str:
    text = _flatten_exception_messages(exc).lower()
    if "insufficient_user_quota" in text or "quota is not enough" in text:
        return "embedding_quota=insufficient_user_quota"
    if "error code: 403" in text:
        return "embedding_http=403"
    return ""


async def _recover_rag_anything_from_parsed_output(
    *,
    rag: Any,
    input_path: Path,
    output_dir: Path,
    working_dir: Path,
    method_options: Mapping[str, Any],
) -> bool:
    content_list_path = _find_rag_anything_parsed_content_list(output_dir)
    if content_list_path is None:
        return False

    content_list = _load_rag_anything_content_list_from_parsed_output(content_list_path)
    if not content_list:
        return False

    await rag.insert_content_list(
        content_list=content_list,
        file_path=str(input_path),
        split_by_character=method_options.get("split_by_character"),
        split_by_character_only=bool(method_options.get("split_by_character_only", False)),
        doc_id=str(method_options.get("doc_id") or input_path.stem),
        display_stats=bool(method_options.get("display_stats", False)),
    )
    return _has_rag_anything_content(working_dir)


async def _finalize_rag_anything_instance(rag: Any | None) -> None:
    if rag is None:
        return
    close_method = getattr(rag, "close", None)
    if callable(close_method):
        try:
            atexit.unregister(close_method)
        except Exception:
            pass
    finalize_method = getattr(rag, "finalize_storages", None)
    if callable(finalize_method):
        try:
            await finalize_method()
        except Exception:
            pass


async def _query_rag_anything_with_fallbacks(
    *,
    rag: Any,
    question: str,
    preferred_mode: str,
    vlm_enhanced: Any,
) -> str | None:
    modes: list[str] = []
    for candidate in [preferred_mode, "mix", "hybrid", "naive"]:
        mode = str(candidate or "").strip().lower()
        if mode and mode not in modes:
            modes.append(mode)
    for mode in modes:
        answer = await rag.aquery(
            question,
            mode=mode,
            vlm_enhanced=vlm_enhanced,
        )
        if _rag_anything_answer_has_context(answer):
            return answer
    return None


def _rag_anything_answer_has_context(answer: Any) -> bool:
    if not isinstance(answer, str):
        return False
    text = answer.strip()
    if not text:
        return False
    lowered = text.lower()
    if "[no-context]" in lowered:
        return False
    if "not able to provide an answer to that question" in lowered:
        return False
    return True


async def _try_rag_anything_parsed_text_fallback(
    *,
    RAGAnything: Any,
    RAGAnythingConfig: Any,
    EmbeddingFunc: Any,
    LightRAG: Any,
    openai_complete_if_cache: Any,
    openai_embed: Any,
    question: str,
    input_path: Path,
    output_dir: Path,
    working_dir: Path,
    method_options: Mapping[str, Any],
    preferred_mode: str,
    vlm_enhanced: Any,
) -> tuple[Any, str] | None:
    rag = await _rebuild_rag_anything_from_parsed_text(
        RAGAnything=RAGAnything,
        RAGAnythingConfig=RAGAnythingConfig,
        EmbeddingFunc=EmbeddingFunc,
        LightRAG=LightRAG,
        openai_complete_if_cache=openai_complete_if_cache,
        openai_embed=openai_embed,
        input_path=input_path,
        output_dir=output_dir,
        working_dir=working_dir,
        method_options=method_options,
    )
    if rag is None:
        return None
    text_only_options = dict(method_options)
    text_only_options["text_only"] = True
    with _third_party_log_scope(text_only_options):
        answer = await _query_rag_anything_with_fallbacks(
            rag=rag,
            question=question,
            preferred_mode=preferred_mode,
            vlm_enhanced=vlm_enhanced,
        )
    if not isinstance(answer, str) or not answer.strip():
        return None
    return rag, answer


async def _rebuild_rag_anything_from_parsed_text(
    *,
    RAGAnything: Any,
    RAGAnythingConfig: Any,
    EmbeddingFunc: Any,
    LightRAG: Any,
    openai_complete_if_cache: Any,
    openai_embed: Any,
    input_path: Path,
    output_dir: Path,
    working_dir: Path,
    method_options: Mapping[str, Any],
) -> Any | None:
    parsed_text = _load_rag_anything_text_from_parsed_output(output_dir)
    if not parsed_text.strip():
        return None

    _clear_rag_anything_storage_preserve_parsed(working_dir, output_dir=output_dir)
    fallback_text_path = output_dir / f"{input_path.stem}_parsed_fallback.txt"
    fallback_text_path.write_text(parsed_text, encoding="utf-8")

    text_only_options = dict(method_options)
    text_only_options["text_only"] = True

    with _third_party_log_scope(text_only_options):
        rag = _build_rag_anything(
            RAGAnything=RAGAnything,
            RAGAnythingConfig=RAGAnythingConfig,
            EmbeddingFunc=EmbeddingFunc,
            LightRAG=LightRAG,
            openai_complete_if_cache=openai_complete_if_cache,
            openai_embed=openai_embed,
            working_dir=working_dir,
            method_options=text_only_options,
        )
    with _third_party_log_scope(text_only_options):
        await _initialize_lightrag_if_available(rag)
        await _insert_text_file_content(
            rag=rag,
            input_path=fallback_text_path,
            method_options=text_only_options,
            question=None,
        )
    if not _has_rag_anything_content(working_dir):
        return None
    _write_ready_marker(working_dir, input_path=input_path)
    return rag


def _find_rag_anything_parsed_content_list(output_dir: Path) -> Path | None:
    candidates = list(output_dir.rglob("*_content_list_v2.json"))
    if not candidates:
        candidates = list(output_dir.rglob("*_content_list.json"))
    if not candidates:
        return None
    candidates.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
    return candidates[0]


def _load_rag_anything_content_list_from_parsed_output(content_list_path: Path) -> list[dict[str, Any]]:
    payload = json.loads(content_list_path.read_text(encoding="utf-8"))
    base_dir = content_list_path.parent
    normalized: list[dict[str, Any]] = []

    if isinstance(payload, list):
        for page_idx, page_items in enumerate(payload):
            if isinstance(page_items, list):
                for item in page_items:
                    normalized_item = _normalize_mineru_parsed_item(
                        item,
                        page_idx=page_idx,
                        base_dir=base_dir,
                    )
                    if normalized_item is not None:
                        normalized.append(normalized_item)
                continue
            normalized_item = _normalize_mineru_parsed_item(
                page_items,
                page_idx=page_idx,
                base_dir=base_dir,
            )
            if normalized_item is not None:
                normalized.append(normalized_item)
    return normalized


def _load_rag_anything_text_from_parsed_output(output_dir: Path) -> str:
    content_list_path = _find_rag_anything_parsed_content_list(output_dir)
    if content_list_path is None:
        return ""
    content_list = _load_rag_anything_content_list_from_parsed_output(content_list_path)
    lines: list[str] = []
    for item in content_list:
        item_type = str(item.get("type") or "").strip().lower()
        if item_type == "text":
            text = str(item.get("text") or "").strip()
            if text:
                lines.append(text)
        elif item_type == "image":
            captions = [str(v).strip() for v in item.get("image_caption", []) if str(v).strip()]
            footnotes = [str(v).strip() for v in item.get("image_footnote", []) if str(v).strip()]
            block = captions + footnotes
            if block:
                lines.append("\n".join(block))
        elif item_type == "table":
            captions = [str(v).strip() for v in item.get("table_caption", []) if str(v).strip()]
            body = str(item.get("table_body") or "").strip()
            footnotes = [str(v).strip() for v in item.get("table_footnote", []) if str(v).strip()]
            block = captions + ([body] if body else []) + footnotes
            if block:
                lines.append("\n".join(block))
        elif item_type == "equation":
            equation = str(item.get("latex") or item.get("text") or "").strip()
            if equation:
                lines.append(equation)
    return "\n\n".join(line for line in lines if line).strip()


def _normalize_mineru_parsed_item(
    item: Any,
    *,
    page_idx: int,
    base_dir: Path,
) -> dict[str, Any] | None:
    if not isinstance(item, Mapping):
        return None

    item_type = str(item.get("type") or "").strip().lower()
    content = item.get("content")

    if item_type in {"title", "paragraph", "page_header", "page_footer", "page_number"}:
        text = _extract_mineru_text_content(content)
        if not text:
            return None
        return {
            "type": "text",
            "text": text,
            "page_idx": page_idx,
        }

    if item_type == "image":
        image_source = content.get("image_source") if isinstance(content, Mapping) else None
        image_path = _resolve_mineru_asset_path(
            base_dir,
            image_source.get("path") if isinstance(image_source, Mapping) else "",
        )
        return {
            "type": "image",
            "img_path": str(image_path) if image_path else "",
            "image_caption": _extract_mineru_text_list(content.get("image_caption") if isinstance(content, Mapping) else None),
            "image_footnote": _extract_mineru_text_list(content.get("image_footnote") if isinstance(content, Mapping) else None),
            "page_idx": page_idx,
        }

    if item_type == "table":
        table_body = ""
        if isinstance(content, Mapping):
            table_body = str(content.get("html") or content.get("latex") or content.get("content") or "").strip()
        return {
            "type": "table",
            "table_body": table_body,
            "table_caption": _extract_mineru_text_list(content.get("table_caption") if isinstance(content, Mapping) else None),
            "table_footnote": _extract_mineru_text_list(content.get("table_footnote") if isinstance(content, Mapping) else None),
            "page_idx": page_idx,
        }

    if item_type in {"equation", "equation_interline", "equation_inline"}:
        math_content = ""
        math_type = ""
        if isinstance(content, Mapping):
            math_content = str(content.get("math_content") or content.get("text") or "").strip()
            math_type = str(content.get("math_type") or "").strip()
        result = {
            "type": "equation",
            "text": math_content,
            "page_idx": page_idx,
        }
        if math_type:
            result["text_format"] = math_type
        if math_type == "latex" and math_content:
            result["latex"] = math_content
        return result if math_content else None

    text = _extract_mineru_text_content(content)
    if not text:
        return None
    return {
        "type": "text",
        "text": text,
        "page_idx": page_idx,
    }


def _extract_mineru_text_content(value: Any) -> str:
    parts = _extract_mineru_text_list(value)
    return "\n".join(part for part in parts if part).strip()


def _extract_mineru_text_list(value: Any) -> list[str]:
    parts: list[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, Mapping):
            if str(node.get("type") or "").strip().lower() == "text":
                text = str(node.get("content") or "").strip()
                if text:
                    parts.append(text)
                return
            for child in node.values():
                visit(child)
            return
        if isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return parts


def _resolve_mineru_asset_path(base_dir: Path, relative_path: Any) -> Path | None:
    if not isinstance(relative_path, str) or not relative_path.strip():
        return None
    candidate = Path(relative_path)
    if candidate.is_absolute():
        return candidate
    return (base_dir / candidate).resolve()


def _should_auto_text_ingest_for_rag_anything(input_path: Path) -> bool:
    return False


def _rag_anything_input_type(input_path: Path) -> str:
    return _input_type_hint(str(input_path)) or ("folder" if input_path.is_dir() else "document")


def _infer_input_kind_from_path(input_path: str) -> str:
    suffix = Path(input_path).suffix.lower()
    if suffix == ".markdown":
        return "md"
    return suffix.lstrip(".") or "unknown"


def _prepare_graphrag_input(
    *,
    input_path: Path,
    input_kind: str,
    working_dir: Path,
    workspace: str | Path | None,
    method_options: Mapping[str, Any],
) -> Path:
    prepared_dir = working_dir / "prepared_input"
    prepared_dir.mkdir(parents=True, exist_ok=True)

    if input_path.is_dir():
        return _prepare_graphrag_folder_input(
            input_path=input_path,
            prepared_dir=prepared_dir,
            workspace=workspace,
            method_options=method_options,
        )

    parseable_document = _is_mineru_parseable(input_path, input_kind)
    if parseable_document:
        markdown_path = _prepare_document_markdown(
            input_path=input_path,
            working_dir=working_dir,
            workspace=workspace,
            method_options=method_options,
        )
        return _materialize_text_from_markdown(markdown_path, prepared_dir=prepared_dir)

    if input_kind in {"txt", "md", "json", "jsonl", "parquet"}:
        return _materialize_text_input(
            input_path=input_path,
            input_kind=input_kind,
            prepared_dir=prepared_dir,
            method_options=method_options,
        )

    raise ValueError(
        "GraphRAG supports local PDF/doc/image documents via MinerU preprocessing "
        "and local text/markdown/json/jsonl/parquet inputs via text normalization."
    )


def _prepare_reasoning_input_bundle(
    *,
    input_paths: list[Path],
    working_dir: Path,
    workspace: str | Path | None,
    method_options: Mapping[str, Any],
) -> Path:
    if len(input_paths) == 1:
        return input_paths[0]

    prepared_dir = working_dir / "prepared_input"
    prepared_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = "||".join(str(path) for path in input_paths)
    digest = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:12]
    bundle_stem = f"{input_paths[0].stem}_bundle_{digest}"
    suffixes = {path.suffix.lower() for path in input_paths}

    if suffixes and suffixes.issubset({".pdf"}):
        bundle_path = prepared_dir / f"{bundle_stem}.pdf"
        if bundle_path.exists() and bundle_path.stat().st_size > 0:
            return bundle_path
        _merge_pdf_documents(input_paths, bundle_path)
        return bundle_path

    bundle_path = prepared_dir / f"{bundle_stem}.md"
    if bundle_path.exists() and bundle_path.stat().st_size > 0:
        return bundle_path

    bundle_text = _render_reasoning_bundle_as_markdown(
        input_paths=input_paths,
        working_dir=working_dir,
        workspace=workspace,
        method_options=method_options,
    )
    if not bundle_text.strip():
        raise ValueError("Merged document bundle is empty")
    _write_normalized_text_file(bundle_path, bundle_text)
    return bundle_path


def _merge_pdf_documents(input_paths: list[Path], bundle_path: Path) -> None:
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from pypdf import PdfMerger
    except ModuleNotFoundError:
        try:
            from PyPDF2 import PdfMerger  # type: ignore
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Merging multiple PDF inputs requires pypdf or PyPDF2. Install one of them with "
                "`pip install pypdf`."
            ) from exc

    merger = PdfMerger()
    try:
        for source in input_paths:
            merger.append(str(source))
        with bundle_path.open("wb") as handle:
            merger.write(handle)
    finally:
        try:
            merger.close()
        except Exception:
            pass


def _render_reasoning_bundle_as_markdown(
    *,
    input_paths: list[Path],
    working_dir: Path,
    workspace: str | Path | None,
    method_options: Mapping[str, Any],
) -> str:
    sections: list[str] = []
    for index, source in enumerate(input_paths, start=1):
        suffix = source.suffix.lower()
        sections.append(f"# Source {index}: {source.name}")
        sections.append("")
        if suffix == ".pdf":
            markdown_path = _prepare_document_markdown(
                input_path=source,
                working_dir=working_dir / ".bundle_mineru" / source.stem,
                workspace=workspace,
                method_options=method_options,
            )
            sections.append(markdown_path.read_text(encoding="utf-8"))
        elif suffix in {".txt", ".md", ".markdown"}:
            sections.append(source.read_text(encoding=str(method_options.get("encoding", "utf-8"))))
        elif suffix in _GRAPHRAG_STRUCTURED_DOC_EXTENSIONS:
            sections.append(_structured_document_to_text(source, method_options=method_options))
        else:
            raise ValueError(f"Unsupported merged input type: {source.suffix}")
        sections.append("")
    return "\n".join(section for section in sections if section is not None).strip() + "\n"


def _prepare_graphrag_folder_input(
    *,
    input_path: Path,
    prepared_dir: Path,
    workspace: str | Path | None,
    method_options: Mapping[str, Any],
) -> Path:
    folder_output = prepared_dir / input_path.name
    folder_output.mkdir(parents=True, exist_ok=True)
    copied = 0
    for source in input_path.rglob("*"):
        if not source.is_file():
            continue
        kind = _infer_input_kind_from_path(str(source))
        relative = source.relative_to(input_path)
        target_base = folder_output / relative
        if _is_mineru_parseable(source, kind):
            markdown_path = _prepare_document_markdown(
                input_path=source,
                working_dir=prepared_dir / ".folder_mineru",
                workspace=workspace,
                method_options=method_options,
            )
            target_path = target_base.with_suffix(".txt")
            _write_normalized_text_file(target_path, markdown_path.read_text(encoding="utf-8"))
            copied += 1
            continue
        if kind in {"txt", "md", "json", "jsonl", "parquet"}:
            target_path = target_base.with_suffix(".txt")
            text = _render_source_as_text(source=source, input_kind=kind, method_options=method_options)
            _write_normalized_text_file(target_path, text)
            copied += 1
    if copied == 0:
        raise ValueError(
            "GraphRAG folder input must contain parseable documents or .txt/.md/.json/.jsonl/.parquet files."
        )
    return folder_output


def _materialize_text_input(
    *,
    input_path: Path,
    input_kind: str,
    prepared_dir: Path,
    method_options: Mapping[str, Any],
) -> Path:
    target_path = prepared_dir / f"{input_path.stem}.txt"
    if target_path.exists():
        return target_path
    text = _render_source_as_text(source=input_path, input_kind=input_kind, method_options=method_options)
    _write_normalized_text_file(target_path, text)
    return target_path


def _render_source_as_text(
    *,
    source: Path,
    input_kind: str,
    method_options: Mapping[str, Any],
) -> str:
    if input_kind in {"txt", "md"}:
        return source.read_text(encoding=str(method_options.get("encoding", "utf-8")))
    if input_kind in {"json", "jsonl", "parquet"}:
        return _structured_document_to_text(source, method_options=method_options)
    raise ValueError(f"Cannot render input as normalized text: {source}")


def _prepare_document_markdown(
    *,
    input_path: Path,
    working_dir: Path,
    workspace: str | Path | None,
    method_options: Mapping[str, Any],
) -> Path:
    parsed_dir = working_dir / "parsed_markdown"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = parsed_dir / f"{input_path.stem}.md"
    if markdown_path.exists() and markdown_path.stat().st_size > 0:
        return markdown_path

    from kgagent.system.system import KGAgentSystem

    system = KGAgentSystem(work_dir=str(workspace or "."))
    parse_result = _run_async_in_worker_thread(
        system.parse_document_async(
            input_path=str(input_path),
            output_path=str(markdown_path),
        )
    )
    if not bool(parse_result.get("success")):
        raise RuntimeError(str(parse_result.get("error") or f"Failed to parse document: {input_path}"))
    output_file = parse_result.get("output_file")
    if isinstance(output_file, str) and output_file.strip():
        return Path(output_file).resolve()
    return markdown_path.resolve()


def _materialize_text_from_markdown(markdown_path: Path, *, prepared_dir: Path) -> Path:
    target_path = prepared_dir / f"{markdown_path.stem}.txt"
    if target_path.exists():
        return target_path
    _write_normalized_text_file(target_path, markdown_path.read_text(encoding="utf-8"))
    return target_path


def _write_normalized_text_file(target_path: Path, text: str) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(text, encoding="utf-8")


def _is_mineru_parseable(input_path: Path, input_kind: str) -> bool:
    if input_kind in {"pdf", "doc", "docx", "ppt", "pptx", "png", "jpg", "jpeg", "bmp", "gif", "tiff"}:
        return True
    try:
        from kgagent.mineru import is_parseable_document

        return bool(is_parseable_document(input_path))
    except Exception:
        return False


def _rag_anything_parser_kwargs(method_options: Mapping[str, Any]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    for option_name in [
        "lang",
        "backend",
        "source",
        "device",
        "vlm_url",
        "start_page",
        "end_page",
        "timeout",
    ]:
        if option_name in method_options and method_options[option_name] is not None:
            kwargs[option_name] = method_options[option_name]
    for option_name in ["formula", "table"]:
        if option_name in method_options:
            kwargs[option_name] = bool(method_options[option_name])

    env_options = method_options.get("parser_env")
    if isinstance(env_options, Mapping):
        kwargs["env"] = {str(key): str(value) for key, value in env_options.items()}

    return kwargs


def _ensure_rag_anything_kgagent_parser_registered() -> None:
    global _RAG_ANYTHING_KGAGENT_PARSER_REGISTERED
    if _RAG_ANYTHING_KGAGENT_PARSER_REGISTERED:
        return

    from raganything.parser import Parser, register_parser

    class KGAgentMineruParser(Parser):
        def check_installation(self) -> bool:
            try:
                from kgagent.mineru import MinerUParser  # noqa: F401
                return True
            except Exception:
                return False

        def parse_pdf(self, pdf_path: str | Path, output_dir: str | Path = "./output", method: str = "auto", **kwargs):
            return _parse_pdf_with_kgagent_mineru(
                pdf_path=pdf_path,
                output_dir=output_dir,
                method=method,
                **kwargs,
            )

        def parse_document(self, file_path: str | Path, output_dir: str | Path = "./output", method: str = "auto", **kwargs):
            file_path = Path(file_path)
            if file_path.suffix.lower() != ".pdf":
                raise NotImplementedError("kgagent_mineru parser only supports PDF input")
            return self.parse_pdf(file_path, output_dir=output_dir, method=method, **kwargs)

    register_parser(_RAG_ANYTHING_KGAGENT_PARSER_NAME, KGAgentMineruParser)
    _RAG_ANYTHING_KGAGENT_PARSER_REGISTERED = True


def _parse_pdf_with_kgagent_mineru(
    *,
    pdf_path: str | Path,
    output_dir: str | Path = "./output",
    method: str = "auto",
    **kwargs: Any,
) -> list[dict[str, Any]]:
    from kgagent.mineru import MinerUParser

    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / f"{pdf_path.stem}.md"
    parser = MinerUParser(work_dir=output_dir.parent)
    result = parser.parse_document(pdf_path, output_path=output_path)
    if not bool(result.get("success")):
        raise RuntimeError(str(result.get("error") or "kgagent MinerU parsing failed"))

    content_list = _find_and_load_mineru_content_list(output_dir)
    if content_list:
        return content_list

    markdown_text = ""
    md_path = Path(str(result.get("output_file") or output_path))
    if md_path.exists():
        markdown_text = md_path.read_text(encoding="utf-8", errors="ignore").strip()

    synthesized: list[dict[str, Any]] = []
    if markdown_text:
        for block in _split_markdown_into_paragraphs(markdown_text):
            synthesized.append(
                {
                    "type": "text",
                    "text": block,
                    "page_idx": 0,
                }
            )

    image_dir_candidates = []
    explicit_images_dir = result.get("images_dir")
    if isinstance(explicit_images_dir, str) and explicit_images_dir.strip():
        image_dir_candidates.append(Path(explicit_images_dir))
    image_dir_candidates.append(output_dir / "images")
    for candidate in image_dir_candidates:
        if not candidate.exists():
            continue
        for image_path in sorted(candidate.rglob("*")):
            if image_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                continue
            synthesized.append(
                {
                    "type": "image",
                    "img_path": str(image_path.resolve()),
                    "image_caption": [],
                    "image_footnote": [],
                    "page_idx": 0,
                }
            )
        if synthesized:
            break

    return synthesized


def _find_and_load_mineru_content_list(output_dir: Path) -> list[dict[str, Any]]:
    content_list_path = _find_rag_anything_parsed_content_list(output_dir)
    if content_list_path is None:
        return []
    try:
        return _load_rag_anything_content_list_from_parsed_output(content_list_path)
    except Exception:
        return []


def _split_markdown_into_paragraphs(markdown_text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in markdown_text.splitlines():
        stripped = line.strip()
        if not stripped:
            if current:
                block = "\n".join(current).strip()
                if block:
                    blocks.append(block)
                current = []
            continue
        current.append(line.rstrip())
    if current:
        block = "\n".join(current).strip()
        if block:
            blocks.append(block)
    return blocks


async def _initialize_lightrag_if_available(rag: Any) -> None:
    lightrag_instance = getattr(rag, "lightrag", None)
    if lightrag_instance is not None and hasattr(lightrag_instance, "initialize_storages"):
        await lightrag_instance.initialize_storages()


def _build_rag_anything(
    *,
    RAGAnything: Any,
    RAGAnythingConfig: Any,
    EmbeddingFunc: Any,
    LightRAG: Any,
    openai_complete_if_cache: Any,
    openai_embed: Any,
    working_dir: Path,
    method_options: Mapping[str, Any],
) -> Any:
    _ensure_rag_anything_kgagent_parser_registered()
    api_key = str(
        method_options.get("api_key")
        or os.environ.get("DF_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("OPENAI_COMPAT_API_KEY")
        or ""
    )
    base_url = method_options.get("base_url") or os.environ.get("DF_API_URL") or os.environ.get("OPENAI_BASE_URL")
    llm_model = str(method_options.get("llm_model") or os.environ.get("DF_LLM_MODEL") or "gpt-4o-mini")
    vision_model = str(method_options.get("vision_model") or os.environ.get("DF_VLM_MODEL") or "gpt-4o")
    embedding_model = str(
        method_options.get("embedding_model")
        or os.environ.get("DF_EMBEDDING_MODEL")
        or "text-embedding-3-large"
    )
    embedding_dim = int(method_options.get("embedding_dim") or os.environ.get("DF_EMBEDDING_DIM") or 3072)

    config = RAGAnythingConfig(
        working_dir=str(working_dir),
        parser=str(method_options.get("parser") or _RAG_ANYTHING_KGAGENT_PARSER_NAME),
        parse_method=str(method_options.get("parse_method", "auto")),
        enable_image_processing=bool(method_options.get("enable_image_processing", True)),
        enable_table_processing=bool(method_options.get("enable_table_processing", True)),
        enable_equation_processing=bool(method_options.get("enable_equation_processing", True)),
    )

    def llm_model_func(prompt, system_prompt=None, history_messages=None, **kwargs):
        return openai_complete_if_cache(
            llm_model,
            prompt,
            system_prompt=system_prompt,
            history_messages=history_messages or [],
            api_key=api_key,
            base_url=base_url,
            **kwargs,
        )

    def vision_model_func(prompt, system_prompt=None, history_messages=None, image_data=None, messages=None, **kwargs):
        if messages:
            return openai_complete_if_cache(
                vision_model,
                "",
                system_prompt=None,
                history_messages=[],
                messages=messages,
                api_key=api_key,
                base_url=base_url,
                **kwargs,
            )
        if image_data:
            return openai_complete_if_cache(
                vision_model,
                "",
                system_prompt=None,
                history_messages=[],
                messages=[
                    {"role": "system", "content": system_prompt} if system_prompt else None,
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{image_data}"},
                            },
                        ],
                    },
                ],
                api_key=api_key,
                base_url=base_url,
                **kwargs,
            )
        return llm_model_func(prompt, system_prompt, history_messages or [], **kwargs)

    local_embedding_model = method_options.get("local_embedding_model")
    if local_embedding_model:
        try:
            import sentence_transformers  # noqa: F401
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Local embeddings require sentence-transformers. Install it with "
                "`pip install sentence-transformers`."
            ) from exc
        embedding_func = EmbeddingFunc(
            embedding_dim=embedding_dim,
            max_token_size=int(method_options.get("embedding_max_token_size", 8192)),
            func=partial(
                _local_sentence_transformers_embed,
                model_name=str(local_embedding_model),
                device=(
                    str(method_options["local_embedding_device"])
                    if method_options.get("local_embedding_device")
                    else None
                ),
            ),
        )
    else:
        embedding_func = EmbeddingFunc(
            embedding_dim=embedding_dim,
            max_token_size=int(method_options.get("embedding_max_token_size", 8192)),
            func=partial(
                openai_embed.func,
                model=embedding_model,
                api_key=api_key,
                base_url=base_url,
                client_configs={
                    "timeout": int(method_options.get("embedding_timeout", 180)),
                },
            ),
        )

    lightrag_kwargs: dict[str, Any] = {
        "working_dir": str(working_dir),
        "llm_model_func": llm_model_func,
        "embedding_func": embedding_func,
    }
    for option_name in [
        "chunk_token_size",
        "chunk_overlap_token_size",
        "embedding_batch_num",
        "embedding_func_max_async",
        "max_parallel_insert",
    ]:
        if option_name in method_options:
            lightrag_kwargs[option_name] = int(method_options[option_name])
    lightrag_instance = LightRAG(**lightrag_kwargs)

    return RAGAnything(
        config=config,
        lightrag=lightrag_instance,
        llm_model_func=llm_model_func,
        vision_model_func=vision_model_func,
        embedding_func=embedding_func,
    )


def _required_str(input_data: Mapping[str, Any], key: str) -> str:
    value = input_data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _normalize_input_paths(raw_input_paths: Any, raw_input_path: Any) -> list[str]:
    candidates: list[Any] = []
    if isinstance(raw_input_paths, (list, tuple)):
        candidates.extend(raw_input_paths)
    elif isinstance(raw_input_paths, str) and raw_input_paths.strip():
        candidates.append(raw_input_paths)

    if not candidates:
        if isinstance(raw_input_path, (list, tuple)):
            candidates.extend(raw_input_path)
        elif isinstance(raw_input_path, str) and raw_input_path.strip():
            candidates.append(raw_input_path)

    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, str):
            candidate = str(candidate)
        path = candidate.strip()
        if not path:
            continue
        key = path.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(path)
    return normalized


def _resolve_input_sources(
    input_data: Mapping[str, Any],
    workspace: str | Path | None,
) -> list[Path]:
    raw_input_paths = input_data.get("input_paths")
    raw_input_path = input_data.get("input_path")
    resolved: list[Path] = []
    for raw_path in _normalize_input_paths(raw_input_paths, raw_input_path):
        resolved.append(_resolve_input_path(raw_path, workspace))
    if not resolved:
        raise ValueError("input_path is required")
    return resolved


def _resolve_question_text(
    input_data: Mapping[str, Any],
    *,
    workspace: str | Path | None = None,
) -> str:
    items = _load_question_items(input_data, workspace=workspace)
    if len(items) > 1:
        source = str(input_data.get("questions_path") or input_data.get("question_path") or input_data.get("question") or "")
        raise ValueError(
            f"Question input contains {len(items)} questions"
            + (f": {source}" if source else "")
            + ". Single QA expects one question; use batch QA."
        )
    if items:
        return str(items[0]["question"]).strip()
    raise ValueError("question is required")


def _load_question_items(
    input_data: Mapping[str, Any],
    *,
    workspace: str | Path | None,
) -> list[dict[str, Any]]:
    question_field = input_data.get("question_field")
    raw_questions = input_data.get("questions")
    questions_path = input_data.get("questions_path")
    question_path = input_data.get("question_path")
    raw_question = input_data.get("question")

    if isinstance(raw_questions, list):
        return _extract_question_items_from_payload(raw_questions, question_field=question_field, source="questions")

    if isinstance(questions_path, str) and questions_path.strip():
        return _load_question_items_from_path(questions_path, workspace=workspace, input_data=input_data)

    if isinstance(question_path, str) and question_path.strip():
        return _load_question_items_from_path(question_path, workspace=workspace, input_data=input_data)

    if isinstance(raw_question, str) and raw_question.strip():
        candidate = _maybe_resolve_existing_path(raw_question.strip(), workspace=workspace)
        if candidate is not None and candidate.is_file() and candidate.suffix.lower() in {".txt", ".json", ".jsonl"}:
            return _load_question_items_from_path(str(candidate), workspace=workspace, input_data=input_data)
        return [{"id": "q1", "question": raw_question.strip()}]

    if isinstance(raw_question, Mapping):
        return _extract_question_items_from_payload(raw_question, question_field=question_field, source="question")

    return []


def _load_question_items_from_path(
    raw_path: str,
    *,
    workspace: str | Path | None,
    input_data: Mapping[str, Any],
) -> list[dict[str, Any]]:
    path = _resolve_input_path(raw_path, workspace)
    suffix = path.suffix.lower()
    if suffix == ".txt":
        text = path.read_text(encoding=str(input_data.get("question_encoding", "utf-8"))).strip()
        if not text:
            raise ValueError(f"Question file is empty: {path}")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) <= 1:
            return [{"id": path.stem or "q1", "question": text}]
        return [
            {
                "id": f"q{index + 1}",
                "question": line,
                "source": str(path),
            }
            for index, line in enumerate(lines)
        ]
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding=str(input_data.get("question_encoding", "utf-8"))))
        return _extract_question_items_from_payload(payload, question_field=input_data.get("question_field"), source=str(path))
    if suffix == ".jsonl":
        records: list[Any] = []
        with path.open("r", encoding=str(input_data.get("question_encoding", "utf-8"))) as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                records.append(json.loads(line))
        return _extract_question_items_from_payload(records, question_field=input_data.get("question_field"), source=str(path))
    raise ValueError(f"Unsupported question file format: {path.suffix}. Use .txt, .json, or .jsonl.")


def _extract_question_items_from_payload(
    payload: Any,
    *,
    question_field: Any,
    source: str,
) -> list[dict[str, Any]]:
    if isinstance(payload, Mapping):
        return [_question_item_from_mapping(payload, index=0, question_field=question_field, source=source)]
    if isinstance(payload, list):
        if not payload:
            raise ValueError(f"Question file is empty: {source}")
        items: list[dict[str, Any]] = []
        for index, item in enumerate(payload):
            if isinstance(item, Mapping):
                items.append(_question_item_from_mapping(item, index=index, question_field=question_field, source=source))
            elif isinstance(item, str) and item.strip():
                items.append({"id": f"q{index + 1}", "question": item.strip()})
            else:
                raise ValueError(
                    f"Unsupported question item at index {index} in {source}. "
                    "Each item must be a string or an object containing a question field."
                )
        return items
    raise ValueError(
        f"Could not extract questions from {source}. "
        "Expected plain text or a JSON object containing a question field."
    )


def _extract_question_from_mapping(payload: Mapping[str, Any], *, question_field: Any) -> str:
    fields: list[str] = []
    if isinstance(question_field, str) and question_field.strip():
        fields.append(question_field.strip())
    fields.extend(["question", "query", "prompt", "instruction"])
    seen: set[str] = set()
    for field in fields:
        key = field.lower()
        if key in seen:
            continue
        seen.add(key)
        for candidate_key, value in payload.items():
            if str(candidate_key).lower() != key:
                continue
            if isinstance(value, str) and value.strip():
                return value.strip()
    raise ValueError(
        "Question JSON must contain a non-empty `question` field "
        "(or set `question_field` to another key such as `query` or `prompt`)."
    )


def _question_item_from_mapping(
    payload: Mapping[str, Any],
    *,
    index: int,
    question_field: Any,
    source: str,
) -> dict[str, Any]:
    question = _extract_question_from_mapping(payload, question_field=question_field)
    item_id = payload.get("id")
    if not isinstance(item_id, str) or not item_id.strip():
        item_id = f"q{index + 1}"
    item: dict[str, Any] = {
        "id": item_id.strip(),
        "question": question,
    }
    for key in ["ans", "answer", "explan", "explanation", "label", "metadata"]:
        if key in payload:
            item[key] = payload[key]
    item["source"] = source
    return item


def _maybe_resolve_existing_path(raw_path: str, workspace: str | Path | None) -> Path | None:
    try:
        path = Path(raw_path).expanduser()
        if not path.is_absolute() and workspace is not None:
            workspace_path = Path(workspace).resolve()
            path = workspace_path / path
            if not path.exists() and len(Path(raw_path).parts) > 1:
                parts = Path(raw_path).parts
                if parts[0].lower() == workspace_path.name.lower():
                    path = workspace_path / Path(*parts[1:])
        path = path.resolve()
    except Exception:
        return None
    if path.exists():
        return path
    return None


def _maybe_run_qa_batch(
    input_data: Mapping[str, Any],
    *,
    workspace: str | Path | None,
    method_name: str,
) -> dict[str, Any] | None:
    question_items = _load_question_items(input_data, workspace=workspace)
    if len(question_items) <= 1:
        return None
    return _run_qa_batch(input_data, question_items=question_items, workspace=workspace, method_name=method_name)


def _run_qa_batch(
    input_data: Mapping[str, Any],
    *,
    question_items: list[dict[str, Any]],
    workspace: str | Path | None,
    method_name: str,
) -> dict[str, Any]:
    batch_working_dir = _resolve_batch_working_dir(input_data, workspace=workspace, method_name=method_name)
    resume_enabled = bool(input_data.get("resume", True))
    progress_path = _qa_batch_progress_path(
        input_data,
        question_items=question_items,
        workspace=workspace,
        method_name=method_name,
        working_dir=batch_working_dir,
    )
    progress = _load_qa_batch_progress(progress_path) if resume_enabled else _empty_qa_batch_progress(
        method_name=method_name,
        progress_path=progress_path,
    )
    existing_items = progress.get("items_by_id", {})
    batch_items: list[dict[str, Any]] = []
    success = 0
    failed = 0
    skipped = 0
    first_success_result: dict[str, Any] | None = None
    remaining_items: list[tuple[int, dict[str, Any]]] = []
    show_progress = bool(input_data.get("show_progress", True))

    for index, question_item in enumerate(question_items):
        item_id = str(question_item.get("id") or f"q{index + 1}")
        existing = existing_items.get(item_id)
        if resume_enabled and _is_resumable_batch_item(existing, question_item):
            resumed_item = dict(existing)
            resumed_item["resumed"] = True
            batch_items.append(resumed_item)
            skipped += 1
            if resumed_item.get("status") == "success":
                success += 1
                if first_success_result is None:
                    first_success_result = _qa_batch_item_runtime_result(resumed_item)
            else:
                failed += 1
            continue
        remaining_items.append((index, question_item))

    if show_progress:
        _print_qa_batch_progress_header(
            method_name=method_name,
            total=len(question_items),
            skipped=skipped,
            resume_enabled=resume_enabled,
            progress_path=progress_path,
        )

    async_runner_context = _async_runner_thread() if method_name == "rag_anything" else nullcontext(None)
    with async_runner_context as async_runner:
        for remaining_index, (index, question_item) in enumerate(remaining_items):
            if show_progress:
                _print_qa_batch_progress_event(
                    event="running",
                    method_name=method_name,
                    current=success + failed + skipped + 1,
                    total=len(question_items),
                    item_id=str(question_item.get("id") or f"q{index + 1}"),
                    question=str(question_item.get("question") or ""),
                )
            child_payload = _build_single_question_payload(
                input_data,
                question=str(question_item["question"]),
                item_id=str(question_item.get("id") or f"q{index + 1}"),
                first_item=(remaining_index == 0),
            )
            try:
                result = _run_single_qa_by_method(
                    method_name,
                    child_payload,
                    workspace=workspace,
                    async_runner=async_runner,
                )
                if first_success_result is None:
                    first_success_result = result
                batch_item = {
                    "id": question_item.get("id") or f"q{index + 1}",
                    "question": question_item["question"],
                    "explain": question_item.get("explan", question_item.get("explanation", "")),
                    "status": "success",
                    "answer": result.get("answer", ""),
                    "error": "",
                    "resumed": False,
                    "question_hash": _qa_question_hash(question_item),
                    **_qa_progress_runtime_metadata(result),
                }
                batch_items.append(batch_item)
                _update_qa_batch_progress(
                    progress_path,
                    method_name=method_name,
                    input_data=input_data,
                    workspace=workspace,
                    question_items=question_items,
                    item=batch_item,
                )
                success += 1
                if show_progress:
                    _print_qa_batch_progress_event(
                        event="success",
                        method_name=method_name,
                        current=success + failed + skipped,
                        total=len(question_items),
                        item_id=str(batch_item["id"]),
                        question=str(batch_item["question"]),
                    )
            except Exception as exc:
                batch_item = {
                    "id": question_item.get("id") or f"q{index + 1}",
                    "question": question_item["question"],
                    "explain": question_item.get("explan", question_item.get("explanation", "")),
                    "status": "error",
                    "answer": "",
                    "error": str(exc),
                    "resumed": False,
                    "question_hash": _qa_question_hash(question_item),
                }
                batch_items.append(batch_item)
                _update_qa_batch_progress(
                    progress_path,
                    method_name=method_name,
                    input_data=input_data,
                    workspace=workspace,
                    question_items=question_items,
                    item=batch_item,
                )
                failed += 1
                if show_progress:
                    _print_qa_batch_progress_event(
                        event="error",
                        method_name=method_name,
                        current=success + failed + skipped,
                        total=len(question_items),
                        item_id=str(batch_item["id"]),
                        question=str(batch_item["question"]),
                        extra=str(exc),
                    )

    batch_items = _order_qa_batch_items(batch_items, question_items)
    input_path = str(input_data.get("input_path") or "")
    input_kind = str(input_data.get("input_kind") or _infer_input_kind_from_path(input_path))
    input_type = "document" if input_kind == "pdf" else (_input_type_hint(input_path) or "text")
    working_dir = ""
    storage_id = ""
    built_index = False
    reused_existing = False
    kg_output_path = ""
    if first_success_result is not None:
        working_dir = str(first_success_result.get("working_dir", "") or "")
        storage_id = str(first_success_result.get("storage_id", "") or "")
        built_index = any(bool(item.get("built_index", False)) for item in batch_items)
        reused_existing = all(
            bool(item.get("reused_existing", False))
            for item in batch_items
            if item.get("status") == "success"
        ) if success > 0 else False
        kg_output_path = str(first_success_result.get("kg_output_path", "") or "")

    return {
        "task_type": "qa_batch",
        "method": method_name,
        "input_path": input_path,
        "input_kind": input_kind,
        "input_type": input_type,
        "working_dir": working_dir,
        "storage_id": storage_id,
        "reused_existing": reused_existing,
        "built_index": built_index,
        "kg_output_path": kg_output_path,
        "status": "success" if failed == 0 else ("partial_success" if success > 0 else "error"),
        "error": "" if failed == 0 else f"{failed} question(s) failed in batch QA.",
        "items": [_public_qa_batch_item(item) for item in batch_items],
        "resume_enabled": resume_enabled,
        "resume_path": str(progress_path),
        "summary": {
            "total": len(question_items),
            "success": success,
            "failed": failed,
            "skipped": skipped,
            "question_count": len(question_items),
            "evidence_count": 0,
        },
    }


def _print_qa_batch_progress_header(
    *,
    method_name: str,
    total: int,
    skipped: int,
    resume_enabled: bool,
    progress_path: Path,
) -> None:
    print(f"🔄 QA batch started: method={method_name}, total={total}, skipped={skipped}")
    if resume_enabled:
        print(f"📝 Resume file: {progress_path}")


def _print_qa_batch_progress_event(
    *,
    event: str,
    method_name: str,
    current: int,
    total: int,
    item_id: str,
    question: str,
    extra: str = "",
) -> None:
    preview = _qa_progress_question_preview(question)
    bar = _qa_progress_bar(current=current, total=total)
    if event == "running":
        print(f"{bar} [{current}/{total}] Running {item_id} | {preview}")
        return
    if event == "success":
        print(f"{bar} [{current}/{total}] Done {item_id} | {preview}")
        return
    if event == "error":
        detail = f" | {extra}" if extra else ""
        print(f"{bar} [{current}/{total}] Failed {item_id} | {preview}{detail}")
        return


def _qa_progress_bar(*, current: int, total: int, width: int = 20) -> str:
    safe_total = max(total, 1)
    safe_current = min(max(current, 0), safe_total)
    filled = int(width * safe_current / safe_total)
    return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"


def _qa_progress_question_preview(question: str, limit: int = 72) -> str:
    compact = " ".join(question.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _build_single_question_payload(
    input_data: Mapping[str, Any],
    *,
    question: str,
    item_id: str,
    first_item: bool,
) -> dict[str, Any]:
    child = dict(input_data)
    child["question"] = question
    child.pop("questions", None)
    child.pop("questions_path", None)
    child.pop("question_path", None)
    child["question_item_id"] = item_id
    child["reuse_existing"] = True if first_item else True
    if not first_item:
        child["build_if_missing"] = False
    return child


def _resolve_batch_working_dir(
    input_data: Mapping[str, Any],
    *,
    workspace: str | Path | None,
    method_name: str,
) -> Path:
    input_paths = _resolve_input_sources(input_data, workspace)
    input_path = input_paths[0]
    return _resolve_working_dir(
        input_data.get("working_dir"),
        input_path=input_path,
        input_paths=input_paths,
        workspace=workspace,
        method_name=method_name,
    )


def _qa_batch_progress_path(
    input_data: Mapping[str, Any],
    *,
    question_items: list[dict[str, Any]],
    workspace: str | Path | None,
    method_name: str,
    working_dir: Path,
) -> Path:
    questions_source = str(
        input_data.get("questions_path")
        or input_data.get("question_path")
        or input_data.get("question")
        or ""
    ).strip()
    input_identity = str(
        _maybe_resolve_existing_path(str(input_data.get("input_path", "")), workspace)
        or input_data.get("input_path", "")
    )
    digest_source = json.dumps(
        {
            "method": method_name,
            "input": input_identity,
            "questions_source": questions_source,
            "question_ids": [str(item.get("id") or "") for item in question_items],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha1(digest_source.encode("utf-8")).hexdigest()[:12]
    return (working_dir / f".kgagent_qa_batch_progress_{digest}.json").resolve()


def _empty_qa_batch_progress(*, method_name: str, progress_path: Path) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "task_type": "qa_batch",
        "method": method_name,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "items_by_id": {},
        "progress_path": str(progress_path),
    }


def _load_qa_batch_progress(progress_path: Path) -> dict[str, Any]:
    if not progress_path.exists():
        return _empty_qa_batch_progress(method_name="", progress_path=progress_path)
    try:
        payload = json.loads(progress_path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_qa_batch_progress(method_name="", progress_path=progress_path)
    if not isinstance(payload, dict):
        return _empty_qa_batch_progress(method_name="", progress_path=progress_path)
    items = payload.get("items_by_id")
    if not isinstance(items, dict):
        payload["items_by_id"] = {}
    return payload


def _update_qa_batch_progress(
    progress_path: Path,
    *,
    method_name: str,
    input_data: Mapping[str, Any],
    workspace: str | Path | None,
    question_items: list[dict[str, Any]],
    item: Mapping[str, Any],
) -> None:
    payload = _load_qa_batch_progress(progress_path)
    items_by_id = payload.get("items_by_id")
    if not isinstance(items_by_id, dict):
        items_by_id = {}
        payload["items_by_id"] = items_by_id
    item_id = str(item.get("id") or "")
    items_by_id[item_id] = dict(item)
    payload["schema_version"] = "1.0"
    payload["task_type"] = "qa_batch"
    payload["method"] = method_name
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    payload["question_count"] = len(question_items)
    payload["input_identity"] = _qa_progress_input_identity(input_data, workspace=workspace, method_name=method_name)
    payload["summary"] = {
        "total": len(question_items),
        "success": sum(1 for value in items_by_id.values() if isinstance(value, Mapping) and value.get("status") == "success"),
        "failed": sum(1 for value in items_by_id.values() if isinstance(value, Mapping) and value.get("status") == "error"),
    }
    progress_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _qa_progress_runtime_metadata(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "working_dir": str(result.get("working_dir", "")),
        "storage_id": str(result.get("storage_id", "")),
        "reused_existing": bool(result.get("reused_existing", False)),
        "built_index": bool(result.get("built_index", False)),
        "kg_output_path": str(result.get("kg_output_path", "")),
    }


def _qa_batch_item_runtime_result(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "working_dir": str(item.get("working_dir", "")),
        "storage_id": str(item.get("storage_id", "")),
        "reused_existing": bool(item.get("reused_existing", False)),
        "built_index": bool(item.get("built_index", False)),
        "kg_output_path": str(item.get("kg_output_path", "")),
    }


def _public_qa_batch_item(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or ""),
        "question": str(item.get("question") or ""),
        "answer": str(item.get("answer") or ""),
        "status": str(item.get("status") or ""),
        "error": str(item.get("error") or ""),
        "explain": str(
            item.get("explain")
            or item.get("explanation")
            or item.get("explan")
            or ""
        ),
    }


def _qa_progress_input_identity(
    input_data: Mapping[str, Any],
    *,
    workspace: str | Path | None,
    method_name: str,
) -> str:
    raw = str(input_data.get("input_path", ""))
    candidate = _maybe_resolve_existing_path(raw, workspace)
    return str(candidate or raw)


def _qa_question_hash(question_item: Mapping[str, Any]) -> str:
    payload = {
        "id": str(question_item.get("id") or ""),
        "question": str(question_item.get("question") or ""),
        "explan": question_item.get("explan", question_item.get("explanation", "")),
    }
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def _is_resumable_batch_item(existing: Any, question_item: Mapping[str, Any]) -> bool:
    if not isinstance(existing, Mapping):
        return False
    if str(existing.get("status", "")) != "success":
        return False
    existing_hash = str(existing.get("question_hash") or "")
    if not existing_hash:
        return False
    return existing_hash == _qa_question_hash(question_item)


def _order_qa_batch_items(
    batch_items: list[dict[str, Any]],
    question_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ordered_ids = [str(item.get("id") or f"q{index + 1}") for index, item in enumerate(question_items)]
    rank = {item_id: index for index, item_id in enumerate(ordered_ids)}
    return sorted(batch_items, key=lambda item: rank.get(str(item.get("id") or ""), len(rank)))


def _run_single_qa_by_method(
    method_name: str,
    input_data: Mapping[str, Any],
    *,
    workspace: str | Path | None,
    async_runner: Any = None,
) -> dict[str, Any]:
    if method_name == "graphrag":
        return run_graphrag_qa_for_agent(input_data, workspace=workspace)
    if method_name == "rag_anything":
        coro = _run_rag_anything_qa(input_data, workspace=workspace)
        if async_runner is not None:
            return async_runner(coro)
        return _run_async_in_worker_thread(coro)
    raise ValueError(f"Unsupported QA batch method: {method_name}")


def _run_async_in_worker_thread(coro: Any) -> dict[str, Any]:
    def _runner() -> dict[str, Any]:
        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(_runner).result()


@contextmanager
def _async_runner_thread():
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    stopped = threading.Event()

    def _thread_main() -> None:
        asyncio.set_event_loop(loop)
        ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            if pending:
                for task in pending:
                    task.cancel()
                try:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except Exception:
                    pass
            loop.close()
            stopped.set()

    thread = threading.Thread(target=_thread_main, name="kgagent-raganything-loop", daemon=True)
    thread.start()
    ready.wait()

    def _runner(coro: Any) -> dict[str, Any]:
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result()

    try:
        yield _runner
    finally:
        loop.call_soon_threadsafe(loop.stop)
        stopped.wait(timeout=10)
        thread.join(timeout=10)


def _load_kg_triples(raw_input: Any, workspace: str | Path | None = None) -> list[tuple[str, str, str]]:
    if raw_input is None:
        return []

    data = raw_input
    if isinstance(raw_input, str):
        raw = raw_input.strip()
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute() and workspace is not None:
            candidate = Path(workspace).expanduser().resolve() / candidate
        if candidate.exists() and candidate.is_file():
            data = json.loads(candidate.read_text(encoding="utf-8"))
        else:
            data = json.loads(raw)

    triples: list[tuple[str, str, str]] = []
    _collect_triples(data, triples)
    deduped: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for triple in triples:
        normalized = tuple(part.strip() for part in triple)
        if any(not part for part in normalized):
            continue
        key = tuple(part.lower() for part in normalized)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)  # type: ignore[arg-type]
    return deduped


def _collect_triples(data: Any, triples: list[tuple[str, str, str]]) -> None:
    if isinstance(data, list):
        for item in data:
            parsed = _parse_triple_item(item)
            if parsed is not None:
                triples.append(parsed)
            else:
                _collect_triples(item, triples)
        return

    if not isinstance(data, Mapping):
        return

    parsed = _parse_triple_item(data)
    if parsed is not None:
        triples.append(parsed)
        return

    for key in ["kg", "triples", "relations", "relation_triples", "edges", "facts", "valid_triple", "triple"]:
        value = data.get(key)
        if value is not None:
            _collect_triples(value, triples)


def _parse_triple_item(item: Any) -> tuple[str, str, str] | None:
    if isinstance(item, (list, tuple)) and len(item) >= 3:
        return (_entity_text(item[0]), _entity_text(item[1]), _entity_text(item[2]))

    if isinstance(item, str):
        return _parse_tagged_triple(item)

    if not isinstance(item, Mapping):
        return None

    subject = _first_present(item, ["subject", "source", "head", "s", "from", "src"])
    relation = _first_present(item, ["relation", "predicate", "rel", "type", "r", "label"])
    obj = _first_present(item, ["object", "target", "tail", "o", "to", "dst"])
    if subject is None or relation is None or obj is None:
        return None
    return (_entity_text(subject), _entity_text(relation), _entity_text(obj))


def _parse_tagged_triple(text: str) -> tuple[str, str, str] | None:
    pattern = r"<subj>\s*(.*?)\s*<obj>\s*(.*?)\s*<rel>\s*(.*)"
    match = re_search(pattern, text)
    if match:
        subject, obj, relation = match
        return (subject, relation, obj)
    for sep in ["\t", "|"]:
        parts = [part.strip() for part in text.split(sep)]
        if len(parts) >= 3:
            return (parts[0], parts[1], parts[2])
    return None


def re_search(pattern: str, text: str) -> tuple[str, ...] | None:
    import re

    match = re.search(pattern, text)
    if match:
        return tuple(group.strip() for group in match.groups())
    return None


def _first_present(data: Mapping[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _entity_text(value: Any) -> str:
    if isinstance(value, Mapping):
        for key in ["name", "label", "id", "value", "text"]:
            if key in value and value[key] is not None:
                return str(value[key]).strip()
    return str(value).strip()


def _triples_to_graphrag_text(
    triples: list[tuple[str, str, str]],
    *,
    question: str | None = None,
    retrieval: Mapping[str, Any] | None = None,
) -> str:
    lines = [
        "# Knowledge Graph Triples",
        "",
        "The following statements are serialized from an existing knowledge graph. "
        "Each statement should be treated as a factual triple.",
        "",
    ]
    if question:
        lines.append(f"Question: {question}")
        lines.append("")
    if retrieval:
        matched_entities = retrieval.get("matched_entities") or []
        bridge_entities = retrieval.get("bridge_entities") or []
        if matched_entities:
            lines.append("Matched anchor entities: " + ", ".join(str(item) for item in matched_entities) + ".")
        if bridge_entities:
            lines.append("Bridge entities discovered from the graph: " + ", ".join(str(item) for item in bridge_entities) + ".")
        paths = retrieval.get("paths") or []
        if paths:
            lines.append("Relevant graph paths:")
            for index, path in enumerate(paths, start=1):
                rendered = " -> ".join(str(part) for part in path)
                lines.append(f"{index}. {rendered}")
        lines.append("")
    for index, (subject, relation, obj) in enumerate(triples, start=1):
        relation_text = relation.replace("_", " ").strip()
        lines.append(f"{index}. [Triple] subject: {subject}; relation: {relation_text}; object: {obj}.")
        lines.append(f"{subject} {relation_text} {obj}.")
    lines.append("")
    return "\n".join(lines)

async def _local_sentence_transformers_embed(
    texts: list[str],
    *,
    model_name: str,
    device: str | None = None,
) -> Any:
    try:
        import numpy as np
        from sentence_transformers import SentenceTransformer
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Local embeddings require sentence-transformers. Install it with "
            "`pip install sentence-transformers`."
        ) from exc

    cache_key = (model_name, device)
    model = _LOCAL_EMBEDDING_MODELS.get(cache_key)
    if model is None:
        model_kwargs: dict[str, Any] = {}
        if device:
            model_kwargs["device"] = device
        model = SentenceTransformer(model_name, **model_kwargs)
        _LOCAL_EMBEDDING_MODELS[cache_key] = model

    embeddings = model.encode(
        texts,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return np.asarray(embeddings)


def _resolve_input_path(raw_path: str, workspace: str | Path | None) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute() and workspace is not None:
        workspace_path = Path(workspace).resolve()
        path = workspace_path / path
        if not path.exists() and len(Path(raw_path).parts) > 1:
            parts = Path(raw_path).parts
            if parts[0].lower() == workspace_path.name.lower():
                path = workspace_path / Path(*parts[1:])
    path = path.resolve()
    if not path.exists():
        raise ValueError(f"input_path does not exist: {path}")
    return path


def _resolve_working_dir(
    raw_working_dir: Any,
    *,
    input_path: Path,
    input_paths: list[Path] | None = None,
    workspace: str | Path | None,
    method_name: str,
) -> Path:
    if isinstance(raw_working_dir, str) and raw_working_dir.strip():
        path = Path(raw_working_dir).expanduser()
        if not path.is_absolute() and workspace is not None:
            path = Path(workspace).resolve() / path
        path.mkdir(parents=True, exist_ok=True)
        return path.resolve()

    source_paths = input_paths or [input_path]
    base_input_path = source_paths[0]
    base = base_input_path.parent / ".kgagent_reasoning" / method_name
    fingerprint_parts = [str(path) for path in source_paths]
    fingerprint = "||".join(fingerprint_parts) if fingerprint_parts else str(input_path)
    digest = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:12]
    stem = input_path.stem if input_path.stem else "input"
    if input_paths and len(input_paths) > 1:
        stem = f"{stem}_bundle"
    path = base / f"{stem}_{digest}"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _should_export_kg_json(
    input_data: Mapping[str, Any],
    method_options: Mapping[str, Any],
) -> bool:
    return bool(
        input_data.get("save_kg_json")
        or input_data.get("export_kg_json")
        or method_options.get("save_kg_json")
        or method_options.get("export_kg_json")
    )


def _resolve_kg_output_path(
    *,
    input_data: Mapping[str, Any],
    method_options: Mapping[str, Any],
    working_dir: Path,
    input_path: Path | None,
    method_name: str,
) -> Path:
    raw_output_path = (
        input_data.get("kg_output_path")
        or input_data.get("graph_output_path")
        or method_options.get("kg_output_path")
        or method_options.get("graph_output_path")
    )
    if isinstance(raw_output_path, str) and raw_output_path.strip():
        output_path = Path(raw_output_path).expanduser()
        if not output_path.is_absolute():
            output_path = working_dir / output_path
    else:
        base_name = input_path.stem if input_path is not None else working_dir.name
        output_path = working_dir / f"{base_name}_{method_name}_kg.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return output_path.resolve()


def _maybe_export_method_kg_json(
    *,
    method_name: str,
    input_data: Mapping[str, Any],
    method_options: Mapping[str, Any],
    input_path: Path | None,
    working_dir: Path,
) -> Path | None:
    if not _should_export_kg_json(input_data, method_options):
        return None

    output_path = _resolve_kg_output_path(
        input_data=input_data,
        method_options=method_options,
        working_dir=working_dir,
        input_path=input_path,
        method_name=method_name,
    )
    if method_name == "rag_anything":
        export_payload = _build_rag_anything_kg_export(
            working_dir=working_dir,
            input_path=input_path,
        )
    elif method_name == "graphrag":
        export_payload = _build_graphrag_export(
            working_dir=working_dir,
            input_path=input_path,
        )
    else:
        raise ValueError(f"KG export is not supported for method: {method_name}")

    output_path.write_text(
        json.dumps(export_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


def _build_rag_anything_kg_export(
    *,
    working_dir: Path,
    input_path: Path | None,
) -> dict[str, Any]:
    graph_path = working_dir / "graph_chunk_entity_relation.graphml"
    if not graph_path.exists():
        raise RuntimeError(
            f"Cannot export RAG-Anything KG because graph file was not found: {graph_path}"
        )

    root = ET.parse(graph_path).getroot()
    namespace = {"g": "http://graphml.graphdrawing.org/xmlns"}
    key_lookup: dict[str, str] = {}
    for key in root.findall("g:key", namespace):
        key_id = key.attrib.get("id")
        if key_id:
            key_lookup[key_id] = key.attrib.get("attr.name") or key_id

    nodes: list[dict[str, Any]] = []
    triples: list[dict[str, str]] = []

    for node in root.findall(".//g:node", namespace):
        raw = _graphml_data_map(node, namespace, key_lookup)
        node_id = str(node.attrib.get("id") or raw.get("entity_id") or "")
        label = str(raw.get("entity_id") or node_id)
        node_type = str(raw.get("entity_type") or "")
        nodes.append(
            {
                "id": node_id,
                "label": label,
                "type": node_type,
            }
        )

    for edge in root.findall(".//g:edge", namespace):
        raw = _graphml_data_map(edge, namespace, key_lookup)
        source = str(edge.attrib.get("source") or "")
        target = str(edge.attrib.get("target") or "")
        relation = str(raw.get("keywords") or "")
        triples.append(
            {
                "subject": source,
                "predicate": relation or "related_to",
                "object": target,
            }
        )

    return _build_export_payload(
        method="rag_anything",
        nodes=nodes,
        triples=triples,
    )


def _build_graphrag_export(
    *,
    working_dir: Path,
    input_path: Path | None,
) -> dict[str, Any]:
    output_dir = working_dir / "output"
    entities_path = output_dir / "entities.parquet"
    relationships_path = output_dir / "relationships.parquet"
    if not entities_path.exists() or not relationships_path.exists():
        raise RuntimeError(
            "Cannot export GraphRAG KG because entities.parquet or relationships.parquet is missing."
        )

    try:
        import pandas as pd
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Exporting GraphRAG KG to JSON requires pandas and pyarrow in the active environment."
        ) from exc

    entities = pd.read_parquet(entities_path).to_dict(orient="records")
    relationships = pd.read_parquet(relationships_path).to_dict(orient="records")
    nodes: list[dict[str, Any]] = []
    triples: list[dict[str, str]] = []

    for entity in entities:
        node_id = str(entity.get("id") or "")
        label = str(entity.get("title") or node_id)
        node_type = str(entity.get("type") or "")
        nodes.append(
            {
                "id": node_id,
                "label": label,
                "type": node_type,
            }
        )

    for relation in relationships:
        source = str(relation.get("source") or "")
        target = str(relation.get("target") or "")
        triples.append(
            {
                "subject": source,
                "predicate": "related_to",
                "object": target,
            }
        )

    return _build_export_payload(
        method="graphrag",
        nodes=nodes,
        triples=triples,
    )


def _build_export_payload(
    *,
    method: str,
    nodes: list[dict[str, Any]],
    triples: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "method": method,
        "nodes": nodes,
        "triples": triples,
        "summary": {
            "node_count": len(nodes),
            "triple_count": len(triples),
        },
    }


def _graphml_data_map(
    element: ET.Element,
    namespace: dict[str, str],
    key_lookup: Mapping[str, str],
) -> dict[str, str]:
    data: dict[str, str] = {}
    for child in element.findall("g:data", namespace):
        key_id = child.attrib.get("key", "")
        data[str(key_lookup.get(key_id, key_id))] = child.text or ""
    return data


def _normalize_source_refs(value: Any) -> list[str]:
    if value is None:
        return []
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, (list, tuple, set)):
        result: list[str] = []
        for item in value:
            text = str(item).strip()
            if text and text not in result:
                result.append(text)
        return result
    text = str(value).strip()
    return [text] if text else []


def _compact_source_refs(*values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        for item in _normalize_source_refs(value):
            if item not in result:
                result.append(item)
    return result


def _safe_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _has_existing_storage(working_dir: Path) -> bool:
    if not working_dir.exists():
        return False
    return (working_dir / ".kgagent_rag_anything_ready.json").exists() and _has_rag_anything_content(working_dir)


def _has_rag_anything_content(working_dir: Path) -> bool:
    non_empty_chunk_store = False
    for store_name in [
        "kv_store_text_chunks.json",
        "vdb_chunks.json",
        "vdb_entities.json",
        "vdb_relationships.json",
    ]:
        store_path = working_dir / store_name
        if not store_path.exists() or store_path.stat().st_size == 0:
            continue
        try:
            data = json.loads(store_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and len(data) > 0:
            non_empty_chunk_store = True
            break
        if isinstance(data, list) and len(data) > 0:
            non_empty_chunk_store = True
            break

    if not non_empty_chunk_store:
        return False

    graph_path = working_dir / "graph_chunk_entity_relation.graphml"
    if not graph_path.exists() or graph_path.stat().st_size == 0:
        return True

    try:
        root = ET.fromstring(graph_path.read_text(encoding="utf-8"))
    except Exception:
        return True

    namespace = {"g": "http://graphml.graphdrawing.org/xmlns"}
    node_count = len(root.findall(".//g:node", namespace))
    edge_count = len(root.findall(".//g:edge", namespace))
    return node_count > 0 or edge_count > 0 or non_empty_chunk_store


def _write_ready_marker(working_dir: Path, *, input_path: Path) -> None:
    marker = {
        "method": "rag_anything",
        "input_path": str(input_path),
    }
    (working_dir / ".kgagent_rag_anything_ready.json").write_text(
        json.dumps(marker, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _clear_reasoning_working_dir(working_dir: Path) -> None:
    if not working_dir.exists():
        return
    marker_names = {
        ".kgagent_rag_anything_ready.json",
        ".kgagent_graphrag_ready.json",
    }
    is_reasoning_dir = any((working_dir / marker).exists() for marker in marker_names)
    is_under_reasoning_root = any(part == ".kgagent_reasoning" for part in working_dir.parts)
    if not (is_reasoning_dir or is_under_reasoning_root):
        raise ValueError(f"Refusing to clear non-reasoning working directory: {working_dir}")
    for item in working_dir.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()


def _clear_rag_anything_storage_preserve_parsed(working_dir: Path, *, output_dir: Path) -> None:
    if not working_dir.exists():
        return
    parsed_dir = output_dir.resolve()
    keep_names = {
        parsed_dir.name,
        _MANIFEST_NAME,
    }
    for item in working_dir.iterdir():
        if item.resolve() == parsed_dir:
            continue
        if item.name.startswith(".kgagent_qa_batch_progress_"):
            continue
        if item.name in keep_names:
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()


def _has_graphrag_storage(working_dir: Path) -> bool:
    if not working_dir.exists():
        return False
    if (working_dir / ".kgagent_graphrag_ready.json").exists():
        return True
    output_dir = working_dir / "output"
    return output_dir.exists() and any(output_dir.rglob("*.parquet"))


def _write_graphrag_ready_marker(working_dir: Path, *, input_path: Path) -> None:
    marker = {
        "method": "graphrag",
        "input_path": str(input_path),
    }
    (working_dir / ".kgagent_graphrag_ready.json").write_text(
        json.dumps(marker, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_reasoning_manifest(
    working_dir: Path,
    *,
    method: str,
    input_type: str,
    input_path: Path | None,
    question: str,
    status: str,
    method_options: Mapping[str, Any],
    paths: Mapping[str, str] | None = None,
    summary: Mapping[str, Any] | None = None,
) -> None:
    manifest_path = working_dir / _MANIFEST_NAME
    existing: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except json.JSONDecodeError:
            existing = {}

    now = datetime.now(timezone.utc).isoformat()
    path_data = {
        "working_dir": str(working_dir),
        "logs_dir": str(working_dir / "logs"),
    }
    if input_path is not None:
        path_data["input_path"] = str(input_path)
    if paths:
        path_data.update(dict(paths))

    existing_summary = existing.get("summary")
    manifest = {
        "schema_version": "1.0",
        "task_type": "qa",
        "method": method,
        "input_type": input_type,
        "input_path": str(input_path) if input_path is not None else "",
        "source_fingerprint": _source_fingerprint(input_path),
        "storage_id": _storage_id(working_dir),
        "question": question,
        "created_at": existing.get("created_at") or now,
        "updated_at": now,
        "status": status,
        "models": _reasoning_model_metadata(method_options),
        "paths": path_data,
        "summary": {
            **(existing_summary if isinstance(existing_summary, dict) else {}),
            **(dict(summary) if summary else {}),
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _reasoning_model_metadata(method_options: Mapping[str, Any]) -> dict[str, str]:
    return {
        "llm_model": str(
            method_options.get("llm_model")
            or method_options.get("chat_model")
            or os.environ.get("DF_LLM_MODEL")
            or os.environ.get("GRAPHRAG_CHAT_MODEL")
            or "gpt-4o-mini"
        ),
        "embedding_model": str(
            method_options.get("embedding_model")
            or os.environ.get("DF_EMBEDDING_MODEL")
            or os.environ.get("GRAPHRAG_EMBEDDING_MODEL")
            or "text-embedding-3-large"
        ),
        "local_embedding_model": str(method_options.get("local_embedding_model") or ""),
    }


def _source_fingerprint(input_path: Path | None) -> str:
    if input_path is None:
        return ""
    try:
        stat = input_path.stat()
        source = f"{input_path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
    except OSError:
        source = str(input_path)
    return hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]


def _storage_id(working_dir: Path) -> str:
    return working_dir.name


@contextmanager
def _third_party_log_scope(method_options: Mapping[str, Any]):
    log_level = str(method_options.get("log_level", "normal")).lower()
    if log_level == "debug":
        yield
        return

    target_level = logging.CRITICAL + 1 if log_level == "quiet" else logging.CRITICAL
    logger_names = [
        "raganything",
        "lightrag",
        "openai",
        "httpx",
        "httpcore",
    ]
    previous_levels = {name: logging.getLogger(name).level for name in logger_names}
    try:
        for name in logger_names:
            logging.getLogger(name).setLevel(target_level)
        yield
    finally:
        for name, level in previous_levels.items():
            logging.getLogger(name).setLevel(level)


def _write_graphrag_env(working_dir: Path, *, method_options: Mapping[str, Any]) -> None:
    api_key = (
        method_options.get("api_key")
        or os.environ.get("GRAPHRAG_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("OPENAI_COMPAT_API_KEY")
        or os.environ.get("DF_API_KEY")
        or ""
    )
    (working_dir / ".env").write_text(
        f"GRAPHRAG_API_KEY={api_key}\n",
        encoding="utf-8",
    )


def _patch_graphrag_settings(working_dir: Path, *, method_options: Mapping[str, Any]) -> None:
    api_base = (
        method_options.get("api_base")
        or method_options.get("base_url")
        or os.environ.get("GRAPHRAG_API_BASE")
        or os.environ.get("OPENAI_COMPAT_BASE_URL")
        or os.environ.get("OPENAI_COMPAT_API_BASE")
        or os.environ.get("OPENAI_COMPAT_API_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("DF_API_URL")
    )
    api_base = _normalize_openai_base_url(api_base)

    settings_path = working_dir / "settings.yaml"
    if not settings_path.exists():
        settings_path = working_dir / "settings.yml"
    if not settings_path.exists():
        return

    try:
        import yaml
    except ModuleNotFoundError:
        return

    data = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
    models = data.get("models")
    if isinstance(models, dict):
        chat_model = models.get("default_chat_model")
        if isinstance(chat_model, dict):
            chat_model["model"] = _graphrag_chat_model(method_options)
            chat_model["max_retries"] = int(method_options.get("max_retries", 2))
            chat_model["concurrent_requests"] = int(method_options.get("concurrent_requests", 2))
            if api_base:
                chat_model["api_base"] = str(api_base)
                chat_model["base_url"] = str(api_base)

        embedding_model = models.get("default_embedding_model")
        if isinstance(embedding_model, dict):
            embedding_model["model"] = _graphrag_embedding_model(method_options)
            embedding_model["max_retries"] = int(method_options.get("max_retries", 2))
            embedding_model["concurrent_requests"] = int(method_options.get("concurrent_requests", 2))
            if api_base:
                embedding_model["api_base"] = str(api_base)
                embedding_model["base_url"] = str(api_base)

    completion_models = data.get("completion_models")
    if isinstance(completion_models, dict):
        for model_config in completion_models.values():
            if isinstance(model_config, dict):
                model_config["model"] = _graphrag_chat_model(method_options)
                model_config["max_retries"] = int(method_options.get("max_retries", 2))
                model_config["concurrent_requests"] = int(method_options.get("concurrent_requests", 2))
                if api_base:
                    model_config["api_base"] = str(api_base)
                    model_config["base_url"] = str(api_base)

    embedding_models = data.get("embedding_models")
    if isinstance(embedding_models, dict):
        for model_config in embedding_models.values():
            if isinstance(model_config, dict):
                model_config["model"] = _graphrag_embedding_model(method_options)
                model_config["max_retries"] = int(method_options.get("max_retries", 2))
                model_config["concurrent_requests"] = int(method_options.get("concurrent_requests", 2))
                if api_base:
                    model_config["api_base"] = str(api_base)
                    model_config["base_url"] = str(api_base)

    settings_path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _prepare_graphrag_workspace(
    *,
    input_path: Path,
    working_dir: Path,
    method_options: Mapping[str, Any],
) -> None:
    input_dir = working_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    if input_path.is_dir():
        copied = 0
        for source in input_path.rglob("*"):
            if source.is_file() and source.suffix.lower() in _GRAPHRAG_SUPPORTED_EXTENSIONS:
                target = _copy_or_convert_graphrag_source(
                    source=source,
                    target_base=input_dir / source.relative_to(input_path),
                    method_options=method_options,
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                copied += 1
        if copied == 0:
            raise ValueError(
                "GraphRAG supports folders containing .txt, .md, .json, .jsonl, or .parquet files."
            )
        return

    suffix = input_path.suffix.lower()
    if suffix not in _GRAPHRAG_SUPPORTED_EXTENSIONS:
        raise ValueError(
            "GraphRAG supports local .txt, .md, .json, .jsonl, and .parquet document inputs. "
            "Use RAG-Anything for PDF, Office, image, table, or multimodal documents."
        )

    target_name = str(method_options.get("input_file_name") or input_path.name)
    _copy_or_convert_graphrag_source(
        source=input_path,
        target_base=input_dir / target_name,
        method_options=method_options,
    )


def _copy_or_convert_graphrag_source(
    *,
    source: Path,
    target_base: Path,
    method_options: Mapping[str, Any],
) -> Path:
    suffix = source.suffix.lower()
    target_base.parent.mkdir(parents=True, exist_ok=True)
    if suffix in _GRAPHRAG_TEXT_EXTENSIONS:
        shutil.copy2(source, target_base)
        return target_base

    target_path = target_base.with_suffix(".txt")
    text = _structured_document_to_text(source, method_options=method_options)
    if not text.strip():
        raise ValueError(f"Structured document is empty after conversion: {source}")
    target_path.write_text(text, encoding="utf-8")
    return target_path


def _structured_document_to_text(
    source: Path,
    *,
    method_options: Mapping[str, Any],
) -> str:
    suffix = source.suffix.lower()
    if suffix == ".json":
        return _json_document_to_text(source, method_options=method_options)
    if suffix == ".jsonl":
        return _jsonl_document_to_text(source, method_options=method_options)
    if suffix == ".parquet":
        return _parquet_document_to_text(source, method_options=method_options)
    raise ValueError(f"Unsupported structured document type for GraphRAG: {source.suffix}")


def _json_document_to_text(source: Path, *, method_options: Mapping[str, Any]) -> str:
    data = json.loads(source.read_text(encoding=str(method_options.get("encoding", "utf-8"))))
    return _structured_payload_to_text(data, source_name=source.name)


def _jsonl_document_to_text(source: Path, *, method_options: Mapping[str, Any]) -> str:
    lines: list[str] = [f"# Source: {source.name}", ""]
    encoding = str(method_options.get("encoding", "utf-8"))
    with source.open("r", encoding=encoding) as handle:
        for index, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            rendered = _structured_payload_to_text(payload, source_name=f"{source.name} line {index}")
            if rendered.strip():
                lines.append(rendered)
                lines.append("")
    return "\n".join(lines).strip() + "\n"


def _parquet_document_to_text(source: Path, *, method_options: Mapping[str, Any]) -> str:
    try:
        import pandas as pd
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Reading .parquet for GraphRAG requires pandas. Install it with `pip install pandas pyarrow`."
        ) from exc

    dataframe = pd.read_parquet(source)
    records = dataframe.to_dict(orient="records")
    return _structured_payload_to_text(records, source_name=source.name)


def _structured_payload_to_text(payload: Any, *, source_name: str) -> str:
    lines: list[str] = [f"# Source: {source_name}", ""]
    _append_structured_text(lines, payload, heading_level=2)
    rendered = "\n".join(line for line in lines if line is not None).strip()
    return rendered + ("\n" if rendered else "")


def _append_structured_text(lines: list[str], value: Any, *, heading_level: int) -> None:
    if value is None:
        return
    if isinstance(value, str):
        text = value.strip()
        if text:
            lines.append(text)
        return
    if isinstance(value, (int, float, bool)):
        lines.append(str(value))
        return
    if isinstance(value, list):
        for index, item in enumerate(value, start=1):
            lines.append(f"{'#' * heading_level} Item {index}")
            _append_structured_text(lines, item, heading_level=min(heading_level + 1, 6))
            lines.append("")
        return
    if isinstance(value, Mapping):
        preferred = _preferred_text_fields(value)
        if preferred:
            lines.extend(preferred)
            lines.append("")
        for key, item in value.items():
            if key in {"text", "content", "body", "description", "title", "summary", "question", "answer"}:
                continue
            pretty_key = str(key).replace("_", " ").strip().title()
            lines.append(f"{'#' * heading_level} {pretty_key}")
            _append_structured_text(lines, item, heading_level=min(heading_level + 1, 6))
            lines.append("")
        return
    lines.append(str(value))


def _preferred_text_fields(value: Mapping[str, Any]) -> list[str]:
    parts: list[str] = []
    title = value.get("title")
    if isinstance(title, str) and title.strip():
        parts.append(f"## {title.strip()}")
    for key in ["text", "content", "body", "description", "summary", "question", "answer"]:
        field = value.get(key)
        if isinstance(field, str) and field.strip():
            parts.append(field.strip())
    return parts


def is_probably_kg_json_path(path: str | Path) -> bool:
    candidate = Path(path)
    if candidate.suffix.lower() not in {".json", ".jsonl"} or not candidate.exists() or not candidate.is_file():
        return False
    try:
        if candidate.suffix.lower() == ".jsonl":
            sample: list[Any] = []
            with candidate.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    sample.append(json.loads(line))
                    if len(sample) >= 5:
                        break
            data = sample
        else:
            data = json.loads(candidate.read_text(encoding="utf-8"))
    except Exception:
        return False
    return _is_probably_kg_payload(data)


def _is_probably_kg_payload(data: Any) -> bool:
    if isinstance(data, Mapping):
        for key in ["kg", "triples", "relations", "relation_triples", "edges", "facts", "valid_triple", "triple"]:
            value = data.get(key)
            if value is not None and _looks_like_triple_collection(value):
                return True
        if _looks_like_triple_item(data):
            return True
        return False
    if isinstance(data, list):
        if data and all(isinstance(item, Mapping) for item in data[: min(5, len(data))]):
            for item in data[: min(5, len(data))]:
                if _is_probably_kg_payload(item):
                    return True
        return _looks_like_triple_collection(data)
    return False


def _looks_like_triple_collection(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    sample = value[: min(5, len(value))]
    return sum(1 for item in sample if _looks_like_triple_item(item)) >= max(1, len(sample) // 2)


def _looks_like_triple_item(item: Any) -> bool:
    if isinstance(item, str):
        return _parse_tagged_triple(item) is not None
    if isinstance(item, (list, tuple)) and len(item) >= 3:
        return True
    if not isinstance(item, Mapping):
        return False
    keys = {str(key).lower() for key in item.keys()}
    subject_keys = {"subject", "source", "head", "s", "from", "src"}
    relation_keys = {"relation", "predicate", "rel", "type", "r", "label"}
    object_keys = {"object", "target", "tail", "o", "to", "dst"}
    return bool(keys & subject_keys) and bool(keys & relation_keys) and bool(keys & object_keys)


def _run_graphrag_command(
    args: list[str],
    *,
    cwd: Path,
    method_options: Mapping[str, Any],
) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("graphrag")
    if executable is None:
        raise RuntimeError("GraphRAG is not installed. Install it with `pip install graphrag`.")

    env = os.environ.copy()
    api_key = (
        method_options.get("api_key")
        or env.get("GRAPHRAG_API_KEY")
        or env.get("OPENAI_API_KEY")
        or env.get("OPENAI_COMPAT_API_KEY")
        or env.get("DF_API_KEY")
    )
    if api_key:
        env["GRAPHRAG_API_KEY"] = str(api_key)

    timeout = int(method_options.get("timeout_seconds", 3600))
    completed = subprocess.run(
        [executable, *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    _write_graphrag_command_logs(cwd, args=args, completed=completed)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"GraphRAG command failed: graphrag {' '.join(args)}\n{detail}")
    return completed


def _write_graphrag_command_logs(
    working_dir: Path,
    *,
    args: list[str],
    completed: subprocess.CompletedProcess[str],
) -> None:
    command_name = args[0] if args else "command"
    logs_dir = working_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    header = f"\n[{datetime.now(timezone.utc).isoformat()}] graphrag {' '.join(args)} exit={completed.returncode}\n"
    for stream_name, text in [("stdout", completed.stdout), ("stderr", completed.stderr)]:
        if not text:
            continue
        log_path = logs_dir / f"kgagent_graphrag_{command_name}.{stream_name}.log"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(header)
            handle.write(text)
            if not text.endswith("\n"):
                handle.write("\n")


def _graphrag_chat_model(method_options: Mapping[str, Any]) -> str:
    return str(method_options.get("chat_model") or os.environ.get("GRAPHRAG_CHAT_MODEL") or os.environ.get("DF_LLM_MODEL") or "gpt-4.1")


def _graphrag_embedding_model(method_options: Mapping[str, Any]) -> str:
    return str(
        method_options.get("embedding_model")
        or os.environ.get("GRAPHRAG_EMBEDDING_MODEL")
        or os.environ.get("DF_EMBEDDING_MODEL")
        or "text-embedding-3-large"
    )


def _normalize_openai_base_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    url = value.strip().rstrip("/")
    suffix = "/chat/completions"
    if url.endswith(suffix):
        url = url[: -len(suffix)]
    return url


def _clean_graphrag_answer(stdout: str) -> str:
    text = stdout.strip()
    if "SUCCESS:" in text:
        return text.split("SUCCESS:", 1)[-1].strip()
    return text


def _input_type_hint(input_path: str | None) -> str | None:
    if not input_path:
        return None
    suffix = Path(input_path).suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}:
        return "office"
    if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".gif", ".webp"}:
        return "image"
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix in {".txt", ".json", ".jsonl", ".parquet"}:
        return "text"
    return None


def _reasoning_recommendation_reason(method_name: str, input_hint: str) -> str:
    if method_name == "rag_anything":
        if input_hint in {"pdf", "office", "image"}:
            return f"Recommended because `{input_hint}` inputs may contain layout, OCR, tables, figures, or other multimodal content."
        if input_hint in {"text", "markdown"}:
            return "Available for text input, but GraphRAG is usually the simpler default for single-modal text."
        return "Available for multimodal document QA."

    if method_name == "graphrag":
        if input_hint in {"text", "markdown"}:
            return f"Recommended because `{input_hint}` inputs are single-modal text and fit GraphRAG's text graph indexing path."
        if input_hint in {"pdf", "office", "image"}:
            return "Not the first choice for this input type; convert to text first or use RAG-Anything."
        return "Available for text-heavy corpora and existing graph/RAG storage."

    return "No specific recommendation reason is available."


def _reasoning_recommendation_rank(method_name: str, input_hint: str) -> int:
    if method_name == "rag_anything" and input_hint in {"pdf", "office", "image"}:
        return 0
    if method_name == "graphrag" and input_hint in {"text", "markdown"}:
        return 0
    if method_name == "rag_anything" and input_hint in {"text", "markdown"}:
        return 1
    if method_name == "graphrag" and input_hint in {"pdf", "office", "image"}:
        return 1
    return 2
