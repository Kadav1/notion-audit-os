"""Deterministic scoring logic.

Phase I placeholder. No LLM may decide final score, weighted points,
maturity band, or package recommendation.
See docs/LOCKED_CONTEXT.md.
"""

# Locked Core Audit v1.1 weights (total = 100).
CORE_WEIGHTS = {
    "Business Fit": 15,
    "Workspace Structure": 12,
    "Database Design": 15,
    "Data Relationships": 12,
    "Workflow Clarity": 15,
    "Views and Dashboards": 10,
    "Intake and Requests": 10,
    "Governance and Adoption": 11,
}

# Locked maturity bands (inclusive lower bound, inclusive upper bound).
MATURITY_BANDS = (
    (0, 24, "Critical disorder"),
    (25, 44, "Fragile"),
    (45, 64, "Functional but weak"),
    (65, 79, "Solid"),
    (80, 100, "Strong"),
)
