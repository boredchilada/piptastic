"""Tests for the JSON renderer (stable schema_version=1)."""

import json
from datetime import datetime, timezone
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from piptastic.models import (
    Dep,
    DepAudit,
    DepSource,
    PinStatus,
    Project,
    ProjectAudit,
    SemverDrift,
    SourceKind,
)
from piptastic.render.json_out import render_json


def _make_audit() -> ProjectAudit:
    src = DepSource(
        kind=SourceKind.REQUIREMENTS_TXT,
        path=Path("/projects/webapp/requirements.txt"),
        group="default",
    )
    dep = Dep(
        name="flask",
        raw_name="Flask",
        specifier=SpecifierSet("==3.0.2"),
        extras=frozenset(),
        marker=None,
        source=src,
        line_no=1,
        url=None,
    )
    project = Project(
        name="webapp",
        path=Path("/projects/webapp"),
        python_version="3.11",
        python_source="pyproject.toml",
        python_constraints=">=3.11",
        dep_sources=(src,),
    )
    audit = DepAudit(
        dep=dep,
        installed=Version("3.0.2"),
        latest=Version("3.1.0"),
        latest_including_prereleases=Version("3.1.0"),
        drift=SemverDrift.MINOR,
        pin_status=PinStatus.PINNED,
        yanked=False,
        warnings=(),
    )
    return ProjectAudit(
        project=project,
        deps=[audit],
        pinning_score=1.0,
        drift_summary={SemverDrift.MINOR: 1},
        yanked_count=0,
        pypi_unreachable=[],
    )


def test_render_json_schema_version():
    out = render_json([_make_audit()], root=Path("/projects"))
    parsed = json.loads(out)
    assert parsed["schema_version"] == 3


def test_render_json_project_shape():
    out = render_json([_make_audit()], root=Path("/projects"))
    parsed = json.loads(out)
    project = parsed["projects"][0]
    assert project["name"] == "webapp"
    assert project["pinning_score"] == 1.0
    assert project["drift_summary"]["minor"] == 1
    assert project["yanked_count"] == 0
    assert project["python"]["version"] == "3.11"
    assert project["python"]["source"] == "pyproject.toml"


def test_render_json_dep_shape():
    out = render_json([_make_audit()], root=Path("/projects"))
    parsed = json.loads(out)
    dep = parsed["projects"][0]["deps"][0]
    assert dep["name"] == "flask"
    assert dep["specifier"] == "==3.0.2"
    assert dep["pin_status"] == "pinned"
    assert dep["current"] == "3.0.2"
    assert dep["latest"] == "3.1.0"
    assert dep["drift"] == "minor"
    assert dep["yanked"] is False
    assert dep["warnings"] == []
    assert dep["direct"] is True  # default; lock transitive deps set this False


def test_render_json_marks_transitive_dep():
    import dataclasses
    base = _make_audit()
    transitive = dataclasses.replace(base.deps[0], dep=dataclasses.replace(base.deps[0].dep, direct=False))
    base.deps = [transitive]
    parsed = json.loads(render_json([base], root=Path("/projects")))
    assert parsed["projects"][0]["deps"][0]["direct"] is False


def test_render_json_handles_url_dep():
    src = DepSource(
        kind=SourceKind.REQUIREMENTS_TXT,
        path=Path("/p/requirements.txt"),
        group="default",
    )
    dep = Dep(
        name="repo",
        raw_name="repo",
        specifier=SpecifierSet(),
        extras=frozenset(),
        marker=None,
        source=src,
        line_no=1,
        url="git+https://example/repo",
    )
    audit = DepAudit(
        dep=dep,
        installed=None,
        latest=None,
        latest_including_prereleases=None,
        drift=SemverDrift.UNKNOWN,
        pin_status=PinStatus.URL,
        yanked=False,
        warnings=("VCS/URL requirement — version cannot be tracked",),
    )
    project = Project(
        name="p",
        path=Path("/p"),
        python_version=None, python_source=None, python_constraints=None,
        dep_sources=(src,),
    )
    pa = ProjectAudit(
        project=project, deps=[audit], pinning_score=None,
        drift_summary={}, yanked_count=0, pypi_unreachable=[],
    )
    out = render_json([pa], root=Path("/p"))
    parsed = json.loads(out)
    d = parsed["projects"][0]["deps"][0]
    assert d["pin_status"] == "url"
    assert d["url"] == "git+https://example/repo"
    assert d["latest"] is None
    assert d["current"] is None


def test_render_json_pinning_score_can_be_none():
    """A project with only URL deps has no pinning_score (URL is excluded
    from PIN_WEIGHTS). It must serialize as JSON null, not crash on round()."""
    src = DepSource(
        kind=SourceKind.REQUIREMENTS_TXT,
        path=Path("/p/requirements.txt"),
        group="default",
    )
    dep = Dep(
        name="repo", raw_name="repo", specifier=SpecifierSet(),
        extras=frozenset(), marker=None, source=src, line_no=1,
        url="git+https://example/repo",
    )
    audit = DepAudit(
        dep=dep, installed=None, latest=None, latest_including_prereleases=None,
        drift=SemverDrift.UNKNOWN, pin_status=PinStatus.URL,
        yanked=False, warnings=(),
    )
    project = Project(
        name="p", path=Path("/p"),
        python_version=None, python_source=None, python_constraints=None,
        dep_sources=(src,),
    )
    pa = ProjectAudit(
        project=project, deps=[audit], pinning_score=None,
        drift_summary={}, yanked_count=0, pypi_unreachable=[],
    )
    out = render_json([pa], root=Path("/p"))
    parsed = json.loads(out)
    assert parsed["projects"][0]["pinning_score"] is None


def test_render_terminal_does_not_raise(capsys):
    from rich.console import Console
    from piptastic.render import render_terminal

    audit = _make_audit()
    console = Console(record=True, no_color=True)
    render_terminal([audit], mode="tree", console=console)
    render_terminal([audit], mode="table", console=console)
    render_terminal([audit], mode="summary", console=console)
    # If we got here, no exception. Spot-check that project name appeared.
    output = console.export_text()
    assert "webapp" in output


def test_render_terminal_handles_none_pin_score(capsys):
    """A project with pinning_score=None must render as 'n/a' without crashing."""
    from rich.console import Console
    from pathlib import Path
    from packaging.specifiers import SpecifierSet
    from piptastic.models import (
        Dep, DepAudit, DepSource, PinStatus, Project, ProjectAudit,
        SemverDrift, SourceKind,
    )
    from piptastic.render import render_terminal

    src = DepSource(
        kind=SourceKind.REQUIREMENTS_TXT,
        path=Path("/p/requirements.txt"),
        group="default",
    )
    dep = Dep(
        name="repo", raw_name="repo", specifier=SpecifierSet(),
        extras=frozenset(), marker=None, source=src, line_no=1,
        url="git+https://example/repo",
    )
    audit = DepAudit(
        dep=dep, installed=None, latest=None,
        latest_including_prereleases=None,
        drift=SemverDrift.UNKNOWN, pin_status=PinStatus.URL,
        yanked=False, warnings=(),
    )
    project = Project(
        name="p", path=Path("/p"),
        python_version=None, python_source=None, python_constraints=None,
        dep_sources=(src,),
    )
    pa = ProjectAudit(
        project=project, deps=[audit], pinning_score=None,
        drift_summary={}, yanked_count=0, pypi_unreachable=[],
    )
    console = Console(record=True, no_color=True)
    # All three modes must handle None pin_score gracefully
    render_terminal([pa], mode="tree", console=console)
    render_terminal([pa], mode="table", console=console)
    render_terminal([pa], mode="summary", console=console)
    output = console.export_text()
    assert "n/a" in output


def test_render_terminal_empty():
    """Empty project list produces a 'No Python projects found.' message,
    not a crash."""
    from rich.console import Console
    from piptastic.render import render_terminal

    console = Console(record=True, no_color=True)
    render_terminal([], console=console)
    output = console.export_text()
    assert "No Python projects found" in output
