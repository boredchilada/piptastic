# SPDX-License-Identifier: AGPL-3.0-or-later
"""Drift classification, pin posture, and per-project audit rollup."""

from __future__ import annotations

import importlib.metadata
from collections import Counter
from typing import Iterable, Protocol

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

from piptastic.logging import get_logger
from piptastic.models import (
    Dep,
    DepAudit,
    PackageMetadata,
    PinStatus,
    Project,
    ProjectAudit,
    ReleaseInfo,
    SemverDrift,
    Vulnerability,
)
from piptastic.parsing import parse_source
from piptastic.vulns import compute_min_safe_version

logger = get_logger(__name__)


PIN_WEIGHTS = {
    PinStatus.PINNED: 1.0,
    PinStatus.COMPATIBLE: 0.8,
    PinStatus.RANGE: 0.6,
    PinStatus.FLOOR: 0.3,
    PinStatus.UNPINNED: 0.0,
    # URL excluded from the average
}


class _MetadataSource(Protocol):
    def fetch_many(self, names: Iterable[str]) -> dict[str, PackageMetadata]: ...


class _VulnSource(Protocol):
    unreachable: list[str]

    def fetch_for(
        self, pkgs: Iterable[tuple[str, "Version"]]
    ) -> dict[tuple[str, str], tuple[Vulnerability, ...]]: ...


# ---------- drift ----------

def classify_drift(current: Version | None, latest: Version | None) -> SemverDrift:
    if current is None or latest is None:
        return SemverDrift.UNKNOWN
    if current == latest:
        return SemverDrift.NONE
    if current.epoch != latest.epoch:
        return SemverDrift.EPOCH

    c_release = current.release
    l_release = latest.release
    # Pad to length 3 for comparison
    def _pad3(t: tuple[int, ...]) -> tuple[int, int, int]:
        return (t + (0, 0, 0))[:3]
    c_maj, c_min, c_mic = _pad3(c_release)
    l_maj, l_min, l_mic = _pad3(l_release)

    if c_maj != l_maj:
        return SemverDrift.MAJOR
    if c_min != l_min:
        return SemverDrift.MINOR
    if c_mic != l_mic:
        return SemverDrift.PATCH
    # Release tuples match — difference must be in post/dev/local/build
    return SemverDrift.BUILD


# ---------- pin posture ----------

def classify_pin_status(spec: SpecifierSet, *, url: str | None) -> PinStatus:
    if url:
        return PinStatus.URL
    clauses = list(spec)
    if not clauses:
        return PinStatus.UNPINNED

    operators = [c.operator for c in clauses]
    versions = [c.version for c in clauses]

    # Single == X.Y.Z or === X.Y.Z (=== is exact-string match, even more strictly pinned)
    if len(clauses) == 1 and operators[0] in ("==", "==="):
        if versions[0].endswith(".*"):
            return PinStatus.COMPATIBLE
        return PinStatus.PINNED

    if any(op == "~=" for op in operators):
        return PinStatus.COMPATIBLE

    has_lower = any(op in (">=", ">") for op in operators)
    has_upper = any(op in ("<=", "<") for op in operators)
    if has_lower and has_upper:
        return PinStatus.RANGE
    if has_lower:
        return PinStatus.FLOOR
    return PinStatus.UNPINNED


# ---------- audit ----------

def audit_project(
    project: Project,
    client: _MetadataSource,
    current_python: Version,
    *,
    include_prereleases: bool = False,
    vuln_client: _VulnSource | None = None,
) -> ProjectAudit:
    deps = _collect_deps(project)
    target_python = _project_target_python(project, current_python)

    names = sorted({d.name for d in deps if d.url is None})
    metadata = client.fetch_many(names) if names else {}
    unreachable = [n for n in names if n not in metadata]

    # Resolve a current version per dep first (needed for both drift and vuln lookup).
    installed_by_name: dict[str, Version | None] = {}
    current_for_drift_by_id: dict[int, Version | None] = {}
    for dep in deps:
        installed = _installed_version(dep.name)
        installed_by_name[dep.name] = installed
        current_for_drift_by_id[id(dep)] = _current_version_for_drift(dep, installed)

    # Build the (name, version) set for the vuln lookup. Prefer the version
    # used for drift (i.e. the == pin if any, else the locally-installed
    # version). Skip URL deps and deps with no resolvable version.
    vuln_results: dict[tuple[str, str], tuple[Vulnerability, ...]] = {}
    vuln_unreachable: list[str] = []
    if vuln_client is not None:
        pairs: list[tuple[str, Version]] = []
        for dep in deps:
            if dep.url is not None:
                continue
            v = current_for_drift_by_id[id(dep)]
            if v is None:
                continue
            pairs.append((dep.name, v))
        if pairs:
            vuln_results = vuln_client.fetch_for(pairs)
            vuln_unreachable = list(vuln_client.unreachable)

    audits: list[DepAudit] = []
    for dep in deps:
        installed = installed_by_name[dep.name]
        latest, latest_pre = _pick_latest(
            metadata.get(dep.name), target_python=target_python
        )
        # If the user asked for prereleases to count as "latest", swap them in.
        effective_latest = latest_pre if include_prereleases else latest
        current_for_drift = current_for_drift_by_id[id(dep)]
        drift = classify_drift(current_for_drift, effective_latest)
        pin = classify_pin_status(dep.specifier, url=dep.url)
        warnings: list[str] = []
        if dep.url:
            warnings.append("VCS/URL requirement — version cannot be tracked")
        if dep.name in unreachable:
            warnings.append("PyPI metadata unavailable")
        if pin is PinStatus.UNPINNED and installed is None:
            warnings.append("unpinned and not installed in current environment")

        yanked = _is_pinned_version_yanked(dep, metadata.get(dep.name))

        vulns: tuple[Vulnerability, ...] = ()
        min_safe: Version | None = None
        if current_for_drift is not None and dep.url is None:
            vulns = vuln_results.get((dep.name, str(current_for_drift)), ())
            if vulns:
                min_safe = compute_min_safe_version(current_for_drift, vulns)
                ids = ", ".join(v.id for v in vulns[:3])
                if len(vulns) > 3:
                    ids += f", +{len(vulns) - 3} more"
                warnings.append(f"{len(vulns)} vulnerability(ies): {ids}")

        audits.append(DepAudit(
            dep=dep,
            installed=installed,
            latest=effective_latest,
            latest_including_prereleases=latest_pre,
            drift=drift,
            pin_status=pin,
            yanked=yanked,
            warnings=tuple(warnings),
            vulnerabilities=vulns,
            min_safe_version=min_safe,
        ))

    score = _pinning_score(audits)
    drift_summary = dict(Counter(a.drift for a in audits))
    yanked_count = sum(1 for a in audits if a.yanked)
    vuln_count = sum(len(a.vulnerabilities) for a in audits)

    return ProjectAudit(
        project=project,
        deps=audits,
        pinning_score=score,
        drift_summary=drift_summary,
        yanked_count=yanked_count,
        pypi_unreachable=unreachable,
        vuln_count=vuln_count,
        vuln_unreachable=vuln_unreachable,
    )


# ---------- internals ----------

def _collect_deps(project: Project) -> list[Dep]:
    out: list[Dep] = []
    for src in project.dep_sources:
        try:
            out.extend(parse_source(src))
        except Exception as e:
            # Never let a single malformed source file kill the whole audit.
            logger.warning("failed to parse %s (%s): %s", src.path, src.kind.value, e)
    return out


def _project_target_python(project: Project, current: Version) -> Version:
    if project.python_version:
        try:
            return Version(project.python_version)
        except InvalidVersion:
            pass
    return current


def _installed_version(name: str) -> Version | None:
    try:
        raw = importlib.metadata.version(name)
        return Version(raw)
    except importlib.metadata.PackageNotFoundError:
        return None
    except InvalidVersion:
        return None


def _current_version_for_drift(dep: Dep, installed: Version | None) -> Version | None:
    """Pick the 'current' version to compare against latest for drift."""
    for clause in dep.specifier:
        if clause.operator == "==":
            v = clause.version.rstrip(".*")
            try:
                return Version(v)
            except InvalidVersion:
                return None
    return installed


def _pick_latest(
    md: PackageMetadata | None,
    *,
    target_python: Version,
) -> tuple[Version | None, Version | None]:
    if md is None:
        return None, None

    stable: list[Version] = []
    with_pre: list[Version] = []
    for r in md.releases:
        if r.yanked:
            continue
        if r.requires_python and not r.requires_python.contains(str(target_python), prereleases=True):
            continue
        with_pre.append(r.version)
        if not r.version.is_prerelease:
            stable.append(r.version)

    latest = max(stable) if stable else None
    latest_pre = max(with_pre) if with_pre else None
    return latest, latest_pre


def _is_pinned_version_yanked(dep: Dep, md: PackageMetadata | None) -> bool:
    if md is None:
        return False
    pinned_str = None
    for clause in dep.specifier:
        if clause.operator == "==":
            pinned_str = clause.version.rstrip(".*")
            break
    if pinned_str is None:
        return False
    try:
        pinned = Version(pinned_str)
    except InvalidVersion:
        return False
    for r in md.releases:
        if r.version == pinned:
            return r.yanked
    return False


def _pinning_score(audits: list[DepAudit]) -> float | None:
    """Weighted mean of PIN_WEIGHTS across all deps; None if no deps have a
    weight (e.g. project with only URL deps, which are deliberately excluded
    from PIN_WEIGHTS because URL pinning posture is fuzzy without parsing the
    ref). Render layer should show 'n/a' for None instead of '0%'."""
    scored = [PIN_WEIGHTS[a.pin_status] for a in audits if a.pin_status in PIN_WEIGHTS]
    if not scored:
        return None
    return sum(scored) / len(scored)
