"""Batch processing utilities for agents."""

from __future__ import annotations

import asyncio
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
