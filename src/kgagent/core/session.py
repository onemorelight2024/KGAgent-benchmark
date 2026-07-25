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
    extraction_type: str  # triples, temporal, hyper, event
    result: dict[str, Any]
    output_path: str | None = None  # where result was saved
    metadata: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        """Human-readable summary."""
        if self.input_type == "file":
            return f"[{self.timestamp.strftime('%H:%M:%S')}] {self.extraction_type} from {self.input_data}"
        else:
            preview = self.input_data[:50] + "..." if len(self.input_data) > 50 else self.input_data
            return f"[{self.timestamp.strftime('%H:%M:%S')}] {self.extraction_type}: {preview}"


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
        )

        self.history.append(record)

        # Keep only recent history
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history :]

    def get_last_extraction(self) -> ExtractionRecord | None:
        """Get the most recent extraction record.

        Returns:
            Last extraction record or None if no history
        """
        return self.history[-1] if self.history else None

    def get_last_n_extractions(self, n: int = 3) -> list[ExtractionRecord]:
        """Get the last N extraction records.

        Args:
            n: Number of records to retrieve

        Returns:
            List of extraction records (most recent last)
        """
        return self.history[-n:] if self.history else []

    def get_last_result(self) -> dict[str, Any] | None:
        """Get the result of the last extraction.

        Returns:
            Extraction result dict or None
        """
        last = self.get_last_extraction()
        return last.result if last else None

    def get_last_file_path(self) -> str | None:
        """Get the file path of the last file-based extraction.

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
            return "No previous extractions in this session."

        recent = self.get_last_n_extractions(max_records)

        lines = ["Recent extraction history:"]
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
            "total_extractions": len(self.history),
            "extraction_types": {
                "triples": sum(1 for r in self.history if r.extraction_type == "triples"),
                "temporal": sum(1 for r in self.history if r.extraction_type == "temporal"),
                "hyper": sum(1 for r in self.history if r.extraction_type == "hyper"),
                "event": sum(1 for r in self.history if r.extraction_type == "event"),
            },
            "files_processed": sum(1 for r in self.history if r.input_type == "file"),
            "text_extractions": sum(1 for r in self.history if r.input_type == "text"),
        }

    def __repr__(self) -> str:
        """String representation."""
        return f"<ChatSession: {len(self.history)} extractions, started {self.session_start.strftime('%Y-%m-%d %H:%M:%S')}>"
