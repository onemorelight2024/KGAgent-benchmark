"""Configuration management for KGAgent."""

from __future__ import annotations

import os
import logging

logger = logging.getLogger(__name__)


def get_model() -> str:
    """Resolve model name from environment variables.

    Resolution order:
    1. KG_MODEL
    2. ANTHROPIC_MODEL
    3. Default: claude-sonnet-4-6
    """
    model = (
        os.getenv("KG_MODEL")
        or os.getenv("ANTHROPIC_MODEL")
        or "claude-sonnet-4-6"
    )
    logger.debug(f"Resolved model: {model}")
    return model


def get_api_config() -> dict[str, str | None]:
    """Resolve API configuration from environment variables.

    Returns:
        Dictionary with api_url and api_key
    """
    api_url = os.getenv("KG_API_URL") or os.getenv("ANTHROPIC_BASE_URL")
    api_key = (
        os.getenv("KG_API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("ANTHROPIC_AUTH_TOKEN")
    )

    # Export to ANTHROPIC_* for Claude Agent SDK compatibility
    if api_url:
        os.environ.setdefault("ANTHROPIC_BASE_URL", api_url)
    if api_key:
        os.environ.setdefault("ANTHROPIC_API_KEY", api_key)

    logger.debug(f"API config: url={api_url is not None}, key={api_key is not None}")

    return {
        "api_url": api_url,
        "api_key": api_key,
    }


def get_log_level() -> str:
    """Get logging level from environment."""
    return os.getenv("KG_LOG_LEVEL", "INFO").upper()
