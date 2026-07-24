"""KGAgentSystem — the unified entry point for knowledge graph extraction.

Single task:
    system = KGAgentSystem()
    result = system.extract("extract triples from: Alice works at Acme")

With file:
    result = system.extract("examples/data.json", extraction_type="hyper")

Batch (parallel):
    results = asyncio.run(system.extract_batch([
        {"data": "...", "extraction_type": "triples"},
        {"data": "...", "extraction_type": "temporal"},
    ], max_concurrency=4))
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from kgagent.core.config import get_model
from kgagent.core.logging_setup import setup_logging
from kgagent.system.orchestrator import run_extraction
from kgagent.system.registry import ExtractionRegistry

logger = logging.getLogger(__name__)


class KGAgentSystem:
    """Unified entry point for knowledge graph extraction."""

    def __init__(
        self,
        model_name: str | None = None,
        work_dir: str | Path = "./tmp_sdk",
        permission_mode: str = "bypassPermissions",
        max_turns: int = 20,
    ):
        """Initialize KGAgent system.

        Args:
            model_name: Model name (default: from KG_MODEL env var)
            work_dir: Working directory for outputs
            permission_mode: Permission mode for Claude SDK
            max_turns: Maximum turns for extraction agent
        """
        setup_logging()
        self.registry = ExtractionRegistry()
        self.model_name = model_name or get_model()
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.permission_mode = permission_mode
        self.max_turns = max_turns

        logger.info(
            f"KGAgentSystem initialized: model={self.model_name}, "
            f"work_dir={self.work_dir}"
        )

    def extract(
        self,
        data: str | dict | list,
        extraction_type: str = "auto",
        *,
        validate: bool = False,
        save_to: str | None = None,
    ) -> dict[str, Any]:
        """Extract knowledge graph from data (synchronous).

        Args:
            data: Input data (file path, inline text, or JSON data)
            extraction_type: Type of extraction (auto, triples, temporal, hyper)
            validate: Whether to validate the result
            save_to: Optional path to save result

        Returns:
            Extraction result as dict

        Examples:
            >>> system = KGAgentSystem()
            >>> result = system.extract("Alice works at Acme", extraction_type="triples")
            >>> result = system.extract("data.json", extraction_type="hyper")
        """
        return asyncio.run(
            self.extract_async(
                data,
                extraction_type,
                validate=validate,
                save_to=save_to,
            )
        )

    async def extract_async(
        self,
        data: str | dict | list,
        extraction_type: str = "auto",
        *,
        validate: bool = False,
        save_to: str | None = None,
    ) -> dict[str, Any]:
        """Extract knowledge graph from data (async).

        Args:
            data: Input data (file path, inline text, or JSON data)
            extraction_type: Type of extraction (auto, triples, temporal, hyper)
            validate: Whether to validate the result
            save_to: Optional path to save result

        Returns:
            Extraction result as dict
        """
        logger.info(f"Starting extraction: type={extraction_type}")

        result = await run_extraction(
            data=data,
            extraction_type=extraction_type,
            model_name=self.model_name,
            work_dir=self.work_dir,
            permission_mode=self.permission_mode,
            max_turns=self.max_turns,
            registry=self.registry,
            validate=validate,
            save_to=save_to,
        )

        logger.info("Extraction complete")
        return result

    async def extract_batch(
        self,
        tasks: list[dict[str, Any]],
        max_concurrency: int = 4,
    ) -> list[dict[str, Any]]:
        """Extract multiple tasks in parallel.

        Args:
            tasks: List of task dicts with keys: data, extraction_type, validate, save_to
            max_concurrency: Maximum concurrent extractions

        Returns:
            List of extraction results

        Example:
            >>> tasks = [
            ...     {"data": "Alice works at Acme", "extraction_type": "triples"},
            ...     {"data": "Bob met Carol in 2020", "extraction_type": "temporal"},
            ... ]
            >>> results = await system.extract_batch(tasks, max_concurrency=2)
        """
        semaphore = asyncio.Semaphore(max_concurrency)

        async def _extract_with_semaphore(task: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                return await self.extract_async(
                    data=task["data"],
                    extraction_type=task.get("extraction_type", "auto"),
                    validate=task.get("validate", False),
                    save_to=task.get("save_to"),
                )

        logger.info(f"Starting batch extraction: {len(tasks)} tasks")
        results = await asyncio.gather(
            *[_extract_with_semaphore(task) for task in tasks],
            return_exceptions=True,
        )

        # Convert exceptions to error dicts
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Task {i} failed: {result}")
                processed_results.append(
                    {
                        "error": str(result),
                        "task": tasks[i],
                    }
                )
            else:
                processed_results.append(result)

        logger.info(f"Batch extraction complete: {len(processed_results)} results")
        return processed_results
