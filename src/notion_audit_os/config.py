"""Configuration and environment helpers.

Phase I placeholder. Real config loading lands in a later phase.
"""

from pathlib import Path

PROJECT_NAME = "notion-audit-os"
DEFAULT_AUDIT_TYPE = "Core Audit v1.1"

# Default local layout — local files are the source of truth.
DEFAULT_DATA_DIR = Path("data")
DEFAULT_OUTPUT_DIR = Path("output")
