"""Optional one-way Notion publishing for Core Audit v1.1.

This module is the adapter boundary between local approved artifacts and
Notion. It is the **only** place that talks to the Notion API.

Locked decisions (see ``docs/LOCKED_CONTEXT.md``):

* Local files are the source of truth. Notion is a delivery surface only.
* Sync is one-way: local \u2192 Notion. No Notion state is ever read back as
  truth. No bidirectional reconciliation.
* Only approved final artifacts are published. Drafts are never synced.
* Sync is create-only in v1. Re-syncing creates a new page; the log
  records the remote page ID for each run.
* No content is rewritten during sync. The module publishes what was
  approved, converting structure to Notion blocks without altering meaning.
* No LLM calls. Content conversion is deterministic and structural.

Public API::

    load_sync_config()          \u2014 validate and load configuration
    convert_report_to_blocks()  \u2014 M.Report \u2192 Notion block list
    convert_proposal_to_blocks()\u2014 M.Proposal \u2192 Notion block list
    sync_audit()                \u2014 full sync orchestration
    write_sync_log()            \u2014 persist SyncResult to notion_sync.json
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import models as M
from . import storage as s

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SyncConfigError(ValueError):
    """Raised when required sync configuration is missing or invalid."""


class SyncPrerequisiteError(ValueError):
    """Raised when a required artifact is missing before sync can proceed."""


class NotionAPIError(Exception):
    """Raised when the Notion API returns an error or cannot be reached."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SyncConfig:
    """Resolved configuration for a single sync operation."""

    token: str
    parent_page_id: str
    api_version: str = "2022-06-28"


def load_sync_config(
    *,
    token: str | None = None,
    parent_page_id: str | None = None,
) -> SyncConfig:
    """Resolve sync configuration from explicit args or environment variables.

    Precedence: explicit argument > environment variable.

    Required environment variables (if not passed explicitly):

    * ``NOTION_API_TOKEN`` \u2014 Notion integration secret.
    * ``NOTION_PARENT_PAGE_ID`` \u2014 ID of the parent page to create under.

    Raises:
        :class:`SyncConfigError`: If token or parent page ID is missing.
    """
    resolved_token = token or os.environ.get("NOTION_API_TOKEN", "").strip()
    resolved_parent = parent_page_id or os.environ.get("NOTION_PARENT_PAGE_ID", "").strip()

    if not resolved_token:
        raise SyncConfigError(
            "Notion API token is not set. "
            "Pass --token or set the NOTION_API_TOKEN environment variable."
        )
    if not resolved_parent:
        raise SyncConfigError(
            "Notion parent page ID is not set. "
            "Pass --parent-id or set the NOTION_PARENT_PAGE_ID environment variable."
        )
    return SyncConfig(token=resolved_token, parent_page_id=resolved_parent)


# ---------------------------------------------------------------------------
# Notion block builders
# ---------------------------------------------------------------------------

#: Notion API maximum rich_text content length per element.
_RICH_TEXT_LIMIT = 1900

#: Notion API maximum children blocks per create_page request.
_MAX_BLOCKS = 100


def _rich_text(content: str) -> list[dict]:
    """Build a rich_text array, splitting long content across elements."""
    chunks = [content[i: i + _RICH_TEXT_LIMIT] for i in range(0, max(len(content), 1), _RICH_TEXT_LIMIT)]
    return [{"type": "text", "text": {"content": chunk}} for chunk in chunks]


def _heading_2(text: str) -> dict:
    return {"object": "block", "type": "heading_2",
            "heading_2": {"rich_text": _rich_text(text)}}


def _heading_3(text: str) -> dict:
    return {"object": "block", "type": "heading_3",
            "heading_3": {"rich_text": _rich_text(text)}}


def _paragraph(text: str) -> dict:
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": _rich_text(text)}}


def _bullet(text: str) -> dict:
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": _rich_text(text)}}


def _divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def _prose_blocks(text: str) -> list[dict]:
    """Convert a multi-line prose string to paragraph blocks.

    Blank lines between paragraphs are preserved as spacing; each
    non-empty line becomes its own paragraph block (handles scorecard
    summary, maturity summary, etc.).
    """
    blocks: list[dict] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped:
            blocks.append(_paragraph(stripped))
    return blocks or [_paragraph(text)]


def _truncate_blocks(blocks: list[dict]) -> list[dict]:
    """Enforce the Notion API 100-block limit per create_page request."""
    if len(blocks) <= _MAX_BLOCKS:
        return blocks
    omitted = len(blocks) - _MAX_BLOCKS + 1
    truncated = blocks[: _MAX_BLOCKS - 1]
    truncated.append(
        _paragraph(
            f"[{omitted} additional block(s) omitted: "
            f"Notion API limit of {_MAX_BLOCKS} blocks per page creation request.]"
        )
    )
    return truncated


# ---------------------------------------------------------------------------
# Content conversion
# ---------------------------------------------------------------------------


def _build_report_blocks(report: M.Report) -> list[dict]:
    """Build all Notion blocks for a report without applying the block limit.

    Internal helper shared by :func:`convert_report_to_blocks` (standalone
    use) and :func:`sync_audit` (combined with proposal blocks). Truncation
    is the caller's responsibility so combined content is capped only once.
    """
    sec = report.sections
    blocks: list[dict] = []

    # Executive Summary
    blocks.append(_heading_2("Executive Summary"))
    blocks.extend(_prose_blocks(sec.executive_summary))

    # Maturity Assessment
    blocks.append(_heading_2("Maturity Assessment"))
    blocks.extend(_prose_blocks(sec.maturity_summary))

    # Key Findings
    if sec.key_findings:
        blocks.append(_heading_2("Key Findings"))
        for kf in sec.key_findings:
            text = kf.title
            if kf.summary:
                text = f"{kf.title} \u2014 {kf.summary}"
            blocks.append(_bullet(text))

    # Scorecard Summary
    blocks.append(_heading_2("Scorecard Summary"))
    blocks.extend(_prose_blocks(sec.scorecard_summary))

    # Roadmap
    if sec.roadmap:
        blocks.append(_heading_2("Roadmap"))
        for phase in sec.roadmap:
            blocks.append(_heading_3(phase.phase))
            blocks.append(_paragraph(phase.summary))
            for item in phase.items:
                blocks.append(_bullet(item))

    # Recommended Next Step
    blocks.append(_heading_2("Recommended Next Step"))
    blocks.extend(_prose_blocks(sec.recommended_next_step))

    # Appendix (optional)
    if sec.appendix:
        blocks.append(_heading_2("Appendix"))
        blocks.extend(_prose_blocks(sec.appendix))

    return blocks


def convert_report_to_blocks(report: M.Report) -> list[dict]:
    """Convert an approved :class:`models.Report` to a Notion block list.

    Converts each section to the appropriate Notion block types. No content
    is added, removed, or reworded \u2014 the conversion is purely structural.

    Returns at most :data:`_MAX_BLOCKS` blocks (Notion API create limit).
    A truncation note is appended if content is cut.
    """
    return _truncate_blocks(_build_report_blocks(report))


def convert_proposal_to_blocks(proposal: M.Proposal) -> list[dict]:
    """Convert an approved :class:`models.Proposal` to a Notion block list.

    Appended after a divider when combined with the report on one page.
    No content is added, removed, or reworded.
    """
    blocks: list[dict] = [
        _divider(),
        _heading_2("Engagement Proposal"),
        _heading_2("Recommended Package"),
        _paragraph(proposal.recommended_package),
        _heading_2("Scope Summary"),
    ]
    blocks.extend(_prose_blocks(proposal.scope_summary))

    if proposal.deliverables:
        blocks.append(_heading_2("Deliverables"))
        for d in proposal.deliverables:
            blocks.append(_bullet(d))

    if proposal.exclusions:
        blocks.append(_heading_2("Exclusions"))
        for e in proposal.exclusions:
            blocks.append(_bullet(e))

    return blocks


# ---------------------------------------------------------------------------
# Notion API adapter
# ---------------------------------------------------------------------------


class NotionAPIAdapter:
    """Thin, testable wrapper around the Notion REST API.

    Only ``create_page`` is implemented in v1. The adapter takes no action
    on local files and reads no local state — it is a pure HTTP adapter.

    Replace with a stub in tests by passing an object with the same
    ``create_page(parent_id, title, children)`` signature to
    :func:`sync_audit`.
    """

    BASE_URL = "https://api.notion.com/v1"

    def __init__(self, token: str, *, api_version: str = "2022-06-28") -> None:
        self._token = token
        self._api_version = api_version

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Notion-Version": self._api_version,
        }

    def create_page(
        self,
        parent_id: str,
        title: str,
        children: list[dict],
    ) -> dict:
        """Create a Notion page under ``parent_id`` and return the API response.

        Args:
            parent_id: ID of the parent page or database.
            title: Plain-text page title.
            children: Notion block objects to include as page body.

        Returns:
            The Notion API response dict (includes ``id`` and ``url``).

        Raises:
            :class:`NotionAPIError`: On HTTP errors or network failures.
        """
        url = f"{self.BASE_URL}/pages"
        payload = {
            "parent": {"page_id": parent_id},
            "properties": {
                "title": {"title": [{"type": "text", "text": {"content": title}}]}
            },
            "children": children,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers=self._headers(), method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise NotionAPIError(
                f"Notion API error {exc.code}: {body[:500]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise NotionAPIError(
                f"Notion API network error: {exc.reason}"
            ) from exc


# ---------------------------------------------------------------------------
# Sync result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SyncResult:
    """Structured result from one sync operation.

    Written to ``notion_sync.json`` (via :func:`write_sync_log`) so the
    operator can always see what was last synced, when, and where it went.
    """

    audit_id: str
    success: bool
    synced_at: datetime
    target_parent_id: str
    page_title: str
    page_id: str | None = None
    page_url: str | None = None
    artifacts_synced: list[str] = field(default_factory=list)
    error: str | None = None
    message: str | None = None

    def to_dict(self) -> dict:
        return {
            "audit_id": self.audit_id,
            "success": self.success,
            "synced_at": self.synced_at.isoformat(timespec="seconds"),
            "target_parent_id": self.target_parent_id,
            "page_title": self.page_title,
            "page_id": self.page_id,
            "page_url": self.page_url,
            "artifacts_synced": self.artifacts_synced,
            "error": self.error,
            "message": self.message,
        }


# ---------------------------------------------------------------------------
# Sync log
# ---------------------------------------------------------------------------


def write_sync_log(paths: s.AuditPaths, result: SyncResult) -> None:
    """Persist ``result`` to ``notion_sync.json`` in the audit directory.

    Always overwrites the previous log. The local log is the operator's
    record of what was synced — it is not the remote source of truth.
    """
    s.write_json(paths.notion_sync_file, result.to_dict(), overwrite=True)


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


def sync_audit(
    paths: s.AuditPaths,
    config: SyncConfig,
    *,
    include_proposal: bool = False,
    adapter: NotionAPIAdapter | None = None,
) -> SyncResult:
    """Publish approved final artifacts to Notion (one-way).

    Loads ``report.final.json`` (required) and optionally
    ``proposal.final.json``. Converts each to Notion blocks and creates
    one page under ``config.parent_page_id``.

    The function is create-only in v1: each call creates a new Notion page.
    The remote page ID is recorded in the returned :class:`SyncResult` and
    written to ``notion_sync.json`` by the caller.

    Args:
        paths: Resolved artifact paths for the audit.
        config: Validated sync configuration (token + parent page ID).
        include_proposal: If ``True``, append proposal blocks to the same
            page. Raises :class:`SyncPrerequisiteError` if the proposal
            final artifact is missing.
        adapter: Optional API adapter override (used in tests to avoid
            live network calls). Defaults to :class:`NotionAPIAdapter`.

    Returns:
        A :class:`SyncResult` describing success or failure.

    Raises:
        :class:`SyncPrerequisiteError`: If required artifacts are missing.
        :class:`NotionAPIError`: If the Notion API call fails.
    """
    now = datetime.now(timezone.utc)

    # --- Prerequisite checks ---
    if not paths.report_final.is_file():
        raise SyncPrerequisiteError(
            "report.final.json is missing. Review report.draft.json and "
            "promote it to report.final.json before syncing."
        )
    if include_proposal and not paths.proposal_final.is_file():
        raise SyncPrerequisiteError(
            "proposal.final.json is missing. Review proposal.draft.json and "
            "promote it to proposal.final.json, or omit --include-proposal."
        )

    # --- Load artifacts ---
    try:
        report_obj = s.load_model(
            paths.report_final, M.Report, schema_name="report.schema.json"
        )
    except s.StorageError as exc:
        raise SyncPrerequisiteError(
            f"could not load report.final.json: {exc}"
        ) from exc

    proposal_obj: M.Proposal | None = None
    if include_proposal:
        try:
            proposal_obj = s.load_model(
                paths.proposal_final, M.Proposal, schema_name="proposal.schema.json"
            )
        except s.StorageError as exc:
            raise SyncPrerequisiteError(
                f"could not load proposal.final.json: {exc}"
            ) from exc

    # --- Build page title ---
    client_name: str | None = None
    if paths.intake_file.is_file():
        try:
            intake_obj = s.load_model(paths.intake_file, M.Intake, schema_name="intake.schema.json")
            client_name = intake_obj.normalized_payload.client_name
        except s.StorageError:
            pass  # Client name is context only; don't block sync.
    subject = client_name or paths.audit_id
    page_title = f"{subject} \u2014 Audit Report"

    # --- Build content blocks ---
    # Use _build_report_blocks (not convert_report_to_blocks) so that
    # report and proposal blocks are combined before the single truncation
    # pass. convert_report_to_blocks truncates independently and would
    # silently drop all proposal blocks if the report fills 100 slots first.
    blocks = _build_report_blocks(report_obj)
    artifacts = ["report.final.json"]

    if proposal_obj is not None:
        proposal_blocks = convert_proposal_to_blocks(proposal_obj)
        blocks = blocks + proposal_blocks
        artifacts.append("proposal.final.json")

    blocks = _truncate_blocks(blocks)

    # --- Publish ---
    if adapter is None:
        adapter = NotionAPIAdapter(config.token, api_version=config.api_version)

    response = adapter.create_page(
        parent_id=config.parent_page_id,
        title=page_title,
        children=blocks,
    )

    page_id = response.get("id")
    page_url = response.get("url")

    return SyncResult(
        audit_id=paths.audit_id,
        success=True,
        synced_at=now,
        target_parent_id=config.parent_page_id,
        page_title=page_title,
        page_id=page_id,
        page_url=page_url,
        artifacts_synced=artifacts,
        message="Page created successfully.",
    )


__all__ = [
    "SyncConfigError",
    "SyncPrerequisiteError",
    "NotionAPIError",
    "SyncConfig",
    "load_sync_config",
    "convert_report_to_blocks",
    "convert_proposal_to_blocks",
    "NotionAPIAdapter",
    "SyncResult",
    "write_sync_log",
    "sync_audit",
]
