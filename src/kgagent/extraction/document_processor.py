"""Document preprocessing and chunking for extraction."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def chunk_text(
    text: str,
    chunk_size: int = 2000,
    overlap: int = 200,
) -> list[dict[str, Any]]:
    """Split text into overlapping chunks.

    Args:
        text: Text to chunk
        chunk_size: Maximum characters per chunk
        overlap: Number of characters to overlap between chunks

    Returns:
        List of chunks with format: [{"index": "chunk_001", "text": "..."}]
    """
    if not text or not text.strip():
        return []

    chunks = []
    start = 0
    chunk_num = 1

    while start < len(text):
        # Get chunk end position
        end = start + chunk_size

        # If not the last chunk, try to break at sentence boundary
        if end < len(text):
            # Look for sentence endings in the last 200 chars
            search_start = max(start, end - 200)
            for sep in [". ", "。", "! ", "！", "? ", "？", "\n\n"]:
                last_sep = text.rfind(sep, search_start, end)
                if last_sep != -1:
                    end = last_sep + len(sep)
                    break

        chunk_text = text[start:end].strip()
        if chunk_text:
            chunks.append({
                "index": f"chunk_{chunk_num:03d}",
                "text": chunk_text,
            })
            chunk_num += 1

        # Move start position with overlap
        start = end - overlap if end < len(text) else end

    logger.info(f"Split text into {len(chunks)} chunks (size={chunk_size}, overlap={overlap})")
    return chunks


def md_to_json(md_content: str) -> str:
    """Convert markdown content to JSON text.

    For now, just returns the markdown as plain text.
    Could be enhanced to preserve structure.

    Args:
        md_content: Markdown content

    Returns:
        Text content
    """
    # Simple conversion: strip markdown syntax
    # Could be enhanced to parse headers, lists, etc.
    return md_content


def txt_to_json(txt_content: str) -> str:
    """Convert plain text to JSON text.

    Args:
        txt_content: Plain text content

    Returns:
        Text content
    """
    return txt_content


def jsonl_to_json(jsonl_content: str) -> list[dict[str, Any]]:
    """Convert JSONL to JSON list.

    Args:
        jsonl_content: JSONL content (one JSON object per line)

    Returns:
        List of JSON objects
    """
    lines = jsonl_content.strip().split("\n")
    result = []

    for i, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            result.append(obj)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse JSONL line {i}: {e}")
            continue

    return result


def detect_pdf_type(md_content: str, images_dir: Path | None = None) -> str:
    """Detect if PDF is text-only or mixed content.

    Args:
        md_content: Markdown content from MinerU
        images_dir: Directory containing extracted images

    Returns:
        "text_only" or "mixed_content"
    """
    # Count meaningful images (skip small decorative images)
    meaningful_images = 0

    if images_dir and images_dir.exists():
        try:
            from PIL import Image
            for img_file in images_dir.glob("*.jpg"):
                try:
                    with Image.open(img_file) as img:
                        width, height = img.size
                        # Skip small images (likely decorative headers/footers)
                        # Meaningful images are usually > 100x100 pixels
                        if width > 100 and height > 100:
                            meaningful_images += 1
                except Exception:
                    pass
        except ImportError:
            # Pillow not available, fall back to counting all images
            image_files = list(images_dir.glob("*.jpg")) + list(images_dir.glob("*.png"))
            meaningful_images = len(image_files)

    # Count image references in markdown
    image_refs = md_content.count("![")

    # If there are many meaningful images, consider it mixed content
    if meaningful_images > 3:
        return "mixed_content"

    # Also check if there are image references but very little text
    text_length = len(md_content.replace("![", "").replace("](", ""))
    if image_refs > 3 and text_length < 500:
        return "mixed_content"

    return "text_only"


async def preprocess_document(
    input_path: str | Path,
    chunk_size: int = 2000,
    overlap: int = 200,
) -> dict[str, Any]:
    """Preprocess document and split into chunks.

    Workflow:
    1. Detect file type
    2. Convert to text/JSON
    3. Split into chunks
    4. Return chunked data

    Args:
        input_path: Input file path
        chunk_size: Maximum characters per chunk
        overlap: Overlap between chunks

    Returns:
        Result dict with keys:
        - success: bool
        - file_type: str (json, pdf, md, txt, jsonl)
        - pdf_type: str (text_only, mixed_content) if PDF
        - chunks: list[dict] - chunked data
        - original_file: str
        - images_dir: str (if PDF with images)
    """
    from kgagent.system.system import KGAgentSystem

    input_path = Path(input_path)

    if not input_path.exists():
        return {
            "success": False,
            "error": f"File not found: {input_path}",
        }

    result = {
        "success": False,
        "original_file": str(input_path),
        "file_type": input_path.suffix.lower().lstrip("."),
    }

    file_ext = input_path.suffix.lower()

    try:
        # Handle JSON input
        if file_ext == ".json":
            with open(input_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # If it's a string, chunk it
            if isinstance(data, str):
                chunks = chunk_text(data, chunk_size, overlap)
            # If it's a dict with text field
            elif isinstance(data, dict) and "text" in data:
                chunks = chunk_text(data["text"], chunk_size, overlap)
            # If it's a list, process each item
            elif isinstance(data, list):
                chunks = []
                for i, item in enumerate(data, 1):
                    if isinstance(item, str):
                        text = item
                    elif isinstance(item, dict):
                        text = item.get("text") or item.get("content") or str(item)
                    else:
                        text = str(item)

                    item_chunks = chunk_text(text, chunk_size, overlap)
                    # Update indices to include item number
                    for chunk in item_chunks:
                        chunk["index"] = f"item_{i:03d}_{chunk['index']}"
                    chunks.extend(item_chunks)
            else:
                # Fallback: convert to string and chunk
                chunks = chunk_text(str(data), chunk_size, overlap)

            result["chunks"] = chunks
            result["success"] = True

        # Handle PDF input
        elif file_ext == ".pdf":
            system = KGAgentSystem()

            # Check if markdown already exists
            expected_md = input_path.parent / f"{input_path.stem}.md"
            expected_chunks = input_path.parent / f"{input_path.stem}_chunks.json"

            # Try to load existing chunks first
            if expected_chunks.exists():
                try:
                    with open(expected_chunks, "r", encoding="utf-8") as f:
                        existing_chunks = json.load(f)

                    if existing_chunks and isinstance(existing_chunks, list):
                        logger.info(f"Found existing chunks file: {expected_chunks}")
                        logger.info(f"Loaded {len(existing_chunks)} chunks from cache")

                        result["chunks"] = existing_chunks
                        result["success"] = True
                        result["pdf_type"] = "text_only"  # Assume text_only if chunks exist
                        result["cached"] = True
                        result["parse_result"] = {
                            "markdown_file": str(expected_md) if expected_md.exists() else None,
                            "chunks_file": str(expected_chunks),
                        }

                        logger.info(f"Skipped parsing, using cached chunks: {len(existing_chunks)} chunks")
                        return result
                except Exception as e:
                    logger.warning(f"Failed to load existing chunks: {e}, will re-parse")

            # If no cached chunks, parse the PDF
            logger.info(f"No cached chunks found, parsing PDF...")

            # Parse with MinerU
            parse_result = await system.parse_document_async(input_path)

            if not parse_result.get("success"):
                result["error"] = parse_result.get("error", "PDF parsing failed")
                return result

            # Read markdown content
            md_file = Path(parse_result["output_file"])
            md_content = md_file.read_text(encoding="utf-8")

            # Return preprocessing result with parse info for user confirmation
            result["parse_result"] = {
                "markdown_file": str(md_file),
                "markdown_length": len(md_content),
                "images_count": parse_result.get("images_count", 0),
                "images_dir": parse_result.get("images_dir"),
            }
            result["success"] = True
            result["requires_user_confirmation"] = True
            result["cached"] = False
            result["message"] = (
                f"PDF parsed successfully:\n"
                f"  - Markdown: {len(md_content)} characters\n"
                f"  - Images: {parse_result.get('images_count', 0)} extracted\n"
                f"Please confirm PDF type: 'text_only' or 'mixed_content'"
            )

            # Store markdown content for later chunking
            result["_md_content"] = md_content

        # Handle Markdown input
        elif file_ext == ".md":
            md_content = input_path.read_text(encoding="utf-8")
            text = md_to_json(md_content)
            chunks = chunk_text(text, chunk_size, overlap)

            result["chunks"] = chunks
            result["success"] = True

        # Handle TXT input
        elif file_ext == ".txt":
            txt_content = input_path.read_text(encoding="utf-8")
            text = txt_to_json(txt_content)
            chunks = chunk_text(text, chunk_size, overlap)

            result["chunks"] = chunks
            result["success"] = True

        # Handle JSONL input
        elif file_ext == ".jsonl":
            jsonl_content = input_path.read_text(encoding="utf-8")
            json_list = jsonl_to_json(jsonl_content)

            # Process each line
            chunks = []
            for i, item in enumerate(json_list, 1):
                if isinstance(item, str):
                    text = item
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content") or str(item)
                else:
                    text = str(item)

                item_chunks = chunk_text(text, chunk_size, overlap)
                # Update indices
                for chunk in item_chunks:
                    chunk["index"] = f"line_{i:03d}_{chunk['index']}"
                chunks.extend(item_chunks)

            result["chunks"] = chunks
            result["success"] = True

        else:
            result["error"] = f"Unsupported file type: {file_ext}"
            return result

        logger.info(
            f"Preprocessed {input_path.name}: "
            f"type={result['file_type']}, chunks={len(result.get('chunks', []))}"
        )

    except Exception as e:
        logger.error(f"Preprocessing failed: {e}")
        result["success"] = False
        result["error"] = str(e)

    return result


def process_pdf_with_type(
    preprocessing_result: dict[str, Any],
    pdf_type: str,
    chunk_size: int = 2000,
    overlap: int = 200,
) -> dict[str, Any]:
    """Process PDF after user confirms the type.

    Args:
        preprocessing_result: Result from preprocess_document
        pdf_type: User-confirmed type ("text_only" or "mixed_content")
        chunk_size: Maximum characters per chunk
        overlap: Overlap between chunks

    Returns:
        Result dict with chunked data or error
    """
    result = {
        "success": False,
        "pdf_type": pdf_type,
    }

    if pdf_type == "mixed_content":
        result["error"] = "Mixed content PDF not yet supported (images + text)"
        return result

    # Text-only PDF: chunk the markdown content
    md_content = preprocessing_result.get("_md_content", "")
    if not md_content:
        result["error"] = "No markdown content found in preprocessing result"
        return result

    text = md_to_json(md_content)
    chunks = chunk_text(text, chunk_size, overlap)

    result["chunks"] = chunks
    result["success"] = True
    result["file_type"] = "pdf"

    logger.info(f"PDF processed as {pdf_type}: {len(chunks)} chunks created")

    return result

    return result
