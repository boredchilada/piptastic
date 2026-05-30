"""Tests for project discovery."""

from pathlib import Path

import pytest

from piptastic.discovery import discover_one, discover_tree
from piptastic.models import SourceKind

FIXTURES = Path(__file__).parent / "fixtures"


def test_discover_tree_finds_all_fixture_projects():
    projects = discover_tree(FIXTURES)
    names = {p.name for p in projects}
    assert "req_only" in names
    assert "pyproject_pep621" in names
    assert "pyproject_poetry" in names
    assert "pipfile_project" in names
    assert "mixed" in names


def test_uv_lock_suppresses_pep621_manifest():
    proj = discover_one(FIXTURES / "uv_lock_project")
    kinds = {s.kind for s in proj.dep_sources}
    assert SourceKind.UV_LOCK in kinds
    assert SourceKind.PYPROJECT_PEP621 not in kinds  # lock wins


def test_poetry_lock_suppresses_poetry_manifest():
    proj = discover_one(FIXTURES / "poetry_lock_project")
    kinds = {s.kind for s in proj.dep_sources}
    assert SourceKind.POETRY_LOCK in kinds
    assert SourceKind.PYPROJECT_POETRY not in kinds


def test_pdm_lock_suppresses_pep621_manifest():
    proj = discover_one(FIXTURES / "pdm_lock_project")
    kinds = {s.kind for s in proj.dep_sources}
    assert SourceKind.PDM_LOCK in kinds
    assert SourceKind.PYPROJECT_PEP621 not in kinds


def test_discover_tree_excludes_venv():
    """A project root with a .venv/ subdir is itself a project, but the .venv
    must not be descended into."""
    projects = discover_tree(FIXTURES)
    # venv_inside is a real project (it has requirements.txt at its root)
    venv_inside = next(p for p in projects if p.name == "venv_inside")
    # ... but the .venv/ subdir must not appear as a project
    assert not any(".venv" in p.path.parts for p in projects)


def test_discover_tree_does_not_match_envoy_prefix():
    """Regression: old code excluded any dir starting with 'env'. envoy/ must
    be discovered."""
    projects = discover_tree(FIXTURES)
    assert any(p.name == "envoy" for p in projects)


def test_discover_tree_collapses_sibling_sources(write_tree):
    """A project dir with multiple dep files becomes ONE project with multiple
    dep_sources."""
    tree = write_tree({
        "requirements.txt": "flask==3.0.2\n",
        "dev-requirements.txt": "pytest>=8\n",
        "pyproject.toml": '[project]\nname="x"\nversion="0"\ndependencies=["requests==2.32.2"]\n',
    })
    projects = discover_tree(tree)
    assert len(projects) == 1
    p = projects[0]
    kinds = {s.kind for s in p.dep_sources}
    assert kinds == {SourceKind.REQUIREMENTS_TXT, SourceKind.PYPROJECT_PEP621}
    # 'dev' group came from filename inference
    groups = {(s.kind, s.group) for s in p.dep_sources}
    assert (SourceKind.REQUIREMENTS_TXT, "dev") in groups


def test_discover_tree_does_not_create_directories(write_tree):
    """Fixes [C1] — scan must be read-only."""
    tree = write_tree({"r.txt": "", "requirements.txt": "flask==3.0.2\n"})
    discover_tree(tree)
    assert not (tree / ".requirements_backups").exists()


def test_discover_one_returns_project_directly(write_tree):
    """Fixes [C5] — no parent rescan."""
    tree = write_tree({
        "myproj": {"requirements.txt": "flask==3.0.2\n"},
        "other": {"requirements.txt": "x==1\n"},
    })
    project = discover_one(tree / "myproj")
    assert project is not None
    assert project.name == "myproj"
    assert len(project.dep_sources) == 1


def test_discover_one_returns_none_for_dir_without_dep_files(tmp_path):
    assert discover_one(tmp_path) is None


def test_user_exclude_pattern(write_tree):
    tree = write_tree({
        "wanted": {"requirements.txt": "flask==3.0.2\n"},
        "skipme": {"requirements.txt": "x==1\n"},
    })
    projects = discover_tree(tree, exclude=["skipme"])
    assert {p.name for p in projects} == {"wanted"}


def test_python_version_detected_from_pyproject():
    projects = discover_tree(FIXTURES)
    mixed = next(p for p in projects if p.name == "mixed")
    assert mixed.python_version == "3.11"
    assert mixed.python_source == "pyproject.toml"


def test_discover_tree_excludes_dir_with_pyvenv_cfg():
    """A dir named arbitrarily (not in VENV_EXACT_NAMES) but containing
    pyvenv.cfg is still treated as a venv and skipped."""
    root = FIXTURES / "oddly_named_runtime"
    projects = discover_tree(root)
    names = {p.name for p in projects}
    assert "real_project" in names
    assert "runtime_env_42" not in names
    # The poison requirements.txt inside the fake venv must not have been read
    assert not any(
        "runtime_env_42" in str(s.path)
        for p in projects for s in p.dep_sources
    )
