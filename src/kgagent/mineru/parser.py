"""MinerU document parser using official CLI."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from kgagent.mineru.detector import get_output_path

logger = logging.getLogger(__name__)


def parse_with_cli(
    input_path: Path,
    output_dir: Path,
    token: str | None = None,
) -> dict[str, Any]:
    """Parse document using official mineru-open-api CLI.

    This method ensures images and all assets are properly saved.

    Args:
        input_path: Input document path
        output_dir: Output directory for results
        token: MinerU API token (optional)

    Returns:
        Result dict with success status
    """
    result = {
        "success": False,
        "input_file": str(input_path),
        "output_dir": str(output_dir),
    }

    if not input_path.exists():
        result["error"] = f"Input file not found: {input_path}"
        return result

    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build CLI command
    cmd = [
        "mineru-open-api",
        "extract",
        str(input_path),
        "-o",
        str(output_dir),
    ]

    # Add token if provided
    if token:
        cmd.extend(["--token", token])
        logger.info(f"Using precision mode with API token (length: {len(token)})")
    else:
        logger.warning("No API token provided, will use flash mode")

    logger.info(f"Running CLI: {' '.join(cmd[:4])} ...")

    try:
        # Run CLI command
        process = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minutes timeout
        )

        if process.returncode != 0:
            result["error"] = f"CLI failed with exit code {process.returncode}: {process.stderr}"
            logger.error(result["error"])
            return result

        # Check if markdown file was created
        expected_md = output_dir / f"{input_path.stem}.md"
        if not expected_md.exists():
            result["error"] = f"Expected output file not found: {expected_md}"
            return result

        result["success"] = True
        result["output_file"] = str(expected_md)
        result["size"] = expected_md.stat().st_size

        # Count images
        images_dir = output_dir / "images"
        if images_dir.exists():
            image_files = list(images_dir.glob("*.jpg")) + list(images_dir.glob("*.png"))
            result["images_count"] = len(image_files)
            result["images_dir"] = str(images_dir)
            logger.info(f"✓ Extracted {len(image_files)} images to {images_dir}")

        logger.info(f"✓ CLI parsing successful: {expected_md}")

    except subprocess.TimeoutExpired:
        result["error"] = "CLI command timed out after 5 minutes"
        logger.error(result["error"])
    except Exception as e:
        result["error"] = f"CLI execution failed: {e}"
        logger.error(result["error"])

    return result


class MinerUParser:
    """MinerU document parser using official CLI tool."""

    def __init__(
        self,
        work_dir: str | Path = "./tmp_sdk",
        token: str | None = None,
    ):
        """Initialize MinerU parser.

        Args:
            work_dir: Working directory (not used by CLI, kept for compatibility)
            token: MinerU API token (optional, defaults to env MINERU_API_TOKEN)
        """
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)

        # Get token from parameter or environment
        self.token = token or os.getenv("MINERU_API_TOKEN", "")

        logger.info(
            f"MinerU parser initialized: "
            f"mode={'precision' if self.token else 'flash (lightweight)'}, "
            f"token_length={len(self.token) if self.token else 0}"
        )

    def parse_document(
        self,
        input_path: str | Path,
        output_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Parse document using official CLI (saves images and all assets).

        This method uses the mineru-open-api CLI tool which properly
        downloads and saves all assets including images.

        Args:
            input_path: Input document path
            output_path: Output markdown path (defaults to input path with .md extension)

        Returns:
            Result dict with keys:
            - input_file: Input file path
            - output_file: Output markdown file path
            - output_dir: Output directory path
            - images_dir: Images directory path (if images exist)
            - images_count: Number of images extracted
            - format: Output format (markdown)
            - mode: Parsing mode (flash or precision)
            - success: Whether parsing succeeded
        """
        input_path = Path(input_path)

        if not input_path.exists():
            raise FileNotFoundError(f"Input file not found: {input_path}")

        # Determine output directory
        if output_path:
            output_path = Path(output_path)
            output_dir = output_path.parent
        else:
            output_dir = input_path.parent
            output_path = output_dir / f"{input_path.stem}.md"

        logger.info(f"Parsing document with CLI: {input_path}")
        logger.info(f"Output directory: {output_dir}")

        # Use CLI to parse
        cli_result = parse_with_cli(input_path, output_dir, self.token)

        # Build final result
        result = {
            "input_file": str(input_path),
            "output_file": str(output_path),
            "output_dir": str(output_dir),
            "format": "markdown",
            "mode": "precision" if self.token else "flash",
            "success": cli_result.get("success", False),
        }

        # Copy additional fields from CLI result
        if "error" in cli_result:
            result["error"] = cli_result["error"]
        if "images_count" in cli_result:
            result["images_count"] = cli_result["images_count"]
        if "images_dir" in cli_result:
            result["images_dir"] = cli_result["images_dir"]
        if "size" in cli_result:
            result["size"] = cli_result["size"]

        # If output path is different from CLI output, copy the file
        cli_output = Path(cli_result.get("output_file", ""))
        if cli_result.get("success") and cli_output.exists():
            if cli_output.resolve() != output_path.resolve():
                shutil.copy2(cli_output, output_path)
                logger.info(f"Copied output to: {output_path}")
                result["output_file"] = str(output_path)

        return result
