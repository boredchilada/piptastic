"""Tests for cross-project stats aggregation."""

from datetime import datetime, timezone
from pathlib import Path

from piptastic.models import (
    PackageFrequency,
    PinStatus,
    SemverDrift,
    StatsReport,
    VersionFragmentation,
    YankedFinding,
)


def test_stats_dataclasses_construct():
    """Smoke test that the new dataclasses are importable + constructable."""
    pf = PackageFrequency(name="requests", project_count=3, projects=("a", "b", "c"))
    assert pf.project_count == 3

    vf = VersionFragmentation(name="jsonschema", versions={"4.21.0": ("a",), "4.25.1": ("b", "c")})
    assert len(vf.versions) == 2

    yf = YankedFinding(
        project_name="foo", project_path=Path("/foo"),
        package_name="python-levenshtein", pinned_version="0.12.0",
        latest_non_yanked="0.27.3",
    )
    assert yf.pinned_version == "0.12.0"

    report = StatsReport(
        scanned_at=datetime.now(timezone.utc),
        root=Path("/laboratory"),
        project_count=10, total_deps=100,
        drift_histogram={SemverDrift.MAJOR: 5},
        pin_status_histogram={PinStatus.PINNED: 50},
        top_packages=(pf,),
        version_fragmentation=(vf,),
        yanked_findings=(yf,),
        unpinned_projects=("project-a",),
    )
    assert report.project_count == 10
    assert report.top_packages[0].name == "requests"
