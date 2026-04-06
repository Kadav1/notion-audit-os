# notion-audit-os

A **local-first**, CLI-driven audit engine for Notion workspaces.

It helps a solo operator parse intake, normalize notes, draft findings,
calculate a deterministic scorecard, generate audit reports and proposals,
and optionally publish approved outputs to Notion.

## Project purpose

`notion-audit-os` produces structured, reviewable audits of Notion
workspaces. It is not a SaaS platform, not a web app, and not a
multi-user backend. v1 is intentionally a single-operator tool.

## Local-first philosophy

- **Local files are the source of truth.** All audit artifacts live on
  disk first.
- **JSON Schemas are the external contract.** They define how files
  validate.
- **Pydantic models are the internal contract.** They define how Python
  validates in memory.
- **Scoring is deterministic.** No LLM decides the final score, weighted
  points, maturity band, or recommended package.
- **AI drafts language, not truth.** LLMs help phrase findings and
  reports; they never invent evidence or alter scores.
- **Human review gates are visible** at every important step.
- **Notion sync is optional and one-way** in v1.

## Phased build approach

- **Phase I — Skeleton (this phase).** Project layout, locked context
  doc, placeholder modules, minimal tests. The package imports cleanly
  and nothing fakes end-to-end behavior.
- **Phase II onward.** Real schemas, models, intake parsing, scoring,
  reporting, proposals, export, and the optional Notion sync, added
  module by module behind visible review gates.

## Canonical project context

`docs/LOCKED_CONTEXT.md` is the canonical source of project rules:
architecture layers, locked categories, locked weights, locked package
names, locked field names, locked CLI shape, and locked module
boundaries. Any coding assistant working on this repo must read it
first and must not silently override locked rules.

## JSON Schemas — the external contract

JSON Schemas are the **external contract** for every local artifact the
audit engine reads or writes. They live in `schemas/` and use JSON
Schema draft 2020-12. Highlights:

- `common.schema.json` — shared `$defs` (ids, timestamps, locked enums,
  category names, category score, recommended package, etc.)
- `client.schema.json`, `audit.schema.json`, `intake.schema.json`,
  `notes.schema.json`
- `finding.schema.json` and `findings.schema.json` (the **wrapper
  object** form: `{"findings": [...]}` — never a bare array)
- `scorecard.schema.json` (supports `0–4` and `"N/A"` per category)
- `report.schema.json`, `proposal.schema.json`, `notion_sync.schema.json`

Cross-file `$ref`s use relative file URIs (e.g.
`common.schema.json#/$defs/audit_id`). When wiring validation in
Phase III, the storage layer should register all schemas in a
`referencing.Registry` (or equivalent) so resolution works without
network access. Minimal example artifacts live under `data/examples/`.

The Pydantic models added in a later phase **must align with these
schemas**. If a model and a schema diverge, the schema is the contract
to fix against — not the other way around.

## Storage and validation layer

`src/notion_audit_os/storage.py` is the only module that touches the
filesystem for audit artifacts. It provides:

- safe JSON / Markdown / text read+write helpers (refuses to clobber
  unless `overwrite=True`)
- a `SchemaRegistry` that loads every `schemas/*.schema.json` into a
  `referencing.Registry`, so cross-file `$ref`s like
  `common.schema.json#/$defs/audit_id` resolve locally with no network
- `load_model(path, Model, schema_name=...)` and `dump_model(...)` so
  any artifact can be validated against **both** its JSON Schema **and**
  its Pydantic model in one call
- `AuditPaths` + `ensure_audit_scaffold()` for the standard
  `data/clients/<slug>/audits/<audit_id>/` layout, with explicit
  `findings.draft.json` / `findings.final.json` (and report/proposal
  draft/final) artifact names to support visible review gates

## Layout

```
docs/                       # LOCKED_CONTEXT.md and other docs
src/notion_audit_os/        # Python package (modules per locked boundaries)
schemas/                    # JSON Schemas (external contract)
data/examples/              # Minimal example artifacts
templates/                  # Report/proposal templates
prompts/                    # LLM prompts
tests/                      # Pytest suite
pyproject.toml              # Python 3.11+ project metadata
```

## Install (dev)

```
pip install -e ".[dev]"
```

## Run

```
audit
```

Phase I prints a version banner. Real commands arrive in later phases.
