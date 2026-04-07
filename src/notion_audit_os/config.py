"""Configuration and environment helpers for notion-audit-os.

Local paths and project-level constants live here. Module-specific
configuration (e.g. Notion sync credentials) lives in the relevant
module (``notion_sync.py``) to keep this file minimal.

Environment variables used by the project:

``NOTION_API_TOKEN``
    Notion integration secret. Used by ``notion_sync.load_sync_config()``.

``NOTION_PARENT_PAGE_ID``
    Notion parent page ID to publish under.
    Used by ``notion_sync.load_sync_config()``.

Secrets are never hardcoded here. Pass them via environment or CLI flags.
"""

from pathlib import Path

PROJECT_NAME = "notion-audit-os"
DEFAULT_AUDIT_TYPE = "Core Audit v1.1"

# Default local layout — local files are the source of truth.
DEFAULT_DATA_DIR = Path("data")
DEFAULT_OUTPUT_DIR = Path("output")
