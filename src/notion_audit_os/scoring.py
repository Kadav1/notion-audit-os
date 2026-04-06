"""Deterministic scoring engine for Core Audit v1.1.

This module is **the** authority for:

* turning reviewed category scores into weighted points
* computing the overall score (with N/A handling)
* assigning a maturity band
* recommending a package
* writing a deterministic rationale string
* assembling a valid :class:`models.Scorecard`

Locked rules (see ``docs/LOCKED_CONTEXT.md``):

* No LLM is allowed in this module. All logic is rule-based.
* Category scores are integers ``0..4`` or the literal string ``"N/A"``.
* Weights, categories, bands, and package names are locked.
* Pattern (which categories are weak) matters more than the raw score
  alone for package recommendation.

The canonical weight source is :data:`models.DEFAULT_CORE_WEIGHTS`.
``CORE_WEIGHTS`` here is a re-export of that dict, kept for backward
compatibility with existing tests; do not edit either copy in isolation.
"""

from __future__ import annotations

from typing import Mapping, Union

from . import models as M

# ---------------------------------------------------------------------------
# Canonical constants (re-exported / derived)
# ---------------------------------------------------------------------------

#: Re-export of the canonical weights from the model layer.
CORE_WEIGHTS: dict[str, int] = dict(M.DEFAULT_CORE_WEIGHTS)

#: Locked maturity bands. Inclusive lower bound, inclusive upper bound.
MATURITY_BANDS: tuple[tuple[int, int, str], ...] = (
    (0, 24, "Critical disorder"),
    (25, 44, "Fragile"),
    (45, 64, "Functional but weak"),
    (65, 79, "Solid"),
    (80, 100, "Strong"),
)

#: Per-category score input type.
ScoreValue = Union[int, str]  # int 0..4 or the literal string "N/A"

#: Categories whose weakness implies the underlying structure is broken.
STRUCTURAL_CORE_CATEGORIES: tuple[str, ...] = (
    "Workspace Structure",
    "Database Design",
    "Data Relationships",
    "Workflow Clarity",
)

#: Categories that govern adoption / intake hygiene.
GOVERNANCE_SET_CATEGORIES: tuple[str, ...] = (
    "Governance and Adoption",
    "Intake and Requests",
)

#: A category is "weak" if its numeric score is <= this value.
WEAK_THRESHOLD = 1

#: A category is "strong" if its numeric score is >= this value.
STRONG_THRESHOLD = 3

NA = "N/A"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ScoringError(ValueError):
    """Raised when score inputs cannot be turned into a valid scorecard."""


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def normalize_scores(scores: Mapping[str, ScoreValue]) -> dict[str, ScoreValue]:
    """Validate and normalize a category-score mapping.

    Returns a fresh dict keyed by the 8 locked category names. Each value
    is either an ``int`` in ``0..4`` or the string ``"N/A"``. Raises
    :class:`ScoringError` if the input is missing categories, has unknown
    categories, or has out-of-range values.
    """
    expected = set(M.CORE_CATEGORIES)
    given = set(scores)
    missing = expected - given
    extra = given - expected
    if missing:
        raise ScoringError(f"missing category scores: {sorted(missing)}")
    if extra:
        raise ScoringError(f"unknown category names: {sorted(extra)}")

    out: dict[str, ScoreValue] = {}
    for category in M.CORE_CATEGORIES:
        raw = scores[category]
        if isinstance(raw, str):
            if raw != NA:
                raise ScoringError(
                    f"{category!r}: string scores must be {NA!r}, got {raw!r}"
                )
            out[category] = NA
        elif isinstance(raw, bool):
            # bool is a subclass of int in Python; reject it explicitly.
            raise ScoringError(f"{category!r}: boolean is not a valid score")
        elif isinstance(raw, int):
            if not 0 <= raw <= 4:
                raise ScoringError(
                    f"{category!r}: integer score must be in 0..4, got {raw}"
                )
            out[category] = raw
        else:
            raise ScoringError(
                f"{category!r}: score must be int 0..4 or {NA!r}, got {type(raw).__name__}"
            )
    return out


# ---------------------------------------------------------------------------
# Math: active weights, weighted points, overall score
# ---------------------------------------------------------------------------


def compute_active_weights(
    normalized_scores: Mapping[str, ScoreValue],
    weights: Mapping[str, int] | None = None,
) -> dict[str, float]:
    """Return the subset of weights for non-N/A categories.

    Categories scored ``"N/A"`` are excluded so they cannot drag the
    overall score down or up.
    """
    base = weights or CORE_WEIGHTS
    return {
        category: float(base[category])
        for category in M.CORE_CATEGORIES
        if normalized_scores[category] != NA
    }


def compute_weighted_points(
    normalized_scores: Mapping[str, ScoreValue],
    active_weights: Mapping[str, float],
) -> dict[str, float]:
    """For each active category: ``(score / 4) * weight``."""
    points: dict[str, float] = {}
    for category, weight in active_weights.items():
        score = normalized_scores[category]
        # active_weights only includes non-N/A categories, so score is int.
        assert isinstance(score, int)
        points[category] = (score / 4.0) * weight
    return points


def compute_overall_score(
    weighted_points: Mapping[str, float],
    active_weights: Mapping[str, float],
) -> float:
    """Locked formula: ``(sum(weighted_points) / sum(active_weights)) * 100``.

    Returns a float in ``[0, 100]``, rounded to one decimal place for
    display stability. Raises :class:`ScoringError` if every category is
    N/A (no active weights to score against).
    """
    total_weight = sum(active_weights.values())
    if total_weight <= 0:
        raise ScoringError("cannot score: every category is N/A")
    raw = (sum(weighted_points.values()) / total_weight) * 100.0
    return round(raw, 1)


def assign_maturity_band(overall_score: float) -> str:
    """Map an overall score to its locked maturity band string."""
    rounded = int(round(overall_score))
    for low, high, label in MATURITY_BANDS:
        if low <= rounded <= high:
            return label
    raise ScoringError(f"overall score {overall_score} outside 0..100")


# ---------------------------------------------------------------------------
# Pattern helpers
# ---------------------------------------------------------------------------


def _weak_categories(scores: Mapping[str, ScoreValue]) -> list[str]:
    return [
        c for c in M.CORE_CATEGORIES
        if isinstance(scores[c], int) and scores[c] <= WEAK_THRESHOLD
    ]


def _strong_categories(scores: Mapping[str, ScoreValue]) -> list[str]:
    return [
        c for c in M.CORE_CATEGORIES
        if isinstance(scores[c], int) and scores[c] >= STRONG_THRESHOLD
    ]


def _weak_in(scores: Mapping[str, ScoreValue], group: tuple[str, ...]) -> list[str]:
    return [c for c in group if isinstance(scores[c], int) and scores[c] <= WEAK_THRESHOLD]


def _strong_in(scores: Mapping[str, ScoreValue], group: tuple[str, ...]) -> list[str]:
    return [c for c in group if isinstance(scores[c], int) and scores[c] >= STRONG_THRESHOLD]


def _avg_in(scores: Mapping[str, ScoreValue], group: tuple[str, ...]) -> float:
    """Average numeric score across a group, ignoring N/A. Returns 0.0 if all N/A."""
    nums = [scores[c] for c in group if isinstance(scores[c], int)]
    if not nums:
        return 0.0
    return sum(nums) / len(nums)


# ---------------------------------------------------------------------------
# Recommendation ladder
# ---------------------------------------------------------------------------


def recommend_package(
    overall_score: float,
    normalized_scores: Mapping[str, ScoreValue],
) -> tuple[str, str]:
    """Pick a recommended package and return ``(package_name, reason)``.

    Deterministic ladder: the first matching rule wins. Each rule
    encodes one of the locked package patterns. Order matters:

    1. Full Rebuild
    2. Partial Rebuild
    3. Governance Add-on
    4. No immediate major project needed (essentially perfect)
    5. Automation / AI Add-on (strong but not perfect)
    6. Optimization Sprint (default)
    """
    weak_structural = _weak_in(normalized_scores, STRUCTURAL_CORE_CATEGORIES)
    weak_governance = _weak_in(normalized_scores, GOVERNANCE_SET_CATEGORIES)
    structural_avg = _avg_in(normalized_scores, STRUCTURAL_CORE_CATEGORIES)
    all_weak = _weak_categories(normalized_scores)
    all_strong = _strong_categories(normalized_scores)

    # Rule 1: Full Rebuild — broad structural collapse OR very low overall.
    if overall_score < 45 or len(weak_structural) >= 3:
        reason = (
            f"overall score {overall_score} is below 45"
            if overall_score < 45
            else f"{len(weak_structural)} of 4 structural-core categories are weak"
        )
        return M.RecommendedPackage.FULL_REBUILD.value, reason

    # Rule 2: Partial Rebuild — mid-range OR pair of structural weaknesses.
    if overall_score < 65 or (len(weak_structural) >= 2 and overall_score < 70):
        if len(weak_structural) >= 2:
            reason = (
                f"weak structural categories: {', '.join(weak_structural)}"
            )
        else:
            reason = f"overall score {overall_score} sits in the 45-64 functional-but-weak band"
        return M.RecommendedPackage.PARTIAL_REBUILD.value, reason

    # Rule 3: Governance Add-on — healthy structure but weak governance/intake.
    if (
        not weak_structural
        and structural_avg >= 2.5
        and weak_governance
        and overall_score >= 55
    ):
        reason = (
            f"structural core is healthy (avg {structural_avg:.1f}) "
            f"but weak in: {', '.join(weak_governance)}"
        )
        return M.RecommendedPackage.GOVERNANCE_ADD_ON.value, reason

    structural_all_strong = all(
        isinstance(normalized_scores[c], int) and normalized_scores[c] >= STRONG_THRESHOLD
        for c in STRUCTURAL_CORE_CATEGORIES
    )
    active_count = len(
        [c for c in M.CORE_CATEGORIES if isinstance(normalized_scores[c], int)]
    )

    # Rule 4: No immediate major project needed — rare; essentially perfect.
    if overall_score >= 90 and not all_weak and len(all_strong) == active_count:
        reason = f"overall {overall_score}; every active category is strong"
        return M.RecommendedPackage.NO_MAJOR_PROJECT.value, reason

    # Rule 5: Automation / AI Add-on — already-strong base, ready to extend.
    if overall_score >= 80 and not all_weak and structural_all_strong:
        reason = (
            f"strong base (overall {overall_score}, structural core all >= {STRONG_THRESHOLD}); "
            f"ready for automation/AI extensions"
        )
        return M.RecommendedPackage.AUTOMATION_AI_ADD_ON.value, reason

    # Rule 6: Optimization Sprint — default for solid systems with minor gaps.
    reason = (
        f"overall {overall_score} is solid; no broad structural weakness — "
        f"target focused improvements"
    )
    return M.RecommendedPackage.OPTIMIZATION_SPRINT.value, reason


# ---------------------------------------------------------------------------
# Rationale
# ---------------------------------------------------------------------------


def build_rationale(
    overall_score: float,
    band: str,
    package: str,
    package_reason: str,
    normalized_scores: Mapping[str, ScoreValue],
) -> str:
    """Build a short deterministic rationale string."""
    weak = _weak_categories(normalized_scores)
    strong = _strong_categories(normalized_scores)
    parts = [
        f"Overall score {overall_score} ({band}).",
        f"Recommended package: {package} — {package_reason}.",
    ]
    if weak:
        parts.append(f"Weak categories: {', '.join(weak)}.")
    if strong:
        parts.append(f"Strong categories: {', '.join(strong)}.")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Top-level: build a Scorecard
# ---------------------------------------------------------------------------


def score_audit(
    audit_id: str,
    scores: Mapping[str, ScoreValue],
    *,
    weights: Mapping[str, int] | None = None,
) -> M.Scorecard:
    """End-to-end deterministic scoring.

    Validates inputs, computes active weights and weighted points,
    assigns the maturity band, picks a recommended package, writes the
    rationale, and returns a fully validated :class:`models.Scorecard`.
    """
    normalized = normalize_scores(scores)
    active = compute_active_weights(normalized, weights)
    points = compute_weighted_points(normalized, active)
    overall = compute_overall_score(points, active)
    band = assign_maturity_band(overall)
    package, package_reason = recommend_package(overall, normalized)
    rationale = build_rationale(overall, band, package, package_reason, normalized)

    # Zero-fill N/A categories so the persisted scorecard honestly reports
    # which weights and points actually contributed to the overall score.
    active_full = {c: float(active.get(c, 0.0)) for c in M.CORE_CATEGORIES}
    points_full = {c: float(points.get(c, 0.0)) for c in M.CORE_CATEGORIES}

    # Pydantic models use human-readable category aliases; build via canonical keys.
    return M.Scorecard.model_validate(
        {
            "audit_id": audit_id,
            "categories": dict(normalized),
            "active_weights": active_full,
            "weighted_points": points_full,
            "overall_score": overall,
            "maturity_band": band,
            "recommended_package": package,
            "rationale": rationale,
        }
    )


__all__ = [
    "CORE_WEIGHTS",
    "MATURITY_BANDS",
    "STRUCTURAL_CORE_CATEGORIES",
    "GOVERNANCE_SET_CATEGORIES",
    "WEAK_THRESHOLD",
    "STRONG_THRESHOLD",
    "NA",
    "ScoreValue",
    "ScoringError",
    "normalize_scores",
    "compute_active_weights",
    "compute_weighted_points",
    "compute_overall_score",
    "assign_maturity_band",
    "recommend_package",
    "build_rationale",
    "score_audit",
]
