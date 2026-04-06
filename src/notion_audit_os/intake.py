"""Intake parsing and normalization.

Turns a Markdown/text intake document into a validated :class:`models.Intake`
artifact. v1 supports a small, explicit Markdown shape: top-level
``# Heading`` sections whose names map to :class:`models.IntakePayload`
fields. Anything not present in the source becomes a ``missing_fields``
entry — the parser never invents data.

Recognized headings (case-insensitive, ``-``/``_``/space tolerant):

* ``Client Name``                → ``client_name`` (string)
* ``Team Size``                  → ``team_size`` (integer)
* ``Notion Plan``                → ``notion_plan`` (enum string)
* ``Primary Use Cases``          → ``primary_use_cases`` (bullet list)
* ``Pain Points``                → ``pain_points`` (bullet list)
* ``Current Notion Usage``       → ``current_notion_usage`` (paragraph)
* ``Desired Outcomes``           → ``desired_outcomes`` (bullet list)
* ``Workspace Owner``            → ``workspace_owner`` (string)
* ``Tools In Use``               → ``tools_in_use`` (bullet list)
* ``AI In Use``                  → ``ai_in_use`` (bullet list)

JSON intake (already in :class:`models.Intake` shape) is also accepted
via :func:`load_intake_file` for the existing CLI input path.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import models as M

# ---------------------------------------------------------------------------
# Heading map (canonical key -> set of accepted variants, case-insensitive)
# ---------------------------------------------------------------------------

#: Order matters only for the "missing_fields" report.
INTAKE_FIELD_HEADINGS: dict[str, set[str]] = {
    "client_name": {"client name", "client"},
    "team_size": {"team size", "team"},
    "notion_plan": {"notion plan", "plan"},
    "primary_use_cases": {"primary use cases", "use cases"},
    "pain_points": {"pain points", "pains"},
    "current_notion_usage": {"current notion usage", "current usage", "usage"},
    "desired_outcomes": {"desired outcomes", "goals", "outcomes"},
    "workspace_owner": {"workspace owner", "owner"},
    "tools_in_use": {"tools in use", "tools"},
    "ai_in_use": {"ai in use", "ai"},
}

#: Fields that are bullet lists in the source.
LIST_FIELDS = {
    "primary_use_cases",
    "pain_points",
    "desired_outcomes",
    "tools_in_use",
    "ai_in_use",
}

#: Fields that are integers.
INT_FIELDS = {"team_size"}

#: Fields whose value is a single line / short string.
SCALAR_FIELDS = {"client_name", "notion_plan", "workspace_owner"}

#: Fields that are a free-form paragraph.
PARAGRAPH_FIELDS = {"current_notion_usage"}


def _norm_heading(h: str) -> str:
    return h.strip().lower().replace("-", " ").replace("_", " ").rstrip(":")


def _heading_to_field(heading: str) -> str | None:
    norm = _norm_heading(heading)
    for field, variants in INTAKE_FIELD_HEADINGS.items():
        if norm in variants:
            return field
    return None


def _split_sections(text: str) -> dict[str, list[str]]:
    """Split a Markdown intake doc into ``{field_name: [body_lines]}``."""
    sections: dict[str, list[str]] = {}
    current_field: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
            current_field = _heading_to_field(heading)
            if current_field is not None:
                sections.setdefault(current_field, [])
            continue
        if current_field is not None:
            sections[current_field].append(line)
    return sections


def _bullets(lines: list[str]) -> list[str]:
    out: list[str] = []
    for ln in lines:
        stripped = ln.strip()
        if stripped.startswith(("-", "*")):
            out.append(stripped.lstrip("-* ").strip())
        elif stripped and stripped[0].isdigit() and "." in stripped[:3]:
            out.append(stripped.split(".", 1)[1].strip())
    return [b for b in out if b]


def _scalar(lines: list[str]) -> str | None:
    for ln in lines:
        s = ln.strip()
        if s:
            return s
    return None


def _paragraph(lines: list[str]) -> str | None:
    text = " ".join(ln.strip() for ln in lines if ln.strip())
    return text or None


def _coerce_int(value: str) -> int | None:
    digits = "".join(c for c in value if c.isdigit())
    if not digits:
        return None
    try:
        n = int(digits)
    except ValueError:
        return None
    return n if n >= 1 else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_intake_text(
    text: str,
    *,
    audit_id: str,
    raw_source_path: str | None = None,
) -> M.Intake:
    """Parse a Markdown/text intake document into an :class:`Intake`.

    Missing or unparseable fields go into ``missing_fields`` rather than
    being silently filled in.
    """
    sections = _split_sections(text)
    payload: dict[str, Any] = {}
    missing: list[str] = []

    for field in INTAKE_FIELD_HEADINGS:
        body = sections.get(field)
        if body is None:
            missing.append(field)
            continue

        if field in LIST_FIELDS:
            items = _bullets(body)
            if not items:
                missing.append(field)
            else:
                payload[field] = items
        elif field in INT_FIELDS:
            raw = _scalar(body)
            n = _coerce_int(raw) if raw else None
            if n is None:
                missing.append(field)
            else:
                payload[field] = n
        elif field == "notion_plan":
            raw = _scalar(body)
            if raw is None:
                missing.append(field)
            else:
                norm = raw.strip().lower()
                allowed = {p.value for p in M.NotionPlan}
                if norm in allowed:
                    payload[field] = norm
                else:
                    payload[field] = M.NotionPlan.UNKNOWN.value
                    missing.append(field)  # operator should review
        elif field in SCALAR_FIELDS:
            value = _scalar(body)
            if value is None:
                missing.append(field)
            else:
                payload[field] = value
        elif field in PARAGRAPH_FIELDS:
            value = _paragraph(body)
            if value is None:
                missing.append(field)
            else:
                payload[field] = value

    intake_payload = M.IntakePayload.model_validate(payload)
    return M.Intake(
        audit_id=audit_id,
        raw_source_path=raw_source_path,
        normalized_payload=intake_payload,
        missing_fields=missing,
        parsed_at=datetime.now(timezone.utc),
    )


def load_intake_file(
    path: Path,
    *,
    audit_id: str,
) -> M.Intake:
    """Load intake from a ``.md``/``.txt`` source or pre-shaped ``.json``.

    JSON files are validated as :class:`models.Intake` directly. Markdown
    or text files are parsed via :func:`parse_intake_text`. Any other
    extension is treated as text.
    """
    if not path.is_file():
        raise FileNotFoundError(f"intake source not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        return M.Intake.model_validate(data)
    text = path.read_text(encoding="utf-8")
    return parse_intake_text(text, audit_id=audit_id, raw_source_path=str(path))


__all__ = [
    "INTAKE_FIELD_HEADINGS",
    "parse_intake_text",
    "load_intake_file",
]
