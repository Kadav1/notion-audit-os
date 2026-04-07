"""Phase IX tests for export.py and the updated CLI export command."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import json
import pytest
from typer.testing import CliRunner

from notion_audit_os import cli
from notion_audit_os import export as E
from notion_audit_os import models as M
from notion_audit_os import reporting as R
from notion_audit_os import proposal as P
from notion_audit_os import scoring as sc
from notion_audit_os import storage as s

runner = CliRunner()

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _common(client: str, audit: str, data_root: Path) -> list[str]:
    return ["--client", client, "--audit", audit, "--data-root", str(data_root)]


def _make_report(audit_id: str = "aud_001") -> M.Report:
    scores = {
        "Business Fit": 3, "Workspace Structure": 2, "Database Design": 1,
        "Data Relationships": 2, "Workflow Clarity": 2, "Views and Dashboards": 3,
        "Intake and Requests": "N/A", "Governance and Adoption": 2,
    }
    card = sc.score_audit(audit_id, scores)
    fc = M.FindingsCollection.model_validate({
        "findings": [{
            "finding_id": "f1", "audit_id": audit_id,
            "category": "Database Design", "title": "DB issue",
            "observation": "obs", "evidence": ["e"],
            "why_it_matters": "Matters a lot.", "severity": "high",
            "quick_win": False, "status": "draft",
        }]
    })
    return R.assemble_report(audit_id, card, fc)


def _make_proposal(audit_id: str = "aud_001") -> M.Proposal:
    scores = {
        "Business Fit": 3, "Workspace Structure": 2, "Database Design": 1,
        "Data Relationships": 2, "Workflow Clarity": 2, "Views and Dashboards": 3,
        "Intake and Requests": "N/A", "Governance and Adoption": 2,
    }
    card = sc.score_audit(audit_id, scores)
    fc = M.FindingsCollection.model_validate({"findings": []})
    return P.assemble_proposal(audit_id, card, fc)


def _make_scorecard(audit_id: str = "aud_001") -> M.Scorecard:
    scores = {
        "Business Fit": 3, "Workspace Structure": 2, "Database Design": 1,
        "Data Relationships": 2, "Workflow Clarity": 2, "Views and Dashboards": 3,
        "Intake and Requests": "N/A", "Governance and Adoption": 2,
    }
    return sc.score_audit(audit_id, scores)


def _write_final_report(audit_dir: Path, audit_id: str = "aud_001") -> Path:
    rpt = _make_report(audit_id)
    data = rpt.model_dump(by_alias=True, mode="json", exclude_none=True)
    p = audit_dir / "report.final.json"
    s.write_json(p, data)
    return p


def _write_final_proposal(audit_dir: Path, audit_id: str = "aud_001") -> Path:
    prop = _make_proposal(audit_id)
    data = prop.model_dump(by_alias=True, mode="json", exclude_none=True)
    p = audit_dir / "proposal.final.json"
    s.write_json(p, data)
    return p


def _write_scorecard(audit_dir: Path, audit_id: str = "aud_001") -> Path:
    card = _make_scorecard(audit_id)
    data = card.model_dump(by_alias=True, mode="json", exclude_none=True)
    p = audit_dir / "scorecard.json"
    s.write_json(p, data)
    return p


def _make_audit_dir(tmp_path: Path, audit_id: str = "aud_001") -> Path:
    audit_dir = tmp_path / "data" / "clients" / "acme" / "audits" / audit_id
    audit_dir.mkdir(parents=True, exist_ok=True)
    return audit_dir


def _make_paths(tmp_path: Path, audit_id: str = "aud_001") -> s.AuditPaths:
    return s.AuditPaths(
        data_root=tmp_path / "data",
        client_slug="acme",
        audit_id=audit_id,
    )


# ---------------------------------------------------------------------------
# check_finalization
# ---------------------------------------------------------------------------


def test_check_finalization_all_missing(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    status = E.check_finalization(paths)
    assert not status.findings_has_draft
    assert not status.findings_is_final
    assert not status.report_has_draft
    assert not status.report_is_final
    assert not status.proposal_has_draft
    assert not status.proposal_is_final
    assert not status.ready_for_export
    assert status.pending_promotions == []  # no drafts means nothing to flag


def test_check_finalization_drafts_only(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)

    # Write draft artifacts (not finals)
    fc = M.FindingsCollection.model_validate({"findings": []})
    s.write_json(audit_dir / "findings.draft.json",
                 fc.model_dump(by_alias=True, mode="json", exclude_none=True))
    rpt = _make_report()
    s.write_json(audit_dir / "report.draft.json",
                 rpt.model_dump(by_alias=True, mode="json", exclude_none=True))

    status = E.check_finalization(paths)
    assert status.findings_has_draft
    assert not status.findings_is_final
    assert status.report_has_draft
    assert not status.report_is_final
    assert not status.ready_for_export

    pending = status.pending_promotions
    assert any("findings" in p for p in pending)
    assert any("report" in p for p in pending)


def test_check_finalization_report_final_ready(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    _write_final_report(audit_dir)

    status = E.check_finalization(paths)
    assert status.report_is_final
    assert status.ready_for_export


def test_check_finalization_all_finals(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)

    _write_final_report(audit_dir)
    _write_final_proposal(audit_dir)
    fc = M.FindingsCollection.model_validate({"findings": []})
    s.write_json(audit_dir / "findings.final.json",
                 fc.model_dump(by_alias=True, mode="json", exclude_none=True))

    status = E.check_finalization(paths)
    assert status.findings_is_final
    assert status.report_is_final
    assert status.proposal_is_final
    assert status.ready_for_export
    assert status.pending_promotions == []


def test_pending_promotions_proposal_draft_without_final(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)

    _write_final_report(audit_dir)  # report is final
    prop = _make_proposal()
    s.write_json(audit_dir / "proposal.draft.json",
                 prop.model_dump(by_alias=True, mode="json", exclude_none=True))

    status = E.check_finalization(paths)
    assert status.ready_for_export  # report final present
    pending = status.pending_promotions
    assert any("proposal" in p for p in pending)


# ---------------------------------------------------------------------------
# build_export_bundle — basic correctness
# ---------------------------------------------------------------------------


def test_build_export_bundle_report_only(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    _write_final_report(audit_dir)
    target = tmp_path / "out"

    bundle = E.build_export_bundle(paths, target)

    assert bundle.audit_id == "aud_001"
    assert bundle.client_slug == "acme"
    assert bundle.target_dir == target
    assert (target / "report.final.json").is_file()
    assert (target / "export_manifest.json").is_file()
    names = [f.name for f in bundle.files]
    assert "report.final.json" in names
    assert "export_manifest.json" in names


def test_build_export_bundle_with_proposal(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    _write_final_report(audit_dir)
    _write_final_proposal(audit_dir)
    target = tmp_path / "out"

    bundle = E.build_export_bundle(paths, target, include_proposal=True)

    assert (target / "report.final.json").is_file()
    assert (target / "proposal.final.json").is_file()
    names = [f.name for f in bundle.files]
    assert "proposal.final.json" in names


def test_build_export_bundle_with_scorecard(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    _write_final_report(audit_dir)
    _write_scorecard(audit_dir)
    target = tmp_path / "out"

    bundle = E.build_export_bundle(paths, target, include_scorecard=True)

    assert (target / "scorecard.json").is_file()
    names = [f.name for f in bundle.files]
    assert "scorecard.json" in names


def test_build_export_bundle_with_markdown(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    _write_final_report(audit_dir)
    target = tmp_path / "out"

    bundle = E.build_export_bundle(paths, target, render_markdown=True)

    assert (target / "report.final.md").is_file()
    md = (target / "report.final.md").read_text(encoding="utf-8")
    assert "Executive Summary" in md
    names = [f.name for f in bundle.files]
    assert "report.final.md" in names


def test_build_export_bundle_markdown_includes_proposal(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    _write_final_report(audit_dir)
    _write_final_proposal(audit_dir)
    target = tmp_path / "out"

    bundle = E.build_export_bundle(
        paths, target, include_proposal=True, render_markdown=True
    )

    assert (target / "proposal.final.md").is_file()
    md = (target / "proposal.final.md").read_text(encoding="utf-8")
    assert "Scope Summary" in md


# ---------------------------------------------------------------------------
# build_export_bundle — manifest
# ---------------------------------------------------------------------------


def test_manifest_is_valid_json(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    _write_final_report(audit_dir)
    target = tmp_path / "out"

    E.build_export_bundle(paths, target)

    manifest = json.loads((target / "export_manifest.json").read_text(encoding="utf-8"))
    assert manifest["audit_id"] == "aud_001"
    assert manifest["client_slug"] == "acme"
    assert "exported_at" in manifest
    assert "files" in manifest
    assert isinstance(manifest["files"], list)


def test_manifest_lists_all_exported_files(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    _write_final_report(audit_dir)
    _write_final_proposal(audit_dir)
    _write_scorecard(audit_dir)
    target = tmp_path / "out"

    bundle = E.build_export_bundle(
        paths, target, include_proposal=True, include_scorecard=True, render_markdown=True
    )

    manifest = json.loads((target / "export_manifest.json").read_text(encoding="utf-8"))
    manifest_names = {f["name"] for f in manifest["files"]}
    bundle_names = {f.name for f in bundle.files}
    # manifest is written last, so it lists everything except itself
    # (or everything including itself if we want consistency)
    # Check that all non-manifest files appear in manifest
    for name in bundle_names:
        if name != "export_manifest.json":
            assert name in manifest_names, f"missing {name!r} in manifest"


def test_manifest_file_sizes_are_positive(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    _write_final_report(audit_dir)
    target = tmp_path / "out"

    E.build_export_bundle(paths, target)

    manifest = json.loads((target / "export_manifest.json").read_text(encoding="utf-8"))
    for f in manifest["files"]:
        assert f["size_bytes"] > 0


# ---------------------------------------------------------------------------
# build_export_bundle — schema validation at export time
# ---------------------------------------------------------------------------


def test_schema_validated_at_export_time(tmp_path: Path):
    """A corrupt report.final.json should raise ExportError, not silently copy."""
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)

    # Write a structurally invalid report (missing required fields)
    corrupt = {"audit_id": "aud_001", "template_version": "v1"}
    s.write_json(audit_dir / "report.final.json", corrupt)
    target = tmp_path / "out"

    with pytest.raises(E.ExportError, match="schema validation"):
        E.build_export_bundle(paths, target)


def test_valid_report_does_not_raise(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    _write_final_report(audit_dir)
    target = tmp_path / "out"

    bundle = E.build_export_bundle(paths, target)  # must not raise
    assert bundle is not None


# ---------------------------------------------------------------------------
# build_export_bundle — error handling
# ---------------------------------------------------------------------------


def test_raises_on_missing_report_final(tmp_path: Path):
    _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    target = tmp_path / "out"

    with pytest.raises(E.ExportError, match="report.final.json is missing"):
        E.build_export_bundle(paths, target)


def test_raises_on_missing_proposal_when_included(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    _write_final_report(audit_dir)
    target = tmp_path / "out"

    with pytest.raises(E.ExportError, match="proposal.final.json is missing"):
        E.build_export_bundle(paths, target, include_proposal=True)


def test_raises_on_missing_scorecard_when_included(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    _write_final_report(audit_dir)
    target = tmp_path / "out"

    with pytest.raises(E.ExportError, match="scorecard.json is missing"):
        E.build_export_bundle(paths, target, include_scorecard=True)


def test_raises_on_overwrite_without_flag(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    _write_final_report(audit_dir)
    target = tmp_path / "out"

    E.build_export_bundle(paths, target)  # first export

    with pytest.raises(E.ExportError, match="refusing to overwrite"):
        E.build_export_bundle(paths, target, overwrite=False)  # second without force


def test_overwrite_flag_allows_re_export(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    _write_final_report(audit_dir)
    target = tmp_path / "out"

    E.build_export_bundle(paths, target)
    bundle = E.build_export_bundle(paths, target, overwrite=True)  # must not raise
    assert bundle is not None


# ---------------------------------------------------------------------------
# build_export_bundle — originals untouched
# ---------------------------------------------------------------------------


def test_originals_are_not_modified(tmp_path: Path):
    """Export must copy to target_dir; original artifacts must be untouched."""
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    _write_final_report(audit_dir)
    target = tmp_path / "out"

    original_mtime = (audit_dir / "report.final.json").stat().st_mtime
    E.build_export_bundle(paths, target)
    assert (audit_dir / "report.final.json").stat().st_mtime == original_mtime


def test_original_report_still_exists_after_export(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    _write_final_report(audit_dir)
    target = tmp_path / "out"

    E.build_export_bundle(paths, target)
    assert (audit_dir / "report.final.json").is_file()


# ---------------------------------------------------------------------------
# CLI integration — export command
# ---------------------------------------------------------------------------


def _setup_for_export(data_root: Path, tmp_path: Path) -> Path:
    """Set up a minimal audit with report.final.json ready for export."""
    client_dir = data_root / "clients" / "acme"
    audit_dir = client_dir / "audits" / "aud_001"
    audit_dir.mkdir(parents=True, exist_ok=True)

    # Write just enough to satisfy the export gate
    _write_final_report(audit_dir)
    return audit_dir


def test_cli_export_blocks_without_report_final(tmp_path: Path):
    data_root = tmp_path / "data"
    audit_dir = data_root / "clients" / "acme" / "audits" / "aud_001"
    audit_dir.mkdir(parents=True, exist_ok=True)  # dir exists but no report.final

    r = runner.invoke(cli.app, ["export", *_common("acme", "aud_001", data_root)])
    assert r.exit_code != 0
    combined = r.stdout + r.stderr
    assert "blocked" in combined


def test_cli_export_produces_output_dir(tmp_path: Path):
    data_root = tmp_path / "data"
    audit_dir = _setup_for_export(data_root, tmp_path)
    out = tmp_path / "out"

    r = runner.invoke(
        cli.app,
        ["export", *_common("acme", "aud_001", data_root), "--output-dir", str(out)],
    )
    assert r.exit_code == 0, r.stdout + r.stderr
    assert (out / "acme" / "aud_001" / "report.final.json").is_file()
    assert (out / "acme" / "aud_001" / "export_manifest.json").is_file()


def test_cli_export_manifest_in_output(tmp_path: Path):
    data_root = tmp_path / "data"
    _setup_for_export(data_root, tmp_path)
    out = tmp_path / "out"

    runner.invoke(
        cli.app,
        ["export", *_common("acme", "aud_001", data_root), "--output-dir", str(out)],
    )
    manifest_path = out / "acme" / "aud_001" / "export_manifest.json"
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["audit_id"] == "aud_001"


def test_cli_export_include_scorecard(tmp_path: Path):
    data_root = tmp_path / "data"
    audit_dir = _setup_for_export(data_root, tmp_path)
    _write_scorecard(audit_dir)
    out = tmp_path / "out"

    r = runner.invoke(
        cli.app,
        [
            "export", *_common("acme", "aud_001", data_root),
            "--output-dir", str(out), "--include-scorecard",
        ],
    )
    assert r.exit_code == 0, r.stdout + r.stderr
    assert (out / "acme" / "aud_001" / "scorecard.json").is_file()


def test_cli_export_render_markdown(tmp_path: Path):
    data_root = tmp_path / "data"
    _setup_for_export(data_root, tmp_path)
    out = tmp_path / "out"

    r = runner.invoke(
        cli.app,
        [
            "export", *_common("acme", "aud_001", data_root),
            "--output-dir", str(out), "--render-markdown",
        ],
    )
    assert r.exit_code == 0, r.stdout + r.stderr
    md_path = out / "acme" / "aud_001" / "report.final.md"
    assert md_path.is_file()
    assert "Executive Summary" in md_path.read_text(encoding="utf-8")


def test_cli_export_dry_run_writes_nothing(tmp_path: Path):
    data_root = tmp_path / "data"
    _setup_for_export(data_root, tmp_path)
    out = tmp_path / "out"

    r = runner.invoke(
        cli.app,
        [
            "export", *_common("acme", "aud_001", data_root),
            "--output-dir", str(out), "--dry-run",
        ],
    )
    assert r.exit_code == 0, r.stdout
    assert "[dry-run]" in r.stdout
    # The output directory itself should not have been created (or be empty)
    assert not (out / "acme" / "aud_001" / "report.final.json").exists()


def test_cli_export_reports_each_file(tmp_path: Path):
    data_root = tmp_path / "data"
    _setup_for_export(data_root, tmp_path)
    out = tmp_path / "out"

    r = runner.invoke(
        cli.app,
        ["export", *_common("acme", "aud_001", data_root), "--output-dir", str(out)],
    )
    assert r.exit_code == 0
    assert "report.final.json" in r.stdout
    assert "manifest" in r.stdout
