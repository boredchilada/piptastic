"""Cross-project aggregation over a list of ProjectAudits."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from piptastic.models import (
    PackageFrequency,
    PinStatus,
    ProjectAudit,
    SemverDrift,
    StatsReport,
    VersionFragmentation,
    YankedFinding,
)


_UNPINNED_DEP_THRESHOLD = 5


def compute_stats(
    audits: Iterable[ProjectAudit],
    *,
    top: int = 20,
    root: Path = Path("."),
) -> StatsReport:
    """Aggregate a list of ProjectAudits into a StatsReport."""
    audits = list(audits)

    # Per-package: which projects depend on it
    pkg_to_projects: dict[str, set[str]] = defaultdict(set)
    # Per-package: which == versions are pinned + which projects pin each
    pkg_to_versions: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))

    drift_counter: Counter = Counter()
    pin_counter: Counter = Counter()
    total_deps = 0
    yanked_findings: list[YankedFinding] = []
    unpinned_projects: list[str] = []

    for audit in audits:
        total_deps += len(audit.deps)
        for da in audit.deps:
            drift_counter[da.drift] += 1
            pin_counter[da.pin_status] += 1
            pkg_to_projects[da.dep.name].add(audit.project.name)

            # Capture == pin version for fragmentation analysis
            for clause in da.dep.specifier:
                if clause.operator == "==":
                    ver = clause.version
                    pkg_to_versions[da.dep.name][ver].append(audit.project.name)
                    break

            if da.yanked:
                # Find the == pin version to report
                pinned = ""
                for clause in da.dep.specifier:
                    if clause.operator == "==":
                        pinned = clause.version
                        break
                yanked_findings.append(YankedFinding(
                    project_name=audit.project.name,
                    project_path=audit.project.path,
                    package_name=da.dep.name,
                    pinned_version=pinned,
                    latest_non_yanked=str(da.latest) if da.latest else None,
                ))

        if (
            audit.pinning_score is not None
            and audit.pinning_score == 0.0
            and len(audit.deps) >= _UNPINNED_DEP_THRESHOLD
        ):
            unpinned_projects.append(audit.project.name)

    # Top packages: sort by count desc, then alphabetical
    pkg_freq_list = sorted(
        (
            PackageFrequency(
                name=name,
                project_count=len(projects),
                projects=tuple(sorted(projects)),
            )
            for name, projects in pkg_to_projects.items()
        ),
        key=lambda pf: (-pf.project_count, pf.name),
    )
    top_packages = tuple(pkg_freq_list[:top])

    # Fragmentation: keep packages with 2+ distinct versions
    fragmentation_list = []
    for name, versions in pkg_to_versions.items():
        if len(versions) < 2:
            continue
        fragmentation_list.append(VersionFragmentation(
            name=name,
            versions={v: tuple(sorted(projs)) for v, projs in versions.items()},
        ))
    fragmentation_list.sort(key=lambda vf: (-len(vf.versions), vf.name))
    version_fragmentation = tuple(fragmentation_list)

    yanked_findings.sort(key=lambda y: (y.project_name, y.package_name))
    unpinned_projects.sort()

    return StatsReport(
        scanned_at=datetime.now(timezone.utc),
        root=root,
        project_count=len(audits),
        total_deps=total_deps,
        drift_histogram=dict(drift_counter),
        pin_status_histogram=dict(pin_counter),
        top_packages=top_packages,
        version_fragmentation=version_fragmentation,
        yanked_findings=tuple(yanked_findings),
        unpinned_projects=tuple(unpinned_projects),
    )
