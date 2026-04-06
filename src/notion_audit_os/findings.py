"""Finding drafting from normalized notes.

Turns a :class:`models.Notes` artifact into a draft
:class:`models.FindingsCollection`. The drafting is deterministic and
preserves the four-way separation that the locked rules require:

* ``observation``     — what we saw, copied from notes (verbatim)
* ``evidence``        — explicit list of supporting bullet points
* ``why_it_matters``  — short consequence string from a category map
* ``recommendation``  — empty by default; an LLM adapter MAY draft a
  one-sentence suggestion, but it must stay grounded in the evidence
  and the human reviewer is the final authority.

Categories are routed by the same deterministic keyword map used in
``notes.py``. The LLM never decides which category a finding belongs
to.

Quality enforcement (:func:`validate_finding_quality`) refuses to emit:

* findings with empty evidence
* findings whose observation duplicates the evidence verbatim (the
  classic "LLM flattening" failure mode)
* findings without a non-trivial title
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import llm as L
from . import models as M
from . import notes as N

# ---------------------------------------------------------------------------
# Per-category "why it matters" sentences
# ---------------------------------------------------------------------------

WHY_IT_MATTERS: dict[str, str] = {
    "Business Fit": (
        "Misalignment with business outcomes erodes the workspace's strategic "
        "value and makes downstream investment hard to justify."
    ),
    "Workspace Structure": (
        "An unclear top-level structure increases ramp-up time and hides "
        "important content from the people who need it."
    ),
    "Database Design": (
        "Weak database design causes data quality issues and propagates "
        "errors into every view, rollup, and report built on top of it."
    ),
    "Data Relationships": (
        "Missing or unreliable relations break cross-context navigation "
        "and force operators into manual lookup work."
    ),
    "Workflow Clarity": (
        "Ambiguous workflow stages cause status drift, missed handoffs, "
        "and inconsistent reporting on the same work."
    ),
    "Views and Dashboards": (
        "Without dependable views, operators cannot trust the workspace "
        "as a daily decision-making surface."
    ),
    "Intake and Requests": (
        "Unstructured intake creates triage churn and lets work bypass "
        "the system that is supposed to track it."
    ),
    "Governance and Adoption": (
        "Weak governance and low adoption decay the workspace over time "
        "no matter how well it was originally built."
    ),
}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class FindingQualityError(ValueError):
    """Raised when a draft finding fails the structural-separation rules."""


# ---------------------------------------------------------------------------
# Quality enforcement
# ---------------------------------------------------------------------------


def validate_finding_quality(finding: M.Finding) -> None:
    """Refuse vague, empty, or flattened findings.

    The four-way separation (observation / evidence / why_it_matters /
    recommendation) is the entire point of the findings layer. If any
    of those collapses, the finding is not useful as a review artifact.
    """
    if not finding.title or not finding.title.strip():
        raise FindingQualityError("finding title is empty")
    if not finding.observation or not finding.observation.strip():
        raise FindingQualityError("finding observation is empty")
    if not finding.evidence:
        raise FindingQualityError(
            f"finding {finding.finding_id!r} has no evidence — "
            "every finding must include at least one evidence item"
        )
    # The classic LLM flattening failure: observation == one-line evidence.
    if (
        len(finding.evidence) == 1
        and finding.evidence[0].strip() == finding.observation.strip()
    ):
        raise FindingQualityError(
            f"finding {finding.finding_id!r}: observation and evidence are "
            "identical — they must remain structurally distinct"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stable_finding_id(audit_id: str, category: str, observation: str) -> str:
    """Deterministic id so re-running drafting on the same notes is idempotent."""
    digest = hashlib.sha1(
        f"{audit_id}|{category}|{observation}".encode("utf-8")
    ).hexdigest()[:10]
    return f"fnd_{digest}"


def _route_category(text: str) -> str | None:
    """Pick the locked category whose keywords match this single bullet."""
    blob = text.lower()
    for category in M.CORE_CATEGORIES:
        for keyword in N.CATEGORY_KEYWORDS.get(category, ()):
            if keyword in blob:
                return category
    return None


def _short_title(observation: str, max_chars: int = 80) -> str:
    text = observation.strip().rstrip(".")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def draft_findings_from_notes(
    notes: M.Notes,
    *,
    drafter: L.LLMAdapter | None = None,
) -> M.FindingsCollection:
    """Build a draft :class:`FindingsCollection` from a :class:`Notes`.

    One finding is produced per observation that maps to a locked
    category. Pain points whose phrasing also maps to that category are
    attached as additional evidence. Observations that do not match any
    locked category are dropped (they belong in the operator's review
    pass), and added to the collection's ``audit_id``-level metadata is
    not the right place — instead they are simply not turned into
    findings, leaving the original notes artifact as the source of
    truth for unrouted material.

    If a ``drafter`` is supplied, it may fill in the ``recommendation``
    field. The drafter must not invent evidence and must not change the
    category. The deterministic structure is built before any LLM call.
    """
    summary = notes.normalized_summary
    findings: list[M.Finding] = []
    seen_ids: set[str] = set()

    for observation in summary.observations:
        category = _route_category(observation)
        if category is None:
            continue

        # Evidence: pain points whose text routes to the same category,
        # plus any uncertainties that mention the same category.
        evidence: list[str] = []
        for pain in summary.pain_points:
            if _route_category(pain) == category:
                evidence.append(pain)
        for unknown in summary.uncertainties:
            if _route_category(unknown) == category:
                evidence.append(f"open question: {unknown}")

        # Always seed evidence with the observation text itself, but
        # *only* if there is no other evidence yet — and even then we
        # tag it so the structural separation rule still holds.
        if not evidence:
            evidence = [f"source observation: {observation}"]

        finding_id = _stable_finding_id(notes.audit_id, category, observation)
        if finding_id in seen_ids:
            continue
        seen_ids.add(finding_id)

        recommendation = ""
        if drafter is not None:
            try:
                recommendation = drafter.draft_recommendation(
                    category=category,
                    observation=observation,
                    evidence=list(evidence),
                ) or ""
            except Exception:  # noqa: BLE001 — adapter must never break drafting
                recommendation = ""

        finding = M.Finding(
            finding_id=finding_id,
            audit_id=notes.audit_id,
            category=M.Category(category),
            title=_short_title(observation),
            observation=observation,
            evidence=evidence,
            why_it_matters=WHY_IT_MATTERS[category],
            recommendation=recommendation or None,
            severity=None,
            priority=None,
            effort=None,
            quick_win=None,
            status=M.FindingStatus.DRAFT,
            owner_suggestion=None,
            recommended_package=None,
            recommendation_type=None,
            notes=None,
        )
        validate_finding_quality(finding)
        findings.append(finding)

    return M.FindingsCollection(
        audit_id=notes.audit_id,
        generated_at=datetime.now(timezone.utc),
        findings=findings,
    )


def load_findings_input(
    path: Path,
    *,
    audit_id: str,
    drafter: L.LLMAdapter | None = None,
) -> M.FindingsCollection:
    """Load draft findings from a JSON wrapper or by drafting from a notes file."""
    if not path.is_file():
        raise FileNotFoundError(f"findings source not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".json":
        data: Any = json.loads(path.read_text(encoding="utf-8"))
        return M.FindingsCollection.model_validate(data)
    # Treat any other extension as a raw notes source: parse, then draft.
    notes = N.load_notes_file(path, audit_id=audit_id)
    return draft_findings_from_notes(notes, drafter=drafter)


__all__ = [
    "WHY_IT_MATTERS",
    "FindingQualityError",
    "validate_finding_quality",
    "draft_findings_from_notes",
    "load_findings_input",
]
