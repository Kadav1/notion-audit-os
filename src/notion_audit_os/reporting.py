"""Report assembly for Core Audit v1.1.

Assembles a validated :class:`models.Report` from:

* a reviewed :class:`models.Scorecard`
* an approved :class:`models.FindingsCollection`
* optionally an :class:`models.Intake` for client context

All section text is derived from structured audit data. No content is
invented. The LLM layer is deliberately not called here; Phase VIII keeps
report assembly deterministic. Richer prose via LLM is additive and belongs
in a later phase.

Locked decisions carried forward:

* ``recommended_package`` comes from the scorecard unchanged.
* Scoring rationale is preserved in the maturity summary, not rewritten.
* ``why_it_matters`` on findings is used as-is for finding summaries.
* No routing or scoring logic is duplicated here.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import models as M

# ---------------------------------------------------------------------------
# Locked prose tables
# ---------------------------------------------------------------------------

#: One paragraph per maturity band describing the band's meaning.
_BAND_PROSE: dict[str, str] = {
    "Critical disorder": (
        "The workspace is in critical disorder. Core structures, databases, and workflows "
        "are misaligned or broken, making reliable day-to-day operation difficult. "
        "Incremental improvements will not hold until the foundation is rebuilt."
    ),
    "Fragile": (
        "The workspace is functional but fragile. Significant structural or governance "
        "gaps make the system vulnerable to inconsistency and workaround accumulation. "
        "Targeted remediation is needed before the workspace can be relied upon consistently."
    ),
    "Functional but weak": (
        "The workspace is functional but shows meaningful weaknesses in one or more key "
        "areas. Day-to-day operations are possible, but the foundation is not strong enough "
        "to support reliable scaling, automation, or team-wide adoption without first "
        "addressing the identified gaps."
    ),
    "Solid": (
        "The workspace is solid. Core structures and workflows are reasonably well-organized "
        "and the system supports reliable daily use. The focus should shift toward tightening "
        "governance, improving consistency, and preparing for more advanced use cases."
    ),
    "Strong": (
        "The workspace is strong. Structures, databases, and workflows are well-designed "
        "and well-maintained. The system is ready for optimization, automation, or AI "
        "extensions without requiring structural changes first."
    ),
}

#: Recommended next step prose per locked package name.
_PACKAGE_NEXT_STEP: dict[str, str] = {
    "Full Rebuild": (
        "The recommended next step is a Full Rebuild engagement. This involves redesigning "
        "the workspace architecture from the ground up: consolidating databases, establishing "
        "clear relational structures, and rebuilding core workflows to a reliable standard. "
        "The current state does not support incremental fixes \u2014 a clean, stable foundation "
        "must be established first."
    ),
    "Partial Rebuild": (
        "The recommended next step is a Partial Rebuild engagement. This targets the specific "
        "structural and database weaknesses identified in the audit while preserving areas "
        "that are already working well. The goal is a more stable and consistent foundation "
        "without the cost or disruption of a full restart."
    ),
    "Governance Add-on": (
        "The recommended next step is a Governance Add-on engagement. The structural core of "
        "the workspace is in reasonable shape, but governance practices and intake workflows "
        "need to be formalized. Without this, the workspace risks drift, inconsistent "
        "ownership, and reduced team-wide adoption over time."
    ),
    "Optimization Sprint": (
        "The recommended next step is an Optimization Sprint. The workspace has a solid "
        "foundation, and the primary opportunity is in refining views, improving clarity, "
        "and closing smaller inconsistencies that are slowing down day-to-day use."
    ),
    "Automation / AI Add-on": (
        "The recommended next step is an Automation / AI Add-on engagement. The workspace "
        "structure is strong and well-organized, making it a ready candidate for workflow "
        "automation, smart filters, or AI-assisted integrations that extend the system's "
        "value without requiring structural changes."
    ),
    "No immediate major project needed": (
        "No immediate major project is needed. The workspace is performing well across all "
        "key dimensions. The recommended focus is on routine maintenance, light refinements "
        "as needs evolve, and monitoring for future requirements as the team grows."
    ),
}

#: Severity sort order (lower = higher priority). Missing/None sorts last.
_SEVERITY_RANK: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}

# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _executive_summary(
    audit_id: str,
    scorecard: M.Scorecard,
    fc: M.FindingsCollection,
    intake: M.Intake | None,
) -> str:
    """Compose the executive summary from structured inputs only."""
    client_name: str | None = (
        intake.normalized_payload.client_name
        if intake and intake.normalized_payload.client_name
        else None
    )
    subject = f"the workspace for {client_name}" if client_name else f"audit {audit_id}"
    n = len(fc.findings)
    high_count = sum(
        1 for f in fc.findings if (f.severity or "") in ("critical", "high")
    )
    finding_phrase = f"{n} finding{'s' if n != 1 else ''}"
    if high_count:
        finding_phrase += f", including {high_count} rated high or critical severity"

    return (
        f"This report presents the results of a Core Audit v1.1 of {subject}. "
        f"The workspace received an overall maturity score of {scorecard.overall_score} "
        f"out of 100, placing it in the \"{scorecard.maturity_band}\" band. "
        f"The audit surfaced {finding_phrase}. "
        f"The recommended engagement is: {scorecard.recommended_package}."
    )


def _maturity_summary(scorecard: M.Scorecard) -> str:
    """Compose the maturity summary from band prose and scoring rationale.

    The deterministic rationale from ``scoring.build_rationale`` is
    preserved verbatim \u2014 the report layer does not rewrite it.
    """
    band = scorecard.maturity_band
    band_prose = _BAND_PROSE.get(band, f"Maturity band: {band}.")
    rationale = scorecard.rationale or ""
    if rationale:
        return f"{band_prose}\n\n{rationale}"
    return band_prose


def _select_key_findings(
    fc: M.FindingsCollection,
    limit: int = 5,
) -> list[M.KeyFindingRef]:
    """Select up to ``limit`` key findings, sorted by severity (highest first).

    Uses ``why_it_matters`` as the summary when present, falling back to
    ``observation``. Both fields come directly from the finding \u2014 no text
    is invented.
    """
    sorted_findings = sorted(
        fc.findings,
        key=lambda f: _SEVERITY_RANK.get(f.severity or "", 4),
    )
    refs: list[M.KeyFindingRef] = []
    for f in sorted_findings[:limit]:
        summary = f.why_it_matters or f.observation
        refs.append(
            M.KeyFindingRef(
                finding_id=f.finding_id,
                title=f.title,
                summary=summary,
            )
        )
    return refs


def _scorecard_summary(scorecard: M.Scorecard) -> str:
    """Build a human-readable scorecard summary string from category scores."""
    cats = scorecard.categories.model_dump(by_alias=True, mode="json")
    weights = scorecard.active_weights.model_dump(by_alias=True, mode="json")

    lines: list[str] = [
        f"Overall score: {scorecard.overall_score} \u2014 {scorecard.maturity_band}.",
        "",
        "Category breakdown:",
    ]
    for cat in M.CORE_CATEGORIES:
        score = cats[cat]
        if score == "N/A":
            lines.append(f"  {cat}: N/A (not applicable)")
        else:
            weight = int(weights.get(cat, 0))
            tag = ""
            if score <= 1:
                tag = "  \u2190 weak"
            elif score >= 3:
                tag = "  \u2190 strong"
            lines.append(f"  {cat}: {score}/4  (weight {weight}){tag}")

    weak = [c for c in M.CORE_CATEGORIES if isinstance(cats[c], int) and cats[c] <= 1]
    strong = [c for c in M.CORE_CATEGORIES if isinstance(cats[c], int) and cats[c] >= 3]
    if weak:
        lines.append("")
        lines.append(f"Categories needing attention: {', '.join(weak)}.")
    if strong:
        lines.append(f"Strongest areas: {', '.join(strong)}.")

    return "\n".join(lines)


def _build_roadmap(fc: M.FindingsCollection) -> list[M.RoadmapItem]:
    """Derive a phased roadmap from findings using ``quick_win`` and severity.

    Three phases (only non-empty phases are emitted):

    * Phase 1 \u2014 Quick Wins: findings marked ``quick_win=True``.
    * Phase 2 \u2014 Core Fixes: critical/high severity findings not already in phase 1.
    * Phase 3 \u2014 Improvements: all remaining findings.
    """
    quick_wins: list[str] = []
    core_fixes: list[str] = []
    improvements: list[str] = []

    for f in fc.findings:
        if f.quick_win:
            quick_wins.append(f.title)
        elif (f.severity or "") in ("critical", "high"):
            core_fixes.append(f.title)
        else:
            improvements.append(f.title)

    roadmap: list[M.RoadmapItem] = []
    if quick_wins:
        roadmap.append(
            M.RoadmapItem(
                phase="Phase 1 \u2014 Quick Wins",
                summary="High-impact changes achievable with minimal effort.",
                items=quick_wins,
            )
        )
    if core_fixes:
        roadmap.append(
            M.RoadmapItem(
                phase="Phase 2 \u2014 Core Fixes",
                summary="Critical and high-severity issues requiring structural attention.",
                items=core_fixes,
            )
        )
    if improvements:
        roadmap.append(
            M.RoadmapItem(
                phase="Phase 3 \u2014 Improvements",
                summary="Lower-priority enhancements and longer-term optimizations.",
                items=improvements,
            )
        )
    return roadmap


def _recommended_next_step(scorecard: M.Scorecard) -> str:
    """Return the locked next-step prose for the scorecard's recommended package."""
    return _PACKAGE_NEXT_STEP.get(
        scorecard.recommended_package,
        f"Recommended package: {scorecard.recommended_package}.",
    )


# ---------------------------------------------------------------------------
# Top-level assembly
# ---------------------------------------------------------------------------


def assemble_report(
    audit_id: str,
    scorecard: M.Scorecard,
    fc: M.FindingsCollection,
    *,
    intake: M.Intake | None = None,
    template_version: str = "v1",
) -> M.Report:
    """Assemble a structured :class:`models.Report` from approved inputs.

    All section content is derived from the supplied scorecard and findings.
    No content is invented. ``recommended_package`` is taken directly from
    the scorecard without modification.

    Args:
        audit_id: The audit identifier.
        scorecard: Validated scorecard artifact.
        fc: Approved findings collection.
        intake: Optional intake artifact for client name context.
        template_version: Template version string. Defaults to ``"v1"``.

    Returns:
        A validated :class:`models.Report` ready for serialization.
    """
    sections = M.ReportSections(
        executive_summary=_executive_summary(audit_id, scorecard, fc, intake),
        maturity_summary=_maturity_summary(scorecard),
        key_findings=_select_key_findings(fc),
        scorecard_summary=_scorecard_summary(scorecard),
        roadmap=_build_roadmap(fc),
        recommended_next_step=_recommended_next_step(scorecard),
    )
    return M.Report(
        audit_id=audit_id,
        template_version=template_version,
        output_format=M.OutputFormat.JSON,
        generated_at=datetime.now(timezone.utc),
        sections=sections,
    )


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def render_report_markdown(report: M.Report) -> str:
    """Render a :class:`models.Report` as a Markdown string.

    The output is a human-readable draft suitable for review and later
    conversion to PDF or DOCX. It is derived solely from the structured
    ``report`` object \u2014 no additional facts are introduced.
    """
    s = report.sections
    generated = (
        report.generated_at.isoformat(timespec="seconds")
        if hasattr(report.generated_at, "isoformat")
        else str(report.generated_at)
    )
    lines: list[str] = [
        f"# Audit Report \u2014 {report.audit_id}",
        "",
        f"_Template: {report.template_version} | Generated: {generated}_",
        "",
        "---",
        "",
        "## Executive Summary",
        "",
        s.executive_summary,
        "",
        "---",
        "",
        "## Maturity Assessment",
        "",
        s.maturity_summary,
        "",
        "---",
        "",
        "## Key Findings",
        "",
    ]
    if s.key_findings:
        for i, kf in enumerate(s.key_findings, 1):
            ref = f" `{kf.finding_id}`" if kf.finding_id else ""
            lines.append(f"{i}. **{kf.title}**{ref}")
            if kf.summary:
                lines.append(f"   _{kf.summary}_")
        lines.append("")
    else:
        lines += ["_(no key findings selected)_", ""]

    lines += [
        "---",
        "",
        "## Scorecard Summary",
        "",
        s.scorecard_summary,
        "",
    ]

    if s.roadmap:
        lines += ["---", "", "## Roadmap", ""]
        for phase in s.roadmap:
            lines += [f"### {phase.phase}", "", phase.summary, ""]
            for item in phase.items:
                lines.append(f"- {item}")
            lines.append("")

    lines += [
        "---",
        "",
        "## Recommended Next Step",
        "",
        s.recommended_next_step,
        "",
    ]

    if s.appendix:
        lines += ["---", "", "## Appendix", "", s.appendix, ""]

    return "\n".join(lines)


__all__ = [
    "assemble_report",
    "render_report_markdown",
]
