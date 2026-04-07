# notion-audit-os

A **local-first**, CLI-driven audit engine for Notion workspaces.

Parses intake, normalizes notes, drafts findings, computes a deterministic
scorecard, generates audit reports and engagement proposals, packages
approved outputs for delivery, and optionally publishes them to Notion.

## Project purpose

`notion-audit-os` produces structured, reviewable audits of Notion
workspaces. It is not a SaaS platform, not a web app, and not a
multi-user backend. v1 is intentionally a single-operator tool.

## Local-first philosophy

- **Local files are the source of truth.** All audit artifacts live on
  disk first. Notion is a delivery surface only.
- **JSON Schemas are the external contract.** They define how files
  validate, independently of Python.
- **Pydantic models are the internal contract.** They define how Python
  validates in memory.
- **Scoring is deterministic.** No LLM decides the final score, weighted
  points, maturity band, or recommended package.
- **AI drafts language, not truth.** LLMs help phrase findings and
  reports; they never invent evidence or alter scores.
- **Human review gates are visible** at every important step. The CLI
  never auto-promotes a draft to final.
- **Notion sync is optional and one-way.** Local canonical files are
  never modified by a sync operation.

## What is implemented (v1.0)

| Module | Responsibility |
|---|---|
| `storage.py` | All filesystem I/O; dual JSON Schema + Pydantic validation; `AuditPaths`; review-gate artifact naming |
| `models.py` | Pydantic v2 strict models for every artifact (Client, Audit, Intake, Notes, FindingsCollection, Scorecard, Report, Proposal, SyncLog, …) |
| `scoring.py` | Deterministic weighted scoring across 8 locked Core Audit v1.1 categories; maturity band + recommended package derivation |
| `intake.py` | Intake normalization and validation |
| `notes.py` | Session notes normalization |
| `findings.py` | Findings drafting and validation |
| `reporting.py` | Structured `Report` assembly from scorecard + findings; Markdown rendering |
| `proposal.py` | `Proposal` assembly with locked per-package deliverables and exclusions; Markdown rendering |
| `export.py` | Export bundle creation; finalization status checks; schema-validated artifact copying; `export_manifest.json` |
| `notion_sync.py` | One-way Notion publish; Markdown-to-block conversion; `SyncLog` persistence; no new runtime dependencies |
| `cli.py` | Typer CLI wiring all commands with review-gate enforcement |

226 tests, 0 failures.

## Canonical project context

`docs/LOCKED_CONTEXT.md` is the canonical source of project rules:
architecture layers, locked categories, locked weights, locked package
names, locked field names, locked CLI shape, and locked module
boundaries. Any coding assistant working on this repo must read it
first and must not silently override locked rules.

## JSON Schemas — the external contract

JSON Schemas live in `schemas/` and use JSON Schema draft 2020-12.

| Schema | Artifact |
|---|---|
| `common.schema.json` | Shared `$defs` (ids, timestamps, locked enums) |
| `client.schema.json`, `audit.schema.json` | Client and audit metadata |
| `intake.schema.json`, `notes.schema.json` | Intake and session notes |
| `finding.schema.json`, `findings.schema.json` | Individual finding and the `{"findings": [...]}` wrapper |
| `scorecard.schema.json` | Scores `0–4` or `"N/A"` per category; weighted totals |
| `report.schema.json`, `proposal.schema.json` | Report and proposal artifacts |
| `notion_sync.schema.json` | `notion_sync.json` log record (written after each sync attempt) |

Cross-file `$ref`s resolve via a local `referencing.Registry` — no network
access required. Example artifacts live under `data/examples/`.

## Storage and validation layer

`storage.py` is the only module that touches the filesystem for audit
artifacts. It provides:

- Safe JSON / Markdown / text read+write helpers (refuses to overwrite
  unless `overwrite=True`)
- A `SchemaRegistry` that loads every `schemas/*.schema.json` into a
  local `referencing.Registry`
- `load_model(path, Model, schema_name=...)` and `dump_model(...)` for
  dual JSON Schema + Pydantic validation in one call
- `AuditPaths` + `ensure_audit_scaffold()` for the standard
  `data/clients/<slug>/audits/<audit_id>/` layout, with explicit
  `*.draft.json` / `*.final.json` names to make review gates visible

## Layout

```
docs/                       # LOCKED_CONTEXT.md and other docs
src/notion_audit_os/        # Python package (modules per locked boundaries)
schemas/                    # JSON Schemas (external contract)
data/examples/              # Minimal example artifacts
templates/                  # Report/proposal templates
prompts/                    # LLM prompts
tests/                      # Pytest suite (226 tests)
pyproject.toml              # Python 3.11+ project metadata
```

## Install (dev)

```
pip install -e ".[dev]"
```

## Run

```
audit --help
```

## Workflow

```bash
# 1. Scaffold
audit init --client acme --audit aud_001 --client-name "Acme Co"

# 2. Load and validate intake
audit intake --client acme --audit aud_001 --input intake.json

# 3. Normalize session notes (repeat per session)
audit normalize-notes --client acme --audit aud_001 --input notes.json --name session1

# 4. Draft findings
audit draft-findings --client acme --audit aud_001 --input findings.json
# → operator reviews findings.draft.json, saves approved version as findings.final.json

# 5. Score
audit score --client acme --audit aud_001 --scores scores.json

# 6. Generate report and proposal
audit report    --client acme --audit aud_001
audit proposal  --client acme --audit aud_001
# → operator reviews *.draft.json files, saves approved versions as *.final.json

# 7. Check finalization status
audit review-status --client acme --audit aud_001

# 8. Export approved artifacts
audit export --client acme --audit aud_001 --include-proposal --render-markdown

# 9. Publish to Notion (optional)
audit sync-notion --client acme --audit aud_001 \
  --token $NOTION_API_TOKEN --parent-id $NOTION_PARENT_PAGE_ID
```

`audit review-status` shows what artifacts exist and what is blocking the
next stage. Every command that requires a final artifact refuses to run if
the review gate is unmet, with a message explaining what is missing.

The CLI never auto-promotes a draft. Promotion from `*.draft.json` to
`*.final.json` is a deliberate human action.

## Scoring

Eight locked Core Audit v1.1 categories, weights summing to 100:

| Category | Weight |
|---|---|
| Business Fit | 15 |
| Workspace Structure | 15 |
| Database Design | 15 |
| Data Relationships | 10 |
| Workflow Clarity | 15 |
| Views and Dashboards | 10 |
| Intake and Requests | 10 |
| Governance and Adoption | 10 |

Scores are `0–4` or `"N/A"`. The weighted total maps to a maturity band
(Critical / Developing / Functional / Advanced / Optimized) and a
recommended engagement package (one of six locked values).

## Notion sync

`audit sync-notion` publishes the approved `report.final.json` (and
optionally `proposal.final.json`) to a Notion page. It requires
`report.final.json` to exist. Credentials are read from `--token` /
`--parent-id` flags or `NOTION_API_TOKEN` / `NOTION_PARENT_PAGE_ID`
environment variables. Each sync creates a new page (create-only in v1).
The result is written to `notion_sync.json` in the audit directory.
