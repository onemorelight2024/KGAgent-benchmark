"""Logging setup for KGAgent."""

from __future__ import annotations

import logging
import sys

_setup_done = False


def setup_logging(level: str | None = None) -> None:
    """Setup logging configuration.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR). If None, uses KG_LOG_LEVEL env var.
    """
    global _setup_done
    if _setup_done:
        return

    if level is None:
        from kgagent.core.config import get_log_level
        level = get_log_level()

    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )

    _setup_done = True
