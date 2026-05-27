# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared data models. All dataclasses are frozen for hashability."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from packaging.markers import Marker
from packaging.specifiers import SpecifierSet
from packaging.version import Version


class SemverDrift(str, Enum):
    NONE = "none"
    BUILD = "build"
    PATCH = "patch"
    MINOR = "minor"
    MAJOR = "major"
    EPOCH = "epoch"
    UNKNOWN = "unknown"

    def __str__(self) -> str:
        return self.value


class PinStatus(str, Enum):
    PINNED = "pinned"
    COMPATIBLE = "compatible"
    RANGE = "range"
    FLOOR = "floor"
    UNPINNED = "unpinned"
    URL = "url"

    def __str__(self) -> str:
        return self.value


class SourceKind(str, Enum):
    REQUIREMENTS_TXT = "requirements_txt"
    CONSTRAINTS_TXT = "constraints_txt"
    PYPROJECT_PEP621 = "pyproject_pep621"
    PYPROJECT_POETRY = "pyproject_poetry"
    PIPFILE = "pipfile"
    PIPFILE_LOCK = "pipfile_lock"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class DepSource:
    kind: SourceKind
    path: Path
    group: str


@dataclass(frozen=True)
class Dep:
    name: str
    raw_name: str
    specifier: SpecifierSet
    extras: frozenset[str]
    marker: Marker | None
    source: DepSource
    line_no: int | None
    url: str | None


@dataclass(frozen=True)
class Project:
    name: str
    path: Path
    python_version: str | None
    python_source: str | None
    python_constraints: str | None
    dep_sources: tuple[DepSource, ...]


@dataclass(frozen=True)
class ReleaseInfo:
    version: Version
    yanked: bool
    yanked_reason: str | None
    requires_python: SpecifierSet | None
    upload_time: datetime | None


@dataclass(frozen=True)
class PackageMetadata:
    name: str
    releases: tuple[ReleaseInfo, ...]
    fetched_at: datetime


@dataclass(frozen=True)
class DepAudit:
    dep: Dep
    installed: Version | None
    latest: Version | None
    latest_including_prereleases: Version | None
    drift: SemverDrift
    pin_status: PinStatus
    yanked: bool
    warnings: tuple[str, ...]


@dataclass
class ProjectAudit:
    project: Project
    deps: list[DepAudit]
    pinning_score: float | None
    drift_summary: dict[SemverDrift, int] = field(default_factory=dict)
    yanked_count: int = 0
    pypi_unreachable: list[str] = field(default_factory=list)


# ---------- stats ----------

@dataclass(frozen=True)
class PackageFrequency:
    name: str
    project_count: int
    projects: tuple[str, ...]


@dataclass(frozen=True)
class VersionFragmentation:
    name: str
    # version_str -> tuple of project names that pin to that version
    versions: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class YankedFinding:
    project_name: str
    project_path: Path
    package_name: str
    pinned_version: str
    latest_non_yanked: str | None


@dataclass(frozen=True)
class StatsReport:
    scanned_at: datetime
    root: Path
    project_count: int
    total_deps: int
    drift_histogram: dict[SemverDrift, int]
    pin_status_histogram: dict[PinStatus, int]
    top_packages: tuple[PackageFrequency, ...]
    version_fragmentation: tuple[VersionFragmentation, ...]
    yanked_findings: tuple[YankedFinding, ...]
    unpinned_projects: tuple[str, ...]
