# Audit Report — {audit_id}

_Template: v1 | Generated: {generated_at}_

---

## Executive Summary

{sections.executive_summary}

---

## Maturity Assessment

{sections.maturity_summary}

---

## Key Findings

<!-- Top findings sorted by severity. Drawn from findings.final.json. -->
<!-- Each entry: finding_id, title, summary (why_it_matters or observation). -->

{sections.key_findings}

---

## Scorecard Summary

{sections.scorecard_summary}

---

## Roadmap

<!-- Phase 1 — Quick Wins: quick_win=True findings -->
<!-- Phase 2 — Core Fixes: critical/high severity findings -->
<!-- Phase 3 — Improvements: remaining findings -->

{sections.roadmap}

---

## Recommended Next Step

{sections.recommended_next_step}

---

<!-- Optional appendix section (omitted if empty) -->
<!-- ## Appendix -->
<!-- {sections.appendix} -->

---

_This is a draft for human review. Promote to report.final.json only after_
_the operator has reviewed and approved the content._
_The recommended_package above must match scorecard.json — it is never_
_overridden by the report layer._
