"""Document type detection for MinerU parser."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# Supported document formats
SUPPORTED_EXTENSIONS = {
    # PDF
    '.pdf': 'pdf',

    # Microsoft Word
    '.docx': 'word',
    '.doc': 'word',

    # Microsoft PowerPoint
    '.pptx': 'powerpoint',
    '.ppt': 'powerpoint',

    # Images
    '.png': 'image',
    '.jpg': 'image',
    '.jpeg': 'image',
    '.bmp': 'image',
    '.gif': 'image',
    '.tiff': 'image',
}


def is_parseable_document(file_path: str | Path) -> bool:
    """Check if file is a parseable document (non-JSON).

    Args:
        file_path: Path to file

    Returns:
        True if file can be parsed by MinerU
    """
    try:
        path = Path(file_path)
        suffix = path.suffix.lower()
        return suffix in SUPPORTED_EXTENSIONS
    except (ValueError, OSError):
        return False


def get_document_type(file_path: str | Path) -> str:
    """Get document type.

    Args:
        file_path: Path to file

    Returns:
        Document type: pdf, word, powerpoint, image, or unknown
    """
    try:
        path = Path(file_path)
        suffix = path.suffix.lower()
        return SUPPORTED_EXTENSIONS.get(suffix, 'unknown')
    except (ValueError, OSError):
        return 'unknown'


def get_output_path(input_path: str | Path, output_path: str | Path | None = None) -> Path:
    """Get output path for parsed document.

    Args:
        input_path: Input document path
        output_path: Optional output path

    Returns:
        Output path (defaults to input path with .md extension)
    """
    input_path = Path(input_path)

    if output_path is None:
        # Default: same directory as input, .md extension
        return input_path.with_suffix('.md')

    output_path = Path(output_path)

    # If output is a directory, generate filename
    if output_path.is_dir() or not output_path.suffix:
        output_path = output_path / f"{input_path.stem}.md"

    return output_path
