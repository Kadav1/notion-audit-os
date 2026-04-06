"""Phase VII tests for intake parsing, notes normalization, and findings drafting."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from notion_audit_os import cli
from notion_audit_os import findings as F
from notion_audit_os import intake as I
from notion_audit_os import llm as L
from notion_audit_os import models as M
from notion_audit_os import notes as N
from notion_audit_os import storage as s


runner = CliRunner()


# ---------------------------------------------------------------------------
# Intake parsing
# ---------------------------------------------------------------------------


def test_parse_intake_full_doc():
    text = """# Client Name
Acme Co
# Team Size
12
# Notion Plan
plus
# Pain Points
- Tasks scattered across three databases
- Workflow status drifts
# Primary Use Cases
- Project tracking
- Knowledge base
# Current Notion Usage
We use Notion for everything except finance.
# Desired Outcomes
- One source of truth for tasks
# Workspace Owner
Jane
# Tools In Use
- Slack
- Linear
# AI In Use
- ChatGPT
"""
    intake = I.parse_intake_text(text, audit_id="aud_test")
    assert isinstance(intake, M.Intake)
    assert intake.normalized_payload.client_name == "Acme Co"
    assert intake.normalized_payload.team_size == 12
    assert intake.normalized_payload.notion_plan == "plus"
    assert "Tasks scattered across three databases" in intake.normalized_payload.pain_points
    assert intake.missing_fields == []


def test_parse_intake_partial_reports_gaps_without_inventing():
    text = """# Client Name
Acme Co
# Pain Points
- Workspace is messy
"""
    intake = I.parse_intake_text(text, audit_id="aud_test")
    # Present fields preserved.
    assert intake.normalized_payload.client_name == "Acme Co"
    assert intake.normalized_payload.pain_points == ["Workspace is messy"]
    # Missing fields reported, not invented.
    for field in [
        "team_size",
        "notion_plan",
        "primary_use_cases",
        "current_notion_usage",
        "desired_outcomes",
        "workspace_owner",
        "tools_in_use",
        "ai_in_use",
    ]:
        assert field in intake.missing_fields, f"expected gap for {field}"
    assert intake.normalized_payload.team_size is None


def test_parse_intake_unknown_notion_plan_defaults_and_flags():
    text = """# Client Name
X
# Notion Plan
ultra-mega
"""
    intake = I.parse_intake_text(text, audit_id="aud_test")
    assert intake.normalized_payload.notion_plan == "unknown"
    assert "notion_plan" in intake.missing_fields


def test_load_intake_file_json_round_trip(tmp_path: Path):
    intake = M.Intake(
        audit_id="aud_test",
        normalized_payload=M.IntakePayload(client_name="Acme"),
        parsed_at=datetime.now(timezone.utc),
    )
    p = tmp_path / "intake.json"
    s.write_json(p, intake.model_dump(by_alias=True, mode="json", exclude_none=True))
    loaded = I.load_intake_file(p, audit_id="aud_test")
    assert loaded.normalized_payload.client_name == "Acme"


# ---------------------------------------------------------------------------
# Notes parsing
# ---------------------------------------------------------------------------


SAMPLE_NOTES = """# Source Type
interview

# Pain Points
- Tasks DB has duplicate entries
- No clear workflow status

# Observations
- Three overlapping task databases exist with no canonical relation
- Permissions are inconsistent across the workspace
- Dashboards filter incorrectly because of stale views

# Uncertainties
- Unclear who owns the governance policy
"""


def test_parse_notes_basic_shape():
    notes = N.parse_notes_text(SAMPLE_NOTES, audit_id="aud_test")
    assert isinstance(notes, M.Notes)
    assert notes.source_type == "interview"
    summary = notes.normalized_summary
    assert len(summary.observations) == 3
    assert len(summary.pain_points) == 2
    assert len(summary.uncertainties) == 1
    assert notes.gaps == []


def test_parse_notes_candidate_categories_in_locked_set():
    notes = N.parse_notes_text(SAMPLE_NOTES, audit_id="aud_test")
    candidates = [c.value if hasattr(c, "value") else c for c in notes.normalized_summary.candidate_categories]
    assert candidates  # at least one
    for c in candidates:
        assert c in M.CORE_CATEGORIES, f"candidate {c!r} is not a locked category"


def test_parse_notes_weak_input_records_gaps():
    text = """# Source Type
interview

# Observations
"""
    notes = N.parse_notes_text(text, audit_id="aud_test")
    assert notes.normalized_summary.observations == []
    assert "no observations parsed from notes" in notes.gaps
    assert "no pain points parsed from notes" in notes.gaps


def test_parse_notes_unknown_source_type_defaults_and_flags():
    text = """# Source Type
seance

# Observations
- Something happened
"""
    notes = N.parse_notes_text(text, audit_id="aud_test")
    assert notes.source_type == "manual"
    assert any("source_type" in g for g in notes.gaps)


def test_parse_notes_summarizer_does_not_invent_items():
    """Stub summarizer rephrases each item but cannot add or drop items."""
    notes = N.parse_notes_text(
        SAMPLE_NOTES,
        audit_id="aud_test",
        summarizer=L.StubLLMAdapter(),
    )
    assert len(notes.normalized_summary.observations) == 3
    assert len(notes.normalized_summary.pain_points) == 2


# ---------------------------------------------------------------------------
# Findings drafting
# ---------------------------------------------------------------------------


def test_draft_findings_produces_valid_collection():
    notes = N.parse_notes_text(SAMPLE_NOTES, audit_id="aud_test")
    fc = F.draft_findings_from_notes(notes)
    assert isinstance(fc, M.FindingsCollection)
    assert len(fc.findings) >= 1
    # Round-trips through schema validation.
    data = fc.model_dump(by_alias=True, mode="json", exclude_none=True)
    s.get_schema_registry().validate("findings.schema.json", data)


def test_draft_findings_keep_observation_evidence_distinct():
    notes = N.parse_notes_text(SAMPLE_NOTES, audit_id="aud_test")
    fc = F.draft_findings_from_notes(notes)
    for f in fc.findings:
        # Required separation: observation, evidence, why_it_matters are all set.
        assert f.observation
        assert f.evidence
        assert f.why_it_matters
        # Observation and a single bare evidence item must not collapse.
        if len(f.evidence) == 1:
            assert f.evidence[0].strip() != f.observation.strip()


def test_draft_findings_attach_pain_points_as_evidence():
    notes = N.parse_notes_text(SAMPLE_NOTES, audit_id="aud_test")
    fc = F.draft_findings_from_notes(notes)
    db = next(
        (f for f in fc.findings if (f.category.value if hasattr(f.category, "value") else f.category) == "Database Design"),
        None,
    )
    assert db is not None
    # The pain point routed to Database Design should appear in evidence.
    assert any("duplicate" in e.lower() for e in db.evidence)


def test_draft_findings_id_is_stable_across_runs():
    notes = N.parse_notes_text(SAMPLE_NOTES, audit_id="aud_test")
    fc1 = F.draft_findings_from_notes(notes)
    fc2 = F.draft_findings_from_notes(notes)
    ids1 = sorted(f.finding_id for f in fc1.findings)
    ids2 = sorted(f.finding_id for f in fc2.findings)
    assert ids1 == ids2


def test_validate_finding_quality_rejects_empty_evidence():
    bad = M.Finding(
        finding_id="fnd_x",
        audit_id="aud_test",
        category=M.Category.DATABASE_DESIGN,
        title="x",
        observation="something",
        evidence=[],
        status=M.FindingStatus.DRAFT,
    )
    with pytest.raises(F.FindingQualityError):
        F.validate_finding_quality(bad)


def test_validate_finding_quality_rejects_flattened_observation_evidence():
    bad = M.Finding(
        finding_id="fnd_x",
        audit_id="aud_test",
        category=M.Category.DATABASE_DESIGN,
        title="x",
        observation="duplicate task DB",
        evidence=["duplicate task DB"],
        status=M.FindingStatus.DRAFT,
    )
    with pytest.raises(F.FindingQualityError):
        F.validate_finding_quality(bad)


def test_drafter_recommendation_is_optional():
    """Stub adapter returns empty -> recommendation stays None."""
    notes = N.parse_notes_text(SAMPLE_NOTES, audit_id="aud_test")
    fc = F.draft_findings_from_notes(notes, drafter=L.StubLLMAdapter())
    for f in fc.findings:
        # Empty drafter output collapses to None via the model.
        assert f.recommendation in (None, "")


def test_drafter_failure_does_not_break_drafting():
    class BoomAdapter:
        name = "boom"

        def summarize(self, text, *, max_chars=400):
            return text

        def draft_recommendation(self, *, category, observation, evidence):
            raise RuntimeError("model offline")

    notes = N.parse_notes_text(SAMPLE_NOTES, audit_id="aud_test")
    fc = F.draft_findings_from_notes(notes, drafter=BoomAdapter())
    # Drafting still produced findings; recommendations are just blank.
    assert len(fc.findings) >= 1


# ---------------------------------------------------------------------------
# LLM adapter
# ---------------------------------------------------------------------------


def test_stub_summarize_truncates_and_does_not_invent():
    stub = L.StubLLMAdapter()
    long = "word " * 500
    out = stub.summarize(long, max_chars=50)
    assert len(out) <= 50
    # Output is a strict prefix-ish of the cleaned input — no new tokens.
    assert all(part in long for part in out.replace("…", "").split())


def test_stub_draft_recommendation_returns_empty():
    stub = L.StubLLMAdapter()
    assert stub.draft_recommendation(category="Database Design", observation="x", evidence=["y"]) == ""


def test_stub_satisfies_protocol():
    assert isinstance(L.StubLLMAdapter(), L.LLMAdapter)


def test_get_default_adapter_returns_stub():
    assert isinstance(L.get_default_adapter(), L.StubLLMAdapter)


def test_set_default_adapter_round_trip():
    original = L.get_default_adapter()
    try:
        custom = L.StubLLMAdapter()
        L.set_default_adapter(custom)
        assert L.get_default_adapter() is custom
    finally:
        L.set_default_adapter(original)


# ---------------------------------------------------------------------------
# CLI integration with the new backend
# ---------------------------------------------------------------------------


def _common(client: str, audit: str, data_root: Path) -> list[str]:
    return ["--client", client, "--audit", audit, "--data-root", str(data_root)]


def test_cli_intake_accepts_markdown(tmp_path: Path):
    data_root = tmp_path / "data"
    # init
    r = runner.invoke(
        cli.app,
        ["init", *_common("acme", "aud_001", data_root), "--client-name", "Acme Co"],
    )
    assert r.exit_code == 0, r.stdout
    # markdown intake
    md = tmp_path / "intake.md"
    md.write_text(
        "# Client Name\nAcme Co\n# Team Size\n12\n# Pain Points\n- Tasks scattered\n",
        encoding="utf-8",
    )
    r = runner.invoke(
        cli.app,
        ["intake", *_common("acme", "aud_001", data_root), "--input", str(md)],
    )
    assert r.exit_code == 0, r.stdout + r.stderr
    saved = data_root / "clients" / "acme" / "audits" / "aud_001" / "intake.json"
    assert saved.is_file()
    body = s.read_json(saved)
    assert body["normalized_payload"]["client_name"] == "Acme Co"
    assert body["normalized_payload"]["team_size"] == 12
    assert "current_notion_usage" in body["missing_fields"]


def test_cli_normalize_notes_accepts_markdown(tmp_path: Path):
    data_root = tmp_path / "data"
    runner.invoke(
        cli.app,
        ["init", *_common("acme", "aud_001", data_root), "--client-name", "Acme Co"],
    )
    # need intake first to satisfy the gate
    md_intake = tmp_path / "intake.md"
    md_intake.write_text("# Client Name\nAcme Co\n", encoding="utf-8")
    runner.invoke(
        cli.app,
        ["intake", *_common("acme", "aud_001", data_root), "--input", str(md_intake)],
    )
    md_notes = tmp_path / "notes.md"
    md_notes.write_text(SAMPLE_NOTES, encoding="utf-8")
    r = runner.invoke(
        cli.app,
        [
            "normalize-notes",
            *_common("acme", "aud_001", data_root),
            "--input", str(md_notes),
            "--name", "session1",
        ],
    )
    assert r.exit_code == 0, r.stdout + r.stderr
    saved = data_root / "clients" / "acme" / "audits" / "aud_001" / "notes" / "session1.json"
    assert saved.is_file()
    body = s.read_json(saved)
    assert body["source_type"] == "interview"
    assert len(body["normalized_summary"]["observations"]) == 3


def test_cli_draft_findings_from_markdown_notes(tmp_path: Path):
    data_root = tmp_path / "data"
    runner.invoke(
        cli.app,
        ["init", *_common("acme", "aud_001", data_root), "--client-name", "Acme Co"],
    )
    md_intake = tmp_path / "intake.md"
    md_intake.write_text("# Client Name\nAcme Co\n", encoding="utf-8")
    runner.invoke(
        cli.app,
        ["intake", *_common("acme", "aud_001", data_root), "--input", str(md_intake)],
    )
    md_notes = tmp_path / "notes.md"
    md_notes.write_text(SAMPLE_NOTES, encoding="utf-8")
    runner.invoke(
        cli.app,
        [
            "normalize-notes",
            *_common("acme", "aud_001", data_root),
            "--input", str(md_notes),
            "--name", "session1",
        ],
    )
    # Draft findings directly from the markdown notes file.
    r = runner.invoke(
        cli.app,
        [
            "draft-findings",
            *_common("acme", "aud_001", data_root),
            "--input", str(md_notes),
        ],
    )
    assert r.exit_code == 0, r.stdout + r.stderr
    saved = data_root / "clients" / "acme" / "audits" / "aud_001" / "findings.draft.json"
    assert saved.is_file()
    body = s.read_json(saved)
    assert "findings" in body
    assert len(body["findings"]) >= 1
    for f in body["findings"]:
        assert f["category"] in M.CORE_CATEGORIES
        assert f["evidence"]
