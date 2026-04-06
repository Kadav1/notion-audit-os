"""Typer CLI orchestration for notion-audit-os.

This module is the operator interface. It owns:

* argument parsing and command registration
* review-gate enforcement (visible, refusing unsafe progression)
* readable status output
* delegation to ``storage``, ``scoring``, etc.

It does **not** own business logic. Scoring math, schema definitions,
and model shapes live in their canonical modules; the CLI only calls
into them. Many backend modules (intake parsing, notes normalization,
findings drafting, report prose, Notion sync) are intentionally still
stubs in v1, so several commands here accept pre-shaped JSON input
files and validate them rather than inventing parsers. That keeps the
CLI honest about what is and is not implemented.

Locked review-gate sequence (see ``docs/LOCKED_CONTEXT.md``):

1. Intake reviewed before deeper processing
2. Notes reviewed before findings are treated as trustworthy
3. Findings reviewed before scoring
4. Score reviewed before final report
5. Report reviewed before export/sync

Promotion of ``*.draft.json`` to ``*.final.json`` is a deliberate
human action — the CLI never auto-promotes a draft. ``review-status``
shows what is missing for the next stage.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer

from . import __version__
from . import findings as F
from . import intake as I
from . import models as M
from . import notes as N
from . import scoring as sc
from . import storage as s

app = typer.Typer(
    name="audit",
    help="Local-first CLI audit engine for Notion workspaces.",
    no_args_is_help=True,
    add_completion=False,
)

# ---------------------------------------------------------------------------
# Shared options / helpers
# ---------------------------------------------------------------------------


def _resolve_paths(
    client: str,
    audit: str,
    data_root: Optional[Path],
) -> s.AuditPaths:
    return s.audit_paths(client, audit, data_root=data_root)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _print_header(command: str, paths: s.AuditPaths) -> None:
    typer.echo(f"[audit {command}]  client={paths.client_slug}  audit={paths.audit_id}")


def _require_artifacts(items: list[tuple[str, Path]]) -> None:
    """Fail with a clear message if any required artifact is missing.

    ``items`` is a list of ``(label, path)`` pairs. The labels are shown
    in the error so the operator knows exactly what is blocking them.
    """
    missing = [(label, path) for label, path in items if not path.is_file()]
    if not missing:
        return
    typer.secho("blocked: required artifacts are missing", fg=typer.colors.RED, err=True)
    for label, path in missing:
        typer.secho(f"  - {label}: {path}", fg=typer.colors.RED, err=True)
    typer.secho(
        "next step: produce or review the missing artifact(s) before retrying.",
        fg=typer.colors.YELLOW,
        err=True,
    )
    raise typer.Exit(code=1)


def _write_or_block(path: Path, data: dict, *, force: bool, dry_run: bool, label: str) -> None:
    if dry_run:
        typer.echo(f"[dry-run] would write {label}: {path}")
        return
    try:
        s.write_json(path, data, overwrite=force)
    except s.ArtifactExistsError as e:
        typer.secho(f"refusing to overwrite {label}: {path}", fg=typer.colors.RED, err=True)
        typer.secho("pass --force to replace it.", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=1) from e
    typer.secho(f"  wrote {label}: {path}", fg=typer.colors.GREEN)


# Common Typer option declarations.
ClientOpt = typer.Option(..., "--client", "-c", help="Client slug, e.g. 'acme'.")
AuditOpt = typer.Option(..., "--audit", "-a", help="Audit id, e.g. 'aud_acme_2026_04'.")
DataRootOpt = typer.Option(
    None,
    "--data-root",
    help="Override the local data root (defaults to <project>/data).",
)
ForceOpt = typer.Option(False, "--force", help="Overwrite existing draft artifacts.")
DryRunOpt = typer.Option(False, "--dry-run", help="Print actions without writing files.")


# ---------------------------------------------------------------------------
# Review-gate model
# ---------------------------------------------------------------------------
#
# Single source of truth for "what does each stage need to exist?"
# Both `review-status` and individual commands consult this.


def _stage_state(paths: s.AuditPaths) -> dict[str, object]:
    """Inspect what exists on disk for an audit."""
    notes_files = (
        sorted(paths.notes_dir.glob("*.json")) if paths.notes_dir.is_dir() else []
    )
    return {
        "client": paths.client_file.is_file(),
        "audit": paths.audit_file.is_file(),
        "intake": paths.intake_file.is_file(),
        "notes_count": len(notes_files),
        "findings_draft": paths.findings_draft.is_file(),
        "findings_final": paths.findings_final.is_file(),
        "scorecard": paths.scorecard_file.is_file(),
        "report_draft": paths.report_draft.is_file(),
        "report_final": paths.report_final.is_file(),
        "proposal_draft": paths.proposal_draft.is_file(),
        "proposal_final": paths.proposal_final.is_file(),
    }


def _next_step_hint(state: dict[str, object]) -> str:
    if not state["audit"]:
        return "run `audit init` to scaffold the client/audit folders"
    if not state["intake"]:
        return "run `audit intake --input <path>` to load normalized intake"
    if not state["notes_count"]:
        return "run `audit normalize-notes --input <path>` (after intake review)"
    if not state["findings_draft"]:
        return "run `audit draft-findings --input <path>` (after notes review)"
    if not state["findings_final"]:
        return (
            "review findings.draft.json, then save the reviewed version as "
            "findings.final.json (the CLI never auto-promotes drafts)"
        )
    if not state["scorecard"]:
        return "run `audit score --scores <path>` to compute the scorecard"
    if not state["report_draft"]:
        return "run `audit report` to generate a draft report"
    if not state["report_final"]:
        return (
            "review report.draft.json, then save the reviewed version as "
            "report.final.json"
        )
    return "run `audit export` (and optionally `audit sync-notion`)"


# ---------------------------------------------------------------------------
# audit init
# ---------------------------------------------------------------------------


@app.command()
def init(
    client: str = ClientOpt,
    audit: str = AuditOpt,
    client_name: str = typer.Option(..., "--client-name", help="Human-readable client name."),
    audit_type: str = typer.Option(
        M.AuditType.CORE_V1_1.value,
        "--audit-type",
        help=f"Audit type. Default: {M.AuditType.CORE_V1_1.value}.",
    ),
    data_root: Optional[Path] = DataRootOpt,
    force: bool = ForceOpt,
    dry_run: bool = DryRunOpt,
):
    """Initialize a client/audit working directory and metadata files."""
    paths = _resolve_paths(client, audit, data_root)
    _print_header("init", paths)

    if not dry_run:
        s.ensure_audit_scaffold(paths)
        typer.echo(f"  scaffolded: {paths.audit_dir}")
    else:
        typer.echo(f"[dry-run] would scaffold {paths.audit_dir}")

    client_obj = M.Client.model_validate(
        {
            "client_id": client,
            "client_name": client_name,
            "slug": client,
            "created_at": _now(),
        }
    )
    audit_obj = M.Audit.model_validate(
        {
            "audit_id": audit,
            "client_id": client,
            "audit_type": audit_type,
            "status": M.AuditStatus.DRAFT.value,
            "created_at": _now(),
        }
    )

    _write_or_block(
        paths.client_file,
        client_obj.model_dump(by_alias=True, mode="json", exclude_none=True),
        force=force,
        dry_run=dry_run,
        label="client",
    )
    _write_or_block(
        paths.audit_file,
        audit_obj.model_dump(by_alias=True, mode="json", exclude_none=True),
        force=force,
        dry_run=dry_run,
        label="audit",
    )
    typer.echo("next: run `audit intake --input <path>` once intake is collected.")


# ---------------------------------------------------------------------------
# audit intake
# ---------------------------------------------------------------------------


@app.command()
def intake(
    client: str = ClientOpt,
    audit: str = AuditOpt,
    input: Path = typer.Option(..., "--input", "-i", help="Path to a JSON file in Intake shape."),
    data_root: Optional[Path] = DataRootOpt,
    force: bool = ForceOpt,
    dry_run: bool = DryRunOpt,
):
    """Load and normalize an intake source.

    Accepts either a pre-shaped JSON file (:class:`models.Intake`) or a
    Markdown/text intake document. The format is detected by file
    extension; the parser in ``intake.py`` handles ``.md``/``.txt``.
    """
    paths = _resolve_paths(client, audit, data_root)
    _print_header("intake", paths)
    _require_artifacts([("audit", paths.audit_file)])

    intake_obj = I.load_intake_file(input, audit_id=audit)
    data = intake_obj.model_dump(by_alias=True, mode="json", exclude_none=True)
    s.get_schema_registry().validate("intake.schema.json", data)

    if intake_obj.missing_fields:
        typer.secho(
            f"  intake reports missing fields: {', '.join(intake_obj.missing_fields)}",
            fg=typer.colors.YELLOW,
        )

    _write_or_block(
        paths.intake_file,
        intake_obj.model_dump(by_alias=True, mode="json", exclude_none=True),
        force=force,
        dry_run=dry_run,
        label="intake",
    )
    typer.echo(
        "next: review intake.json with the operator, then run `audit normalize-notes`."
    )


# ---------------------------------------------------------------------------
# audit normalize-notes
# ---------------------------------------------------------------------------


@app.command("normalize-notes")
def normalize_notes(
    client: str = ClientOpt,
    audit: str = AuditOpt,
    input: Path = typer.Option(..., "--input", "-i", help="Path to a JSON file in Notes shape."),
    name: str = typer.Option(
        "session", "--name", help="Short name for the notes file (no extension)."
    ),
    data_root: Optional[Path] = DataRootOpt,
    force: bool = ForceOpt,
    dry_run: bool = DryRunOpt,
):
    """Normalize and store a notes artifact for an audit.

    Accepts either a pre-shaped JSON file (:class:`models.Notes`) or a
    raw Markdown/text notes document. The format is detected by file
    extension; the parser in ``notes.py`` handles ``.md``/``.txt``.
    """
    paths = _resolve_paths(client, audit, data_root)
    _print_header("normalize-notes", paths)
    _require_artifacts([("intake", paths.intake_file)])

    notes_obj = N.load_notes_file(input, audit_id=audit)
    data = notes_obj.model_dump(by_alias=True, mode="json", exclude_none=True)
    s.get_schema_registry().validate("notes.schema.json", data)

    typer.echo(f"  source_type: {notes_obj.source_type}")
    summary = notes_obj.normalized_summary
    typer.echo(
        f"  observations={len(summary.observations)} "
        f"pain_points={len(summary.pain_points)} "
        f"uncertainties={len(summary.uncertainties)} "
        f"gaps={len(notes_obj.gaps)}"
    )

    if not dry_run:
        paths.notes_dir.mkdir(parents=True, exist_ok=True)
    _write_or_block(
        paths.notes_file(name),
        notes_obj.model_dump(by_alias=True, mode="json", exclude_none=True),
        force=force,
        dry_run=dry_run,
        label=f"notes/{name}",
    )
    typer.echo("next: review notes, then run `audit draft-findings`.")


# ---------------------------------------------------------------------------
# audit draft-findings
# ---------------------------------------------------------------------------


@app.command("draft-findings")
def draft_findings(
    client: str = ClientOpt,
    audit: str = AuditOpt,
    input: Path = typer.Option(
        ..., "--input", "-i", help="Path to a JSON file in FindingsCollection shape."
    ),
    data_root: Optional[Path] = DataRootOpt,
    force: bool = ForceOpt,
    dry_run: bool = DryRunOpt,
):
    """Draft and write a findings collection.

    Accepts either a pre-shaped JSON file (:class:`models.FindingsCollection`)
    or a raw notes source (``.md``/``.txt``); in the latter case the
    deterministic drafter in ``findings.py`` builds findings from the
    parsed notes, keeping observation/evidence/why_it_matters/recommendation
    structurally distinct.
    """
    paths = _resolve_paths(client, audit, data_root)
    _print_header("draft-findings", paths)
    if not paths.notes_dir.is_dir() or not list(paths.notes_dir.glob("*.json")):
        _require_artifacts([("notes/* (any normalized notes file)", paths.notes_dir)])

    fc = F.load_findings_input(input, audit_id=audit)
    data = fc.model_dump(by_alias=True, mode="json", exclude_none=True)
    s.get_schema_registry().validate("findings.schema.json", data)

    by_cat: dict[str, int] = {}
    for f in fc.findings:
        key = f.category if isinstance(f.category, str) else f.category.value
        by_cat[key] = by_cat.get(key, 0) + 1
    typer.echo(f"  findings: {len(fc.findings)} total")
    for cat, n in sorted(by_cat.items()):
        typer.echo(f"    {cat}: {n}")

    _write_or_block(
        paths.findings_draft,
        fc.model_dump(by_alias=True, mode="json", exclude_none=True),
        force=force,
        dry_run=dry_run,
        label="findings.draft",
    )
    typer.secho(
        "review reminder: findings must be reviewed before scoring. "
        "Save the reviewed version as findings.final.json before running `audit score`.",
        fg=typer.colors.YELLOW,
    )


# ---------------------------------------------------------------------------
# audit review-status
# ---------------------------------------------------------------------------


@app.command("review-status")
def review_status(
    client: str = ClientOpt,
    audit: str = AuditOpt,
    data_root: Optional[Path] = DataRootOpt,
):
    """Show audit stage, existing artifacts, and the next required step."""
    paths = _resolve_paths(client, audit, data_root)
    _print_header("review-status", paths)
    state = _stage_state(paths)

    def mark(ok: bool) -> str:
        return "[x]" if ok else "[ ]"

    typer.echo("artifacts:")
    typer.echo(f"  {mark(bool(state['client']))} client.json")
    typer.echo(f"  {mark(bool(state['audit']))} audit.json")
    typer.echo(f"  {mark(bool(state['intake']))} intake.json")
    typer.echo(f"  {mark(bool(state['notes_count']))} notes/* ({state['notes_count']} file(s))")
    typer.echo(f"  {mark(bool(state['findings_draft']))} findings.draft.json")
    typer.echo(f"  {mark(bool(state['findings_final']))} findings.final.json  <- review gate")
    typer.echo(f"  {mark(bool(state['scorecard']))} scorecard.json")
    typer.echo(f"  {mark(bool(state['report_draft']))} report.draft.json")
    typer.echo(f"  {mark(bool(state['report_final']))} report.final.json  <- review gate")
    typer.echo(f"  {mark(bool(state['proposal_draft']))} proposal.draft.json")
    typer.echo(f"  {mark(bool(state['proposal_final']))} proposal.final.json")

    typer.secho(f"next: {_next_step_hint(state)}", fg=typer.colors.CYAN)


# ---------------------------------------------------------------------------
# audit score
# ---------------------------------------------------------------------------


@app.command()
def score(
    client: str = ClientOpt,
    audit: str = AuditOpt,
    scores_file: Path = typer.Option(
        ...,
        "--scores",
        "-s",
        help="JSON file with reviewed category scores keyed by canonical category names.",
    ),
    data_root: Optional[Path] = DataRootOpt,
    force: bool = ForceOpt,
    dry_run: bool = DryRunOpt,
):
    """Calculate the deterministic scorecard for a reviewed audit.

    Requires ``findings.final.json`` to exist (the findings review gate).
    The CLI does not invent scores: ``--scores`` must point to a JSON
    file with the 8 reviewed category scores.
    """
    paths = _resolve_paths(client, audit, data_root)
    _print_header("score", paths)
    _require_artifacts(
        [
            ("audit", paths.audit_file),
            ("findings.final (reviewed findings)", paths.findings_final),
        ]
    )

    raw_scores = s.read_json(scores_file)
    if not isinstance(raw_scores, dict):
        typer.secho("scores file must be a JSON object keyed by category name.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    try:
        card = sc.score_audit(audit, raw_scores)
    except sc.ScoringError as e:
        typer.secho(f"scoring failed: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from e

    typer.echo(f"  overall_score: {card.overall_score}")
    typer.echo(f"  maturity_band: {card.maturity_band}")
    typer.echo(f"  recommended_package: {card.recommended_package}")
    typer.echo("  (active_weights reflect post-N/A effective weights)")

    _write_or_block(
        paths.scorecard_file,
        card.model_dump(by_alias=True, mode="json", exclude_none=True),
        force=force,
        dry_run=dry_run,
        label="scorecard",
    )
    typer.echo("next: review the scorecard, then run `audit report`.")


# ---------------------------------------------------------------------------
# audit report
# ---------------------------------------------------------------------------


@app.command()
def report(
    client: str = ClientOpt,
    audit: str = AuditOpt,
    template_version: str = typer.Option("v1", "--template", help="Report template version."),
    data_root: Optional[Path] = DataRootOpt,
    force: bool = ForceOpt,
    dry_run: bool = DryRunOpt,
):
    """Generate a draft report skeleton from the scorecard.

    v1 produces a minimal structured Report. Rich prose belongs in
    ``reporting.py`` in a later phase. Output is always a draft;
    promotion to ``report.final.json`` is a deliberate human action.
    """
    paths = _resolve_paths(client, audit, data_root)
    _print_header("report", paths)
    _require_artifacts(
        [
            ("scorecard", paths.scorecard_file),
            ("findings.final", paths.findings_final),
        ]
    )

    card = s.load_model(
        paths.scorecard_file, M.Scorecard, schema_name="scorecard.schema.json"
    )
    fc = s.load_model(
        paths.findings_final, M.FindingsCollection, schema_name="findings.schema.json"
    )

    key = [
        M.KeyFindingRef(finding_id=f.finding_id, title=f.title)
        for f in fc.findings[:5]
    ]
    sections = M.ReportSections(
        executive_summary=(
            f"Audit {audit} for client {client}: overall score {card.overall_score} "
            f"({card.maturity_band})."
        ),
        maturity_summary=f"Maturity band: {card.maturity_band}.",
        key_findings=key,
        scorecard_summary=card.rationale or "(no rationale)",
        roadmap=[],
        recommended_next_step=f"Recommended package: {card.recommended_package}.",
        appendix=None,
    )
    report_obj = M.Report(
        audit_id=audit,
        template_version=template_version,
        output_format=M.OutputFormat.JSON,
        path=str(paths.report_draft),
        generated_at=datetime.now(timezone.utc),
        sections=sections,
    )
    _write_or_block(
        paths.report_draft,
        report_obj.model_dump(by_alias=True, mode="json", exclude_none=True),
        force=force,
        dry_run=dry_run,
        label="report.draft",
    )
    typer.secho(
        "review reminder: this is a structural draft. Edit it, then save the "
        "reviewed version as report.final.json before `audit export`.",
        fg=typer.colors.YELLOW,
    )


# ---------------------------------------------------------------------------
# audit proposal
# ---------------------------------------------------------------------------


@app.command()
def proposal(
    client: str = ClientOpt,
    audit: str = AuditOpt,
    data_root: Optional[Path] = DataRootOpt,
    force: bool = ForceOpt,
    dry_run: bool = DryRunOpt,
):
    """Generate a draft proposal aligned with the recommended package."""
    paths = _resolve_paths(client, audit, data_root)
    _print_header("proposal", paths)
    _require_artifacts(
        [
            ("scorecard", paths.scorecard_file),
            ("findings.final", paths.findings_final),
        ]
    )

    card = s.load_model(
        paths.scorecard_file, M.Scorecard, schema_name="scorecard.schema.json"
    )
    proposal_obj = M.Proposal(
        audit_id=audit,
        recommended_package=M.RecommendedPackage(card.recommended_package),
        scope_summary=(
            f"Engagement aligned with the recommended package: {card.recommended_package}."
        ),
        deliverables=[],
        exclusions=[],
        generated_at=datetime.now(timezone.utc),
    )
    _write_or_block(
        paths.proposal_draft,
        proposal_obj.model_dump(by_alias=True, mode="json", exclude_none=True),
        force=force,
        dry_run=dry_run,
        label="proposal.draft",
    )
    typer.echo(
        "next: edit deliverables/exclusions in the draft, then save as proposal.final.json."
    )


# ---------------------------------------------------------------------------
# audit export
# ---------------------------------------------------------------------------


@app.command()
def export(
    client: str = ClientOpt,
    audit: str = AuditOpt,
    output_dir: Optional[Path] = typer.Option(
        None, "--output-dir", help="Where to write exported artifacts (default: <repo>/output)."
    ),
    include_proposal: bool = typer.Option(
        False, "--include-proposal", help="Also export proposal.final.json if present."
    ),
    data_root: Optional[Path] = DataRootOpt,
    force: bool = ForceOpt,
    dry_run: bool = DryRunOpt,
):
    """Export approved final artifacts.

    Refuses to run unless ``report.final.json`` exists. v1 copies the
    JSON artifacts as-is; rendering to PDF/DOCX belongs in
    ``export.py`` in a later phase.
    """
    paths = _resolve_paths(client, audit, data_root)
    _print_header("export", paths)
    _require_artifacts([("report.final", paths.report_final)])

    out_root = output_dir or (s.project_root() / "output")
    target_dir = out_root / paths.client_slug / paths.audit_id

    to_copy: list[tuple[str, Path]] = [("report.final.json", paths.report_final)]
    if include_proposal:
        if not paths.proposal_final.is_file():
            typer.secho(
                "blocked: --include-proposal requested but proposal.final.json is missing.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1)
        to_copy.append(("proposal.final.json", paths.proposal_final))

    for name, src in to_copy:
        dest = target_dir / name
        if dry_run:
            typer.echo(f"[dry-run] would copy {src} -> {dest}")
            continue
        data = s.read_json(src)
        s.write_json(dest, data, overwrite=force)
        typer.secho(f"  exported: {dest}", fg=typer.colors.GREEN)


# ---------------------------------------------------------------------------
# audit sync-notion
# ---------------------------------------------------------------------------


@app.command("sync-notion")
def sync_notion(
    client: str = ClientOpt,
    audit: str = AuditOpt,
    data_root: Optional[Path] = DataRootOpt,
):
    """Publish approved final artifacts to Notion. (v1: not yet implemented.)

    The gate check still runs so this command can never accidentally
    publish a draft. The actual Notion adapter lives in
    ``notion_sync.py`` and is intentionally a stub in v1.
    """
    paths = _resolve_paths(client, audit, data_root)
    _print_header("sync-notion", paths)
    _require_artifacts([("report.final", paths.report_final)])
    typer.secho(
        "sync-notion is intentionally not implemented in v1. The Notion "
        "adapter (notion_sync.py) is a stub. v1 is local-first; Notion "
        "publish lands in a later phase.",
        fg=typer.colors.YELLOW,
    )
    raise typer.Exit(code=2)


# ---------------------------------------------------------------------------
# audit validate
# ---------------------------------------------------------------------------


@app.command()
def validate(
    client: Optional[str] = typer.Option(None, "--client", "-c"),
    audit: Optional[str] = typer.Option(None, "--audit", "-a"),
    file: Optional[Path] = typer.Option(
        None, "--file", "-f", help="Validate a single JSON file."
    ),
    schema: Optional[str] = typer.Option(
        None,
        "--schema",
        help="Schema name to validate --file against (e.g. findings.schema.json).",
    ),
    data_root: Optional[Path] = DataRootOpt,
):
    """Validate one file or all known artifacts in an audit."""
    if file is not None:
        if schema is None:
            typer.secho("--file requires --schema.", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        try:
            data = s.read_json(file)
            s.get_schema_registry().validate(schema, data)
        except s.StorageError as e:
            typer.secho(f"FAIL  {file}\n  {e}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from e
        typer.secho(f"PASS  {file}  ({schema})", fg=typer.colors.GREEN)
        return

    if client is None or audit is None:
        typer.secho(
            "validate needs either --file --schema, or --client --audit.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    paths = _resolve_paths(client, audit, data_root)
    _print_header("validate", paths)
    targets: list[tuple[str, Path]] = []
    schema_for: dict[str, str] = {
        "client.json": "client.schema.json",
        "audit.json": "audit.schema.json",
        "intake.json": "intake.schema.json",
        "findings.draft.json": "findings.schema.json",
        "findings.final.json": "findings.schema.json",
        "scorecard.json": "scorecard.schema.json",
        "report.draft.json": "report.schema.json",
        "report.final.json": "report.schema.json",
        "proposal.draft.json": "proposal.schema.json",
        "proposal.final.json": "proposal.schema.json",
    }
    found = s.list_audit_artifacts(paths)
    for label, path in found.items():
        name = path.name
        if name in schema_for:
            targets.append((schema_for[name], path))
        elif label.startswith("notes/"):
            targets.append(("notes.schema.json", path))

    if not targets:
        typer.secho("no validatable artifacts found for this audit.", fg=typer.colors.YELLOW)
        return

    results = s.validate_files(targets)
    failures = 0
    for path, err in results.items():
        if err is None:
            typer.secho(f"PASS  {path}", fg=typer.colors.GREEN)
        else:
            failures += 1
            typer.secho(f"FAIL  {path}\n  {err}", fg=typer.colors.RED, err=True)
    if failures:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# audit info
# ---------------------------------------------------------------------------


@app.command()
def info(
    client: str = ClientOpt,
    audit: str = AuditOpt,
    data_root: Optional[Path] = DataRootOpt,
):
    """Show audit metadata, paths, and existing artifacts (read-only)."""
    paths = _resolve_paths(client, audit, data_root)
    _print_header("info", paths)
    typer.echo(f"  client_dir: {paths.client_dir}")
    typer.echo(f"  audit_dir:  {paths.audit_dir}")

    if paths.audit_file.is_file():
        try:
            audit_obj = s.load_model(paths.audit_file, M.Audit, schema_name="audit.schema.json")
            typer.echo(f"  audit_type: {audit_obj.audit_type}")
            typer.echo(f"  status:     {audit_obj.status}")
            typer.echo(f"  created_at: {audit_obj.created_at}")
        except s.StorageError as e:
            typer.secho(f"  could not load audit.json: {e}", fg=typer.colors.YELLOW)
    else:
        typer.secho("  audit.json missing — run `audit init`.", fg=typer.colors.YELLOW)

    found = s.list_audit_artifacts(paths)
    if found:
        typer.echo("  artifacts:")
        for label, path in sorted(found.items()):
            typer.echo(f"    {label}: {path}")
    else:
        typer.echo("  artifacts: (none yet)")


# ---------------------------------------------------------------------------
# audit version (small convenience, not in the locked list but read-only)
# ---------------------------------------------------------------------------


@app.command()
def version():
    """Print the notion-audit-os version."""
    typer.echo(f"notion-audit-os v{__version__}")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    app()


if __name__ == "__main__":
    main()
