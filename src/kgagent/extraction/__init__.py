"""Extraction route for KGAgent."""

from kgagent.extraction.kg_entry import ExtractionEntry
from kgagent.extraction.config import ExtractionConfig
from kgagent.extraction.document_processor import (
    chunk_text,
    preprocess_document,
    process_pdf_with_type,
)
from kgagent.extraction.merge import merge_kg_chunks

__all__ = [
    "ExtractionEntry",
    "ExtractionConfig",
    "chunk_text",
    "preprocess_document",
    "process_pdf_with_type",
    "merge_kg_chunks",
]
