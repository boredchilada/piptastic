"""Tests for cross-project stats aggregation."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from piptastic.models import (
    Dep,
    DepAudit,
    DepSource,
    PackageFrequency,
    PinStatus,
    PinStatus as PS,
    Project,
    ProjectAudit,
    SemverDrift,
    SemverDrift as SD,
    SourceKind,
    StatsReport,
    VersionFragmentation,
    YankedFinding,
)
from piptastic.stats import compute_stats


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


def _make_dep(name, spec="", url=None):
    src = DepSource(
        kind=SourceKind.REQUIREMENTS_TXT,
        path=Path(f"/dummy/requirements.txt"),
        group="default",
    )
    return Dep(
        name=name, raw_name=name,
        specifier=SpecifierSet(spec) if spec else SpecifierSet(),
        extras=frozenset(), marker=None, source=src,
        line_no=1, url=url,
    )


def _make_audit(project_name, deps_data):
    """deps_data is a list of (name, spec, pin_status, drift, yanked, latest)."""
    project = Project(
        name=project_name, path=Path(f"/{project_name}"),
        python_version=None, python_source=None, python_constraints=None,
        dep_sources=(),
    )
    deps = []
    drift_counter = {}
    yanked_count = 0
    for name, spec, pin, drift, yanked, latest in deps_data:
        dep = _make_dep(name, spec)
        deps.append(DepAudit(
            dep=dep, installed=None,
            latest=Version(latest) if latest else None,
            latest_including_prereleases=None,
            drift=drift, pin_status=pin, yanked=yanked, warnings=(),
        ))
        drift_counter[drift] = drift_counter.get(drift, 0) + 1
        if yanked:
            yanked_count += 1
    pin_score = 1.0 if all(d[2] == PS.PINNED for d in deps_data) else 0.0
    return ProjectAudit(
        project=project, deps=deps, pinning_score=pin_score,
        drift_summary=drift_counter, yanked_count=yanked_count,
        pypi_unreachable=[],
    )


def test_compute_stats_top_packages_sorted_by_count_then_alpha():
    audits = [
        _make_audit("a", [("requests", "==2.32.2", PS.PINNED, SD.NONE, False, "2.32.2")]),
        _make_audit("b", [("requests", "==2.32.2", PS.PINNED, SD.NONE, False, "2.32.2"),
                          ("flask", "==3.0.2", PS.PINNED, SD.NONE, False, "3.0.2")]),
        _make_audit("c", [("requests", "==2.32.2", PS.PINNED, SD.NONE, False, "2.32.2"),
                          ("aaa", "==1.0", PS.PINNED, SD.NONE, False, "1.0")]),
    ]
    report = compute_stats(audits, top=10)
    names = [p.name for p in report.top_packages]
    # requests appears in 3 projects, flask + aaa in 1 each.
    # Tie-break: alphabetical, so aaa before flask.
    assert names == ["requests", "aaa", "flask"]
    assert report.top_packages[0].project_count == 3
    assert report.top_packages[0].projects == ("a", "b", "c")


def test_compute_stats_top_n_limit():
    audits = [_make_audit("p", [(f"pkg{i}", "==1.0", PS.PINNED, SD.NONE, False, "1.0") for i in range(50)])]
    report = compute_stats(audits, top=5)
    assert len(report.top_packages) == 5


def test_compute_stats_version_fragmentation_only_multi_version():
    audits = [
        _make_audit("a", [("jsonschema", "==4.21.0", PS.PINNED, SD.NONE, False, "4.21.0")]),
        _make_audit("b", [("jsonschema", "==4.25.1", PS.PINNED, SD.NONE, False, "4.25.1")]),
        _make_audit("c", [("jsonschema", "==4.21.0", PS.PINNED, SD.NONE, False, "4.21.0")]),
        _make_audit("d", [("flask", "==3.0.2", PS.PINNED, SD.NONE, False, "3.0.2")]),
        _make_audit("e", [("flask", "==3.0.2", PS.PINNED, SD.NONE, False, "3.0.2")]),
    ]
    report = compute_stats(audits, top=10)
    # jsonschema has 2 distinct versions; flask has only 1 (so NOT fragmented)
    frag_names = [v.name for v in report.version_fragmentation]
    assert "jsonschema" in frag_names
    assert "flask" not in frag_names
    js = next(v for v in report.version_fragmentation if v.name == "jsonschema")
    assert set(js.versions.keys()) == {"4.21.0", "4.25.1"}
    assert js.versions["4.21.0"] == ("a", "c")
    assert js.versions["4.25.1"] == ("b",)


def test_compute_stats_yanked_findings():
    audits = [
        _make_audit("phishing_catcher", [
            ("python-levenshtein", "==0.12.0", PS.PINNED, SD.MAJOR, True, "0.27.3"),
        ]),
    ]
    report = compute_stats(audits, top=10)
    assert len(report.yanked_findings) == 1
    f = report.yanked_findings[0]
    assert f.project_name == "phishing_catcher"
    assert f.package_name == "python-levenshtein"
    assert f.pinned_version == "0.12.0"
    assert f.latest_non_yanked == "0.27.3"


def test_compute_stats_unpinned_projects_threshold():
    """0% pin score + >=5 deps qualifies; <5 deps does not."""
    big_unpinned = _make_audit("big", [(f"x{i}", "", PS.UNPINNED, SD.UNKNOWN, False, None) for i in range(6)])
    small_unpinned = _make_audit("small", [("y", "", PS.UNPINNED, SD.UNKNOWN, False, None)])
    audits = [big_unpinned, small_unpinned]
    report = compute_stats(audits, top=10)
    assert "big" in report.unpinned_projects
    assert "small" not in report.unpinned_projects


def test_compute_stats_drift_histogram_sums():
    audits = [
        _make_audit("a", [("p", "==1.0", PS.PINNED, SD.MAJOR, False, "2.0"),
                          ("q", "==1.0", PS.PINNED, SD.MINOR, False, "1.1")]),
        _make_audit("b", [("r", "==1.0", PS.PINNED, SD.MAJOR, False, "2.0")]),
    ]
    report = compute_stats(audits, top=10)
    assert report.drift_histogram.get(SD.MAJOR) == 2
    assert report.drift_histogram.get(SD.MINOR) == 1
    assert report.total_deps == 3
    assert report.project_count == 2


def test_compute_stats_empty_input():
    report = compute_stats([], top=10)
    assert report.project_count == 0
    assert report.total_deps == 0
    assert report.top_packages == ()
    assert report.version_fragmentation == ()
    assert report.yanked_findings == ()
    assert report.unpinned_projects == ()


# ---------- JSON renderer ----------

import json
from piptastic.render.json_out import render_stats_json


def test_render_stats_json_schema_and_kind():
    audits = [_make_audit("a", [("requests", "==2.32.2", PS.PINNED, SD.NONE, False, "2.32.2")])]
    report = compute_stats(audits, top=5, root=Path("/lab"))
    out = render_stats_json(report)
    parsed = json.loads(out)
    assert parsed["schema_version"] == 2
    assert parsed["kind"] == "stats"
    # str(Path("/lab")) is OS-native — use as_posix for portable comparison.
    assert parsed["root"] == Path("/lab").as_posix() or parsed["root"] == str(Path("/lab"))
    assert parsed["totals"]["project_count"] == 1
    assert parsed["totals"]["total_deps"] == 1


def test_render_stats_json_full_shape():
    audits = [
        _make_audit("phishing_catcher", [
            ("python-levenshtein", "==0.12.0", PS.PINNED, SD.MAJOR, True, "0.27.3"),
            ("flask", "==3.0.2", PS.PINNED, SD.MAJOR, False, "3.1.0"),
        ]),
        _make_audit("other", [
            ("python-levenshtein", "==0.27.0", PS.PINNED, SD.PATCH, False, "0.27.3"),
        ]),
    ]
    report = compute_stats(audits, top=5, root=Path("/lab"))
    out = render_stats_json(report)
    parsed = json.loads(out)

    # Histograms present
    assert parsed["totals"]["drift_histogram"]["major"] == 2
    assert parsed["totals"]["pin_status_histogram"]["pinned"] == 3

    # Top packages: python-levenshtein appears in 2 projects, flask in 1
    top_names = [p["name"] for p in parsed["top_packages"]]
    assert top_names[0] == "python-levenshtein"

    # Version fragmentation: python-levenshtein has 2 distinct == pins
    frag_names = [v["name"] for v in parsed["version_fragmentation"]]
    assert "python-levenshtein" in frag_names

    # Yanked findings preserve all 5 fields
    assert len(parsed["yanked_findings"]) == 1
    yf = parsed["yanked_findings"][0]
    assert yf["project_name"] == "phishing_catcher"
    assert yf["package_name"] == "python-levenshtein"
    assert yf["pinned_version"] == "0.12.0"
    assert yf["latest_non_yanked"] == "0.27.3"


# ---------- Terminal renderer ----------

from rich.console import Console
from piptastic.render.terminal import render_stats_terminal, _make_console


def test_make_console_safe_box_for_non_utf8(monkeypatch):
    """When stdout encoding isn't UTF-8, the helper should pick safe_box=True."""
    # Force the helper's check to think we're on cp1252
    import piptastic.render.terminal as term_mod
    monkeypatch.setattr(term_mod, "_stdout_is_utf8", lambda: False)
    c = _make_console()
    # rich.Console exposes safe_box as an instance attribute
    assert c.safe_box is True


def test_make_console_no_safe_box_for_utf8(monkeypatch):
    import piptastic.render.terminal as term_mod
    monkeypatch.setattr(term_mod, "_stdout_is_utf8", lambda: True)
    c = _make_console()
    assert c.safe_box is False


def test_render_stats_terminal_does_not_raise():
    audits = [
        _make_audit("a", [
            ("requests", "==2.32.2", PS.PINNED, SD.MAJOR, False, "3.0.0"),
        ]),
        _make_audit("b", [
            ("requests", "==2.32.2", PS.PINNED, SD.MAJOR, False, "3.0.0"),
            ("ynl", "==4.*", PS.PINNED, SD.UNKNOWN, True, "5.0.0"),
        ]),
    ]
    report = compute_stats(audits, top=5, root=Path("/lab"))
    console = Console(record=True, no_color=True, safe_box=True)
    render_stats_terminal(report, console=console)
    text = console.export_text()
    assert "requests" in text
    assert "ynl" in text  # yanked finding


def test_render_stats_terminal_empty():
    report = compute_stats([], top=5, root=Path("/lab"))
    console = Console(record=True, no_color=True, safe_box=True)
    render_stats_terminal(report, console=console)
    text = console.export_text()
    # Smoke: must mention zero projects without crashing
    assert "0" in text
