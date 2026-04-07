"""Export and finalization helpers for Core Audit v1.1.

This module has three responsibilities:

1. **Finalization status** — a read-only check of which draft→final
   promotions have been completed for an audit. Never promotes anything;
   that is always a deliberate human action.

2. **Bundle assembly** — collecting approved final artifacts, validating
   each against its JSON Schema at export time, copying them to a delivery
   directory, and optionally rendering Markdown from the approved JSON.

3. **Manifest generation** — writing a machine-readable
   ``export_manifest.json`` in the output bundle listing every file, its
   size, and the export timestamp.

Locked decisions carried forward:

* Draft→final promotion is never automatic. ``check_finalization()``
  reports status; it never writes files.
* Canonical local artifacts are read-only here. Export copies to a
  separate output directory; originals are never moved or deleted.
* PDF/DOCX rendering is deferred. Markdown and JSON are the v1 formats.
* No LLM calls. No content modification. Export packages what was approved.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import models as M
from . import reporting as R
from . import proposal as P
from . import storage as s

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ExportError(Exception):
    """Raised when export cannot proceed due to missing or invalid artifacts."""


# ---------------------------------------------------------------------------
# Finalization status
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FinalizationStatus:
    """Read-only snapshot of draft→final promotion state for one audit.

    Used to give the operator a clear picture before export. Not used to
    auto-promote anything.
    """

    findings_has_draft: bool
    findings_is_final: bool
    report_has_draft: bool
    report_is_final: bool
    proposal_has_draft: bool
    proposal_is_final: bool

    @property
    def ready_for_export(self) -> bool:
        """True when the minimum gate for export is satisfied.

        The minimum gate is ``report.final.json`` existing. Proposal
        finalization is optional for export.
        """
        return self.report_is_final

    @property
    def pending_promotions(self) -> list[str]:
        """List of draft→final transitions that have not been completed yet.

        Each entry is a human-readable description of the missing promotion.
        An empty list means all present drafts have been promoted.
        """
        pending: list[str] = []
        if self.findings_has_draft and not self.findings_is_final:
            pending.append(
                "findings.draft.json \u2192 findings.final.json  "
                "(review and rename/copy when approved)"
            )
        if self.report_has_draft and not self.report_is_final:
            pending.append(
                "report.draft.json \u2192 report.final.json  "
                "(review and rename/copy when approved)"
            )
        if self.proposal_has_draft and not self.proposal_is_final:
            pending.append(
                "proposal.draft.json \u2192 proposal.final.json  "
                "(review and rename/copy when approved)"
            )
        return pending


def check_finalization(paths: s.AuditPaths) -> FinalizationStatus:
    """Return the draft→final promotion status for the given audit.

    Read-only. Never writes or modifies any file.
    """
    return FinalizationStatus(
        findings_has_draft=paths.findings_draft.is_file(),
        findings_is_final=paths.findings_final.is_file(),
        report_has_draft=paths.report_draft.is_file(),
        report_is_final=paths.report_final.is_file(),
        proposal_has_draft=paths.proposal_draft.is_file(),
        proposal_is_final=paths.proposal_final.is_file(),
    )


# ---------------------------------------------------------------------------
# Export bundle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExportedFile:
    """Metadata for one file in an export bundle."""

    name: str
    size_bytes: int


@dataclass
class ExportBundle:
    """Describes the contents and location of one completed export bundle."""

    audit_id: str
    client_slug: str
    target_dir: Path
    exported_at: datetime
    files: list[ExportedFile] = field(default_factory=list)

    @property
    def manifest_path(self) -> Path:
        return self.target_dir / "export_manifest.json"

    def to_dict(self) -> dict:
        """Serialise to a plain dict suitable for JSON output."""
        return {
            "audit_id": self.audit_id,
            "client_slug": self.client_slug,
            "target_dir": str(self.target_dir),
            "exported_at": self.exported_at.isoformat(timespec="seconds"),
            "file_count": len(self.files),
            "files": [
                {"name": f.name, "size_bytes": f.size_bytes} for f in self.files
            ],
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _copy_json(
    src: Path,
    dest: Path,
    *,
    schema_name: str | None,
    overwrite: bool,
) -> ExportedFile:
    """Read, optionally schema-validate, and write a JSON artifact.

    Raises :class:`ExportError` if the source is missing or invalid.
    """
    try:
        data = s.read_json(src)
    except s.StorageError as e:
        raise ExportError(f"cannot read {src.name}: {e}") from e

    if schema_name is not None:
        try:
            s.get_schema_registry().validate(schema_name, data)
        except s.SchemaValidationError as e:
            raise ExportError(
                f"{src.name} failed schema validation at export time: {e}"
            ) from e

    try:
        s.write_json(dest, data, overwrite=overwrite)
    except s.ArtifactExistsError as e:
        raise ExportError(
            f"refusing to overwrite {dest.name} in export dir "
            f"(pass overwrite=True to replace)"
        ) from e

    return ExportedFile(name=dest.name, size_bytes=dest.stat().st_size)


def _write_text(dest: Path, content: str, *, overwrite: bool) -> ExportedFile:
    """Write a text/Markdown artifact to the export directory."""
    try:
        s.write_text(dest, content, overwrite=overwrite)
    except s.ArtifactExistsError as e:
        raise ExportError(
            f"refusing to overwrite {dest.name} in export dir "
            f"(pass overwrite=True to replace)"
        ) from e
    return ExportedFile(name=dest.name, size_bytes=dest.stat().st_size)


def _write_manifest(bundle: ExportBundle, *, overwrite: bool) -> ExportedFile:
    """Write ``export_manifest.json`` to the bundle's target directory."""
    dest = bundle.manifest_path
    payload = json.dumps(bundle.to_dict(), indent=2, sort_keys=False, ensure_ascii=False)
    if not payload.endswith("\n"):
        payload += "\n"
    if dest.exists() and not overwrite:
        raise ExportError(
            f"refusing to overwrite existing manifest: {dest} "
            f"(pass overwrite=True to replace)"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(payload, encoding="utf-8")
    return ExportedFile(name=dest.name, size_bytes=dest.stat().st_size)


# ---------------------------------------------------------------------------
# Top-level bundle assembly
# ---------------------------------------------------------------------------


def build_export_bundle(
    paths: s.AuditPaths,
    target_dir: Path,
    *,
    include_proposal: bool = False,
    include_scorecard: bool = False,
    render_markdown: bool = False,
    overwrite: bool = False,
) -> ExportBundle:
    """Assemble and write an export bundle for an approved audit.

    Validates every included final artifact against its JSON Schema before
    copying. Original artifacts are never moved or deleted \u2014 the bundle is
    always a copy.

    Args:
        paths: Resolved artifact paths for the audit.
        target_dir: Directory to write the export bundle into. Created if
            it does not exist.
        include_proposal: If ``True``, include ``proposal.final.json`` in
            the bundle. Raises :class:`ExportError` if it is missing.
        include_scorecard: If ``True``, include ``scorecard.json`` in the
            bundle. Raises :class:`ExportError` if it is missing.
        render_markdown: If ``True``, render the approved report (and
            proposal if included) to ``.md`` files in the bundle. The
            Markdown is derived from the final JSON \u2014 no content is added.
        overwrite: If ``True``, overwrite existing files in ``target_dir``.

    Returns:
        An :class:`ExportBundle` describing every file written, including
        the manifest.

    Raises:
        :class:`ExportError`: If a required artifact is missing, fails
            schema validation, or a destination file exists and
            ``overwrite=False``.
    """
    # Gate: report.final.json must exist.
    if not paths.report_final.is_file():
        raise ExportError(
            "report.final.json is missing \u2014 the report must be reviewed and "
            "promoted before export. Run `audit report` to generate a draft, "
            "review it, then save as report.final.json."
        )

    target_dir.mkdir(parents=True, exist_ok=True)

    bundle = ExportBundle(
        audit_id=paths.audit_id,
        client_slug=paths.client_slug,
        target_dir=target_dir,
        exported_at=datetime.now(timezone.utc),
    )

    # --- report.final.json (always included) ---
    bundle.files.append(
        _copy_json(
            paths.report_final,
            target_dir / "report.final.json",
            schema_name="report.schema.json",
            overwrite=overwrite,
        )
    )

    # --- proposal.final.json (optional) ---
    if include_proposal:
        if not paths.proposal_final.is_file():
            raise ExportError(
                "proposal.final.json is missing. Review and promote "
                "proposal.draft.json first, or omit --include-proposal."
            )
        bundle.files.append(
            _copy_json(
                paths.proposal_final,
                target_dir / "proposal.final.json",
                schema_name="proposal.schema.json",
                overwrite=overwrite,
            )
        )

    # --- scorecard.json (optional) ---
    if include_scorecard:
        if not paths.scorecard_file.is_file():
            raise ExportError(
                "scorecard.json is missing. Run `audit score` first, "
                "or omit --include-scorecard."
            )
        bundle.files.append(
            _copy_json(
                paths.scorecard_file,
                target_dir / "scorecard.json",
                schema_name="scorecard.schema.json",
                overwrite=overwrite,
            )
        )

    # --- Markdown rendering (optional) ---
    if render_markdown:
        # Load the approved report model from final JSON and render.
        try:
            report_obj = s.load_model(
                paths.report_final, M.Report, schema_name="report.schema.json"
            )
        except s.StorageError as e:
            raise ExportError(
                f"could not load report.final.json for Markdown rendering: {e}"
            ) from e
        md = R.render_report_markdown(report_obj)
        bundle.files.append(
            _write_text(target_dir / "report.final.md", md, overwrite=overwrite)
        )

        # Render proposal Markdown if included.
        if include_proposal:
            try:
                proposal_obj = s.load_model(
                    paths.proposal_final,
                    M.Proposal,
                    schema_name="proposal.schema.json",
                )
            except s.StorageError as e:
                raise ExportError(
                    f"could not load proposal.final.json for Markdown rendering: {e}"
                ) from e
            md_p = P.render_proposal_markdown(proposal_obj)
            bundle.files.append(
                _write_text(
                    target_dir / "proposal.final.md", md_p, overwrite=overwrite
                )
            )

    # --- manifest (always last so it includes all other files) ---
    manifest_entry = _write_manifest(bundle, overwrite=overwrite)
    bundle.files.append(manifest_entry)

    return bundle


__all__ = [
    "ExportError",
    "FinalizationStatus",
    "check_finalization",
    "ExportedFile",
    "ExportBundle",
    "build_export_bundle",
]
