"""Phase IV tests for the storage/validation layer."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from notion_audit_os import models as M
from notion_audit_os import storage as s


# ---------------------------------------------------------------------------
# JSON read/write
# ---------------------------------------------------------------------------


def test_json_round_trip(tmp_path: Path):
    target = tmp_path / "thing.json"
    s.write_json(target, {"a": 1, "b": [1, 2, 3]})
    assert s.read_json(target) == {"a": 1, "b": [1, 2, 3]}


def test_write_json_refuses_overwrite(tmp_path: Path):
    target = tmp_path / "thing.json"
    s.write_json(target, {"v": 1})
    with pytest.raises(s.ArtifactExistsError):
        s.write_json(target, {"v": 2})
    s.write_json(target, {"v": 2}, overwrite=True)
    assert s.read_json(target) == {"v": 2}


def test_read_missing_json(tmp_path: Path):
    with pytest.raises(s.ArtifactNotFoundError):
        s.read_json(tmp_path / "nope.json")


def test_read_malformed_json(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(s.MalformedJSONError):
        s.read_json(bad)


def test_text_round_trip(tmp_path: Path):
    target = tmp_path / "note.md"
    s.write_text(target, "# hello")
    assert s.read_text(target) == "# hello\n"


# ---------------------------------------------------------------------------
# Schema registry / cross-file $ref resolution
# ---------------------------------------------------------------------------


def test_schema_registry_loads_all_schemas():
    reg = s.SchemaRegistry()
    expected = {
        "common.schema.json",
        "client.schema.json",
        "audit.schema.json",
        "intake.schema.json",
        "notes.schema.json",
        "finding.schema.json",
        "findings.schema.json",
        "scorecard.schema.json",
        "report.schema.json",
        "proposal.schema.json",
        "notion_sync.schema.json",
    }
    assert expected.issubset(set(reg.names))


def test_cross_file_ref_resolution_findings_uses_finding_schema():
    """findings.schema.json -> finding.schema.json -> common.schema.json"""
    reg = s.SchemaRegistry()
    instance = {
        "findings": [
            {
                "finding_id": "f1",
                "audit_id": "a1",
                "category": "Database Design",
                "title": "t",
                "observation": "o",
                "status": "draft",
            }
        ]
    }
    reg.validate("findings.schema.json", instance)


def test_cross_file_ref_rejects_invalid_category():
    reg = s.SchemaRegistry()
    instance = {
        "findings": [
            {
                "finding_id": "f1",
                "audit_id": "a1",
                "category": "Not A Real Category",
                "title": "t",
                "observation": "o",
                "status": "draft",
            }
        ]
    }
    with pytest.raises(s.SchemaValidationError):
        reg.validate("findings.schema.json", instance)


def test_scorecard_na_validates_via_common_ref():
    reg = s.SchemaRegistry()
    instance = {
        "audit_id": "aud1",
        "categories": {
            "Business Fit": 3,
            "Workspace Structure": 2,
            "Database Design": 1,
            "Data Relationships": 2,
            "Workflow Clarity": 2,
            "Views and Dashboards": 3,
            "Intake and Requests": "N/A",
            "Governance and Adoption": 2,
        },
        "active_weights": {},
        "weighted_points": {},
        "overall_score": 53,
        "maturity_band": "Functional but weak",
        "recommended_package": "Partial Rebuild",
    }
    reg.validate("scorecard.schema.json", instance)


# ---------------------------------------------------------------------------
# Example artifact conformance: schema AND model
# ---------------------------------------------------------------------------


EXAMPLES = [
    ("data/examples/findings.example.json", "findings.schema.json", M.FindingsCollection),
    ("data/examples/scorecard.example.json", "scorecard.schema.json", M.Scorecard),
]


@pytest.mark.parametrize("rel,schema_name,model", EXAMPLES)
def test_example_validates_against_schema_and_model(rel, schema_name, model):
    path = s.project_root() / rel
    data = s.read_json(path)
    s.get_schema_registry().validate(schema_name, data)
    model.model_validate(data)


# ---------------------------------------------------------------------------
# Pydantic load/dump round-trip via storage helpers
# ---------------------------------------------------------------------------


def test_dump_and_load_findings_collection(tmp_path: Path):
    fc = M.FindingsCollection.model_validate(
        {
            "findings": [
                {
                    "finding_id": "f1",
                    "audit_id": "a1",
                    "category": "Database Design",
                    "title": "Tasks DB lacks SoT",
                    "observation": "Three overlapping task DBs.",
                    "status": "draft",
                }
            ]
        }
    )
    target = tmp_path / "findings.draft.json"
    s.dump_model(target, fc, schema_name="findings.schema.json")

    loaded = s.load_model(target, M.FindingsCollection, schema_name="findings.schema.json")
    assert len(loaded.findings) == 1
    assert loaded.findings[0].finding_id == "f1"


def test_load_model_surfaces_schema_error(tmp_path: Path):
    target = tmp_path / "bad_findings.json"
    s.write_json(
        target,
        {
            "findings": [
                {
                    "finding_id": "f1",
                    "audit_id": "a1",
                    "category": "Not Real",
                    "title": "t",
                    "observation": "o",
                    "status": "draft",
                }
            ]
        },
    )
    with pytest.raises(s.SchemaValidationError):
        s.load_model(target, M.FindingsCollection, schema_name="findings.schema.json")


# ---------------------------------------------------------------------------
# Path resolution and scaffolding
# ---------------------------------------------------------------------------


def test_audit_paths_layout(tmp_path: Path):
    paths = s.audit_paths("acme", "aud_001", data_root=tmp_path)
    assert paths.client_dir == tmp_path / "clients" / "acme"
    assert paths.audit_dir == tmp_path / "clients" / "acme" / "audits" / "aud_001"
    assert paths.findings_draft.name == "findings.draft.json"
    assert paths.findings_final.name == "findings.final.json"


def test_ensure_scaffold_is_idempotent(tmp_path: Path):
    paths = s.audit_paths("acme", "aud_001", data_root=tmp_path)
    s.ensure_audit_scaffold(paths)
    s.ensure_audit_scaffold(paths)  # second call must not raise
    assert paths.client_dir.is_dir()
    assert paths.audit_dir.is_dir()
    assert paths.notes_dir.is_dir()


def test_list_audit_artifacts(tmp_path: Path):
    paths = s.audit_paths("acme", "aud_001", data_root=tmp_path)
    s.ensure_audit_scaffold(paths)
    s.write_json(paths.findings_draft, {"findings": []})
    found = s.list_audit_artifacts(paths)
    assert "findings.draft" in found
    assert found["findings.draft"] == paths.findings_draft


def test_validate_files_bulk(tmp_path: Path):
    paths = s.audit_paths("acme", "aud_001", data_root=tmp_path)
    s.ensure_audit_scaffold(paths)
    good = paths.findings_draft
    s.write_json(good, {"findings": []})
    bad = paths.audit_dir / "bad.json"
    s.write_json(bad, {"findings": [{"finding_id": "x"}]})  # missing required fields
    results = s.validate_files(
        [
            ("findings.schema.json", good),
            ("findings.schema.json", bad),
        ]
    )
    assert results[good] is None
    assert results[bad] is not None and "validation failed" in results[bad]
