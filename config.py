"""Default configuration for KGAgent.

Create config.local.py (gitignored) to override these settings.
"""

import os

# Model configuration
os.environ.setdefault("KG_MODEL", "claude-sonnet-4-6")

# API configuration (override in config.local.py)
# os.environ.setdefault("KG_API_URL", "http://...")
# os.environ.setdefault("KG_API_KEY", "sk-...")

# Logging
os.environ.setdefault("KG_LOG_LEVEL", "INFO")

# Output directory
os.environ.setdefault("KG_WORK_DIR", "./kg_outputs")

# Load local overrides if exists
try:
    from config_local import *  # noqa: F401, F403
except ImportError:
    pass
