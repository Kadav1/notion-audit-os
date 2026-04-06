"""Phase VI tests for the Typer CLI orchestration layer."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from notion_audit_os import cli
from notion_audit_os import models as M
from notion_audit_os import storage as s


runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    return tmp_path / "data"


def _common(client: str, audit: str, data_root: Path) -> list[str]:
    return ["--client", client, "--audit", audit, "--data-root", str(data_root)]


def _init_audit(data_root: Path, client: str = "acme", audit: str = "aud_001") -> Path:
    """Run `audit init` and return the audit dir."""
    result = runner.invoke(
        cli.app,
        ["init", *_common(client, audit, data_root), "--client-name", "Acme Co"],
    )
    assert result.exit_code == 0, result.stdout
    return data_root / "clients" / client / "audits" / audit


def _make_intake_file(tmp_path: Path, audit_id: str = "aud_001") -> Path:
    intake = M.Intake(
        audit_id=audit_id,
        normalized_payload=M.IntakePayload(client_name="Acme Co"),
        parsed_at=datetime.now(timezone.utc),
    )
    p = tmp_path / "intake_input.json"
    s.write_json(p, intake.model_dump(by_alias=True, mode="json", exclude_none=True))
    return p


def _make_notes_file(tmp_path: Path, audit_id: str = "aud_001") -> Path:
    notes = M.Notes(
        audit_id=audit_id,
        source_type=M.SourceType.INTERVIEW,
        normalized_summary=M.NormalizedSummary(observations=["o1"]),
        generated_at=datetime.now(timezone.utc),
    )
    p = tmp_path / "notes_input.json"
    s.write_json(p, notes.model_dump(by_alias=True, mode="json", exclude_none=True))
    return p


def _make_findings_file(tmp_path: Path, audit_id: str = "aud_001") -> Path:
    fc = M.FindingsCollection.model_validate(
        {
            "findings": [
                {
                    "finding_id": "f1",
                    "audit_id": audit_id,
                    "category": "Database Design",
                    "title": "Tasks DB lacks SoT",
                    "observation": "Three overlapping task DBs",
                    "status": "draft",
                }
            ]
        }
    )
    p = tmp_path / "findings_input.json"
    s.write_json(p, fc.model_dump(by_alias=True, mode="json", exclude_none=True))
    return p


def _make_scores_file(tmp_path: Path) -> Path:
    p = tmp_path / "scores.json"
    s.write_json(
        p,
        {
            "Business Fit": 3,
            "Workspace Structure": 3,
            "Database Design": 2,
            "Data Relationships": 3,
            "Workflow Clarity": 3,
            "Views and Dashboards": 3,
            "Intake and Requests": 3,
            "Governance and Adoption": 3,
        },
    )
    return p


# ---------------------------------------------------------------------------
# Basic invocation
# ---------------------------------------------------------------------------


def test_cli_help_runs():
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    assert "Local-first CLI audit engine" in result.stdout


def test_all_locked_commands_registered():
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    for cmd in [
        "init",
        "intake",
        "normalize-notes",
        "draft-findings",
        "review-status",
        "score",
        "report",
        "proposal",
        "export",
        "sync-notion",
        "validate",
        "info",
    ]:
        assert cmd in result.stdout, f"missing command: {cmd}"


def test_version_command():
    result = runner.invoke(cli.app, ["version"])
    assert result.exit_code == 0
    assert "notion-audit-os" in result.stdout


# ---------------------------------------------------------------------------
# init / info / review-status
# ---------------------------------------------------------------------------


def test_init_creates_scaffold_and_metadata(data_root: Path):
    audit_dir = _init_audit(data_root)
    assert (audit_dir / "audit.json").is_file()
    assert (data_root / "clients" / "acme" / "client.json").is_file()
    assert (audit_dir / "notes").is_dir()


def test_init_refuses_overwrite_without_force(data_root: Path):
    _init_audit(data_root)
    result = runner.invoke(
        cli.app,
        ["init", *_common("acme", "aud_001", data_root), "--client-name", "Acme Co"],
    )
    assert result.exit_code != 0
    assert "refusing to overwrite" in (result.stdout + result.stderr)


def test_init_with_force_overwrites(data_root: Path):
    _init_audit(data_root)
    result = runner.invoke(
        cli.app,
        [
            "init",
            *_common("acme", "aud_001", data_root),
            "--client-name", "Acme Co",
            "--force",
        ],
    )
    assert result.exit_code == 0


def test_info_runs_after_init(data_root: Path):
    _init_audit(data_root)
    result = runner.invoke(cli.app, ["info", *_common("acme", "aud_001", data_root)])
    assert result.exit_code == 0
    assert "audit_type" in result.stdout
    assert "Core Audit v1.1" in result.stdout


def test_review_status_reports_next_step(data_root: Path):
    _init_audit(data_root)
    result = runner.invoke(
        cli.app, ["review-status", *_common("acme", "aud_001", data_root)]
    )
    assert result.exit_code == 0
    assert "audit intake" in result.stdout  # next step hint


# ---------------------------------------------------------------------------
# Review-gate blocking
# ---------------------------------------------------------------------------


def test_score_blocks_when_findings_final_missing(data_root: Path, tmp_path: Path):
    _init_audit(data_root)
    scores = _make_scores_file(tmp_path)
    result = runner.invoke(
        cli.app,
        ["score", *_common("acme", "aud_001", data_root), "--scores", str(scores)],
    )
    assert result.exit_code != 0
    combined = result.stdout + result.stderr
    assert "blocked" in combined
    assert "findings.final" in combined


def test_report_blocks_when_scorecard_missing(data_root: Path):
    _init_audit(data_root)
    result = runner.invoke(cli.app, ["report", *_common("acme", "aud_001", data_root)])
    assert result.exit_code != 0
    combined = result.stdout + result.stderr
    assert "blocked" in combined
    assert "scorecard" in combined


def test_export_blocks_when_report_final_missing(data_root: Path):
    _init_audit(data_root)
    result = runner.invoke(cli.app, ["export", *_common("acme", "aud_001", data_root)])
    assert result.exit_code != 0
    combined = result.stdout + result.stderr
    assert "blocked" in combined
    assert "report.final" in combined


def test_sync_notion_blocks_when_report_final_missing(data_root: Path):
    _init_audit(data_root)
    result = runner.invoke(
        cli.app, ["sync-notion", *_common("acme", "aud_001", data_root)]
    )
    assert result.exit_code != 0
    combined = result.stdout + result.stderr
    assert "blocked" in combined


# ---------------------------------------------------------------------------
# Happy path: intake -> notes -> draft-findings -> score -> report
# ---------------------------------------------------------------------------


def test_intake_normalizenotes_draftfindings_score_report_flow(
    data_root: Path, tmp_path: Path
):
    audit_dir = _init_audit(data_root)

    # Intake
    intake_in = _make_intake_file(tmp_path)
    r = runner.invoke(
        cli.app,
        ["intake", *_common("acme", "aud_001", data_root), "--input", str(intake_in)],
    )
    assert r.exit_code == 0, r.stdout
    assert (audit_dir / "intake.json").is_file()

    # Normalize notes
    notes_in = _make_notes_file(tmp_path)
    r = runner.invoke(
        cli.app,
        [
            "normalize-notes",
            *_common("acme", "aud_001", data_root),
            "--input", str(notes_in),
            "--name", "session1",
        ],
    )
    assert r.exit_code == 0, r.stdout
    assert (audit_dir / "notes" / "session1.json").is_file()

    # Draft findings
    findings_in = _make_findings_file(tmp_path)
    r = runner.invoke(
        cli.app,
        [
            "draft-findings",
            *_common("acme", "aud_001", data_root),
            "--input", str(findings_in),
        ],
    )
    assert r.exit_code == 0, r.stdout
    assert (audit_dir / "findings.draft.json").is_file()

    # Score must still block — findings.final.json doesn't exist yet.
    scores = _make_scores_file(tmp_path)
    r = runner.invoke(
        cli.app,
        ["score", *_common("acme", "aud_001", data_root), "--scores", str(scores)],
    )
    assert r.exit_code != 0

    # Operator promotes the reviewed draft to final (deliberate human action).
    (audit_dir / "findings.final.json").write_text(
        (audit_dir / "findings.draft.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    # Score should now succeed.
    r = runner.invoke(
        cli.app,
        ["score", *_common("acme", "aud_001", data_root), "--scores", str(scores)],
    )
    assert r.exit_code == 0, r.stdout
    assert (audit_dir / "scorecard.json").is_file()
    assert "recommended_package" in r.stdout

    # Verify the saved scorecard uses canonical aliases and locked package values.
    saved = s.read_json(audit_dir / "scorecard.json")
    assert "Business Fit" in saved["categories"]
    assert saved["recommended_package"] in {p.value for p in M.RecommendedPackage}

    # Report should succeed once scorecard exists.
    r = runner.invoke(cli.app, ["report", *_common("acme", "aud_001", data_root)])
    assert r.exit_code == 0, r.stdout
    assert (audit_dir / "report.draft.json").is_file()


# ---------------------------------------------------------------------------
# validate command
# ---------------------------------------------------------------------------


def test_validate_single_file_pass(tmp_path: Path):
    p = tmp_path / "fc.json"
    s.write_json(p, {"findings": []})
    result = runner.invoke(
        cli.app,
        ["validate", "--file", str(p), "--schema", "findings.schema.json"],
    )
    assert result.exit_code == 0
    assert "PASS" in result.stdout


def test_validate_single_file_fail(tmp_path: Path):
    p = tmp_path / "bad.json"
    s.write_json(p, {"findings": [{"finding_id": "x"}]})  # missing required fields
    result = runner.invoke(
        cli.app,
        ["validate", "--file", str(p), "--schema", "findings.schema.json"],
    )
    assert result.exit_code != 0
    combined = result.stdout + result.stderr
    assert "FAIL" in combined


def test_validate_audit_runs_on_initialized_audit(data_root: Path):
    _init_audit(data_root)
    result = runner.invoke(
        cli.app, ["validate", *_common("acme", "aud_001", data_root)]
    )
    assert result.exit_code == 0
    assert "PASS" in result.stdout
