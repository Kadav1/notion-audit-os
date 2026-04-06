"""Phase I placeholder tests for templates directory presence."""

from pathlib import Path


def test_templates_directory_exists():
    repo_root = Path(__file__).resolve().parents[1]
    assert (repo_root / "templates").is_dir()


def test_prompts_directory_exists():
    repo_root = Path(__file__).resolve().parents[1]
    assert (repo_root / "prompts").is_dir()


def test_schemas_directory_exists():
    repo_root = Path(__file__).resolve().parents[1]
    assert (repo_root / "schemas").is_dir()
