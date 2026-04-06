"""Phase VIII tests for reporting.py and proposal.py."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from notion_audit_os import cli
from notion_audit_os import models as M
from notion_audit_os import proposal as P
from notion_audit_os import reporting as R
from notion_audit_os import scoring as sc
from notion_audit_os import storage as s

runner = CliRunner()

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _scorecard(
    scores: dict | None = None,
    audit_id: str = "aud_test_001",
) -> M.Scorecard:
    """Build a real scorecard via the deterministic scoring engine."""
    if scores is None:
        scores = {
            "Business Fit": 3,
            "Workspace Structure": 2,
            "Database Design": 1,
            "Data Relationships": 2,
            "Workflow Clarity": 2,
            "Views and Dashboards": 3,
            "Intake and Requests": "N/A",
            "Governance and Adoption": 2,
        }
    return sc.score_audit(audit_id, scores)


def _all_scores(value: int = 0) -> dict:
    return {c: value for c in M.CORE_CATEGORIES}


def _findings(
    audit_id: str = "aud_test_001",
    include_quick_win: bool = True,
    include_high: bool = True,
) -> M.FindingsCollection:
    items = []
    if include_high:
        items.append({
            "finding_id": "f001",
            "audit_id": audit_id,
            "category": "Database Design",
            "title": "Tasks DB lacks a single source of truth",
            "observation": "Three overlapping task databases exist with no canonical relation.",
            "evidence": ["screenshot-tasks.png"],
            "why_it_matters": "Status reporting is unreliable and duplicates effort.",
            "recommendation": "Consolidate into one Tasks DB with project relation.",
            "severity": "high",
            "priority": "high",
            "effort": "m",
            "quick_win": False,
            "status": "draft",
            "recommendation_type": "Structural Fix",
        })
    if include_quick_win:
        items.append({
            "finding_id": "f002",
            "audit_id": audit_id,
            "category": "Governance and Adoption",
            "title": "No page ownership conventions",
            "observation": "Pages lack consistent ownership tagging.",
            "evidence": ["audit-notes"],
            "why_it_matters": "Difficult to assign accountability for outdated content.",
            "severity": "medium",
            "priority": "medium",
            "effort": "s",
            "quick_win": True,
            "status": "draft",
            "recommendation_type": "Governance Fix",
        })
    items.append({
        "finding_id": "f003",
        "audit_id": audit_id,
        "category": "Views and Dashboards",
        "title": "Dashboard filters are inconsistent",
        "observation": "Each team uses different filter conventions.",
        "evidence": ["screenshare-2026-04"],
        "severity": "low",
        "quick_win": False,
        "status": "draft",
    })
    return M.FindingsCollection.model_validate(
        {"audit_id": audit_id, "generated_at": "2026-04-06T10:00:00Z", "findings": items}
    )


def _intake(audit_id: str = "aud_test_001", client_name: str = "Acme Corp") -> M.Intake:
    return M.Intake(
        audit_id=audit_id,
        normalized_payload=M.IntakePayload(client_name=client_name),
        parsed_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# reporting.assemble_report — basic correctness
# ---------------------------------------------------------------------------


def test_assemble_report_returns_report_model():
    card = _scorecard()
    fc = _findings()
    rpt = R.assemble_report("aud_test_001", card, fc)
    assert isinstance(rpt, M.Report)
    assert rpt.audit_id == "aud_test_001"


def test_assemble_report_all_sections_present():
    card = _scorecard()
    fc = _findings()
    rpt = R.assemble_report("aud_test_001", card, fc)
    s = rpt.sections
    assert s.executive_summary
    assert s.maturity_summary
    assert s.scorecard_summary
    assert s.recommended_next_step
    # key_findings and roadmap may be empty lists but must be lists
    assert isinstance(s.key_findings, list)
    assert isinstance(s.roadmap, list)


def test_assemble_report_recommended_package_matches_scorecard():
    """report layer must not override recommended_package."""
    card = _scorecard()
    fc = _findings()
    rpt = R.assemble_report("aud_test_001", card, fc)
    assert card.recommended_package in rpt.sections.recommended_next_step


def test_assemble_report_executive_summary_contains_score_and_band():
    card = _scorecard()
    fc = _findings()
    rpt = R.assemble_report("aud_test_001", card, fc)
    summary = rpt.sections.executive_summary
    assert str(card.overall_score) in summary
    assert card.maturity_band in summary


def test_assemble_report_uses_client_name_from_intake():
    card = _scorecard()
    fc = _findings()
    intake = _intake(client_name="Globex Ltd")
    rpt = R.assemble_report("aud_test_001", card, fc, intake=intake)
    assert "Globex Ltd" in rpt.sections.executive_summary


def test_assemble_report_without_intake_uses_audit_id():
    card = _scorecard()
    fc = _findings()
    rpt = R.assemble_report("aud_test_001", card, fc, intake=None)
    assert "aud_test_001" in rpt.sections.executive_summary


def test_assemble_report_template_version_propagates():
    card = _scorecard()
    fc = _findings()
    rpt = R.assemble_report("aud_test_001", card, fc, template_version="v2")
    assert rpt.template_version == "v2"


def test_assemble_report_output_format_is_json():
    card = _scorecard()
    fc = _findings()
    rpt = R.assemble_report("aud_test_001", card, fc)
    assert rpt.output_format == M.OutputFormat.JSON


# ---------------------------------------------------------------------------
# reporting — key findings selection
# ---------------------------------------------------------------------------


def test_key_findings_sorted_by_severity():
    """critical/high findings should appear before medium/low."""
    fc = M.FindingsCollection.model_validate({
        "findings": [
            {
                "finding_id": "flow", "audit_id": "a", "category": "Business Fit",
                "title": "Low sev", "observation": "obs", "evidence": ["e"],
                "severity": "low", "status": "draft",
            },
            {
                "finding_id": "fhigh", "audit_id": "a", "category": "Business Fit",
                "title": "High sev", "observation": "obs", "evidence": ["e"],
                "severity": "high", "status": "draft",
            },
            {
                "finding_id": "fcrit", "audit_id": "a", "category": "Business Fit",
                "title": "Critical sev", "observation": "obs", "evidence": ["e"],
                "severity": "critical", "status": "draft",
            },
        ]
    })
    card = _scorecard()
    rpt = R.assemble_report("a", card, fc)
    titles = [kf.title for kf in rpt.sections.key_findings]
    assert titles.index("Critical sev") < titles.index("High sev")
    assert titles.index("High sev") < titles.index("Low sev")


def test_key_findings_respects_limit():
    findings_data = [
        {
            "finding_id": f"f{i}", "audit_id": "a", "category": "Business Fit",
            "title": f"Finding {i}", "observation": "obs", "evidence": ["e"],
            "status": "draft",
        }
        for i in range(10)
    ]
    fc = M.FindingsCollection.model_validate({"findings": findings_data})
    card = _scorecard()
    rpt = R.assemble_report("a", card, fc)
    assert len(rpt.sections.key_findings) <= 5


def test_key_findings_uses_why_it_matters_as_summary():
    fc = M.FindingsCollection.model_validate({
        "findings": [{
            "finding_id": "f1", "audit_id": "a", "category": "Database Design",
            "title": "DB issue",
            "observation": "raw obs text",
            "evidence": ["e"],
            "why_it_matters": "This matters because of X.",
            "status": "draft",
        }]
    })
    card = _scorecard()
    rpt = R.assemble_report("a", card, fc)
    assert rpt.sections.key_findings[0].summary == "This matters because of X."


def test_key_findings_falls_back_to_observation():
    fc = M.FindingsCollection.model_validate({
        "findings": [{
            "finding_id": "f1", "audit_id": "a", "category": "Database Design",
            "title": "DB issue", "observation": "only observation here",
            "evidence": ["e"], "status": "draft",
        }]
    })
    card = _scorecard()
    rpt = R.assemble_report("a", card, fc)
    assert rpt.sections.key_findings[0].summary == "only observation here"


# ---------------------------------------------------------------------------
# reporting — roadmap derivation
# ---------------------------------------------------------------------------


def test_roadmap_quick_win_in_phase1():
    fc = _findings(include_quick_win=True, include_high=False)
    card = _scorecard()
    rpt = R.assemble_report("aud_test_001", card, fc)
    phases = {p.phase: p for p in rpt.sections.roadmap}
    assert any("Quick Win" in ph for ph in phases), "expected a Quick Wins phase"
    quick_phase = next(p for p in rpt.sections.roadmap if "Quick Win" in p.phase)
    assert "No page ownership conventions" in quick_phase.items


def test_roadmap_high_severity_in_phase2():
    fc = _findings(include_quick_win=False, include_high=True)
    card = _scorecard()
    rpt = R.assemble_report("aud_test_001", card, fc)
    core_phase = next(
        (p for p in rpt.sections.roadmap if "Core Fix" in p.phase), None
    )
    assert core_phase is not None
    assert "Tasks DB lacks a single source of truth" in core_phase.items


def test_roadmap_remaining_in_phase3():
    fc = _findings(include_quick_win=False, include_high=False)
    # Only f003 (low severity, quick_win=False) should be in phase 3
    card = _scorecard()
    rpt = R.assemble_report("aud_test_001", card, fc)
    improvement_phase = next(
        (p for p in rpt.sections.roadmap if "Improvement" in p.phase), None
    )
    assert improvement_phase is not None
    assert "Dashboard filters are inconsistent" in improvement_phase.items


def test_roadmap_empty_findings_produces_no_phases():
    fc = M.FindingsCollection.model_validate({"findings": []})
    card = _scorecard()
    rpt = R.assemble_report("aud_test_001", card, fc)
    assert rpt.sections.roadmap == []


# ---------------------------------------------------------------------------
# reporting — scorecard summary content
# ---------------------------------------------------------------------------


def test_scorecard_summary_contains_overall_score():
    card = _scorecard()
    fc = _findings()
    rpt = R.assemble_report("aud_test_001", card, fc)
    assert str(card.overall_score) in rpt.sections.scorecard_summary


def test_scorecard_summary_marks_na_categories():
    scores = {
        "Business Fit": 3, "Workspace Structure": 2, "Database Design": 1,
        "Data Relationships": 2, "Workflow Clarity": 2, "Views and Dashboards": 3,
        "Intake and Requests": "N/A", "Governance and Adoption": 2,
    }
    card = sc.score_audit("aud_test_001", scores)
    fc = _findings()
    rpt = R.assemble_report("aud_test_001", card, fc)
    assert "N/A" in rpt.sections.scorecard_summary
    assert "Intake and Requests" in rpt.sections.scorecard_summary


def test_scorecard_summary_preserves_rationale():
    card = _scorecard()
    fc = _findings()
    rpt = R.assemble_report("aud_test_001", card, fc)
    # The rationale from scoring is in the maturity summary, not the scorecard_summary
    # But the maturity_summary should contain the band and rationale
    assert card.maturity_band in rpt.sections.maturity_summary
    if card.rationale:
        assert card.rationale in rpt.sections.maturity_summary


# ---------------------------------------------------------------------------
# reporting — schema conformance
# ---------------------------------------------------------------------------


def test_report_json_validates_against_schema():
    card = _scorecard()
    fc = _findings()
    rpt = R.assemble_report("aud_test_001", card, fc)
    data = rpt.model_dump(by_alias=True, mode="json", exclude_none=True)
    # Should not raise
    s.get_schema_registry().validate("report.schema.json", data)


def test_report_does_not_invent_findings():
    """Report key_findings must only reference findings present in the input."""
    fc = _findings()
    input_ids = {f.finding_id for f in fc.findings}
    card = _scorecard()
    rpt = R.assemble_report("aud_test_001", card, fc)
    for kf in rpt.sections.key_findings:
        if kf.finding_id is not None:
            assert kf.finding_id in input_ids, (
                f"key finding {kf.finding_id!r} not in input findings"
            )


# ---------------------------------------------------------------------------
# reporting — Markdown rendering
# ---------------------------------------------------------------------------


def test_render_report_markdown_is_non_empty():
    card = _scorecard()
    fc = _findings()
    rpt = R.assemble_report("aud_test_001", card, fc)
    md = R.render_report_markdown(rpt)
    assert isinstance(md, str)
    assert len(md) > 100


def test_render_report_markdown_contains_key_sections():
    card = _scorecard()
    fc = _findings()
    rpt = R.assemble_report("aud_test_001", card, fc)
    md = R.render_report_markdown(rpt)
    for heading in [
        "Executive Summary",
        "Maturity Assessment",
        "Key Findings",
        "Scorecard Summary",
        "Recommended Next Step",
    ]:
        assert heading in md, f"missing section: {heading}"


def test_render_report_markdown_includes_audit_id():
    card = _scorecard()
    fc = _findings()
    rpt = R.assemble_report("aud_test_001", card, fc)
    md = R.render_report_markdown(rpt)
    assert "aud_test_001" in md


def test_render_report_markdown_includes_roadmap_when_present():
    fc = _findings(include_quick_win=True, include_high=True)
    card = _scorecard()
    rpt = R.assemble_report("aud_test_001", card, fc)
    md = R.render_report_markdown(rpt)
    assert "Roadmap" in md


# ---------------------------------------------------------------------------
# proposal.assemble_proposal — basic correctness
# ---------------------------------------------------------------------------


def test_assemble_proposal_returns_proposal_model():
    card = _scorecard()
    fc = _findings()
    prop = P.assemble_proposal("aud_test_001", card, fc)
    assert isinstance(prop, M.Proposal)
    assert prop.audit_id == "aud_test_001"


def test_assemble_proposal_package_propagates_unchanged():
    """recommended_package must come from the scorecard, not be invented."""
    card = _scorecard()
    fc = _findings()
    prop = P.assemble_proposal("aud_test_001", card, fc)
    assert prop.recommended_package == card.recommended_package


def test_assemble_proposal_scope_summary_contains_score():
    card = _scorecard()
    fc = _findings()
    prop = P.assemble_proposal("aud_test_001", card, fc)
    assert str(card.overall_score) in prop.scope_summary


def test_assemble_proposal_scope_summary_contains_package():
    card = _scorecard()
    fc = _findings()
    prop = P.assemble_proposal("aud_test_001", card, fc)
    assert card.recommended_package in prop.scope_summary


def test_assemble_proposal_deliverables_not_empty():
    card = _scorecard()
    fc = _findings()
    prop = P.assemble_proposal("aud_test_001", card, fc)
    assert len(prop.deliverables) > 0


def test_assemble_proposal_exclusions_not_empty():
    card = _scorecard()
    fc = _findings()
    prop = P.assemble_proposal("aud_test_001", card, fc)
    assert len(prop.exclusions) > 0


def test_all_packages_have_deliverables():
    for package in M.RecommendedPackage:
        assert package.value in P.PACKAGE_DELIVERABLES, (
            f"missing deliverables for package: {package.value}"
        )
        assert len(P.PACKAGE_DELIVERABLES[package.value]) > 0


def test_all_packages_have_exclusions():
    for package in M.RecommendedPackage:
        assert package.value in P.PACKAGE_EXCLUSIONS, (
            f"missing exclusions for package: {package.value}"
        )
        assert len(P.PACKAGE_EXCLUSIONS[package.value]) > 0


def test_assemble_proposal_full_rebuild_has_rebuild_deliverables():
    scores = _all_scores(0)
    card = sc.score_audit("aud_full", scores)
    assert card.recommended_package == "Full Rebuild"
    fc = M.FindingsCollection.model_validate({"findings": []})
    prop = P.assemble_proposal("aud_full", card, fc)
    assert prop.recommended_package == "Full Rebuild"
    joined = " ".join(prop.deliverables)
    assert "database" in joined.lower() or "architecture" in joined.lower()


def test_assemble_proposal_does_not_override_package():
    """Proposal layer must never change the package from the scorecard."""
    for package in M.RecommendedPackage:
        # Build a scorecard directly with model_validate to test each package
        raw = M.Scorecard.model_validate({
            "audit_id": "x",
            "categories": {c: 2 for c in M.CORE_CATEGORIES},
            "active_weights": {c: float(M.DEFAULT_CORE_WEIGHTS[c]) for c in M.CORE_CATEGORIES},
            "weighted_points": {c: (2 / 4.0) * M.DEFAULT_CORE_WEIGHTS[c] for c in M.CORE_CATEGORIES},
            "overall_score": 50.0,
            "maturity_band": "Functional but weak",
            "recommended_package": package.value,
        })
        fc = M.FindingsCollection.model_validate({"findings": []})
        prop = P.assemble_proposal("x", raw, fc)
        assert prop.recommended_package == package.value, (
            f"package changed from {package.value!r} to {prop.recommended_package!r}"
        )


# ---------------------------------------------------------------------------
# proposal — schema conformance
# ---------------------------------------------------------------------------


def test_proposal_json_validates_against_schema():
    card = _scorecard()
    fc = _findings()
    prop = P.assemble_proposal("aud_test_001", card, fc)
    data = prop.model_dump(by_alias=True, mode="json", exclude_none=True)
    s.get_schema_registry().validate("proposal.schema.json", data)


# ---------------------------------------------------------------------------
# proposal — Markdown rendering
# ---------------------------------------------------------------------------


def test_render_proposal_markdown_is_non_empty():
    card = _scorecard()
    fc = _findings()
    prop = P.assemble_proposal("aud_test_001", card, fc)
    md = P.render_proposal_markdown(prop)
    assert isinstance(md, str)
    assert len(md) > 50


def test_render_proposal_markdown_contains_key_sections():
    card = _scorecard()
    fc = _findings()
    prop = P.assemble_proposal("aud_test_001", card, fc)
    md = P.render_proposal_markdown(prop)
    for heading in ["Recommended Package", "Scope Summary", "Deliverables", "Exclusions"]:
        assert heading in md, f"missing section: {heading}"


def test_render_proposal_markdown_contains_package_name():
    card = _scorecard()
    fc = _findings()
    prop = P.assemble_proposal("aud_test_001", card, fc)
    md = P.render_proposal_markdown(prop)
    assert card.recommended_package in md


# ---------------------------------------------------------------------------
# CLI integration — report command
# ---------------------------------------------------------------------------


def _common(client: str, audit: str, data_root: Path) -> list[str]:
    return ["--client", client, "--audit", audit, "--data-root", str(data_root)]


def _setup_audit_with_scorecard(
    data_root: Path, tmp_path: Path
) -> tuple[Path, M.Scorecard]:
    """Run init + intake + notes + findings + score to produce a scorecard."""
    audit_dir = data_root / "clients" / "acme" / "audits" / "aud_001"

    # init
    r = runner.invoke(
        cli.app,
        ["init", *_common("acme", "aud_001", data_root), "--client-name", "Acme Co"],
    )
    assert r.exit_code == 0, r.stdout

    # intake
    intake = M.Intake(
        audit_id="aud_001",
        normalized_payload=M.IntakePayload(client_name="Acme Co"),
        parsed_at=datetime.now(timezone.utc),
    )
    intake_p = tmp_path / "intake.json"
    s.write_json(intake_p, intake.model_dump(by_alias=True, mode="json", exclude_none=True))
    r = runner.invoke(
        cli.app, ["intake", *_common("acme", "aud_001", data_root), "--input", str(intake_p)]
    )
    assert r.exit_code == 0, r.stdout

    # notes
    notes = M.Notes(
        audit_id="aud_001",
        source_type=M.SourceType.INTERVIEW,
        normalized_summary=M.NormalizedSummary(observations=["o1"]),
        generated_at=datetime.now(timezone.utc),
    )
    notes_p = tmp_path / "notes.json"
    s.write_json(notes_p, notes.model_dump(by_alias=True, mode="json", exclude_none=True))
    r = runner.invoke(
        cli.app,
        ["normalize-notes", *_common("acme", "aud_001", data_root), "--input", str(notes_p)],
    )
    assert r.exit_code == 0, r.stdout

    # findings
    fc = M.FindingsCollection.model_validate({
        "findings": [{
            "finding_id": "f1", "audit_id": "aud_001",
            "category": "Database Design", "title": "DB issue",
            "observation": "obs", "evidence": ["e"],
            "severity": "high", "quick_win": False, "status": "draft",
        }]
    })
    fc_p = tmp_path / "findings.json"
    s.write_json(fc_p, fc.model_dump(by_alias=True, mode="json", exclude_none=True))
    r = runner.invoke(
        cli.app,
        ["draft-findings", *_common("acme", "aud_001", data_root), "--input", str(fc_p)],
    )
    assert r.exit_code == 0, r.stdout

    # promote to final
    draft = (audit_dir / "findings.draft.json").read_text(encoding="utf-8")
    (audit_dir / "findings.final.json").write_text(draft, encoding="utf-8")

    # score
    scores_p = tmp_path / "scores.json"
    s.write_json(scores_p, {
        "Business Fit": 3, "Workspace Structure": 2, "Database Design": 1,
        "Data Relationships": 2, "Workflow Clarity": 2, "Views and Dashboards": 3,
        "Intake and Requests": "N/A", "Governance and Adoption": 2,
    })
    r = runner.invoke(
        cli.app,
        ["score", *_common("acme", "aud_001", data_root), "--scores", str(scores_p)],
    )
    assert r.exit_code == 0, r.stdout

    card = s.load_model(audit_dir / "scorecard.json", M.Scorecard)
    return audit_dir, card


def test_cli_report_command_produces_report_draft(tmp_path: Path):
    data_root = tmp_path / "data"
    audit_dir, card = _setup_audit_with_scorecard(data_root, tmp_path)

    r = runner.invoke(cli.app, ["report", *_common("acme", "aud_001", data_root)])
    assert r.exit_code == 0, r.stdout
    assert (audit_dir / "report.draft.json").is_file()


def test_cli_report_draft_is_schema_valid(tmp_path: Path):
    data_root = tmp_path / "data"
    audit_dir, _ = _setup_audit_with_scorecard(data_root, tmp_path)
    runner.invoke(cli.app, ["report", *_common("acme", "aud_001", data_root)])
    data = s.read_json(audit_dir / "report.draft.json")
    s.get_schema_registry().validate("report.schema.json", data)


def test_cli_report_package_consistent_with_scorecard(tmp_path: Path):
    data_root = tmp_path / "data"
    audit_dir, card = _setup_audit_with_scorecard(data_root, tmp_path)
    runner.invoke(cli.app, ["report", *_common("acme", "aud_001", data_root)])
    data = s.read_json(audit_dir / "report.draft.json")
    assert card.recommended_package in data["sections"]["recommended_next_step"]


def test_cli_report_also_markdown_writes_md_file(tmp_path: Path):
    data_root = tmp_path / "data"
    audit_dir, _ = _setup_audit_with_scorecard(data_root, tmp_path)
    r = runner.invoke(
        cli.app,
        ["report", *_common("acme", "aud_001", data_root), "--also-markdown"],
    )
    assert r.exit_code == 0, r.stdout
    assert (audit_dir / "report.draft.md").is_file()
    md = (audit_dir / "report.draft.md").read_text(encoding="utf-8")
    assert "Executive Summary" in md


# ---------------------------------------------------------------------------
# CLI integration — proposal command
# ---------------------------------------------------------------------------


def test_cli_proposal_command_produces_proposal_draft(tmp_path: Path):
    data_root = tmp_path / "data"
    audit_dir, _ = _setup_audit_with_scorecard(data_root, tmp_path)
    r = runner.invoke(cli.app, ["proposal", *_common("acme", "aud_001", data_root)])
    assert r.exit_code == 0, r.stdout
    assert (audit_dir / "proposal.draft.json").is_file()


def test_cli_proposal_draft_is_schema_valid(tmp_path: Path):
    data_root = tmp_path / "data"
    audit_dir, _ = _setup_audit_with_scorecard(data_root, tmp_path)
    runner.invoke(cli.app, ["proposal", *_common("acme", "aud_001", data_root)])
    data = s.read_json(audit_dir / "proposal.draft.json")
    s.get_schema_registry().validate("proposal.schema.json", data)


def test_cli_proposal_package_consistent_with_scorecard(tmp_path: Path):
    data_root = tmp_path / "data"
    audit_dir, card = _setup_audit_with_scorecard(data_root, tmp_path)
    runner.invoke(cli.app, ["proposal", *_common("acme", "aud_001", data_root)])
    data = s.read_json(audit_dir / "proposal.draft.json")
    assert data["recommended_package"] == card.recommended_package


def test_cli_proposal_deliverables_and_exclusions_populated(tmp_path: Path):
    data_root = tmp_path / "data"
    audit_dir, _ = _setup_audit_with_scorecard(data_root, tmp_path)
    runner.invoke(cli.app, ["proposal", *_common("acme", "aud_001", data_root)])
    data = s.read_json(audit_dir / "proposal.draft.json")
    assert len(data.get("deliverables", [])) > 0
    assert len(data.get("exclusions", [])) > 0


def test_cli_proposal_also_markdown_writes_md_file(tmp_path: Path):
    data_root = tmp_path / "data"
    audit_dir, _ = _setup_audit_with_scorecard(data_root, tmp_path)
    r = runner.invoke(
        cli.app,
        ["proposal", *_common("acme", "aud_001", data_root), "--also-markdown"],
    )
    assert r.exit_code == 0, r.stdout
    assert (audit_dir / "proposal.draft.md").is_file()
    md = (audit_dir / "proposal.draft.md").read_text(encoding="utf-8")
    assert "Scope Summary" in md
