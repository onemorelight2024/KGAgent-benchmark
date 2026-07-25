"""Batch processing utilities for agents."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable, Awaitable


async def process_batch(
    items: list[Any],
    process_fn: Callable[[Any], Awaitable[dict[str, Any]]],
    max_concurrent: int = 5,
    return_errors: bool = True,
) -> dict[str, Any]:
    """Process a batch of items concurrently.

    Args:
        items: List of items to process
        process_fn: Async function that processes a single item
        max_concurrent: Maximum number of concurrent tasks (default: 5)
        return_errors: If True, include failed items in results (default: True)

    Returns:
        Dictionary containing:
        - batch_results: List of successful results
        - batch_errors: List of errors (if return_errors=True)
        - summary: Statistics about the batch

    Example:
        async def extract_single(text: str) -> dict:
            # Call extraction tool for single text
            return {"entities": [...], "relations": [...]}

        result = await process_batch(
            items=["text1", "text2", "text3"],
            process_fn=extract_single,
            max_concurrent=3
        )
    """
    if not items:
        return {
            "batch_results": [],
            "batch_errors": [],
            "summary": {
                "total": 0,
                "successful": 0,
                "failed": 0,
            }
        }

    # Create semaphore to limit concurrency
    semaphore = asyncio.Semaphore(max_concurrent)

    async def process_with_semaphore(index: int, item: Any) -> tuple[int, dict | Exception]:
        """Process single item with concurrency limit."""
        async with semaphore:
            try:
                result = await process_fn(item)
                return (index, result)
            except Exception as e:
                return (index, e)

    # Process all items concurrently (with limit)
    tasks = [process_with_semaphore(i, item) for i, item in enumerate(items)]
    results = await asyncio.gather(*tasks)

    # Separate successful results from errors
    batch_results = []
    batch_errors = []

    for index, result in results:
        if isinstance(result, Exception):
            error_info = {
                "index": index,
                "item": items[index] if index < len(items) else None,
                "error": str(result),
            }
            batch_errors.append(error_info)
        else:
            batch_results.append({
                "index": index,
                "result": result,
            })

    # Build response
    response = {
        "batch_results": batch_results,
        "summary": {
            "total": len(items),
            "successful": len(batch_results),
            "failed": len(batch_errors),
        }
    }

    if return_errors and batch_errors:
        response["batch_errors"] = batch_errors

    return response


def is_batch_input(data: Any) -> bool:
    """Check if input is a batch (array of items).

    Returns True if:
    - Input is a list with more than 1 item
    - Each item is a dict with 'text', 'content', or similar text fields

    Args:
        data: Input data to check

    Returns:
        True if input should be processed as batch
    """
    if not isinstance(data, list):
        return False

    if len(data) <= 1:
        return False

    # Check if list items are extractable (have text content)
    if not data:
        return False

    first_item = data[0]

    # List of dicts with text fields → batch
    if isinstance(first_item, dict):
        text_fields = ["text", "content", "description", "body", "message", "title"]
        has_text_field = any(field in first_item for field in text_fields)
        return has_text_field

    # List of strings → batch
    if isinstance(first_item, str):
        return True

    return False


async def process_batch_with_resume(
    items: list[Any],
    process_fn: Callable[[Any], Awaitable[dict[str, Any]]],
    output_path: str | Path,
    max_concurrent: int = 5,
    format_fn: Callable[[dict, Any, int], dict] | None = None,
) -> dict[str, Any]:
    """Process a batch of items with real-time saving and resume support.

    Args:
        items: List of items to process
        process_fn: Async function that processes a single item
        output_path: Path to output file (will be created/updated)
        max_concurrent: Maximum number of concurrent tasks
        format_fn: Optional function to format each result before saving
                   Signature: format_fn(result, original_item, index) -> formatted_dict

    Returns:
        Dictionary containing:
        - total: Total number of items
        - completed: Number of completed items
        - skipped: Number of skipped items (already processed)
        - failed: Number of failed items
        - output_path: Path to the output file

    Example:
        def format_result(result, item, index):
            return {
                "index": index,
                "text": item.get("text", ""),
                "kg": result.get("relations", [])
            }

        result = await process_batch_with_resume(
            items=data_list,
            process_fn=extract_single,
            output_path="output.json",
            format_fn=format_result
        )
    """
    output_path = Path(output_path)

    # Load existing results if file exists
    existing_results = []
    completed_indices = set()

    if output_path.exists():
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                existing_results = json.load(f)
                # Track which indices are already completed
                completed_indices = {item["index"] for item in existing_results if "index" in item}
                print(f"📂 Found existing file with {len(completed_indices)} completed items")
        except Exception as e:
            print(f"⚠️  Could not load existing file: {e}")
            existing_results = []
            completed_indices = set()

    # Determine which items need processing
    items_to_process = []
    for i, item in enumerate(items):
        if i not in completed_indices:
            items_to_process.append((i, item))

    if not items_to_process:
        print(f"✓ All {len(items)} items already processed")
        return {
            "total": len(items),
            "completed": len(completed_indices),
            "skipped": len(completed_indices),
            "failed": 0,
            "output_path": str(output_path),
        }

    print(f"🔄 Processing {len(items_to_process)} remaining items (skipping {len(completed_indices)})")

    # Create semaphore to limit concurrency
    semaphore = asyncio.Semaphore(max_concurrent)
    file_lock = asyncio.Lock()

    # Stats tracking
    stats = {
        "completed": len(completed_indices),
        "failed": 0,
    }

    async def process_and_save(index: int, item: Any):
        """Process single item and immediately save to file."""
        async with semaphore:
            try:
                # Process the item
                result = await process_fn(item)

                # Format the result
                if format_fn:
                    formatted = format_fn(result, item, index)
                else:
                    formatted = {
                        "index": index,
                        "result": result,
                    }

                # Append to file immediately (thread-safe with lock)
                async with file_lock:
                    # Read current file
                    current_results = []
                    if output_path.exists():
                        try:
                            with open(output_path, "r", encoding="utf-8") as f:
                                current_results = json.load(f)
                        except:
                            current_results = existing_results.copy()
                    else:
                        current_results = existing_results.copy()

                    # Add new result
                    current_results.append(formatted)

                    # Write back to file
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(output_path, "w", encoding="utf-8") as f:
                        json.dump(current_results, f, indent=2, ensure_ascii=False)

                    stats["completed"] += 1
                    print(f"✓ [{stats['completed']}/{len(items)}] Processed index {index}")

                return (index, "success")

            except asyncio.CancelledError:
                # Task was cancelled, propagate the cancellation
                print(f"⚠️  Cancelling task for index {index}")
                raise
            except Exception as e:
                stats["failed"] += 1
                print(f"✗ [{stats['completed'] + stats['failed']}/{len(items)}] Failed index {index}: {e}")

                # Save error to file
                async with file_lock:
                    current_results = []
                    if output_path.exists():
                        try:
                            with open(output_path, "r", encoding="utf-8") as f:
                                current_results = json.load(f)
                        except:
                            current_results = existing_results.copy()
                    else:
                        current_results = existing_results.copy()

                    current_results.append({
                        "index": index,
                        "error": str(e),
                    })

                    with open(output_path, "w", encoding="utf-8") as f:
                        json.dump(current_results, f, indent=2, ensure_ascii=False)

                return (index, e)

    # Process all remaining items
    tasks = [process_and_save(index, item) for index, item in items_to_process]

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        # Cancel all pending tasks
        for task in tasks:
            if not task.done():
                task.cancel()
        # Wait for all tasks to finish cancelling
        await asyncio.gather(*tasks, return_exceptions=True)
        print(f"\n⚠️  Batch processing cancelled. Progress saved: {stats['completed']}/{len(items)} items")
        raise

    return {
        "total": len(items),
        "completed": stats["completed"],
        "skipped": len(completed_indices),
        "failed": stats["failed"],
        "output_path": str(output_path),
    }
