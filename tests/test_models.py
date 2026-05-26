"""Tests for shared data models."""

from pathlib import Path

from packaging.markers import Marker
from packaging.specifiers import SpecifierSet

from piptastic.models import (
    Dep,
    DepSource,
    PinStatus,
    Project,
    SemverDrift,
    SourceKind,
)


def test_semver_drift_enum_values():
    assert SemverDrift.NONE.value == "none"
    assert SemverDrift.BUILD.value == "build"
    assert SemverDrift.PATCH.value == "patch"
    assert SemverDrift.MINOR.value == "minor"
    assert SemverDrift.MAJOR.value == "major"
    assert SemverDrift.EPOCH.value == "epoch"
    assert SemverDrift.UNKNOWN.value == "unknown"


def test_pin_status_enum_values():
    assert PinStatus.PINNED.value == "pinned"
    assert PinStatus.COMPATIBLE.value == "compatible"
    assert PinStatus.RANGE.value == "range"
    assert PinStatus.FLOOR.value == "floor"
    assert PinStatus.UNPINNED.value == "unpinned"
    assert PinStatus.URL.value == "url"


def test_dep_is_hashable():
    src = DepSource(
        kind=SourceKind.REQUIREMENTS_TXT,
        path=Path("requirements.txt"),
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
    # If anything in the dataclass is unhashable, this raises TypeError.
    {dep}


def test_dep_with_marker_and_extras():
    src = DepSource(
        kind=SourceKind.PYPROJECT_PEP621,
        path=Path("pyproject.toml"),
        group="default",
    )
    dep = Dep(
        name="httpx",
        raw_name="httpx",
        specifier=SpecifierSet(">=0.27"),
        extras=frozenset({"http2"}),
        marker=Marker('python_version >= "3.10"'),
        source=src,
        line_no=None,
        url=None,
    )
    assert "http2" in dep.extras
    assert dep.marker is not None


def test_project_dataclass():
    src = DepSource(
        kind=SourceKind.REQUIREMENTS_TXT,
        path=Path("requirements.txt"),
        group="default",
    )
    project = Project(
        name="webapp",
        path=Path("/projects/webapp"),
        python_version="3.11",
        python_source="pyproject.toml",
        python_constraints=">=3.11",
        dep_sources=(src,),
    )
    assert project.name == "webapp"
    assert project.dep_sources[0].kind == SourceKind.REQUIREMENTS_TXT
