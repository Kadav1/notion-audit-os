"""Local file IO, schema validation, and Pydantic loading.

This is the storage/validation layer for notion-audit-os. It is the only
module that should touch the filesystem for audit artifacts in v1.

Local layout (under ``data_root``)::

    data/
      clients/<client_slug>/
        client.json
        audits/<audit_id>/
          audit.json
          intake.json
          notes/
          findings.draft.json
          findings.final.json
          scorecard.json
          report.draft.json     report.final.json
          proposal.draft.json   proposal.final.json
          notion_sync.json

Schema validation uses ``jsonschema`` + ``referencing`` so cross-file
``$ref``s in ``schemas/*.schema.json`` resolve against a local
``Registry`` (no network).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Type, TypeVar

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JSONSchemaValidationError
from pydantic import BaseModel, ValidationError as PydanticValidationError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from . import models as M

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class StorageError(Exception):
    """Base error for storage/validation problems."""


class ArtifactNotFoundError(StorageError):
    """Raised when an expected artifact file is missing."""


class ArtifactExistsError(StorageError):
    """Raised when a write would overwrite an existing artifact unexpectedly."""


class MalformedJSONError(StorageError):
    """Raised when a file is not valid JSON."""


class SchemaValidationError(StorageError):
    """Raised when a JSON instance fails schema validation."""

    def __init__(self, schema_name: str, errors: list[str]):
        self.schema_name = schema_name
        self.errors = errors
        joined = "\n  - ".join(errors) if errors else "(no detail)"
        super().__init__(f"schema validation failed for {schema_name}:\n  - {joined}")


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def project_root() -> Path:
    """Return the repo root (the directory that contains ``schemas/``)."""
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        if (parent / "schemas").is_dir() and (parent / "src").is_dir():
            return parent
    raise StorageError("could not locate project root from " + str(here))


def schemas_dir() -> Path:
    return project_root() / "schemas"


def default_data_root() -> Path:
    return project_root() / "data"


@dataclass(frozen=True)
class AuditPaths:
    """Resolved paths for one audit's artifacts."""

    data_root: Path
    client_slug: str
    audit_id: str

    @property
    def client_dir(self) -> Path:
        return self.data_root / "clients" / self.client_slug

    @property
    def client_file(self) -> Path:
        return self.client_dir / "client.json"

    @property
    def audit_dir(self) -> Path:
        return self.client_dir / "audits" / self.audit_id

    @property
    def audit_file(self) -> Path:
        return self.audit_dir / "audit.json"

    @property
    def intake_file(self) -> Path:
        return self.audit_dir / "intake.json"

    @property
    def notes_dir(self) -> Path:
        return self.audit_dir / "notes"

    def notes_file(self, name: str) -> Path:
        return self.notes_dir / f"{name}.json"

    @property
    def findings_draft(self) -> Path:
        return self.audit_dir / "findings.draft.json"

    @property
    def findings_final(self) -> Path:
        return self.audit_dir / "findings.final.json"

    @property
    def scorecard_file(self) -> Path:
        return self.audit_dir / "scorecard.json"

    @property
    def report_draft(self) -> Path:
        return self.audit_dir / "report.draft.json"

    @property
    def report_final(self) -> Path:
        return self.audit_dir / "report.final.json"

    @property
    def proposal_draft(self) -> Path:
        return self.audit_dir / "proposal.draft.json"

    @property
    def proposal_final(self) -> Path:
        return self.audit_dir / "proposal.final.json"

    @property
    def notion_sync_file(self) -> Path:
        return self.audit_dir / "notion_sync.json"


def audit_paths(
    client_slug: str,
    audit_id: str,
    *,
    data_root: Path | None = None,
) -> AuditPaths:
    """Build an :class:`AuditPaths` for a client/audit pair."""
    return AuditPaths(
        data_root=data_root or default_data_root(),
        client_slug=client_slug,
        audit_id=audit_id,
    )


# ---------------------------------------------------------------------------
# Safe file IO
# ---------------------------------------------------------------------------


def read_json(path: Path) -> Any:
    """Read a JSON file, raising clear errors on common failures."""
    if not path.exists():
        raise ArtifactNotFoundError(f"missing JSON artifact: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise StorageError(f"could not read {path}: {e}") from e
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise MalformedJSONError(f"malformed JSON in {path}: {e}") from e


def write_json(
    path: Path,
    data: Any,
    *,
    overwrite: bool = False,
    indent: int = 2,
) -> Path:
    """Write a JSON artifact in stable pretty form.

    Refuses to clobber an existing file unless ``overwrite=True``.
    """
    if path.exists() and not overwrite:
        raise ArtifactExistsError(
            f"refusing to overwrite existing artifact: {path} "
            f"(pass overwrite=True to replace)"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=indent, sort_keys=False, ensure_ascii=False)
    path.write_text(payload + "\n", encoding="utf-8")
    return path


def read_text(path: Path) -> str:
    """Read a UTF-8 text/markdown file."""
    if not path.exists():
        raise ArtifactNotFoundError(f"missing text artifact: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        raise StorageError(f"could not read {path}: {e}") from e


def write_text(path: Path, content: str, *, overwrite: bool = False) -> Path:
    """Write a UTF-8 text/markdown artifact."""
    if path.exists() and not overwrite:
        raise ArtifactExistsError(
            f"refusing to overwrite existing artifact: {path} "
            f"(pass overwrite=True to replace)"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    if not content.endswith("\n"):
        content = content + "\n"
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Schema registry (cross-file $ref resolution)
# ---------------------------------------------------------------------------


class SchemaRegistry:
    """Loads all local JSON Schemas and validates instances against them.

    Each schema is registered under both its absolute ``$id`` and its
    bare filename so relative ``$ref``s like
    ``"common.schema.json#/$defs/..."`` resolve correctly.
    """

    def __init__(self, schemas_path: Path | None = None):
        self.schemas_path = schemas_path or schemas_dir()
        self._schemas: dict[str, dict[str, Any]] = {}
        self._registry: Registry = Registry()
        self._load()

    def _load(self) -> None:
        if not self.schemas_path.is_dir():
            raise StorageError(f"schemas directory not found: {self.schemas_path}")
        resources: list[tuple[str, Resource]] = []
        for path in sorted(self.schemas_path.glob("*.schema.json")):
            try:
                schema = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise StorageError(f"malformed schema {path}: {e}") from e
            self._schemas[path.name] = schema
            resource = Resource(contents=schema, specification=DRAFT202012)
            # Register under both filename (for relative refs) and $id.
            resources.append((path.name, resource))
            schema_id = schema.get("$id")
            if schema_id:
                resources.append((schema_id, resource))
        self._registry = Registry().with_resources(resources)

    @property
    def names(self) -> list[str]:
        return sorted(self._schemas)

    def get(self, name: str) -> dict[str, Any]:
        if name not in self._schemas:
            raise StorageError(
                f"unknown schema {name!r}; known: {', '.join(self.names)}"
            )
        return self._schemas[name]

    def validate(self, schema_name: str, instance: Any) -> None:
        """Validate ``instance`` against the named schema.

        Raises :class:`SchemaValidationError` with all error messages on
        failure. Returns ``None`` on success.
        """
        schema = self.get(schema_name)
        validator = Draft202012Validator(schema, registry=self._registry)
        errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
        if errors:
            messages = [_format_error(e) for e in errors]
            raise SchemaValidationError(schema_name, messages)

    def is_valid(self, schema_name: str, instance: Any) -> bool:
        try:
            self.validate(schema_name, instance)
        except SchemaValidationError:
            return False
        return True


def _format_error(err: JSONSchemaValidationError) -> str:
    location = "/".join(str(p) for p in err.absolute_path) or "<root>"
    return f"{location}: {err.message}"


@lru_cache(maxsize=1)
def get_schema_registry() -> SchemaRegistry:
    """Return a process-wide cached :class:`SchemaRegistry`."""
    return SchemaRegistry()


# ---------------------------------------------------------------------------
# Pydantic loading
# ---------------------------------------------------------------------------

T = TypeVar("T", bound=BaseModel)


def load_model(path: Path, model: Type[T], *, schema_name: str | None = None) -> T:
    """Read a JSON artifact and parse it into a Pydantic model.

    If ``schema_name`` is given, the raw JSON is validated against the
    schema *before* Pydantic parsing, so callers see schema errors first.
    """
    data = read_json(path)
    if schema_name is not None:
        get_schema_registry().validate(schema_name, data)
    try:
        return model.model_validate(data)
    except PydanticValidationError as e:
        raise StorageError(f"model validation failed for {path} as {model.__name__}: {e}") from e


def dump_model(
    path: Path,
    instance: BaseModel,
    *,
    overwrite: bool = False,
    schema_name: str | None = None,
) -> Path:
    """Serialize a Pydantic model to a JSON artifact.

    Uses ``by_alias=True, mode="json"`` so locked human-readable keys
    (e.g. scorecard category names) round-trip cleanly. If
    ``schema_name`` is given, the dumped data is validated against the
    schema before being written.
    """
    data = instance.model_dump(by_alias=True, mode="json", exclude_none=True)
    if schema_name is not None:
        get_schema_registry().validate(schema_name, data)
    return write_json(path, data, overwrite=overwrite)


# Mapping of canonical schema names to (model, artifact-locator).
# Locator returns the canonical Path for that artifact within an AuditPaths.
ARTIFACT_REGISTRY: dict[str, tuple[type[BaseModel], str]] = {
    "client.schema.json": (M.Client, "client_file"),
    "audit.schema.json": (M.Audit, "audit_file"),
    "intake.schema.json": (M.Intake, "intake_file"),
    "findings.schema.json": (M.FindingsCollection, "findings_final"),
    "scorecard.schema.json": (M.Scorecard, "scorecard_file"),
    "report.schema.json": (M.Report, "report_final"),
    "proposal.schema.json": (M.Proposal, "proposal_final"),
    "notion_sync.schema.json": (M.SyncLog, "notion_sync_file"),
}


# ---------------------------------------------------------------------------
# Artifact existence / listing
# ---------------------------------------------------------------------------


def artifact_exists(path: Path) -> bool:
    return path.is_file()


def list_audit_artifacts(paths: AuditPaths) -> dict[str, Path]:
    """Return the existing artifacts for an audit, keyed by short label."""
    candidates: dict[str, Path] = {
        "client": paths.client_file,
        "audit": paths.audit_file,
        "intake": paths.intake_file,
        "findings.draft": paths.findings_draft,
        "findings.final": paths.findings_final,
        "scorecard": paths.scorecard_file,
        "report.draft": paths.report_draft,
        "report.final": paths.report_final,
        "proposal.draft": paths.proposal_draft,
        "proposal.final": paths.proposal_final,
        "notion_sync": paths.notion_sync_file,
    }
    found = {label: p for label, p in candidates.items() if p.is_file()}
    if paths.notes_dir.is_dir():
        for note in sorted(paths.notes_dir.glob("*.json")):
            found[f"notes/{note.stem}"] = note
    return found


# ---------------------------------------------------------------------------
# Audit scaffolding
# ---------------------------------------------------------------------------


def ensure_audit_scaffold(paths: AuditPaths) -> AuditPaths:
    """Create the standard directory layout for a client/audit if missing.

    Idempotent. Does not create any artifact files.
    """
    paths.client_dir.mkdir(parents=True, exist_ok=True)
    paths.audit_dir.mkdir(parents=True, exist_ok=True)
    paths.notes_dir.mkdir(parents=True, exist_ok=True)
    return paths


# ---------------------------------------------------------------------------
# Bulk validation helper
# ---------------------------------------------------------------------------


def validate_files(items: Iterable[tuple[str, Path]]) -> dict[Path, str | None]:
    """Validate many ``(schema_name, path)`` pairs.

    Returns a dict mapping each path to ``None`` (valid) or a string error.
    Useful for the ``audit validate`` CLI command in a later phase.
    """
    registry = get_schema_registry()
    results: dict[Path, str | None] = {}
    for schema_name, path in items:
        try:
            data = read_json(path)
            registry.validate(schema_name, data)
            results[path] = None
        except StorageError as e:
            results[path] = str(e)
    return results


__all__ = [
    "StorageError",
    "ArtifactNotFoundError",
    "ArtifactExistsError",
    "MalformedJSONError",
    "SchemaValidationError",
    "AuditPaths",
    "audit_paths",
    "project_root",
    "schemas_dir",
    "default_data_root",
    "read_json",
    "write_json",
    "read_text",
    "write_text",
    "SchemaRegistry",
    "get_schema_registry",
    "load_model",
    "dump_model",
    "ARTIFACT_REGISTRY",
    "artifact_exists",
    "list_audit_artifacts",
    "ensure_audit_scaffold",
    "validate_files",
]
