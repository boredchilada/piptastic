"""Tests for the terminal renderer: multi-project footer and summary columns."""

from __future__ import annotations

from pathlib import Path

from packaging.specifiers import SpecifierSet
from rich.console import Console

from piptastic.models import (
    Dep, DepAudit, DepSource, PinStatus, Project, ProjectAudit,
    SemverDrift, SourceKind,
)
from piptastic.render.terminal import render_terminal

SRC = DepSource(kind=SourceKind.REQUIREMENTS_TXT, path=Path("requirements.txt"), group="default")


def _dep(name: str, drift: SemverDrift) -> DepAudit:
    dep = Dep(name=name, raw_name=name, specifier=SpecifierSet("==1.0.0"),
              extras=frozenset(), marker=None, source=SRC, line_no=1, url=None)
    return DepAudit(
        dep=dep, installed=None, latest=None, latest_including_prereleases=None,
        drift=drift, pin_status=PinStatus.PINNED, yanked=False, warnings=(),
    )


def _project(name: str, deps, **kw) -> ProjectAudit:
    proj = Project(name=name, path=Path(f"/{name}"), python_version="3.11",
                   python_source=None, python_constraints=None, dep_sources=(SRC,))
    drift_summary: dict = {}
    for d in deps:
        drift_summary[d.drift] = drift_summary.get(d.drift, 0) + 1
    return ProjectAudit(project=proj, deps=list(deps), pinning_score=1.0,
                        drift_summary=drift_summary, **kw)


def _render(audits, mode):
    console = Console(record=True, width=200, force_terminal=False)
    render_terminal(audits, mode=mode, console=console)
    return console.export_text()


def test_footer_shown_for_multiple_projects():
    audits = [
        _project("a", [_dep("flask", SemverDrift.MAJOR)], vuln_count=2),
        _project("b", [_dep("requests", SemverDrift.NONE)], yanked_count=1),
    ]
    out = _render(audits, "summary")
    assert "2 projects" in out
    assert "2 deps" in out
    assert "2 CVEs" in out
    assert "1 yanked" in out


def test_footer_absent_for_single_project():
    out = _render([_project("solo", [_dep("flask", SemverDrift.MINOR)])], "table")
    # No "N projects" totals line for a single-project render.
    assert "1 projects" not in out


def test_summary_other_column_surfaces_build_and_epoch():
    audits = [
        _project("x", [_dep("a", SemverDrift.BUILD), _dep("b", SemverDrift.EPOCH)]),
        _project("y", [_dep("c", SemverDrift.NONE)]),
    ]
    out = _render(audits, "summary")
    assert "Other" in out  # column header present
    # project x has 1 build + 1 epoch == 2 in the Other column
    assert "2" in out
