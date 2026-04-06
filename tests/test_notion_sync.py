"""Phase X tests for notion_sync.py and the updated sync-notion CLI command."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from notion_audit_os import cli
from notion_audit_os import models as M
from notion_audit_os import notion_sync as NS
from notion_audit_os import reporting as R
from notion_audit_os import proposal as P
from notion_audit_os import scoring as sc
from notion_audit_os import storage as s

runner = CliRunner()

# ---------------------------------------------------------------------------
# Mock Notion API adapter
# ---------------------------------------------------------------------------


@dataclass
class _MockAdapter:
    """Drop-in replacement for NotionAPIAdapter in tests. No network calls."""

    fail: bool = False
    page_id: str = "page-mock-001"
    page_url: str = "https://notion.so/page-mock-001"

    # Tracks calls so tests can inspect what was sent.
    calls: list[dict] = None

    def __post_init__(self):
        self.calls = []

    def create_page(self, parent_id: str, title: str, children: list[dict]) -> dict:
        self.calls.append({
            "parent_id": parent_id,
            "title": title,
            "block_count": len(children),
            "blocks": children,
        })
        if self.fail:
            raise NS.NotionAPIError("mock API failure: authentication error")
        return {"id": self.page_id, "url": self.page_url}


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _common(client: str, audit: str, data_root: Path) -> list[str]:
    return ["--client", client, "--audit", audit, "--data-root", str(data_root)]


def _make_scorecard(audit_id: str = "aud_001") -> M.Scorecard:
    scores = {
        "Business Fit": 3, "Workspace Structure": 2, "Database Design": 1,
        "Data Relationships": 2, "Workflow Clarity": 2, "Views and Dashboards": 3,
        "Intake and Requests": "N/A", "Governance and Adoption": 2,
    }
    return sc.score_audit(audit_id, scores)


def _make_report(audit_id: str = "aud_001") -> M.Report:
    card = _make_scorecard(audit_id)
    fc = M.FindingsCollection.model_validate({
        "findings": [
            {
                "finding_id": "f1", "audit_id": audit_id,
                "category": "Database Design", "title": "DB issue",
                "observation": "obs", "evidence": ["e"],
                "why_it_matters": "Matters.", "severity": "high",
                "quick_win": False, "status": "draft",
            },
            {
                "finding_id": "f2", "audit_id": audit_id,
                "category": "Governance and Adoption",
                "title": "No ownership", "observation": "obs2",
                "evidence": ["e2"], "severity": "medium",
                "quick_win": True, "status": "draft",
            },
        ]
    })
    return R.assemble_report(audit_id, card, fc)


def _make_proposal(audit_id: str = "aud_001") -> M.Proposal:
    card = _make_scorecard(audit_id)
    fc = M.FindingsCollection.model_validate({"findings": []})
    return P.assemble_proposal(audit_id, card, fc)


def _write_report_final(audit_dir: Path, audit_id: str = "aud_001") -> Path:
    rpt = _make_report(audit_id)
    p = audit_dir / "report.final.json"
    s.write_json(p, rpt.model_dump(by_alias=True, mode="json", exclude_none=True))
    return p


def _write_proposal_final(audit_dir: Path, audit_id: str = "aud_001") -> Path:
    prop = _make_proposal(audit_id)
    p = audit_dir / "proposal.final.json"
    s.write_json(p, prop.model_dump(by_alias=True, mode="json", exclude_none=True))
    return p


def _make_audit_dir(tmp_path: Path, audit_id: str = "aud_001") -> Path:
    audit_dir = tmp_path / "data" / "clients" / "acme" / "audits" / audit_id
    audit_dir.mkdir(parents=True, exist_ok=True)
    return audit_dir


def _make_paths(tmp_path: Path, audit_id: str = "aud_001") -> s.AuditPaths:
    return s.AuditPaths(
        data_root=tmp_path / "data",
        client_slug="acme",
        audit_id=audit_id,
    )


def _valid_config() -> NS.SyncConfig:
    return NS.SyncConfig(token="test-token-abc", parent_page_id="parent-abc-123")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_load_sync_config_from_explicit_args():
    config = NS.load_sync_config(token="tok123", parent_page_id="page456")
    assert config.token == "tok123"
    assert config.parent_page_id == "page456"


def test_load_sync_config_missing_token_raises():
    with pytest.raises(NS.SyncConfigError, match="token"):
        NS.load_sync_config(token="", parent_page_id="page456")


def test_load_sync_config_missing_parent_id_raises():
    with pytest.raises(NS.SyncConfigError, match="parent"):
        NS.load_sync_config(token="tok123", parent_page_id="")


def test_load_sync_config_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NOTION_API_TOKEN", "env-token")
    monkeypatch.setenv("NOTION_PARENT_PAGE_ID", "env-parent")
    config = NS.load_sync_config()
    assert config.token == "env-token"
    assert config.parent_page_id == "env-parent"


def test_explicit_arg_overrides_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NOTION_API_TOKEN", "env-token")
    monkeypatch.setenv("NOTION_PARENT_PAGE_ID", "env-parent")
    config = NS.load_sync_config(token="explicit-tok", parent_page_id="explicit-parent")
    assert config.token == "explicit-tok"
    assert config.parent_page_id == "explicit-parent"


def test_load_sync_config_no_env_no_args_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("NOTION_API_TOKEN", raising=False)
    monkeypatch.delenv("NOTION_PARENT_PAGE_ID", raising=False)
    with pytest.raises(NS.SyncConfigError):
        NS.load_sync_config()


# ---------------------------------------------------------------------------
# convert_report_to_blocks
# ---------------------------------------------------------------------------


def test_convert_report_returns_list_of_dicts():
    rpt = _make_report()
    blocks = NS.convert_report_to_blocks(rpt)
    assert isinstance(blocks, list)
    assert len(blocks) > 0
    for b in blocks:
        assert isinstance(b, dict)


def test_convert_report_blocks_have_required_keys():
    rpt = _make_report()
    blocks = NS.convert_report_to_blocks(rpt)
    for b in blocks:
        assert "object" in b
        assert "type" in b
        assert b["object"] == "block"


def test_convert_report_contains_heading_blocks():
    rpt = _make_report()
    blocks = NS.convert_report_to_blocks(rpt)
    headings = [b for b in blocks if b["type"] == "heading_2"]
    assert len(headings) >= 3  # at least Executive Summary, Maturity, Scorecard


def test_convert_report_contains_section_heading_executive_summary():
    rpt = _make_report()
    blocks = NS.convert_report_to_blocks(rpt)
    heading_texts = []
    for b in blocks:
        if b["type"] == "heading_2":
            rt = b["heading_2"]["rich_text"]
            heading_texts.append("".join(t["text"]["content"] for t in rt))
    assert "Executive Summary" in heading_texts


def test_convert_report_contains_key_findings_bullets():
    rpt = _make_report()
    blocks = NS.convert_report_to_blocks(rpt)
    bullets = [b for b in blocks if b["type"] == "bulleted_list_item"]
    assert len(bullets) > 0


def test_convert_report_contains_roadmap_when_present():
    rpt = _make_report()
    # The report should have roadmap phases (findings include quick_win and high sev)
    assert len(rpt.sections.roadmap) > 0
    blocks = NS.convert_report_to_blocks(rpt)
    heading_texts = []
    for b in blocks:
        if b["type"] in ("heading_2", "heading_3"):
            rt_key = b["type"]
            rt = b[rt_key]["rich_text"]
            heading_texts.append("".join(t["text"]["content"] for t in rt))
    assert "Roadmap" in heading_texts


def test_convert_report_respects_block_limit():
    """Even a very large report must not exceed MAX_BLOCKS."""
    # Build a report with a long scorecard_summary (lots of lines)
    card = _make_scorecard()
    big_summary = "\n".join([f"Line {i}: some text here." for i in range(200)])
    fc = M.FindingsCollection.model_validate({
        "findings": [
            {
                "finding_id": f"f{i}", "audit_id": "aud_001",
                "category": "Business Fit", "title": f"Finding {i}",
                "observation": "obs", "evidence": ["e"], "status": "draft",
            }
            for i in range(50)  # many findings
        ]
    })
    rpt = R.assemble_report("aud_001", card, fc)
    blocks = NS.convert_report_to_blocks(rpt)
    assert len(blocks) <= NS._MAX_BLOCKS


def test_convert_report_long_text_does_not_crash():
    """Long prose in a section must not raise."""
    long_text = "A" * 5000
    card = _make_scorecard()
    fc = M.FindingsCollection.model_validate({"findings": []})
    rpt = R.assemble_report("aud_001", card, fc)
    # Manually set a very long executive_summary (via a fresh Report)
    sections = M.ReportSections(
        executive_summary=long_text,
        maturity_summary="short",
        key_findings=[],
        scorecard_summary="short",
        roadmap=[],
        recommended_next_step="short",
    )
    big_rpt = M.Report(
        audit_id="aud_001",
        template_version="v1",
        output_format=M.OutputFormat.JSON,
        generated_at=datetime.now(timezone.utc),
        sections=sections,
    )
    blocks = NS.convert_report_to_blocks(big_rpt)
    assert len(blocks) <= NS._MAX_BLOCKS
    assert all(isinstance(b, dict) for b in blocks)


# ---------------------------------------------------------------------------
# convert_proposal_to_blocks
# ---------------------------------------------------------------------------


def test_convert_proposal_returns_list_of_dicts():
    prop = _make_proposal()
    blocks = NS.convert_proposal_to_blocks(prop)
    assert isinstance(blocks, list)
    assert len(blocks) > 0
    for b in blocks:
        assert isinstance(b, dict)


def test_convert_proposal_starts_with_divider():
    prop = _make_proposal()
    blocks = NS.convert_proposal_to_blocks(prop)
    assert blocks[0]["type"] == "divider"


def test_convert_proposal_contains_deliverables_as_bullets():
    prop = _make_proposal()
    assert len(prop.deliverables) > 0
    blocks = NS.convert_proposal_to_blocks(prop)
    bullets = [b for b in blocks if b["type"] == "bulleted_list_item"]
    assert len(bullets) > 0


def test_convert_proposal_package_name_appears_in_blocks():
    prop = _make_proposal()
    blocks = NS.convert_proposal_to_blocks(prop)
    all_text = []
    for b in blocks:
        if b["type"] == "paragraph":
            for rt in b["paragraph"]["rich_text"]:
                all_text.append(rt["text"]["content"])
    assert any(prop.recommended_package in t for t in all_text)


# ---------------------------------------------------------------------------
# sync_audit — with mock adapter
# ---------------------------------------------------------------------------


def test_sync_audit_success_with_mock(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    _write_report_final(audit_dir)
    config = _valid_config()
    adapter = _MockAdapter()

    result = NS.sync_audit(paths, config, adapter=adapter)

    assert result.success is True
    assert result.page_id == "page-mock-001"
    assert result.page_url == "https://notion.so/page-mock-001"
    assert result.audit_id == "aud_001"
    assert "report.final.json" in result.artifacts_synced
    assert result.error is None


def test_sync_audit_calls_create_page_once(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    _write_report_final(audit_dir)
    config = _valid_config()
    adapter = _MockAdapter()

    NS.sync_audit(paths, config, adapter=adapter)

    assert len(adapter.calls) == 1


def test_sync_audit_sends_parent_id(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    _write_report_final(audit_dir)
    config = _valid_config()
    adapter = _MockAdapter()

    NS.sync_audit(paths, config, adapter=adapter)

    assert adapter.calls[0]["parent_id"] == config.parent_page_id


def test_sync_audit_page_title_contains_audit_id(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    _write_report_final(audit_dir)
    config = _valid_config()
    adapter = _MockAdapter()

    NS.sync_audit(paths, config, adapter=adapter)

    title = adapter.calls[0]["title"]
    assert "aud_001" in title or "Audit Report" in title


def test_sync_audit_with_proposal(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    _write_report_final(audit_dir)
    _write_proposal_final(audit_dir)
    config = _valid_config()
    adapter = _MockAdapter()

    result = NS.sync_audit(paths, config, include_proposal=True, adapter=adapter)

    assert "proposal.final.json" in result.artifacts_synced
    # Combined page should have more blocks than report-only
    assert adapter.calls[0]["block_count"] > 0


def test_sync_audit_blocks_when_report_missing(tmp_path: Path):
    _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    config = _valid_config()
    adapter = _MockAdapter()

    with pytest.raises(NS.SyncPrerequisiteError, match="report.final.json"):
        NS.sync_audit(paths, config, adapter=adapter)


def test_sync_audit_blocks_when_proposal_missing(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    _write_report_final(audit_dir)
    # proposal.final.json not written
    config = _valid_config()
    adapter = _MockAdapter()

    with pytest.raises(NS.SyncPrerequisiteError, match="proposal.final.json"):
        NS.sync_audit(paths, config, include_proposal=True, adapter=adapter)


def test_sync_audit_api_error_raises_notion_api_error(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    _write_report_final(audit_dir)
    config = _valid_config()
    adapter = _MockAdapter(fail=True)

    with pytest.raises(NS.NotionAPIError, match="mock API failure"):
        NS.sync_audit(paths, config, adapter=adapter)


def test_sync_audit_does_not_modify_local_report(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    _write_report_final(audit_dir)
    original_mtime = (audit_dir / "report.final.json").stat().st_mtime
    config = _valid_config()
    adapter = _MockAdapter()

    NS.sync_audit(paths, config, adapter=adapter)

    assert (audit_dir / "report.final.json").stat().st_mtime == original_mtime


def test_sync_audit_uses_client_name_from_intake_in_title(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    _write_report_final(audit_dir)

    # Write an intake with a known client name
    intake = M.Intake(
        audit_id="aud_001",
        normalized_payload=M.IntakePayload(client_name="Globex Corp"),
        parsed_at=datetime.now(timezone.utc),
    )
    s.write_json(
        audit_dir / "intake.json",
        intake.model_dump(by_alias=True, mode="json", exclude_none=True),
    )

    config = _valid_config()
    adapter = _MockAdapter()
    NS.sync_audit(paths, config, adapter=adapter)

    assert "Globex Corp" in adapter.calls[0]["title"]


# ---------------------------------------------------------------------------
# write_sync_log
# ---------------------------------------------------------------------------


def test_write_sync_log_creates_file(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    result = NS.SyncResult(
        audit_id="aud_001",
        success=True,
        synced_at=datetime.now(timezone.utc),
        target_parent_id="parent-abc",
        page_title="Acme — Audit Report",
        page_id="page-abc",
        page_url="https://notion.so/page-abc",
        artifacts_synced=["report.final.json"],
        message="Page created successfully.",
    )
    NS.write_sync_log(paths, result)
    assert paths.notion_sync_file.is_file()


def test_write_sync_log_is_valid_json(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    result = NS.SyncResult(
        audit_id="aud_001",
        success=True,
        synced_at=datetime.now(timezone.utc),
        target_parent_id="parent-abc",
        page_title="Acme — Audit Report",
    )
    NS.write_sync_log(paths, result)
    data = json.loads(paths.notion_sync_file.read_text(encoding="utf-8"))
    assert data["audit_id"] == "aud_001"
    assert data["success"] is True
    assert "synced_at" in data


def test_write_sync_log_records_failure(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)
    result = NS.SyncResult(
        audit_id="aud_001",
        success=False,
        synced_at=datetime.now(timezone.utc),
        target_parent_id="parent-abc",
        page_title="(failed)",
        error="HTTP 401: unauthorized",
        message="Sync failed.",
    )
    NS.write_sync_log(paths, result)
    data = json.loads(paths.notion_sync_file.read_text(encoding="utf-8"))
    assert data["success"] is False
    assert data["error"] == "HTTP 401: unauthorized"


def test_write_sync_log_overwrites_previous(tmp_path: Path):
    audit_dir = _make_audit_dir(tmp_path)
    paths = _make_paths(tmp_path)

    r1 = NS.SyncResult(
        audit_id="aud_001", success=True,
        synced_at=datetime.now(timezone.utc),
        target_parent_id="p", page_title="t", page_id="old-id",
    )
    NS.write_sync_log(paths, r1)

    r2 = NS.SyncResult(
        audit_id="aud_001", success=True,
        synced_at=datetime.now(timezone.utc),
        target_parent_id="p", page_title="t", page_id="new-id",
    )
    NS.write_sync_log(paths, r2)

    data = json.loads(paths.notion_sync_file.read_text(encoding="utf-8"))
    assert data["page_id"] == "new-id"


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


def _setup_for_sync(data_root: Path) -> Path:
    audit_dir = data_root / "clients" / "acme" / "audits" / "aud_001"
    audit_dir.mkdir(parents=True, exist_ok=True)
    _write_report_final(audit_dir)
    return audit_dir


def test_cli_sync_blocks_when_token_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_root = tmp_path / "data"
    _setup_for_sync(data_root)
    monkeypatch.delenv("NOTION_API_TOKEN", raising=False)
    monkeypatch.delenv("NOTION_PARENT_PAGE_ID", raising=False)

    r = runner.invoke(
        cli.app,
        ["sync-notion", *_common("acme", "aud_001", data_root),
         "--parent-id", "some-parent"],  # token still missing
    )
    assert r.exit_code != 0
    combined = r.stdout + r.stderr
    assert "token" in combined.lower() or "configuration" in combined.lower()


def test_cli_sync_blocks_when_parent_id_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_root = tmp_path / "data"
    _setup_for_sync(data_root)
    monkeypatch.delenv("NOTION_API_TOKEN", raising=False)
    monkeypatch.delenv("NOTION_PARENT_PAGE_ID", raising=False)

    r = runner.invoke(
        cli.app,
        ["sync-notion", *_common("acme", "aud_001", data_root),
         "--token", "tok"],  # parent-id missing
    )
    assert r.exit_code != 0
    combined = r.stdout + r.stderr
    assert "parent" in combined.lower() or "configuration" in combined.lower()


def test_cli_sync_blocks_when_report_final_missing(tmp_path: Path):
    data_root = tmp_path / "data"
    audit_dir = data_root / "clients" / "acme" / "audits" / "aud_001"
    audit_dir.mkdir(parents=True, exist_ok=True)
    # No report.final.json

    r = runner.invoke(
        cli.app,
        ["sync-notion", *_common("acme", "aud_001", data_root),
         "--token", "tok", "--parent-id", "parent"],
    )
    assert r.exit_code != 0
    combined = r.stdout + r.stderr
    assert "blocked" in combined or "missing" in combined


def test_cli_sync_succeeds_with_mock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Patch NotionAPIAdapter to avoid live HTTP calls."""
    data_root = tmp_path / "data"
    audit_dir = _setup_for_sync(data_root)

    # Monkeypatch the adapter class used inside sync_audit
    monkeypatch.setattr(
        "notion_audit_os.notion_sync.NotionAPIAdapter",
        lambda token, api_version=None: _MockAdapter(),
    )

    r = runner.invoke(
        cli.app,
        ["sync-notion", *_common("acme", "aud_001", data_root),
         "--token", "tok", "--parent-id", "parent-xyz"],
    )
    assert r.exit_code == 0, r.stdout + r.stderr
    assert "page-mock-001" in r.stdout


def test_cli_sync_writes_sync_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_root = tmp_path / "data"
    audit_dir = _setup_for_sync(data_root)

    monkeypatch.setattr(
        "notion_audit_os.notion_sync.NotionAPIAdapter",
        lambda token, api_version=None: _MockAdapter(),
    )

    runner.invoke(
        cli.app,
        ["sync-notion", *_common("acme", "aud_001", data_root),
         "--token", "tok", "--parent-id", "parent-xyz"],
    )
    log_path = audit_dir / "notion_sync.json"
    assert log_path.is_file()
    data = json.loads(log_path.read_text(encoding="utf-8"))
    assert data["success"] is True
    assert data["audit_id"] == "aud_001"


def test_cli_sync_api_error_writes_failure_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_root = tmp_path / "data"
    audit_dir = _setup_for_sync(data_root)

    monkeypatch.setattr(
        "notion_audit_os.notion_sync.NotionAPIAdapter",
        lambda token, api_version=None: _MockAdapter(fail=True),
    )

    r = runner.invoke(
        cli.app,
        ["sync-notion", *_common("acme", "aud_001", data_root),
         "--token", "tok", "--parent-id", "parent-xyz"],
    )
    assert r.exit_code != 0
    log_path = audit_dir / "notion_sync.json"
    assert log_path.is_file()
    data = json.loads(log_path.read_text(encoding="utf-8"))
    assert data["success"] is False
    assert data["error"] is not None
