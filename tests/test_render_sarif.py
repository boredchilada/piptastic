"""Tests for the SARIF renderer."""

from __future__ import annotations

import json
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from piptastic.models import (
    Dep, DepAudit, DepSource, PinStatus, Project, ProjectAudit,
    SemverDrift, SourceKind, Vulnerability,
)
from piptastic.render.sarif import render_sarif


def _basic_dep(name: str, version: str, *, vulns=()) -> DepAudit:
    src = DepSource(kind=SourceKind.REQUIREMENTS_TXT, path=Path("requirements.txt"), group="default")
    dep = Dep(
        name=name, raw_name=name,
        specifier=SpecifierSet(f"=={version}"),
        extras=frozenset(), marker=None,
        source=src, line_no=1, url=None,
    )
    min_safe = None
    if vulns:
        fixes = [f for v in vulns for f in v.fix_versions if str(f) > version]
        if fixes:
            min_safe = max(fixes)
    return DepAudit(
        dep=dep, installed=Version(version), latest=Version(version),
        latest_including_prereleases=Version(version),
        drift=SemverDrift.NONE, pin_status=PinStatus.PINNED, yanked=False,
        warnings=(), vulnerabilities=tuple(vulns), min_safe_version=min_safe,
    )


def _project_audit(deps, *, vuln_unreachable=()) -> ProjectAudit:
    src = DepSource(kind=SourceKind.REQUIREMENTS_TXT, path=Path("requirements.txt"), group="default")
    proj = Project(
        name="p", path=Path("/p"),
        python_version=None, python_source=None, python_constraints=None,
        dep_sources=(src,),
    )
    return ProjectAudit(
        project=proj, deps=deps, pinning_score=1.0,
        vuln_count=sum(1 for d in deps for v in d.vulnerabilities if not v.suppressed),
        vuln_unreachable=list(vuln_unreachable),
        suppressed_count=sum(1 for d in deps for v in d.vulnerabilities if v.suppressed),
    )


def test_sarif_basic_shape():
    v = Vulnerability(
        id="CVE-2024-1", aliases=("GHSA-xxxx-yyyy-zzzz",),
        fix_versions=(Version("2.0.1"),), description="bad bug",
    )
    pa = _project_audit([_basic_dep("flask", "2.0.0", vulns=[v])])
    out = render_sarif([pa], root=Path("/p"))
    sarif = json.loads(out)

    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["tool"]["driver"]["name"] == "piptastic"
    rules = sarif["runs"][0]["tool"]["driver"]["rules"]
    assert any(r["id"] == "CVE-2024-1" for r in rules)
    results = sarif["runs"][0]["results"]
    assert any(r["ruleId"] == "CVE-2024-1" for r in results)
    # The flask result must have a `fixes[]` since a fix version is known.
    flask_result = next(r for r in results if r["ruleId"] == "CVE-2024-1")
    assert "fixes" in flask_result
    assert "2.0.1" in flask_result["fixes"][0]["description"]["text"]


def test_sarif_suppressed_cve_emits_suppressions_block():
    v = Vulnerability(
        id="CVE-S", aliases=(),
        fix_versions=(Version("2.0.1"),), description="accepted",
        suppressed=True, suppression_reason="not exposed",
        suppression_expires="2099-01-01",
    )
    pa = _project_audit([_basic_dep("flask", "2.0.0", vulns=[v])])
    out = render_sarif([pa], root=Path("/p"))
    sarif = json.loads(out)
    result = sarif["runs"][0]["results"][0]
    assert "suppressions" in result
    assert result["suppressions"][0]["kind"] == "external"
    # Suppressed vulns don't emit `fixes` (we're explicitly not bumping).
    assert "fixes" not in result


def test_sarif_vuln_unreachable_becomes_note_result():
    pa = _project_audit([_basic_dep("flask", "2.0.0")], vuln_unreachable=["flask"])
    out = render_sarif([pa], root=Path("/p"))
    sarif = json.loads(out)
    results = sarif["runs"][0]["results"]
    unreachable = [r for r in results if r["ruleId"] == "piptastic.unreachable"]
    assert len(unreachable) == 1
    assert unreachable[0]["level"] == "note"


def test_sarif_empty_audit_still_valid():
    out = render_sarif([], root=Path("/p"))
    sarif = json.loads(out)
    assert sarif["runs"][0]["results"] == []
    assert sarif["runs"][0]["tool"]["driver"]["rules"] == []
