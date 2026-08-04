"""Run state helpers for resumable benchmark generation."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


STAGE_NAMES = (
    "load_graph",
    "sample_subgraphs",
    "build_method_input",
    "run_method",
    "format_benchmark",
)


@dataclass
class BenchmarkRunState:
    """Mutable state persisted for one benchmark run."""

    run_id: str
    task: str
    graph_type: str
    method: str
    model: str
    sample_count: int
    status: str = "running"
    stages: dict[str, str] = field(default_factory=lambda: {name: "pending" for name in STAGE_NAMES})
    progress: dict[str, int] = field(
        default_factory=lambda: {
            "sampled": 0,
            "method_input": 0,
            "method_done": 0,
            "formatted": 0,
            "batch_done": 0,
            "batch_total": 0,
        }
    )

    @classmethod
    def load(cls, path: Path) -> "BenchmarkRunState":
        """Load a run state file."""
        raw = json.loads(path.read_text(encoding="utf-8"))
        state = cls(
            run_id=str(raw["run_id"]),
            task=str(raw["task"]),
            graph_type=str(raw["graph_type"]),
            method=str(raw["method"]),
            model=str(raw["model"]),
            sample_count=int(raw["sample_count"]),
            status=str(raw.get("status", "running")),
        )
        state.stages.update(raw.get("stages", {}))
        state.progress.update({k: int(v) for k, v in raw.get("progress", {}).items()})
        return state

    def to_dict(self) -> dict[str, Any]:
        """Convert state to JSON-serializable data."""
        return {
            "run_id": self.run_id,
            "task": self.task,
            "graph_type": self.graph_type,
            "method": self.method,
            "model": self.model,
            "sample_count": self.sample_count,
            "status": self.status,
            "stages": self.stages,
            "progress": self.progress,
        }

    def save(self, path: Path) -> None:
        """Atomically save the run state file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load JSONL rows. Missing files return an empty list."""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write JSONL rows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """Append JSONL rows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def completed_by_sample_id(path: Path) -> dict[str, dict[str, Any]]:
    """Load completed method outputs keyed by sample_id."""
    completed: dict[str, dict[str, Any]] = {}
    for row in load_jsonl(path):
        sample_id = row.get("sample_id")
        if sample_id:
            completed[str(sample_id)] = row
    return completed


def iter_batches(items: list[dict[str, Any]], batch_size: int) -> list[list[dict[str, Any]]]:
    """Split items into batches."""
    size = max(1, batch_size)
    return [items[idx : idx + size] for idx in range(0, len(items), size)]
