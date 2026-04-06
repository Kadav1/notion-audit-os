"""Phase III tests for the Pydantic model layer."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from notion_audit_os import models as m


def _ts() -> datetime:
    return datetime(2026, 4, 6, 10, 0, tzinfo=timezone.utc)


def test_locked_constants_present():
    assert len(m.CORE_CATEGORIES) == 8
    assert sum(m.DEFAULT_CORE_WEIGHTS.values()) == 100
    assert set(m.RECOMMENDED_PACKAGES) == {p.value for p in m.RecommendedPackage}


def test_category_scores_round_trip_with_na():
    payload = {
        "Business Fit": 3,
        "Workspace Structure": 2,
        "Database Design": 1,
        "Data Relationships": 2,
        "Workflow Clarity": 2,
        "Views and Dashboards": 3,
        "Intake and Requests": "N/A",
        "Governance and Adoption": 2,
    }
    cs = m.CategoryScores.model_validate(payload)
    assert cs.intake_and_requests == "N/A"
    dumped = cs.model_dump(by_alias=True, mode="json")
    assert dumped == payload


def test_category_score_rejects_out_of_range():
    payload = {k: 0 for k in m.CORE_CATEGORIES}
    payload["Business Fit"] = 5
    with pytest.raises(ValidationError):
        m.CategoryScores.model_validate(payload)


def test_findings_collection_is_wrapper_object():
    fc = m.FindingsCollection.model_validate(
        {
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
    )
    dumped = fc.model_dump(mode="json")
    assert "findings" in dumped and isinstance(dumped["findings"], list)


def test_findings_collection_unique_ids_enforced():
    with pytest.raises(ValidationError):
        m.FindingsCollection.model_validate(
            {
                "findings": [
                    {
                        "finding_id": "dup",
                        "audit_id": "a1",
                        "category": "Database Design",
                        "title": "t",
                        "observation": "o",
                        "status": "draft",
                    },
                    {
                        "finding_id": "dup",
                        "audit_id": "a1",
                        "category": "Database Design",
                        "title": "t2",
                        "observation": "o2",
                        "status": "draft",
                    },
                ]
            }
        )


def test_finding_rejects_blank_evidence():
    with pytest.raises(ValidationError):
        m.Finding.model_validate(
            {
                "finding_id": "f1",
                "audit_id": "a1",
                "category": "Database Design",
                "title": "t",
                "observation": "o",
                "status": "draft",
                "evidence": ["", "ok"],
            }
        )


def test_recommended_package_locked_values():
    expected = {
        "Optimization Sprint",
        "Partial Rebuild",
        "Full Rebuild",
        "Governance Add-on",
        "Automation / AI Add-on",
        "No immediate major project needed",
    }
    assert {p.value for p in m.RecommendedPackage} == expected


def test_slug_validator():
    assert m.validate_slug("acme-co") == "acme-co"
    with pytest.raises(ValueError):
        m.validate_slug("Acme Co")


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        m.Client.model_validate(
            {
                "client_id": "c1",
                "client_name": "Acme",
                "slug": "acme",
                "created_at": _ts().isoformat(),
                "unexpected": "nope",
            }
        )


def test_audit_context_holds_artifacts():
    ctx = m.AuditContext()
    assert ctx.client is None
    assert ctx.findings is None
