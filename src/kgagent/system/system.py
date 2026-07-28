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
from kgagent.benchmark import BenchmarkEntry
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

    def benchmark(
        self,
        data: str,
        *,
        graph_type: str = "KG",
        task: str = "KGQA",
        method: str = "sgsh_prompt",
        sample_count: int = 5,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        output_path: str | None = None,
        temperature: float = 0.7,
        parallelism: int = 4,
    ) -> dict[str, Any]:
        """Generate KGQA/KGQG benchmark data synchronously."""
        return asyncio.run(
            self.benchmark_async(
                data=data,
                graph_type=graph_type,
                task=task,
                method=method,
                sample_count=sample_count,
                model=model,
                base_url=base_url,
                api_key=api_key,
                output_path=output_path,
                temperature=temperature,
                parallelism=parallelism,
            )
        )

    async def benchmark_async(
        self,
        data: str,
        *,
        graph_type: str = "KG",
        task: str = "KGQA",
        method: str = "sgsh_prompt",
        sample_count: int = 5,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        output_path: str | None = None,
        temperature: float = 0.7,
        parallelism: int = 4,
    ) -> dict[str, Any]:
        """Generate KGQA/KGQG benchmark data asynchronously."""
        entry = BenchmarkEntry(
            model_name=model or "gpt-4o-mini",
            work_dir=self.work_dir,
            output_dir=self.work_dir / "outputs",
        )
        return await entry.run_async(
            data=data,
            graph_type=graph_type,
            task=task,
            method=method,
            sample_count=sample_count,
            base_url=base_url,
            api_key=api_key,
            output_path=output_path,
            temperature=temperature,
            parallelism=parallelism,
        )

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

    def convert(
        self,
        input_data: str | Path | dict | list,
        output_format: str,
        output_path: str | Path | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Convert KG data to specified format.

        Args:
            input_data: Input data (file path, dict, or list)
            output_format: Target format (neo4j_csv, rdf, graphml, etc.)
            output_path: Output file/directory path
            **kwargs: Additional format-specific options

        Returns:
            Conversion result with paths and statistics

        Examples:
            >>> system = KGAgentSystem()
            >>> result = system.convert("data.json", "neo4j_csv", "output/")
            >>> result = system.convert(kg_data, "graphml", "graph.graphml")
        """
        from kgagent.conversion.entry import ConversionEntry

        converter = ConversionEntry()
        return converter.convert(input_data, output_format, output_path, **kwargs)

    def convert_from(
        self,
        input_path: str | Path,
        output_path: str | Path | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Convert from external format to JSON KG format.

        Args:
            input_path: Input file path (.dump, .graphml, etc.)
            output_path: Output JSON file path
            **kwargs: Additional format-specific options (e.g., neo4j_home)

        Returns:
            Conversion result with paths and statistics

        Examples:
            >>> system = KGAgentSystem()
            >>> result = system.convert_from("neo4j.dump", "output.json")
            >>> result = system.convert_from("graph.graphml", "output.json")
        """
        from kgagent.conversion.entry import ConversionEntry

        converter = ConversionEntry()
        return converter.convert_from(input_path, output_path, **kwargs)

    def parse_document(
        self,
        input_path: str | Path,
        output_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Parse document to Markdown using MinerU.

        Args:
            input_path: Input document path (PDF, DOCX, PPTX, etc.)
            output_path: Output markdown path (defaults to input path with .md extension)

        Returns:
            Parse result with keys:
            - input_file: Input file path
            - output_file: Output markdown file path
            - format: Output format (markdown)
            - pages: Number of pages (if available)
            - mode: Parsing mode (flash or precision)
            - success: Whether parsing succeeded

        Examples:
            >>> system = KGAgentSystem()
            >>> result = system.parse_document("paper.pdf")
            >>> # Output: paper.md (same directory)
            >>> result = system.parse_document("paper.pdf", "parsed/paper.md")
        """
        from kgagent.mineru import MinerUParser
        import asyncio

        parser = MinerUParser(work_dir=self.work_dir)

        # Try to get current event loop
        try:
            loop = asyncio.get_running_loop()
            # We're in an async context, use create_task
            # But we need to return synchronously, so we'll use run_coroutine_threadsafe
            import concurrent.futures
            future = asyncio.run_coroutine_threadsafe(
                parser.parse_document(input_path, output_path),
                loop
            )
            return future.result()
        except RuntimeError:
            # No running loop, use asyncio.run
            return asyncio.run(parser.parse_document(input_path, output_path))

    async def parse_document_async(
        self,
        input_path: str | Path,
        output_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Parse document to Markdown using MinerU (async version).

        Args:
            input_path: Input document path (PDF, DOCX, PPTX, etc.)
            output_path: Output markdown path (defaults to input path with .md extension)

        Returns:
            Parse result dict with keys:
            - success: bool
            - input_file: str
            - output_file: str
            - images_dir: str (if images exist)
            - images_count: int (if images exist)
        """
        import os
        from kgagent.mineru import MinerUParser

        # Get token from environment
        token = os.getenv("MINERU_API_TOKEN")

        parser = MinerUParser(work_dir=self.work_dir, token=token)

        # Use CLI method (synchronous, but we run it in executor for async compatibility)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            parser.parse_document,
            input_path,
            output_path
        )
