"""Entry point for the reasoning route."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Mapping

from kgagent.core.json_io import load_json
from kgagent.extraction.config import ExtractionConfig
from kgagent.reasoning.QA.manager import (
    run_graphrag_qa_for_agent,
    run_rag_anything_qa_for_agent_async,
)
from kgagent.reasoning.completion.manager import run_kicgpt_kg_completion_for_agent

logger = logging.getLogger(__name__)


class ReasoningEntry:
    """Unified entry point for reasoning tasks."""

    def __init__(self, config: ExtractionConfig):
        self.config = config

    def reason(
        self,
        data: str | dict[str, Any],
        task_type: str,
    ) -> dict[str, Any]:
        """Synchronous reasoning wrapper."""
        return asyncio.run(self.reason_async(data, task_type))

    async def reason_async(
        self,
        data: str | dict[str, Any],
        task_type: str,
    ) -> dict[str, Any]:
        """Run a reasoning task."""
        normalized = self._normalize_input(data, task_type)
        workspace = self.config.work_dir
        logger.info("ReasoningEntry received task: task_type=%s", task_type)

        if task_type == "qa":
            method = str(normalized.get("method") or "auto").strip().lower()
            logger.info("ReasoningEntry dispatching QA task: requested_method=%s", method or "auto")
            return await self._run_qa(normalized, method, workspace)

        if task_type == "completion":
            logger.info("ReasoningEntry dispatching completion task")
            return run_kicgpt_kg_completion_for_agent(normalized, workspace=workspace)

        raise ValueError(f"Unknown reasoning task type: {task_type}")

    async def _run_qa(
        self,
        input_data: dict[str, Any],
        method: str,
        workspace: str,
    ) -> dict[str, Any]:
        if method in {"", "auto"}:
            method = self._infer_qa_method(input_data)
            logger.info("ReasoningEntry inferred QA method: method=%s", method)
        else:
            logger.info("ReasoningEntry using explicit QA method: method=%s", method)

        if method == "rag_anything":
            logger.info("ReasoningEntry routed QA task to rag_anything")
            return await run_rag_anything_qa_for_agent_async(input_data, workspace=workspace)
        if method == "graphrag":
            logger.info("ReasoningEntry routed QA task to graphrag")
            return run_graphrag_qa_for_agent(input_data, workspace=workspace)

        raise ValueError(f"Unsupported QA reasoning method: {method}")

    def _normalize_input(
        self,
        data: str | dict[str, Any],
        task_type: str,
    ) -> dict[str, Any]:
        if isinstance(data, Mapping):
            normalized = dict(data)
        elif isinstance(data, str):
            normalized = self._normalize_string_input(data, task_type)
        else:
            raise ValueError("Reasoning input must be a dict or string path/JSON payload.")

        if "task_type" not in normalized:
            normalized["task_type"] = task_type
        if task_type == "qa":
            normalized = self._normalize_qa_job_request(normalized)
        return normalized

    def _normalize_string_input(self, data: str, task_type: str) -> dict[str, Any]:
        text = data.strip()
        if not text:
            raise ValueError("Reasoning input cannot be empty.")

        path = Path(text)
        if path.exists() and path.is_file():
            if path.suffix.lower() in {".json", ".jsonl"}:
                loaded = load_json(path)
                if isinstance(loaded, Mapping):
                    return dict(loaded)
                raise ValueError(f"Expected JSON object in reasoning input file: {path}")
            return self._default_path_payload(task_type, str(path))

        if text.startswith("{"):
            import json

            loaded = json.loads(text)
            if isinstance(loaded, Mapping):
                return dict(loaded)
            raise ValueError("Reasoning JSON input must be an object.")

        return self._default_path_payload(task_type, text)

    def _default_path_payload(self, task_type: str, path_text: str) -> dict[str, Any]:
        if task_type == "qa":
            return {
                "input_path": path_text,
                "method": "auto",
            }
        if task_type == "completion":
            return {
                "kg_input": path_text,
            }
        return {"input_path": path_text}

    def _infer_qa_method(self, input_data: Mapping[str, Any]) -> str:
        raw_input_path = str(input_data.get("input_path") or "")
        input_path = raw_input_path.lower()
        if input_path.endswith((".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")):
            return "rag_anything"
        return "graphrag"

    def _normalize_qa_job_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        input_path = str(normalized.get("input_path") or "").strip()
        if not input_path:
            legacy_path = normalized.get("file_path") or normalized.get("path")
            if isinstance(legacy_path, str) and legacy_path.strip():
                input_path = legacy_path.strip()

        questions_path = normalized.get("questions_path") or normalized.get("question_path") or ""
        if not isinstance(questions_path, str):
            questions_path = ""

        method_options = normalized.get("method_options", {})
        if not isinstance(method_options, Mapping):
            method_options = {}

        save_kg_json = bool(
            normalized.get("save_kg_json")
            or normalized.get("export_kg_json")
            or method_options.get("save_kg_json")
            or method_options.get("export_kg_json")
        )
        kg_output_path = (
            normalized.get("kg_output_path")
            or normalized.get("graph_output_path")
            or method_options.get("kg_output_path")
            or method_options.get("graph_output_path")
            or ""
        )

        normalized["task_type"] = "qa"
        normalized["method"] = str(normalized.get("method") or "auto").strip().lower() or "auto"
        normalized["input_path"] = input_path
        normalized["input_kind"] = str(
            normalized.get("input_kind") or self._infer_input_kind(input_path)
        ).strip().lower()
        normalized["question"] = str(normalized.get("question") or "").strip()
        normalized["questions_path"] = questions_path.strip()
        normalized["working_dir"] = str(normalized.get("working_dir") or "").strip()
        normalized["reuse_existing"] = bool(normalized.get("reuse_existing", True))
        normalized["build_if_missing"] = bool(normalized.get("build_if_missing", True))
        normalized["save_kg_json"] = save_kg_json
        normalized["kg_output_path"] = str(kg_output_path).strip()
        normalized["method_options"] = dict(method_options)
        normalized.pop("export_kg_json", None)
        normalized.pop("question_path", None)
        return normalized

    def _infer_input_kind(self, input_path: str) -> str:
        if not input_path:
            return "unknown"
        suffix = Path(input_path).suffix.lower()
        if suffix == ".pdf":
            return "pdf"
        if suffix in {".md", ".markdown"}:
            return "md"
        if suffix == ".txt":
            return "txt"
        if suffix == ".json":
            return "json"
        if suffix == ".jsonl":
            return "jsonl"
        if suffix == ".parquet":
            return "parquet"
        if suffix in {".doc", ".docx", ".ppt", ".pptx", ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tiff", ".webp"}:
            return suffix.lstrip(".")
        return suffix.lstrip(".") or "unknown"
