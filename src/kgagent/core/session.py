"""Session memory management for chat conversations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from datetime import datetime


@dataclass
class ExtractionRecord:
    """Record of a single extraction operation."""

    timestamp: datetime
    input_type: str  # "file" or "text"
    input_data: str  # file path or text content
    extraction_type: str  # triples, temporal, hyper, event, qa, completion
    result: dict[str, Any]
    output_path: str | None = None  # where result was saved
    metadata: dict[str, Any] = field(default_factory=dict)
    operation_kind: str = "extraction"  # extraction or reasoning
    task_name: str | None = None

    def __post_init__(self) -> None:
        if self.task_name is None:
            self.task_name = self.extraction_type

    def __str__(self) -> str:
        """Human-readable summary."""
        task_label = self.task_name or self.extraction_type
        if self.input_type == "file":
            return (
                f"[{self.timestamp.strftime('%H:%M:%S')}] "
                f"{self.operation_kind}:{task_label} from {self.input_data}"
            )
        preview = self.input_data[:50] + "..." if len(self.input_data) > 50 else self.input_data
        return f"[{self.timestamp.strftime('%H:%M:%S')}] {self.operation_kind}:{task_label}: {preview}"


class ChatSession:
    """Manages conversation context and extraction history for a chat session."""

    def __init__(self, max_history: int = 10):
        """Initialize chat session.

        Args:
            max_history: Maximum number of extraction records to keep
        """
        self.max_history = max_history
        self.history: list[ExtractionRecord] = []
        self.session_start = datetime.now()

    def add_extraction(
        self,
        input_type: str,
        input_data: str,
        extraction_type: str,
        result: dict[str, Any],
        output_path: str | None = None,
        **metadata,
    ) -> None:
        """Record a new extraction operation.

        Args:
            input_type: "file" or "text"
            input_data: File path or text content
            extraction_type: Type of extraction
            result: Extraction result
            output_path: Where result was saved (if applicable)
            **metadata: Additional metadata
        """
        record = ExtractionRecord(
            timestamp=datetime.now(),
            input_type=input_type,
            input_data=input_data,
            extraction_type=extraction_type,
            result=result,
            output_path=output_path,
            metadata=metadata,
            operation_kind="extraction",
            task_name=extraction_type,
        )

        self.history.append(record)

        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history :]

    def add_reasoning(
        self,
        input_type: str,
        input_data: str,
        task_type: str,
        result: dict[str, Any],
        output_path: str | None = None,
        **metadata,
    ) -> None:
        """Record a new reasoning operation."""
        record = ExtractionRecord(
            timestamp=datetime.now(),
            input_type=input_type,
            input_data=input_data,
            extraction_type=task_type,
            result=result,
            output_path=output_path,
            metadata=metadata,
            operation_kind="reasoning",
            task_name=task_type,
        )

        self.history.append(record)

        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history :]

    def get_last_record(self) -> ExtractionRecord | None:
        """Get the most recent record."""
        return self.history[-1] if self.history else None

    def get_last_extraction(self) -> ExtractionRecord | None:
        """Get the most recent extraction record.

        Returns:
            Last extraction record or None if no history
        """
        for record in reversed(self.history):
            if record.operation_kind == "extraction":
                return record
        return None

    def get_last_reasoning(self) -> ExtractionRecord | None:
        """Get the most recent reasoning record."""
        for record in reversed(self.history):
            if record.operation_kind == "reasoning":
                return record
        return None

    def get_last_n_extractions(self, n: int = 3) -> list[ExtractionRecord]:
        """Get the last N records.

        Args:
            n: Number of records to retrieve

        Returns:
            List of extraction records (most recent last)
        """
        return self.history[-n:] if self.history else []

    def get_last_result(self) -> dict[str, Any] | None:
        """Get the result of the last operation.

        Returns:
            Result dict or None
        """
        last = self.get_last_record()
        return last.result if last else None

    def get_last_file_path(self) -> str | None:
        """Get the file path of the last file-based operation.

        Returns:
            File path or None
        """
        for record in reversed(self.history):
            if record.input_type == "file":
                return record.input_data
        return None

    def get_last_output_path(self) -> str | None:
        """Get the output path of the last saved result.

        Returns:
            Output file path or None
        """
        for record in reversed(self.history):
            if record.output_path:
                return record.output_path
        return None

    def build_context_summary(self, max_records: int = 3) -> str:
        """Build a summary of recent conversation context for Intent Agent.

        Args:
            max_records: Maximum number of recent records to include

        Returns:
            Context summary string
        """
        if not self.history:
            return "No previous operations in this session."

        recent = self.get_last_n_extractions(max_records)

        lines = ["Recent session history:"]
        for i, record in enumerate(recent, 1):
            lines.append(f"{i}. {record}")
            if record.output_path:
                lines.append(f"   Saved to: {record.output_path}")

        return "\n".join(lines)

    def clear_history(self) -> None:
        """Clear all extraction history."""
        self.history.clear()

    def get_session_stats(self) -> dict[str, Any]:
        """Get session statistics.

        Returns:
            Dict with session stats
        """
        return {
            "session_duration": (datetime.now() - self.session_start).total_seconds(),
            "total_extractions": sum(1 for r in self.history if r.operation_kind == "extraction"),
            "total_reasoning": sum(1 for r in self.history if r.operation_kind == "reasoning"),
            "extraction_types": {
                "triples": sum(1 for r in self.history if r.operation_kind == "extraction" and r.task_name == "triples"),
                "temporal": sum(1 for r in self.history if r.operation_kind == "extraction" and r.task_name == "temporal"),
                "hyper": sum(1 for r in self.history if r.operation_kind == "extraction" and r.task_name == "hyper"),
                "event": sum(1 for r in self.history if r.operation_kind == "extraction" and r.task_name == "event"),
            },
            "reasoning_types": {
                "qa": sum(1 for r in self.history if r.operation_kind == "reasoning" and r.task_name == "qa"),
                "completion": sum(1 for r in self.history if r.operation_kind == "reasoning" and r.task_name == "completion"),
            },
            "files_processed": sum(1 for r in self.history if r.input_type == "file"),
            "text_extractions": sum(
                1 for r in self.history if r.input_type == "text" and r.operation_kind == "extraction"
            ),
        }

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"<ChatSession: {len(self.history)} operations, "
            f"started {self.session_start.strftime('%Y-%m-%d %H:%M:%S')}>"
        )
