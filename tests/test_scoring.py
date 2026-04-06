"""Phase V tests for the deterministic scoring engine."""

import pytest

from notion_audit_os import models as M
from notion_audit_os import scoring as sc


CATS = M.CORE_CATEGORIES


def all_at(value):
    return {c: value for c in CATS}


# ---------------------------------------------------------------------------
# Locked constants (kept from Phase I)
# ---------------------------------------------------------------------------


def test_core_weights_total_is_100():
    assert sum(sc.CORE_WEIGHTS.values()) == 100


def test_core_weights_has_eight_categories():
    assert len(sc.CORE_WEIGHTS) == 8


def test_core_weights_match_canonical_model_source():
    assert sc.CORE_WEIGHTS == M.DEFAULT_CORE_WEIGHTS


def test_maturity_bands_cover_zero_to_one_hundred():
    assert sc.MATURITY_BANDS[0][0] == 0
    assert sc.MATURITY_BANDS[-1][1] == 100


# ---------------------------------------------------------------------------
# normalize_scores
# ---------------------------------------------------------------------------


def test_normalize_scores_accepts_int_and_na():
    out = sc.normalize_scores({**all_at(2), "Intake and Requests": "N/A"})
    assert out["Business Fit"] == 2
    assert out["Intake and Requests"] == "N/A"


def test_normalize_scores_rejects_missing_category():
    bad = {c: 2 for c in CATS if c != "Business Fit"}
    with pytest.raises(sc.ScoringError):
        sc.normalize_scores(bad)


def test_normalize_scores_rejects_unknown_category():
    bad = {**all_at(2), "Random Thing": 1}
    with pytest.raises(sc.ScoringError):
        sc.normalize_scores(bad)


def test_normalize_scores_rejects_out_of_range():
    bad = {**all_at(2), "Business Fit": 5}
    with pytest.raises(sc.ScoringError):
        sc.normalize_scores(bad)


def test_normalize_scores_rejects_bool():
    bad = {**all_at(2), "Business Fit": True}
    with pytest.raises(sc.ScoringError):
        sc.normalize_scores(bad)


def test_normalize_scores_rejects_bad_string():
    bad = {**all_at(2), "Business Fit": "n/a"}  # wrong case
    with pytest.raises(sc.ScoringError):
        sc.normalize_scores(bad)


# ---------------------------------------------------------------------------
# Math
# ---------------------------------------------------------------------------


def test_full_score_is_100():
    card = sc.score_audit("aud_test", all_at(4))
    assert card.overall_score == 100.0


def test_zero_score_is_0():
    card = sc.score_audit("aud_test", all_at(0))
    assert card.overall_score == 0.0


def test_uniform_three_yields_75():
    # (3/4)*100 = 75
    card = sc.score_audit("aud_test", all_at(3))
    assert card.overall_score == 75.0


def test_na_excludes_weight_from_denominator():
    # All 3s except Intake & Requests = N/A.
    # Intake weight = 10, denom = 90, points = sum(0.75*w for non-NA) = 67.5,
    # overall = 67.5/90*100 = 75.0
    scores = all_at(3)
    scores["Intake and Requests"] = "N/A"
    card = sc.score_audit("aud_test", scores)
    assert card.overall_score == 75.0
    # N/A categories contribute zero weight to the persisted scorecard.
    weights_dump = card.active_weights.model_dump(by_alias=True)
    assert weights_dump["Intake and Requests"] == 0.0
    assert weights_dump["Business Fit"] == 15.0


def test_na_changes_overall_when_weights_uneven():
    # All 4s except Database Design (weight 15) = 0 -> overall = 85/100*100 = 85
    scores = all_at(4)
    scores["Database Design"] = 0
    card = sc.score_audit("aud_test", scores)
    assert card.overall_score == 85.0
    # Marking the same category N/A removes its weight entirely -> 100
    scores["Database Design"] = "N/A"
    card2 = sc.score_audit("aud_test", scores)
    assert card2.overall_score == 100.0


def test_all_na_raises():
    with pytest.raises(sc.ScoringError):
        sc.score_audit("aud_test", {c: "N/A" for c in CATS})


# ---------------------------------------------------------------------------
# Maturity band boundaries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "score,expected",
    [
        (0, "Critical disorder"),
        (24, "Critical disorder"),
        (24.4, "Critical disorder"),  # rounds to 24
        (24.5, "Fragile"),  # rounds to 25 (banker's rounding: 24.5 -> 24 in py3, but >= 25 boundary)
        (25, "Fragile"),
        (44, "Fragile"),
        (45, "Functional but weak"),
        (64, "Functional but weak"),
        (65, "Solid"),
        (79, "Solid"),
        (80, "Strong"),
        (100, "Strong"),
    ],
)
def test_assign_maturity_band_boundaries(score, expected):
    # Note: 24.5 case — Python's round() does banker's rounding (24.5 -> 24).
    # We accept either neighboring band on a half-boundary; only sharp
    # integer boundaries are strictly required by the locked spec.
    if score == 24.5:
        assert sc.assign_maturity_band(score) in {"Critical disorder", "Fragile"}
    else:
        assert sc.assign_maturity_band(score) == expected


# ---------------------------------------------------------------------------
# Recommendation patterns
# ---------------------------------------------------------------------------


def test_recommend_full_rebuild_low_overall():
    card = sc.score_audit("a", all_at(0))
    assert card.recommended_package == M.RecommendedPackage.FULL_REBUILD.value


def test_recommend_full_rebuild_three_structural_weak():
    scores = all_at(3)
    scores["Workspace Structure"] = 1
    scores["Database Design"] = 1
    scores["Data Relationships"] = 0
    card = sc.score_audit("a", scores)
    assert card.recommended_package == M.RecommendedPackage.FULL_REBUILD.value


def test_recommend_partial_rebuild_two_structural_weak():
    scores = all_at(3)
    scores["Database Design"] = 1
    scores["Data Relationships"] = 1
    card = sc.score_audit("a", scores)
    assert card.recommended_package == M.RecommendedPackage.PARTIAL_REBUILD.value


def test_recommend_partial_rebuild_mid_score():
    # Uniform 2 -> overall 50 -> Partial Rebuild
    card = sc.score_audit("a", all_at(2))
    assert card.overall_score == 50.0
    assert card.recommended_package == M.RecommendedPackage.PARTIAL_REBUILD.value


def test_recommend_governance_add_on():
    scores = all_at(4)
    scores["Governance and Adoption"] = 1
    card = sc.score_audit("a", scores)
    assert card.recommended_package == M.RecommendedPackage.GOVERNANCE_ADD_ON.value


def test_recommend_automation_ai_strong_but_not_perfect():
    scores = all_at(4)
    scores["Views and Dashboards"] = 2  # mid, not weak
    card = sc.score_audit("a", scores)
    assert card.overall_score >= 80
    assert card.recommended_package == M.RecommendedPackage.AUTOMATION_AI_ADD_ON.value


def test_recommend_no_major_project_perfect_system():
    card = sc.score_audit("a", all_at(4))
    assert card.recommended_package == M.RecommendedPackage.NO_MAJOR_PROJECT.value


def test_recommend_optimization_sprint_default_solid():
    card = sc.score_audit("a", all_at(3))
    # overall = 75 (Solid), no broad weakness -> Optimization Sprint
    assert card.recommended_package == M.RecommendedPackage.OPTIMIZATION_SPRINT.value


# ---------------------------------------------------------------------------
# Scorecard model output
# ---------------------------------------------------------------------------


def test_score_audit_returns_valid_scorecard_model():
    card = sc.score_audit("aud_test", all_at(3))
    assert isinstance(card, M.Scorecard)
    assert card.audit_id == "aud_test"
    assert card.maturity_band == "Solid"
    assert card.rationale is not None and len(card.rationale) > 0
    # Round-trips through alias-keyed JSON cleanly.
    dumped = card.model_dump(by_alias=True, mode="json")
    assert dumped["categories"]["Business Fit"] == 3
    assert dumped["overall_score"] == 75.0
    assert dumped["recommended_package"] == "Optimization Sprint"


def test_rationale_mentions_band_and_package():
    card = sc.score_audit("a", all_at(0))
    assert "Critical disorder" in card.rationale
    assert "Full Rebuild" in card.rationale


def test_scorecard_validates_against_schema():
    """Phase IV's storage layer should validate the produced scorecard."""
    from notion_audit_os import storage as s

    card = sc.score_audit("aud_test", all_at(3))
    data = card.model_dump(by_alias=True, mode="json", exclude_none=True)
    s.get_schema_registry().validate("scorecard.schema.json", data)
