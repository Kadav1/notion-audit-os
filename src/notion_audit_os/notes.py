"""Note normalization.

Turns raw discovery/workspace notes (Markdown or text) into a validated
:class:`models.Notes` artifact. The structural extraction is fully
deterministic; the LLM adapter is only allowed to rewrite prose.

Recognized headings (case-insensitive, ``-``/``_``/space tolerant):

* ``Pain Points``      → ``pain_points``
* ``Observations``     → ``observations``
* ``Uncertainties`` /
  ``Open Questions`` /
  ``Gaps``             → ``uncertainties``
* ``Source Type``      → top-level ``source_type`` (one of the locked
  :class:`models.SourceType` values; defaults to ``manual`` if missing
  or unrecognized).

Candidate categories are derived deterministically from a small,
explicit keyword map. They are *suggestions only* — the locked Core
Audit categories remain the only authoritative routing target, and the
final category for each finding is decided by a human reviewer (or by
the deterministic findings drafter using the same map).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import llm as L
from . import models as M

# ---------------------------------------------------------------------------
# Headings
# ---------------------------------------------------------------------------

NOTES_SECTION_HEADINGS: dict[str, set[str]] = {
    "pain_points": {"pain points", "pains"},
    "observations": {"observations", "what we saw", "findings"},
    "uncertainties": {"uncertainties", "open questions", "gaps", "unknowns"},
    "source_type": {"source type", "source"},
}


# ---------------------------------------------------------------------------
# Candidate-category keyword map
# ---------------------------------------------------------------------------
#
# Explicit, reviewable, and deliberately small. Phrase keywords are
# matched as case-insensitive substrings in the joined observation +
# pain-point text. The output is a *candidate* list — never used as
# the authoritative category for scoring or finding category routing
# without further deterministic logic in findings.py.

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Business Fit": (
        "business goal", "outcome", "kpi", "okr", "strategy", "roadmap",
        "fit", "purpose", "north star",
    ),
    "Workspace Structure": (
        "workspace", "sidebar", "top-level", "page tree", "hierarchy",
        "navigation", "naming convention", "folder",
    ),
    "Database Design": (
        "database", "db ", "properties", "schema", "rollup", "formula",
        "table design", "column", "field type",
    ),
    "Data Relationships": (
        "relation", "linked", "linked database", "join", "cross-reference",
        "many-to-many", "one-to-many", "foreign key",
    ),
    "Workflow Clarity": (
        "workflow", "process", "status", "kanban", "lifecycle",
        "handoff", "stages", "swimlane",
    ),
    "Views and Dashboards": (
        "view", "dashboard", "filter", "sort", "saved view",
        "board view", "calendar view", "gallery",
    ),
    "Intake and Requests": (
        "intake", "request", "form", "submission", "ticket",
        "queue", "triage",
    ),
    "Governance and Adoption": (
        "governance", "adoption", "training", "ownership", "permissions",
        "access", "stale", "abandoned", "policy", "guideline",
    ),
}


def _norm_heading(h: str) -> str:
    return h.strip().lower().replace("-", " ").replace("_", " ").rstrip(":")


def _heading_to_field(heading: str) -> str | None:
    norm = _norm_heading(heading)
    for field, variants in NOTES_SECTION_HEADINGS.items():
        if norm in variants:
            return field
    return None


def _bullets(lines: list[str]) -> list[str]:
    out: list[str] = []
    for ln in lines:
        s = ln.strip()
        if s.startswith(("-", "*")):
            out.append(s.lstrip("-* ").strip())
        elif s and s[0].isdigit() and "." in s[:3]:
            out.append(s.split(".", 1)[1].strip())
    return [b for b in out if b]


def _scalar(lines: list[str]) -> str | None:
    for ln in lines:
        s = ln.strip()
        if s:
            return s
    return None


def _split_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
            current = _heading_to_field(heading)
            if current is not None:
                sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)
    return sections


def detect_candidate_categories(texts: list[str]) -> list[str]:
    """Return candidate locked-category names mentioned in ``texts``.

    Deterministic substring keyword match. Returns categories in the
    locked canonical order, with no duplicates. The result is a
    suggestion — never authoritative.
    """
    blob = " ".join(texts).lower()
    hits: list[str] = []
    for category in M.CORE_CATEGORIES:
        for keyword in CATEGORY_KEYWORDS.get(category, ()):
            if keyword in blob:
                hits.append(category)
                break
    return hits


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_notes_text(
    text: str,
    *,
    audit_id: str,
    raw_path: str | None = None,
    summarizer: L.LLMAdapter | None = None,
) -> M.Notes:
    """Parse a raw notes document into a :class:`Notes` artifact.

    Structural extraction is deterministic. If ``summarizer`` is given,
    each observation/pain point is *optionally* re-rendered through the
    adapter (e.g. to tighten phrasing). The original list lengths and
    item count are preserved — the summarizer must not add or drop
    items. ``gaps`` and ``uncertainties`` capture anything ambiguous.
    """
    sections = _split_sections(text)

    pain_points = _bullets(sections.get("pain_points", []))
    observations = _bullets(sections.get("observations", []))
    uncertainties = _bullets(sections.get("uncertainties", []))

    raw_source_type = _scalar(sections.get("source_type", []))
    source_type = _coerce_source_type(raw_source_type)

    if summarizer is not None:
        pain_points = [summarizer.summarize(p) for p in pain_points]
        observations = [summarizer.summarize(o) for o in observations]
        uncertainties = [summarizer.summarize(u) for u in uncertainties]

    candidates = detect_candidate_categories(observations + pain_points)

    gaps: list[str] = []
    if not observations:
        gaps.append("no observations parsed from notes")
    if not pain_points:
        gaps.append("no pain points parsed from notes")
    if raw_source_type and source_type.value != raw_source_type.strip().lower():
        gaps.append(f"source_type {raw_source_type!r} not recognized; defaulted to {source_type.value}")

    summary = M.NormalizedSummary(
        pain_points=pain_points,
        observations=observations,
        candidate_categories=[M.Category(c) for c in candidates],
        uncertainties=uncertainties,
    )
    return M.Notes(
        audit_id=audit_id,
        source_type=source_type,
        raw_path=raw_path,
        normalized_summary=summary,
        gaps=gaps,
        generated_at=datetime.now(timezone.utc),
    )


def _coerce_source_type(value: str | None) -> M.SourceType:
    if not value:
        return M.SourceType.MANUAL
    norm = value.strip().lower()
    for st in M.SourceType:
        if st.value == norm:
            return st
    return M.SourceType.MANUAL


def load_notes_file(
    path: Path,
    *,
    audit_id: str,
    summarizer: L.LLMAdapter | None = None,
) -> M.Notes:
    """Load notes from a ``.md``/``.txt`` source or pre-shaped ``.json``."""
    if not path.is_file():
        raise FileNotFoundError(f"notes source not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".json":
        data: Any = json.loads(path.read_text(encoding="utf-8"))
        return M.Notes.model_validate(data)
    text = path.read_text(encoding="utf-8")
    return parse_notes_text(
        text,
        audit_id=audit_id,
        raw_path=str(path),
        summarizer=summarizer,
    )


__all__ = [
    "NOTES_SECTION_HEADINGS",
    "CATEGORY_KEYWORDS",
    "detect_candidate_categories",
    "parse_notes_text",
    "load_notes_file",
]
