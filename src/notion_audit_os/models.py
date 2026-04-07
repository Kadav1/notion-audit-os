"""Pydantic v2 internal model layer for notion-audit-os.

This module is the **internal contract**. JSON Schemas in ``schemas/``
remain the external contract; these models mirror them for in-memory
validation, CLI loading, and downstream module typing.

Locked rules (see ``docs/LOCKED_CONTEXT.md``):

* Findings are stored as a wrapper object ``{"findings": [...]}``.
* The package field is named ``recommended_package``.
* Core Audit v1.1 has 8 locked categories with locked weights.
* Category scores are integers ``0-4`` or the literal string ``"N/A"``.
* Scoring math lives in ``scoring.py``, not here.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------------------
# Locked constants
# ---------------------------------------------------------------------------

#: The 8 locked Core Audit v1.1 category names, in canonical order.
CORE_CATEGORIES: tuple[str, ...] = (
    "Business Fit",
    "Workspace Structure",
    "Database Design",
    "Data Relationships",
    "Workflow Clarity",
    "Views and Dashboards",
    "Intake and Requests",
    "Governance and Adoption",
)

#: Locked default weights. Sum to 100.
DEFAULT_CORE_WEIGHTS: dict[str, int] = {
    "Business Fit": 15,
    "Workspace Structure": 12,
    "Database Design": 15,
    "Data Relationships": 12,
    "Workflow Clarity": 15,
    "Views and Dashboards": 10,
    "Intake and Requests": 10,
    "Governance and Adoption": 11,
}

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class NotionPlan(str, Enum):
    FREE = "free"
    PLUS = "plus"
    BUSINESS = "business"
    ENTERPRISE = "enterprise"
    UNKNOWN = "unknown"


class AuditType(str, Enum):
    CORE_V1_1 = "Core Audit v1.1"


class AuditStatus(str, Enum):
    DRAFT = "draft"
    INTAKE = "intake"
    NOTES = "notes"
    FINDINGS = "findings"
    SCORING = "scoring"
    REPORT = "report"
    PROPOSAL = "proposal"
    DELIVERED = "delivered"
    ARCHIVED = "archived"


class SourceType(str, Enum):
    INTERVIEW = "interview"
    SCREENSHARE = "screenshare"
    DOCUMENT = "document"
    FORM = "form"
    MANUAL = "manual"
    OTHER = "other"


class Category(str, Enum):
    BUSINESS_FIT = "Business Fit"
    WORKSPACE_STRUCTURE = "Workspace Structure"
    DATABASE_DESIGN = "Database Design"
    DATA_RELATIONSHIPS = "Data Relationships"
    WORKFLOW_CLARITY = "Workflow Clarity"
    VIEWS_AND_DASHBOARDS = "Views and Dashboards"
    INTAKE_AND_REQUESTS = "Intake and Requests"
    GOVERNANCE_AND_ADOPTION = "Governance and Adoption"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Effort(str, Enum):
    XS = "xs"
    S = "s"
    M = "m"
    L = "l"
    XL = "xl"


class FindingStatus(str, Enum):
    DRAFT = "draft"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    REJECTED = "rejected"


class RecommendedPackage(str, Enum):
    OPTIMIZATION_SPRINT = "Optimization Sprint"
    PARTIAL_REBUILD = "Partial Rebuild"
    FULL_REBUILD = "Full Rebuild"
    GOVERNANCE_ADD_ON = "Governance Add-on"
    AUTOMATION_AI_ADD_ON = "Automation / AI Add-on"
    NO_MAJOR_PROJECT = "No immediate major project needed"


class MaturityBand(str, Enum):
    CRITICAL_DISORDER = "Critical disorder"
    FRAGILE = "Fragile"
    FUNCTIONAL_BUT_WEAK = "Functional but weak"
    SOLID = "Solid"
    STRONG = "Strong"


class RecommendationType(str, Enum):
    """Finding-level fix type label. Not the package field."""

    QUICK_FIX = "Quick Fix"
    STRUCTURAL_FIX = "Structural Fix"
    REBUILD_ITEM = "Rebuild Item"
    GOVERNANCE_FIX = "Governance Fix"
    TRAINING_FIX = "Training Fix"
    FUTURE_ENHANCEMENT = "Future Enhancement"


class OutputFormat(str, Enum):
    JSON = "json"
    MARKDOWN = "markdown"
    PDF = "pdf"
    DOCX = "docx"


#: Tuple form of locked package names. Re-exported for tests/back-compat.
RECOMMENDED_PACKAGES: tuple[str, ...] = tuple(p.value for p in RecommendedPackage)


# Category score: 0-4 integer or the literal string "N/A".
CategoryScore = Union[Literal[0, 1, 2, 3, 4], Literal["N/A"]]


# ---------------------------------------------------------------------------
# Base model
# ---------------------------------------------------------------------------


class StrictBase(BaseModel):
    """Project-wide strict base model."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
        populate_by_name=True,
        use_enum_values=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

import re

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def validate_slug(value: str) -> str:
    """Validate a URL/path-safe slug."""
    if not _SLUG_RE.match(value):
        raise ValueError(
            "slug must be lowercase letters/digits separated by single hyphens"
        )
    return value


# ---------------------------------------------------------------------------
# Client / Audit
# ---------------------------------------------------------------------------


class PrimaryContact(StrictBase):
    name: str | None = None
    email: str | None = None
    role: str | None = None


class Client(StrictBase):
    client_id: str = Field(min_length=1, max_length=128)
    client_name: str = Field(min_length=1)
    slug: str
    primary_contact: PrimaryContact | None = None
    team_size: int | None = Field(default=None, ge=1)
    notion_plan: NotionPlan | None = None
    segment: str | None = None
    created_at: datetime

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        return validate_slug(v)


class AuditLead(StrictBase):
    name: str | None = None
    email: str | None = None


class Audit(StrictBase):
    audit_id: str = Field(min_length=1, max_length=128)
    client_id: str = Field(min_length=1, max_length=128)
    audit_type: AuditType = AuditType.CORE_V1_1
    status: AuditStatus
    lead: AuditLead | None = None
    scope_notes: str | None = None
    plan_constraints: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime | None = None


# ---------------------------------------------------------------------------
# Intake
# ---------------------------------------------------------------------------


class IntakePayload(StrictBase):
    client_name: str | None = None
    team_size: int | None = Field(default=None, ge=1)
    notion_plan: NotionPlan | None = None
    primary_use_cases: list[str] = Field(default_factory=list)
    pain_points: list[str] = Field(default_factory=list)
    current_notion_usage: str | None = None
    desired_outcomes: list[str] = Field(default_factory=list)
    workspace_owner: str | None = None
    tools_in_use: list[str] = Field(default_factory=list)
    ai_in_use: list[str] = Field(default_factory=list)


class Intake(StrictBase):
    audit_id: str = Field(min_length=1, max_length=128)
    raw_source_path: str | None = None
    normalized_payload: IntakePayload
    missing_fields: list[str] = Field(default_factory=list)
    parsed_at: datetime


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------


class NormalizedSummary(StrictBase):
    pain_points: list[str] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    candidate_categories: list[Category] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)


class Notes(StrictBase):
    audit_id: str = Field(min_length=1, max_length=128)
    source_type: SourceType
    raw_path: str | None = None
    normalized_summary: NormalizedSummary
    gaps: list[str] = Field(default_factory=list)
    generated_at: datetime


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


class Finding(StrictBase):
    finding_id: str = Field(min_length=1, max_length=128)
    audit_id: str = Field(min_length=1, max_length=128)
    category: Category
    title: str = Field(min_length=1)
    observation: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)
    why_it_matters: str | None = None
    recommendation: str | None = None
    severity: Severity | None = None
    priority: Priority | None = None
    effort: Effort | None = None
    quick_win: bool | None = None
    status: FindingStatus = FindingStatus.DRAFT
    owner_suggestion: str | None = None
    recommended_package: RecommendedPackage | None = None
    recommendation_type: RecommendationType | None = None
    notes: str | None = None

    @field_validator("evidence")
    @classmethod
    def _no_blank_evidence(cls, v: list[str]) -> list[str]:
        if any(not item or not item.strip() for item in v):
            raise ValueError("evidence entries must be non-empty strings")
        return v


class FindingsCollection(StrictBase):
    """Canonical findings wrapper object: ``{"findings": [...]}``."""

    audit_id: str | None = None
    generated_at: datetime | None = None
    findings: list[Finding] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_finding_ids(self) -> "FindingsCollection":
        ids = [f.finding_id for f in self.findings]
        if len(ids) != len(set(ids)):
            raise ValueError("finding_id values in a FindingsCollection must be unique")
        return self


# ---------------------------------------------------------------------------
# Scorecard
# ---------------------------------------------------------------------------
#
# Aliases: serialized JSON uses the human-readable category keys
# ("Business Fit", ...). Internal Python attributes are snake_case for
# readability. Use ``model_dump(by_alias=True, mode="json")`` to emit
# the canonical artifact form, and construct from canonical JSON with
# ``CategoryScores.model_validate({"Business Fit": 3, ...})``.


class CategoryScores(StrictBase):
    business_fit: CategoryScore = Field(alias="Business Fit")
    workspace_structure: CategoryScore = Field(alias="Workspace Structure")
    database_design: CategoryScore = Field(alias="Database Design")
    data_relationships: CategoryScore = Field(alias="Data Relationships")
    workflow_clarity: CategoryScore = Field(alias="Workflow Clarity")
    views_and_dashboards: CategoryScore = Field(alias="Views and Dashboards")
    intake_and_requests: CategoryScore = Field(alias="Intake and Requests")
    governance_and_adoption: CategoryScore = Field(alias="Governance and Adoption")


class CategoryWeights(StrictBase):
    business_fit: float = Field(default=15, alias="Business Fit", ge=0, le=100)
    workspace_structure: float = Field(default=12, alias="Workspace Structure", ge=0, le=100)
    database_design: float = Field(default=15, alias="Database Design", ge=0, le=100)
    data_relationships: float = Field(default=12, alias="Data Relationships", ge=0, le=100)
    workflow_clarity: float = Field(default=15, alias="Workflow Clarity", ge=0, le=100)
    views_and_dashboards: float = Field(default=10, alias="Views and Dashboards", ge=0, le=100)
    intake_and_requests: float = Field(default=10, alias="Intake and Requests", ge=0, le=100)
    governance_and_adoption: float = Field(default=11, alias="Governance and Adoption", ge=0, le=100)


class WeightedPoints(StrictBase):
    business_fit: float = Field(default=0, alias="Business Fit")
    workspace_structure: float = Field(default=0, alias="Workspace Structure")
    database_design: float = Field(default=0, alias="Database Design")
    data_relationships: float = Field(default=0, alias="Data Relationships")
    workflow_clarity: float = Field(default=0, alias="Workflow Clarity")
    views_and_dashboards: float = Field(default=0, alias="Views and Dashboards")
    intake_and_requests: float = Field(default=0, alias="Intake and Requests")
    governance_and_adoption: float = Field(default=0, alias="Governance and Adoption")


class Scorecard(StrictBase):
    audit_id: str = Field(min_length=1, max_length=128)
    categories: CategoryScores
    active_weights: CategoryWeights
    weighted_points: WeightedPoints
    overall_score: float = Field(ge=0, le=100)
    maturity_band: MaturityBand
    recommended_package: RecommendedPackage
    rationale: str | None = None


# ---------------------------------------------------------------------------
# Report / Proposal / Notion sync
# ---------------------------------------------------------------------------


class KeyFindingRef(StrictBase):
    finding_id: str | None = None
    title: str = Field(min_length=1)
    summary: str | None = None


class RoadmapItem(StrictBase):
    phase: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    items: list[str] = Field(default_factory=list)


class ReportSections(StrictBase):
    executive_summary: str
    maturity_summary: str
    key_findings: list[KeyFindingRef] = Field(default_factory=list)
    scorecard_summary: str
    roadmap: list[RoadmapItem] = Field(default_factory=list)
    recommended_next_step: str
    appendix: str | None = None


class Report(StrictBase):
    audit_id: str = Field(min_length=1, max_length=128)
    template_version: str = Field(min_length=1)
    output_format: OutputFormat
    path: str | None = None
    generated_at: datetime
    sections: ReportSections


class Proposal(StrictBase):
    audit_id: str = Field(min_length=1, max_length=128)
    recommended_package: RecommendedPackage
    scope_summary: str = Field(min_length=1)
    deliverables: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    generated_at: datetime


class SyncLog(StrictBase):
    """On-disk record written to ``notion_sync.json`` after each sync attempt."""

    audit_id: str = Field(min_length=1, max_length=128)
    success: bool
    synced_at: datetime
    target_parent_id: str = Field(min_length=1)
    page_title: str = Field(min_length=1)
    page_id: str | None = None
    page_url: str | None = None
    artifacts_synced: list[str] = Field(default_factory=list)
    error: str | None = None
    message: str | None = None


# ---------------------------------------------------------------------------
# AuditContext
# ---------------------------------------------------------------------------


class AuditContext(StrictBase):
    """Lightweight in-memory container for the artifacts of a single audit.

    Used by orchestration code (CLI, later phases) to pass loaded
    artifacts around. Not persisted as a single file.
    """

    client: Client | None = None
    audit: Audit | None = None
    intake: Intake | None = None
    notes: Notes | None = None
    findings: FindingsCollection | None = None
    scorecard: Scorecard | None = None
    report: Report | None = None
    proposal: Proposal | None = None


__all__ = [
    "CORE_CATEGORIES",
    "DEFAULT_CORE_WEIGHTS",
    "RECOMMENDED_PACKAGES",
    "CategoryScore",
    # Enums
    "NotionPlan",
    "AuditType",
    "AuditStatus",
    "SourceType",
    "Category",
    "Severity",
    "Priority",
    "Effort",
    "FindingStatus",
    "RecommendedPackage",
    "MaturityBand",
    "RecommendationType",
    "OutputFormat",
    # Base
    "StrictBase",
    "validate_slug",
    # Models
    "PrimaryContact",
    "Client",
    "AuditLead",
    "Audit",
    "IntakePayload",
    "Intake",
    "NormalizedSummary",
    "Notes",
    "Finding",
    "FindingsCollection",
    "CategoryScores",
    "CategoryWeights",
    "WeightedPoints",
    "Scorecard",
    "KeyFindingRef",
    "RoadmapItem",
    "ReportSections",
    "Report",
    "Proposal",
    "SyncLog",
    "AuditContext",
]
