"""Tests for drift classification, pin posture, and project rollup."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from packaging.markers import Marker
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from piptastic.analysis import (
    audit_project,
    classify_drift,
    classify_pin_status,
)
from piptastic.models import (
    Dep,
    DepSource,
    PackageMetadata,
    PinStatus,
    Project,
    ReleaseInfo,
    SemverDrift,
    SourceKind,
)


# ---------- drift classifier ----------

@pytest.mark.parametrize("current,latest,expected", [
    ("1.2.3", "1.2.3", SemverDrift.NONE),
    ("1.2.3", "1.2.3.post1", SemverDrift.BUILD),
    ("1.2.3", "1.2.3+local", SemverDrift.BUILD),
    ("1.2.3", "1.2.4", SemverDrift.PATCH),
    ("1.2.3", "1.3.0", SemverDrift.MINOR),
    ("1.2.3", "2.0.0", SemverDrift.MAJOR),
    ("1!1.2.3", "2!1.2.3", SemverDrift.EPOCH),
])
def test_classify_drift_known_cases(current, latest, expected):
    assert classify_drift(Version(current), Version(latest)) is expected


def test_classify_drift_none_for_both():
    assert classify_drift(None, None) is SemverDrift.UNKNOWN


# ---------- pin status classifier ----------

@pytest.mark.parametrize("spec_str,expected", [
    ("==1.2.3", PinStatus.PINNED),
    ("~=1.2", PinStatus.COMPATIBLE),
    ("==1.2.*", PinStatus.COMPATIBLE),
    (">=1.2,<2", PinStatus.RANGE),
    (">=1.2", PinStatus.FLOOR),
    ("", PinStatus.UNPINNED),
])
def test_classify_pin_status(spec_str, expected):
    spec = SpecifierSet(spec_str)
    assert classify_pin_status(spec, url=None) is expected


def test_classify_pin_status_url_overrides_spec():
    assert classify_pin_status(SpecifierSet(), url="git+https://x") is PinStatus.URL


# ---------- audit_project (integration with a fake client) ----------

class FakeClient:
    def __init__(self, metadata: dict[str, PackageMetadata]):
        self._md = metadata

    def fetch_many(self, names):
        return {n: self._md[n] for n in names if n in self._md}


def _md(name: str, versions: dict[str, dict]) -> PackageMetadata:
    releases = []
    for v, info in versions.items():
        upload = info.get("upload")
        if isinstance(upload, str):
            upload = datetime.fromisoformat(upload)
        releases.append(ReleaseInfo(
            version=Version(v),
            yanked=info.get("yanked", False),
            yanked_reason=info.get("yanked_reason"),
            requires_python=SpecifierSet(info["rp"]) if "rp" in info else None,
            upload_time=upload,
        ))
    return PackageMetadata(name=name, releases=tuple(releases), fetched_at=datetime.now(timezone.utc))


def _project_with_deps(deps_specs: list[tuple[str, str]]) -> Project:
    src = DepSource(kind=SourceKind.REQUIREMENTS_TXT, path=Path("/x/requirements.txt"), group="default")
    return Project(
        name="x",
        path=Path("/x"),
        python_version="3.11",
        python_source=None,
        python_constraints=None,
        dep_sources=(src,),
    )


def _dep(name: str, spec: str) -> Dep:
    src = DepSource(kind=SourceKind.REQUIREMENTS_TXT, path=Path("/x/requirements.txt"), group="default")
    return Dep(
        name=name,
        raw_name=name,
        specifier=SpecifierSet(spec) if spec else SpecifierSet(),
        extras=frozenset(),
        marker=None,
        source=src,
        line_no=1,
        url=None,
    )


def test_audit_project_classifies_each_dep(monkeypatch):
    project = _project_with_deps([])
    deps = [
        _dep("flask", "==3.0.2"),
        _dep("requests", ">=2.30"),
        _dep("unpinned", ""),
    ]
    md = {
        "flask": _md("flask", {"3.0.2": {"rp": ">=3.8"}, "3.1.0": {"rp": ">=3.9"}}),
        "requests": _md("requests", {"2.32.2": {"rp": ">=3.8"}, "2.32.3": {"rp": ">=3.8"}}),
        "unpinned": _md("unpinned", {"1.0.0": {"rp": ">=3.8"}}),
    }
    client = FakeClient(md)

    from piptastic import analysis
    # Inject `deps` by monkeypatching the parse step used inside audit_project.
    monkeypatch.setattr(analysis, "_collect_deps", lambda project: deps)

    report = audit_project(project, client, current_python=Version("3.11"))
    by_name = {d.dep.name: d for d in report.deps}
    assert by_name["flask"].drift is SemverDrift.MINOR
    assert by_name["flask"].pin_status is PinStatus.PINNED
    assert by_name["requests"].pin_status is PinStatus.FLOOR
    assert by_name["unpinned"].pin_status is PinStatus.UNPINNED


def test_audit_project_skips_yanked_for_latest(monkeypatch):
    project = _project_with_deps([])
    deps = [_dep("flask", "==3.0.0")]
    md = {
        "flask": _md("flask", {
            "3.0.0": {"rp": ">=3.8"},
            "3.0.1": {"rp": ">=3.8", "yanked": True},
            "3.0.2": {"rp": ">=3.8"},
        }),
    }
    from piptastic import analysis
    monkeypatch.setattr(analysis, "_collect_deps", lambda project: deps)
    report = audit_project(project, FakeClient(md), current_python=Version("3.11"))
    assert str(report.deps[0].latest) == "3.0.2"


def test_audit_project_skips_incompatible_python(monkeypatch):
    project = _project_with_deps([])
    deps = [_dep("flask", "==3.0.2")]
    md = {
        "flask": _md("flask", {
            "3.0.2": {"rp": ">=3.8"},
            "3.1.0": {"rp": ">=3.12"},  # requires newer python than target
        }),
    }
    from piptastic import analysis
    monkeypatch.setattr(analysis, "_collect_deps", lambda project: deps)
    report = audit_project(project, FakeClient(md), current_python=Version("3.11"))
    assert str(report.deps[0].latest) == "3.0.2"


def test_audit_project_attaches_latest_release_date(monkeypatch):
    """latest_release_date must be the upload_time of the *selected latest*
    version, not an older release — verifies the _upload_time_for linkage that
    feeds the Age column and --fail-on-age."""
    project = _project_with_deps([])
    deps = [_dep("flask", "==3.0.2")]
    md = {
        "flask": _md("flask", {
            "3.0.2": {"rp": ">=3.8", "upload": "2023-01-01T00:00:00+00:00"},
            "3.1.0": {"rp": ">=3.8", "upload": "2024-10-10T00:00:00+00:00"},
        }),
    }
    from piptastic import analysis
    monkeypatch.setattr(analysis, "_collect_deps", lambda project: deps)
    report = audit_project(project, FakeClient(md), current_python=Version("3.11"))
    da = report.deps[0]
    assert str(da.latest) == "3.1.0"
    # The date is 3.1.0's, NOT the older 3.0.2 pin's.
    assert da.latest_release_date == datetime(2024, 10, 10, tzinfo=timezone.utc)


def test_audit_project_latest_release_date_none_when_unknown(monkeypatch):
    """No upload_time on the latest version -> latest_release_date is None
    (so the age gate fails open rather than tripping on missing data)."""
    project = _project_with_deps([])
    deps = [_dep("flask", "==3.0.2")]
    md = {"flask": _md("flask", {"3.0.2": {"rp": ">=3.8"}, "3.1.0": {"rp": ">=3.8"}})}
    from piptastic import analysis
    monkeypatch.setattr(analysis, "_collect_deps", lambda project: deps)
    report = audit_project(project, FakeClient(md), current_python=Version("3.11"))
    assert report.deps[0].latest_release_date is None


def test_audit_project_pinning_score(monkeypatch):
    project = _project_with_deps([])
    deps = [
        _dep("a", "==1.0.0"),  # PINNED 1.0
        _dep("b", "~=1.0"),    # COMPATIBLE 0.8
        _dep("c", ">=1.0,<2"), # RANGE 0.6
        _dep("d", ">=1.0"),    # FLOOR 0.3
        _dep("e", ""),         # UNPINNED 0.0
    ]
    md = {
        n: _md(n, {"1.0.0": {"rp": ">=3.8"}}) for n in ["a", "b", "c", "d", "e"]
    }
    from piptastic import analysis
    monkeypatch.setattr(analysis, "_collect_deps", lambda project: deps)
    report = audit_project(project, FakeClient(md), current_python=Version("3.11"))
    expected = (1.0 + 0.8 + 0.6 + 0.3 + 0.0) / 5
    assert abs(report.pinning_score - expected) < 1e-9
