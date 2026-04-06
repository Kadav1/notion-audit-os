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

## Layout

```
docs/                       # LOCKED_CONTEXT.md and other docs
src/notion_audit_os/        # Python package (modules per locked boundaries)
schemas/                    # JSON Schemas (external contract)
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
