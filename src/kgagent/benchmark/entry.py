"""Public benchmark entry point."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from kgagent.benchmark.orchestrator import run_benchmark


class BenchmarkEntry:
    """Unified entry for benchmark generation."""

    def __init__(
        self,
        model_name: str = "gpt-4o-mini",
        work_dir: str | Path = ".",
        output_dir: str | Path | None = None,
    ):
        self.model_name = model_name
        self.work_dir = Path(work_dir)
        self.output_dir = Path(output_dir) if output_dir else self.work_dir / "outputs"

    def run(self, **kwargs: Any) -> dict[str, Any]:
        """Run benchmark generation synchronously."""
        return asyncio.run(self.run_async(**kwargs))

    async def run_async(self, **kwargs: Any) -> dict[str, Any]:
        """Run benchmark generation asynchronously."""
        return await run_benchmark(
            model=kwargs.pop("model", self.model_name),
            work_dir=self.work_dir,
            output_dir=self.output_dir,
            **kwargs,
        )
