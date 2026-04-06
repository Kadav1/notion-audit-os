LOCKED CONTEXT — notion-audit-os

Purpose

This file defines the locked architectural, naming, and workflow rules for the project "notion-audit-os".

Claude Code and any future coding assistant must treat this file as canonical project context unless explicitly told otherwise.

If a proposed change conflicts with this file, the assistant must:

1. point out the conflict clearly
2. not silently override the locked rule
3. ask for direction only if the conflict cannot be resolved safely

---

Project identity

Project name: "notion-audit-os"
Project type: local-first Python CLI application
Primary use case: audit Notion workspaces, generate structured findings, calculate scorecards, produce audit reports, and optionally sync approved outputs to Notion

This is not:

- a SaaS platform
- a web app
- a multi-user backend
- a general-purpose CRM
- a replacement for Notion
- a full autonomous agent system

Version 1 is a solo operator tool.

---

Core architecture

The system is built around 4 layers:

Layer A — Local Assets

Contains:

- audit standards
- schemas
- templates
- prompts
- package mapping
- scoring rules

Layer B — Local Audit Engine

Python app that handles:

- intake parsing
- note normalization
- finding drafting
- score calculation
- report generation
- export
- optional Notion sync

Layer C — Local LLM Layer

Local model provider, planned via LM Studio.

Use case:

- summarization
- finding drafting
- report drafting
- proposal drafting

Layer D — Delivery Layer

Where final outputs go:

- JSON
- Markdown
- PDF/DOCX later
- Notion optionally

---

Locked principles

These principles are mandatory.

1. Local files are the source of truth

All audit artifacts must be stored locally first.

Notion is a delivery surface, not the canonical data layer.

2. JSON Schemas are the external contract

JSON Schemas define the file-level storage/validation contract.

3. Pydantic models are the internal contract

Pydantic models define the in-memory Python validation layer.

4. Scoring logic must be deterministic

No LLM may decide the final score, weighted points, maturity band, or package recommendation without explicit human-controlled logic.

5. AI drafts language, not truth

LLMs may:

- summarize
- rewrite
- draft findings
- draft reports
- draft proposals

LLMs may not:

- invent evidence
- invent workspace facts
- silently alter scores
- silently alter final package recommendation

6. Human review gates must remain visible

The workflow must preserve explicit review points before:

- scoring
- final reporting
- proposal generation
- Notion sync

7. Notion sync is optional and one-way in v1

Allowed:

- local -> Notion publish

Not allowed in v1:

- Notion as canonical storage
- bidirectional sync
- live mutation loop where Notion changes local truth

---

Locked audit scope

Default audit type

Core Audit v1.1

Core categories

These 8 categories are locked for Core Audit v1.1:

1. Business Fit
2. Workspace Structure
3. Database Design
4. Data Relationships
5. Workflow Clarity
6. Views and Dashboards
7. Intake and Requests
8. Governance and Adoption

Do not rename these categories unless explicitly told to.

---

Locked scoring rules

Core weights

These weights are locked:

- Business Fit — 15
- Workspace Structure — 12
- Database Design — 15
- Data Relationships — 12
- Workflow Clarity — 15
- Views and Dashboards — 10
- Intake and Requests — 10
- Governance and Adoption — 11

Total = 100

Score values

Per category:

- 0
- 1
- 2
- 3
- 4
- or ""N/A"" where applicable

Maturity bands

Locked bands:

- 0–24 = Critical disorder
- 25–44 = Fragile
- 45–64 = Functional but weak
- 65–79 = Solid
- 80–100 = Strong

Score calculation rule

Weighted score must support N/A handling.

The system should calculate against active weights when one or more categories are marked N/A.

---

Locked recommendation packages

These package names are locked:

- Optimization Sprint
- Partial Rebuild
- Full Rebuild
- Governance Add-on
- Automation / AI Add-on
- No immediate major project needed

Locked naming rule

Use the field name:

"recommended_package"

Do not introduce or reintroduce:

- "recommendation_type" for package selection
- "package_recommendation"
- "proposal_type"

"recommendation_type" is allowed only for finding-level fix type labels such as:

- Quick Fix
- Structural Fix
- Rebuild Item
- Governance Fix
- Training Fix
- Future Enhancement

---

Locked findings storage decision

Findings must be stored as a wrapper object, not a bare JSON array.

Canonical shape

{
  "findings": [
    {
      "...": "..."
    }
  ]
}

Do not switch findings storage back to a raw array.

---

Locked file/data philosophy

Human-readable first

Prefer readable:

- JSON
- Markdown
- clear directory structure

Minimal abstraction

Prefer:

- explicit modules
- readable flow
- simple helper functions

Avoid:

- unnecessary metaprogramming
- excessive abstraction
- “framework-like” indirection for v1

Safe file handling

Do not silently overwrite important artifacts.
Preserve:

- draft outputs
- final outputs
- clear naming conventions

---

Locked CLI philosophy

The CLI is the main operator interface.

Command shape

Use:

audit <command> ...

Core v1 commands

These are the intended commands:

- audit init
- audit intake
- audit normalize-notes
- audit draft-findings
- audit review-status
- audit score
- audit report
- audit proposal
- audit export
- audit sync-notion
- audit validate
- audit info

CLI principles

- one clear responsibility per command
- explicit output
- clear failure messages
- visible review gates
- no fake “magic” end-to-end behavior in v1

---

Locked module boundaries

These module roles are canonical unless explicitly revised.

- "config.py" → configuration/environment
- "models.py" → Pydantic models/enums
- "storage.py" → local file IO and schema/model validation helpers
- "intake.py" → intake parsing/normalization
- "notes.py" → note normalization
- "findings.py" → finding drafting/validation helpers
- "scoring.py" → deterministic scoring logic
- "reporting.py" → report assembly
- "proposal.py" → proposal assembly
- "export.py" → export/finalization helpers
- "notion_sync.py" → optional one-way Notion publishing
- "llm.py" → LLM adapter boundary
- "cli.py" → Typer CLI entrypoint only, not business logic dump

---

Locked implementation boundaries

Do not build in v1

Unless explicitly requested, do not introduce:

- web UI
- background workers
- multi-user auth
- cloud deployment
- PostgreSQL requirement
- React frontend
- bidirectional Notion sync
- autonomous agents making final decisions
- browser automation for Notion crawling

Keep v1 small

The first version should be:

- local-first
- CLI-driven
- schema-backed
- human-reviewed
- deterministic where it matters

---

Locked LM Studio integration philosophy

LM Studio is the intended local LLM layer.

Use it for:

- summarizing notes
- drafting findings
- drafting reports
- drafting proposals

Do not use it for:

- final scoring logic
- final truth arbitration
- replacing review gates

Adapter rule

LLM integration must stay behind a clean boundary in "llm.py" so it can be stubbed, mocked, or replaced later.

---

Locked Notion sync philosophy

Notion sync is optional in v1.

Allowed scope

- create a page
- set a title
- push approved content blocks
- log sync results

Not allowed in v1

- bidirectional sync
- treating Notion as canonical
- silent mutation of local data from Notion state

---

Locked naming and consistency rules

Use these exact names

- "recommended_package"
- "findings"
- "scorecard"
- "report"
- "proposal"
- "audit_id"
- "client_id"

Avoid drift

Do not rename fields casually.
If a rename seems useful, flag it first and explain the impact.

---

Locked review gate philosophy

The workflow must preserve these review gates:

1. Intake reviewed before deeper processing
2. Notes reviewed before findings are treated as trustworthy
3. Findings reviewed before scoring
4. Score reviewed before final report
5. Report reviewed before export/sync

These gates should be visible in both code and CLI behavior.

---

If you are Claude Code

When working on this repo:

1. Inspect the current files first.
2. Explain your implementation plan before coding.
3. Do not silently change locked rules.
4. If you see a conflict between current code and this file, call it out explicitly.
5. Prefer small, clean, phase-appropriate changes.
6. After coding, summarize:
   - what changed
   - why
   - risks
   - next follow-up work
