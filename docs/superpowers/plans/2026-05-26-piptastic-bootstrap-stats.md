# piptastic v0.2.1 Bootstrap + Stats Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two new piptastic subcommands — `bootstrap` (generate requirements.txt from a venv) and `stats` (cross-project rollup over a tree) — both surfaced by real-world v0.2 use.

**Architecture:** Two new pure modules (`bootstrap.py`, `stats.py`) provide testable logic; `cli.py` grows two subparsers + handlers; `render/` grows two new renderer functions and a small `_make_console()` helper that fixes the cp1252 truncation issue. New dataclasses in `models.py`. Built entirely on existing primitives (`discover_tree`, `audit_project`, `PyPIClient`) — no new IO layer.

**Tech Stack:** Python 3.10+, `importlib.metadata` (for reading distributions out of an arbitrary venv's site-packages), `packaging`, `rich`, stdlib only otherwise.

**Spec:** [`docs/superpowers/specs/2026-05-26-piptastic-bootstrap-stats-design.md`](../specs/2026-05-26-piptastic-bootstrap-stats-design.md)

**Current repo state (entry point):** `main` at SHA `0b06946`, 27 commits, 67 tests passing on Python 3.10. Working tree clean.

---

## File Structure

**Created:**
- `src/piptastic/bootstrap.py` — venv discovery + `freeze_venv` pure function.
- `src/piptastic/stats.py` — `compute_stats` pure aggregator.
- `tests/test_bootstrap.py` — fixture builder + unit tests for bootstrap helpers and CLI smoke.
- `tests/test_stats.py` — unit tests for the aggregator + JSON shape.

**Modified:**
- `src/piptastic/models.py` — add 4 frozen dataclasses for stats output.
- `src/piptastic/cli.py` — add `bootstrap` and `stats` subparsers + `_cmd_bootstrap` and `_cmd_stats` handlers. Also tighten `--exclude` help string.
- `src/piptastic/render/terminal.py` — add `_make_console()` (safe_box for non-UTF-8 stdout, fixes cp1252 `…` → `�`); add `render_stats_terminal()`; route all existing renderers through `_make_console()` when no Console is passed.
- `src/piptastic/render/json_out.py` — add `render_stats_json()` with `schema_version=1` and `"kind": "stats"` discriminator.
- `src/piptastic/render/__init__.py` — re-export the two new render functions.
- `tests/test_cli.py` — add smoke tests for `bootstrap` and `stats` subcommands.
- `README.md` — add a "Bootstrap and stats" section.

**Unchanged:** parsing, discovery, analysis, pypi, update. Feature builds on existing primitives.

---

## Task 1: Stats data models

**Files:**
- Modify: `src/piptastic/models.py`
- Create: `tests/test_stats.py` (initial structure; full tests in Task 2)

- [ ] **Step 1.1: Add the 4 new dataclasses to `models.py`**

Append to `F:/laboratory/pyRequirements-manager/src/piptastic/models.py` (after the existing `ProjectAudit` block):

```python


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
```

Note: `VersionFragmentation.versions` is a `dict` (mutable), so `VersionFragmentation` cannot live in a hashable container. That's fine — it's only consumed as a list. Don't try to freeze it via mapping types.

- [ ] **Step 1.2: Write a smoke test for the new dataclasses**

Create `F:/laboratory/pyRequirements-manager/tests/test_stats.py`:

```python
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
```

- [ ] **Step 1.3: Run the test**

```bash
cd F:/laboratory/pyRequirements-manager
py -3.10 -m pytest tests/test_stats.py -v
```

Expected: 1 passed.

- [ ] **Step 1.4: Commit**

```bash
git add src/piptastic/models.py tests/test_stats.py
git commit -m "feat(models): add StatsReport + nested types for cross-project rollup

- PackageFrequency: name + count + sample of project names
- VersionFragmentation: name + {version: (projects,)} map
- YankedFinding: project + package + pinned + latest_non_yanked
- StatsReport: top-level aggregate with histograms + sections
"
```

---

## Task 2: Stats aggregator (`compute_stats`)

**Files:**
- Create: `src/piptastic/stats.py`
- Modify: `tests/test_stats.py`

- [ ] **Step 2.1: Write failing tests**

Append to `F:/laboratory/pyRequirements-manager/tests/test_stats.py`:

```python
import pytest
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from piptastic.models import (
    Dep,
    DepAudit,
    DepSource,
    PinStatus as PS,
    Project,
    ProjectAudit,
    SemverDrift as SD,
    SourceKind,
)
from piptastic.stats import compute_stats


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
```

- [ ] **Step 2.2: Run tests to verify they fail**

```bash
py -3.10 -m pytest tests/test_stats.py -v -k compute_stats
```

Expected: ModuleNotFoundError for `piptastic.stats`.

- [ ] **Step 2.3: Write `src/piptastic/stats.py`**

```python
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
```

- [ ] **Step 2.4: Run tests**

```bash
py -3.10 -m pytest tests/test_stats.py -v
```

Expected: 8 passed (1 from Task 1 + 7 new).

Full suite:
```bash
py -3.10 -m pytest -q
```

Expected: 74 passed (67 prior + 7 new aggregator tests). Task 1's dataclass smoke test was already counted in the prior commit.

- [ ] **Step 2.5: Commit**

```bash
git add src/piptastic/stats.py tests/test_stats.py
git commit -m "feat(stats): cross-project compute_stats aggregator

Pure function: list[ProjectAudit] -> StatsReport. Surfaces:
- top N most-required packages (by project count, alpha tie-break)
- version fragmentation (packages with 2+ distinct == pins)
- yanked findings (project + pkg + pinned version + latest non-yanked)
- unpinned projects (pin_score == 0.0 AND deps >= 5)
- tree-wide drift + pin posture histograms
"
```

---

## Task 3: Stats JSON renderer

**Files:**
- Modify: `src/piptastic/render/json_out.py`
- Modify: `src/piptastic/render/__init__.py`
- Modify: `tests/test_stats.py`

- [ ] **Step 3.1: Write failing tests**

Append to `F:/laboratory/pyRequirements-manager/tests/test_stats.py`:

```python
import json
from piptastic.render.json_out import render_stats_json


def test_render_stats_json_schema_and_kind():
    audits = [_make_audit("a", [("requests", "==2.32.2", PS.PINNED, SD.NONE, False, "2.32.2")])]
    report = compute_stats(audits, top=5, root=Path("/lab"))
    out = render_stats_json(report)
    parsed = json.loads(out)
    assert parsed["schema_version"] == 1
    assert parsed["kind"] == "stats"
    assert parsed["root"] == "/lab"
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
```

- [ ] **Step 3.2: Run tests to verify they fail**

```bash
py -3.10 -m pytest tests/test_stats.py -v -k render
```

Expected: ImportError for `render_stats_json`.

- [ ] **Step 3.3: Extend `src/piptastic/render/json_out.py`**

Read the current file first to confirm structure. Then append (after the existing `_dep_to_dict` function):

```python


# ---------- stats ----------

def render_stats_json(report) -> str:
    """Render a StatsReport as a JSON string with schema_version=1 and
    kind='stats'. (The audit shape uses kind='audit' implicitly via its
    'projects' key; stats uses an explicit discriminator.)"""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": "stats",
        "scanned_at": report.scanned_at.isoformat(),
        "root": str(report.root),
        "totals": {
            "project_count": report.project_count,
            "total_deps": report.total_deps,
            "drift_histogram": {k.value: v for k, v in report.drift_histogram.items()},
            "pin_status_histogram": {k.value: v for k, v in report.pin_status_histogram.items()},
        },
        "top_packages": [
            {"name": p.name, "project_count": p.project_count, "projects": list(p.projects)}
            for p in report.top_packages
        ],
        "version_fragmentation": [
            {"name": v.name, "versions": {ver: list(projs) for ver, projs in v.versions.items()}}
            for v in report.version_fragmentation
        ],
        "yanked_findings": [
            {
                "project_name": y.project_name,
                "project_path": str(y.project_path),
                "package_name": y.package_name,
                "pinned_version": y.pinned_version,
                "latest_non_yanked": y.latest_non_yanked,
            }
            for y in report.yanked_findings
        ],
        "unpinned_projects": list(report.unpinned_projects),
    }
    return json.dumps(payload, indent=2)
```

- [ ] **Step 3.4: Update `src/piptastic/render/__init__.py`**

Replace its content:

```python
"""Output renderers."""

from piptastic.render.json_out import render_json, render_stats_json
from piptastic.render.terminal import render_terminal

__all__ = ["render_json", "render_stats_json", "render_terminal"]
```

(`render_stats_terminal` will be added to this list in Task 4.)

- [ ] **Step 3.5: Run tests**

```bash
py -3.10 -m pytest tests/test_stats.py -v
py -3.10 -m pytest -q
```

Expected: 10 stats tests passed; full suite at 76 (74 prior + 2 new JSON tests).

- [ ] **Step 3.6: Commit**

```bash
git add src/piptastic/render/json_out.py src/piptastic/render/__init__.py tests/test_stats.py
git commit -m "feat(render): stats JSON renderer with schema_version=1, kind=stats

Stable shape for CI consumers. Discriminator 'kind: stats' distinguishes
from audit's JSON shape (which has 'projects' at the top level).
"
```

---

## Task 4: Stats terminal renderer + cp1252 safe-box fix

**Files:**
- Modify: `src/piptastic/render/terminal.py`
- Modify: `src/piptastic/render/__init__.py`
- Modify: `tests/test_stats.py`

- [ ] **Step 4.1: Write the smoke test**

Append to `F:/laboratory/pyRequirements-manager/tests/test_stats.py`:

```python
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
```

- [ ] **Step 4.2: Run tests to verify they fail**

```bash
py -3.10 -m pytest tests/test_stats.py -v -k "terminal or console"
```

Expected: ImportError on `render_stats_terminal` / `_make_console` / `_stdout_is_utf8`.

- [ ] **Step 4.3: Extend `src/piptastic/render/terminal.py`**

Read the current file first. Then make these changes:

1. Add at the top of the file, after the existing imports:

```python
import sys
```

2. Add the helper functions immediately after the `DRIFT_STYLE` dict definition:

```python
def _stdout_is_utf8() -> bool:
    """Return True when sys.stdout.encoding is UTF-8 family."""
    enc = (getattr(sys.stdout, "encoding", "") or "").lower()
    return "utf" in enc


def _make_console() -> Console:
    """Construct a Console with safe_box=True when stdout cannot encode the
    fancy box-drawing + ellipsis characters rich uses by default (e.g.
    Windows cp1252)."""
    return Console(safe_box=not _stdout_is_utf8())
```

3. Update `render_terminal` to use the helper when `console` is None:

Find:
```python
def render_terminal(
    audits: Iterable[ProjectAudit],
    *,
    mode: ViewMode = "tree",
    console: Console | None = None,
) -> None:
    """Render audits to the terminal. Default view is `tree`."""
    console = console or Console()
```

Replace with:
```python
def render_terminal(
    audits: Iterable[ProjectAudit],
    *,
    mode: ViewMode = "tree",
    console: Console | None = None,
) -> None:
    """Render audits to the terminal. Default view is `tree`."""
    console = console or _make_console()
```

4. Append the new stats renderer at the bottom of the file:

```python


# ---------- stats renderer ----------

def render_stats_terminal(report, *, console: Console | None = None) -> None:
    """Render a StatsReport to the terminal as a series of rich Tables."""
    console = console or _make_console()
    root_label = str(report.root)
    console.print(
        f"[bold]piptastic stats[/bold] - {root_label} "
        f"({report.project_count} projects, {report.total_deps} deps)\n"
    )

    # Top packages
    if report.top_packages:
        t = Table(title=f"Top {len(report.top_packages)} most-required packages", show_lines=False)
        t.add_column("Package")
        t.add_column("Projects", justify="right")
        t.add_column("Sample of projects")
        for p in report.top_packages:
            sample = ", ".join(p.projects[:5])
            if len(p.projects) > 5:
                sample += f", ... (+{len(p.projects) - 5})"
            t.add_row(p.name, str(p.project_count), sample)
        console.print(t)

    # Version fragmentation
    if report.version_fragmentation:
        t = Table(title="Most version-fragmented packages", show_lines=False)
        t.add_column("Package")
        t.add_column("Distinct versions")
        for vf in report.version_fragmentation:
            pieces = []
            for ver, projs in vf.versions.items():
                pieces.append(f"=={ver} ({len(projs)})")
            t.add_row(vf.name, ", ".join(pieces))
        console.print(t)

    # Drift histogram
    drift_pieces = []
    for level in (SemverDrift.NONE, SemverDrift.BUILD, SemverDrift.PATCH,
                  SemverDrift.MINOR, SemverDrift.MAJOR, SemverDrift.EPOCH,
                  SemverDrift.UNKNOWN):
        count = report.drift_histogram.get(level, 0)
        if count:
            style = DRIFT_STYLE.get(level, "white")
            drift_pieces.append(f"[{style}]{level.value}: {count}[/{style}]")
    if drift_pieces:
        console.print("Drift across the tree:  " + "  ".join(drift_pieces))

    # Pin posture histogram
    pin_pieces = []
    for status in (PinStatus.PINNED, PinStatus.COMPATIBLE, PinStatus.RANGE,
                   PinStatus.FLOOR, PinStatus.UNPINNED, PinStatus.URL):
        count = report.pin_status_histogram.get(status, 0)
        if count:
            pin_pieces.append(f"{status.value}: {count}")
    if pin_pieces:
        console.print("Pin posture across the tree:  " + "  ".join(pin_pieces))

    # Yanked findings
    if report.yanked_findings:
        t = Table(title=f"Yanked pins ({len(report.yanked_findings)})", show_lines=False)
        t.add_column("Project")
        t.add_column("Package")
        t.add_column("Pinned")
        t.add_column("Latest non-yanked")
        for y in report.yanked_findings:
            t.add_row(
                y.project_name, y.package_name,
                f"=={y.pinned_version}" if y.pinned_version else "-",
                y.latest_non_yanked or "-",
            )
        console.print(t)

    # Unpinned projects
    if report.unpinned_projects:
        console.print(
            f"\nUnpinned projects (deps >= 5):  "
            + ", ".join(report.unpinned_projects)
        )

    # Footer with the project count even when empty
    if report.project_count == 0:
        console.print("[dim]0 projects in audit.[/dim]")
```

5. Update `__init__.py` to re-export the new function:

Read `F:/laboratory/pyRequirements-manager/src/piptastic/render/__init__.py` and replace its content:

```python
"""Output renderers."""

from piptastic.render.json_out import render_json, render_stats_json
from piptastic.render.terminal import render_stats_terminal, render_terminal

__all__ = ["render_json", "render_stats_json", "render_stats_terminal", "render_terminal"]
```

- [ ] **Step 4.4: Run tests**

```bash
py -3.10 -m pytest tests/test_stats.py -v
py -3.10 -m pytest -q
```

Expected: 14 stats tests passed; full suite at 80 (76 prior + 4 new terminal tests).

- [ ] **Step 4.5: Commit**

```bash
git add src/piptastic/render/terminal.py src/piptastic/render/__init__.py tests/test_stats.py
git commit -m "feat(render): stats terminal renderer + cp1252 safe_box helper

- render_stats_terminal: rich Tables for top packages, fragmentation,
  yanked, unpinned projects; histogram lines for drift and pin posture
- _make_console() picks safe_box=True when stdout encoding isn't UTF-8
  (fixes the final-review cp1252 truncation issue: ellipsis U+2026
  rendering as � on Windows default consoles)
- render_terminal now uses _make_console() when no console is passed
"
```

---

## Task 5: Bootstrap helpers (pure module)

**Files:**
- Create: `src/piptastic/bootstrap.py`
- Create: `tests/test_bootstrap.py`

- [ ] **Step 5.1: Write failing tests with a fake-venv fixture**

Create `F:/laboratory/pyRequirements-manager/tests/test_bootstrap.py`:

```python
"""Tests for piptastic bootstrap (venv → requirements.txt)."""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path

import pytest

from piptastic.bootstrap import (
    find_site_packages,
    find_venv,
    freeze_venv,
    is_plumbing,
    is_self_install,
)


def build_fake_venv(
    venv_root: Path,
    *,
    on_windows: bool = False,
    packages: dict[str, str] | None = None,
    editable_self: dict | None = None,
) -> Path:
    """Build a minimal venv directory tree.

    Args:
        venv_root: Path to create the venv at (must not yet exist).
        on_windows: when True, uses Windows-style 'Lib/site-packages'.
        packages: {canonical_name: version} to write as dist-info dirs.
        editable_self: optional {"package_name": ..., "project_path": ...}
            that writes a direct_url.json marking that package as editable
            from project_path.
    """
    if on_windows:
        site_packages = venv_root / "Lib" / "site-packages"
    else:
        # Pick a python version dir; real venvs use the actual interpreter version.
        site_packages = venv_root / "lib" / "python3.11" / "site-packages"
    site_packages.mkdir(parents=True, exist_ok=True)

    # Plant pyvenv.cfg so other tools can recognize it as a venv
    (venv_root / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")

    for name, version in (packages or {}).items():
        dist_info = site_packages / f"{name}-{version}.dist-info"
        dist_info.mkdir()
        (dist_info / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
            encoding="utf-8",
        )
        (dist_info / "RECORD").write_text("", encoding="utf-8")
        (dist_info / "WHEEL").write_text(
            "Wheel-Version: 1.0\nGenerator: bdist_wheel\n",
            encoding="utf-8",
        )

    if editable_self:
        pkg_name = editable_self["package_name"]
        project_path = Path(editable_self["project_path"])
        # Find which dist-info matches; if not present, create a stub
        match = list(site_packages.glob(f"{pkg_name}-*.dist-info"))
        if not match:
            stub = site_packages / f"{pkg_name}-0.0.0.dist-info"
            stub.mkdir()
            (stub / "METADATA").write_text(
                f"Metadata-Version: 2.1\nName: {pkg_name}\nVersion: 0.0.0\n",
                encoding="utf-8",
            )
            match = [stub]
        (match[0] / "direct_url.json").write_text(
            _json.dumps({"url": project_path.as_uri(), "dir_info": {"editable": True}}),
            encoding="utf-8",
        )

    return site_packages


# ---------- find_site_packages ----------

@pytest.mark.parametrize("on_windows", [True, False])
def test_find_site_packages(tmp_path, on_windows):
    venv = tmp_path / "venv"
    build_fake_venv(venv, on_windows=on_windows, packages={"flask": "3.0.2"})
    sp = find_site_packages(venv)
    assert sp is not None
    assert sp.is_dir()
    assert (sp / "flask-3.0.2.dist-info").is_dir()


def test_find_site_packages_returns_none_when_missing(tmp_path):
    empty = tmp_path / "not-a-venv"
    empty.mkdir()
    assert find_site_packages(empty) is None


# ---------- find_venv ----------

def test_find_venv_zero_candidates(tmp_path):
    candidates, chosen = find_venv(tmp_path)
    assert candidates == []
    assert chosen is None


def test_find_venv_explicit_path(tmp_path):
    venv = tmp_path / "custom-runtime"
    build_fake_venv(venv, packages={})
    candidates, chosen = find_venv(tmp_path, explicit=venv)
    assert chosen == venv


def test_find_venv_single_default(tmp_path):
    venv = tmp_path / ".venv"
    build_fake_venv(venv, packages={})
    candidates, chosen = find_venv(tmp_path)
    assert chosen == venv


def test_find_venv_multiple_returns_none_for_chosen(tmp_path):
    build_fake_venv(tmp_path / ".venv", packages={})
    build_fake_venv(tmp_path / "venv", packages={})
    candidates, chosen = find_venv(tmp_path)
    assert chosen is None
    assert len(candidates) == 2


# ---------- is_plumbing / is_self_install ----------

@pytest.mark.parametrize("name,expected", [
    ("pip", True),
    ("PIP", True),
    ("setuptools", True),
    ("wheel", True),
    ("pkg-resources", True),
    ("distlib", True),
    ("_distutils_hack", True),
    ("flask", False),
    ("requests", False),
    ("setuptools-scm", False),  # only the bare 'setuptools' is plumbing
])
def test_is_plumbing(name, expected):
    assert is_plumbing(name) is expected


def test_is_self_install_with_direct_url(tmp_path):
    project = tmp_path / "myproj"
    project.mkdir()
    venv = tmp_path / ".venv"
    build_fake_venv(
        venv,
        packages={"myproj": "0.0.0"},
        editable_self={"package_name": "myproj", "project_path": project},
    )
    import importlib.metadata
    sp = find_site_packages(venv)
    dist = next(d for d in importlib.metadata.distributions(path=[str(sp)]) if d.metadata["Name"] == "myproj")
    assert is_self_install(dist, project) is True


def test_is_self_install_false_for_normal_dep(tmp_path):
    project = tmp_path / "myproj"
    project.mkdir()
    venv = tmp_path / ".venv"
    build_fake_venv(venv, packages={"flask": "3.0.2"})
    import importlib.metadata
    sp = find_site_packages(venv)
    dist = next(d for d in importlib.metadata.distributions(path=[str(sp)]) if d.metadata["Name"] == "flask")
    assert is_self_install(dist, project) is False


# ---------- freeze_venv ----------

def test_freeze_venv_basic_pinning(tmp_path):
    project = tmp_path / "myproj"
    project.mkdir()
    venv = tmp_path / ".venv"
    build_fake_venv(venv, packages={
        "flask": "3.0.2",
        "requests": "2.32.2",
        "pip": "24.0",        # plumbing → skipped
        "setuptools": "70.0", # plumbing → skipped
        "wheel": "0.43",      # plumbing → skipped
    })
    lines = freeze_venv(project, venv)
    assert lines == ["flask==3.0.2", "requests==2.32.2"]


def test_freeze_venv_excludes_self(tmp_path):
    project = tmp_path / "myproj"
    project.mkdir()
    venv = tmp_path / ".venv"
    build_fake_venv(
        venv,
        packages={"flask": "3.0.2", "myproj": "0.0.0"},
        editable_self={"package_name": "myproj", "project_path": project},
    )
    lines = freeze_venv(project, venv)
    assert lines == ["flask==3.0.2"]


def test_freeze_venv_sorted_alphabetical(tmp_path):
    project = tmp_path / "myproj"
    project.mkdir()
    venv = tmp_path / ".venv"
    build_fake_venv(venv, packages={
        "ZZZ-late": "1.0",
        "aaa-early": "2.0",
        "mmm-mid": "3.0",
    })
    lines = freeze_venv(project, venv)
    # PEP 503 canonicalization lowercases names; sorted order should match
    assert lines == ["aaa-early==2.0", "mmm-mid==3.0", "zzz-late==1.0"]


def test_freeze_venv_empty(tmp_path):
    project = tmp_path / "myproj"
    project.mkdir()
    venv = tmp_path / ".venv"
    build_fake_venv(venv, packages={})
    lines = freeze_venv(project, venv)
    assert lines == []
```

- [ ] **Step 5.2: Run tests to verify they fail**

```bash
py -3.10 -m pytest tests/test_bootstrap.py -v
```

Expected: ModuleNotFoundError for `piptastic.bootstrap`.

- [ ] **Step 5.3: Write `src/piptastic/bootstrap.py`**

```python
"""Bootstrap a requirements.txt from a project's venv.

Pure-data helpers here; the CLI handler does the file IO.
"""

from __future__ import annotations

import importlib.metadata
import json
import sys
from pathlib import Path
from typing import Iterable

from packaging.utils import canonicalize_name

from piptastic.logging import get_logger

logger = get_logger(__name__)


# Distributions that are part of the venv plumbing rather than the
# project's actual dependencies. Skipped during freeze.
_PLUMBING_NAMES = frozenset({
    "pip",
    "setuptools",
    "wheel",
    "pkg-resources",
    "distlib",
    "_distutils_hack",
})


# Default venv directory names to probe under a project root, in order.
_DEFAULT_VENV_NAMES = (".venv", "venv", "env", ".env")


def is_plumbing(name: str) -> bool:
    """True if `name` is a venv-plumbing distribution that bootstrap should
    skip. Match is case-insensitive on the PEP 503 canonical form, AND
    on the literal underscore-style names that some plumbing uses."""
    canon = canonicalize_name(name)
    if canon in _PLUMBING_NAMES:
        return True
    # _distutils_hack canonicalizes to '-distutils-hack' which is not the
    # bare canonical form, so also check the lowercase raw name.
    if name.lower() in _PLUMBING_NAMES:
        return True
    return False


def is_self_install(dist, project_path: Path) -> bool:
    """True if `dist` is an editable install pointing back at the project."""
    try:
        text = dist.read_text("direct_url.json")
    except (FileNotFoundError, OSError):
        return False
    if text is None:
        return False
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False
    url = data.get("url", "")
    if not url:
        return False
    # url is typically a file:// URI; compare resolved paths
    try:
        from urllib.parse import urlparse, unquote
        parsed = urlparse(url)
        if parsed.scheme != "file":
            return False
        # urlparse gives us a path like '/F:/laboratory/...' on Windows
        path_str = unquote(parsed.path).lstrip("/")
        # On POSIX the leading / was real; restore it
        if not path_str.startswith(("/", ".")) and ":" not in path_str[:3]:
            path_str = "/" + path_str
        return Path(path_str).resolve() == project_path.resolve()
    except Exception:
        return False


def find_site_packages(venv_dir: Path) -> Path | None:
    """Return the venv's site-packages directory, cross-platform.

    Windows: <venv>/Lib/site-packages
    POSIX:   <venv>/lib/python*/site-packages
    """
    win = venv_dir / "Lib" / "site-packages"
    if win.is_dir():
        return win
    lib = venv_dir / "lib"
    if lib.is_dir():
        for py_dir in sorted(lib.glob("python*")):
            sp = py_dir / "site-packages"
            if sp.is_dir():
                return sp
    return None


def find_venv(
    project_path: Path,
    *,
    explicit: Path | None = None,
) -> tuple[list[Path], Path | None]:
    """Return (all_candidates, chosen).

    - If `explicit` is given, returns it as the only candidate (and chosen).
    - Otherwise probes the default venv names + scans for pyvenv.cfg.
    - chosen is None when 0 candidates OR multiple candidates were found.
    """
    if explicit is not None:
        path = explicit if explicit.is_absolute() else (project_path / explicit)
        return [path], path

    candidates: list[Path] = []

    for name in _DEFAULT_VENV_NAMES:
        candidate = project_path / name
        if candidate.is_dir() and (candidate / "pyvenv.cfg").is_file():
            candidates.append(candidate)

    # Also scan any other top-level subdir containing pyvenv.cfg, but only if
    # we didn't already find one of the canonical names. This catches
    # arbitrarily-named venvs.
    if not candidates:
        for child in sorted(project_path.iterdir() if project_path.is_dir() else []):
            if child.is_dir() and (child / "pyvenv.cfg").is_file():
                candidates.append(child)

    if len(candidates) == 1:
        return candidates, candidates[0]
    return candidates, None


def freeze_venv(project_path: Path, venv_dir: Path) -> list[str]:
    """Return sorted 'name==version' lines for non-plumbing, non-self
    distributions installed in venv_dir."""
    site_packages = find_site_packages(venv_dir)
    if site_packages is None:
        return []

    out: list[tuple[str, str]] = []
    for dist in importlib.metadata.distributions(path=[str(site_packages)]):
        raw_name = dist.metadata["Name"]
        if raw_name is None:
            continue
        if is_plumbing(raw_name):
            continue
        if is_self_install(dist, project_path):
            continue
        canon = canonicalize_name(raw_name)
        out.append((canon, dist.version))

    out.sort(key=lambda pair: pair[0])
    return [f"{name}=={version}" for name, version in out]
```

- [ ] **Step 5.4: Run tests**

```bash
py -3.10 -m pytest tests/test_bootstrap.py -v
```

Expected: All bootstrap tests pass (10 parametrized + non-parametrized = around 15 cases).

Full suite:
```bash
py -3.10 -m pytest -q
```

Expected: ~95 passed (80 prior + ~15 new).

- [ ] **Step 5.5: Commit**

```bash
git add src/piptastic/bootstrap.py tests/test_bootstrap.py
git commit -m "feat(bootstrap): pure helpers for venv discovery and freeze

- find_venv(project, explicit=None): returns (candidates, chosen);
  chosen is None for 0 candidates or ambiguous (>1) cases
- find_site_packages(venv): cross-platform site-packages resolution
- is_plumbing(name): identifies pip/setuptools/wheel/etc to skip
- is_self_install(dist, project): detects editable self via
  direct_url.json (PEP 610)
- freeze_venv(project, venv): returns sorted 'name==version' lines
  with plumbing + self excluded; uses importlib.metadata against the
  venv's site-packages
"
```

---

## Task 6: Bootstrap CLI subcommand

**Files:**
- Modify: `src/piptastic/cli.py`
- Modify: `tests/test_bootstrap.py` (or `tests/test_cli.py` — append to the latter for consistency)

- [ ] **Step 6.1: Write the CLI smoke tests**

Append to `F:/laboratory/pyRequirements-manager/tests/test_cli.py`:

```python
from tests.test_bootstrap import build_fake_venv


def test_bootstrap_writes_requirements(tmp_path, monkeypatch, capsys):
    project = tmp_path / "myproj"
    project.mkdir()
    build_fake_venv(project / ".venv", packages={"flask": "3.0.2", "pip": "24.0"})

    exit_code = main(["bootstrap", str(project)])
    captured = capsys.readouterr()
    assert exit_code == 0
    req_file = project / "requirements.txt"
    assert req_file.is_file()
    content = req_file.read_text(encoding="utf-8")
    assert "flask==3.0.2" in content
    assert "pip==" not in content  # plumbing skipped


def test_bootstrap_refuses_overwrite_without_force(tmp_path, capsys):
    project = tmp_path / "myproj"
    project.mkdir()
    build_fake_venv(project / ".venv", packages={"flask": "3.0.2"})
    (project / "requirements.txt").write_text("preexisting==1.0\n", encoding="utf-8")

    exit_code = main(["bootstrap", str(project)])
    assert exit_code == 1
    # Original content preserved
    assert (project / "requirements.txt").read_text(encoding="utf-8") == "preexisting==1.0\n"


def test_bootstrap_force_backs_up_then_overwrites(tmp_path):
    project = tmp_path / "myproj"
    project.mkdir()
    build_fake_venv(project / ".venv", packages={"flask": "3.0.2"})
    (project / "requirements.txt").write_text("preexisting==1.0\n", encoding="utf-8")

    exit_code = main(["bootstrap", str(project), "--force"])
    assert exit_code == 0
    new_content = (project / "requirements.txt").read_text(encoding="utf-8")
    assert "flask==3.0.2" in new_content
    assert "preexisting==1.0" not in new_content
    # Backup exists
    backups = list((project / ".requirements_backups").glob("requirements_*.txt"))
    assert len(backups) == 1
    assert "preexisting==1.0" in backups[0].read_text(encoding="utf-8")


def test_bootstrap_dry_run(tmp_path, capsys):
    project = tmp_path / "myproj"
    project.mkdir()
    build_fake_venv(project / ".venv", packages={"flask": "3.0.2"})

    exit_code = main(["bootstrap", str(project), "--dry-run"])
    assert exit_code == 0
    assert not (project / "requirements.txt").exists()
    captured = capsys.readouterr()
    assert "flask==3.0.2" in captured.out


def test_bootstrap_no_venv(tmp_path):
    project = tmp_path / "myproj"
    project.mkdir()
    exit_code = main(["bootstrap", str(project)])
    assert exit_code == 1


def test_bootstrap_ambiguous_venv(tmp_path):
    project = tmp_path / "myproj"
    project.mkdir()
    build_fake_venv(project / ".venv", packages={"flask": "3.0.2"})
    build_fake_venv(project / "venv", packages={"flask": "3.0.2"})
    exit_code = main(["bootstrap", str(project)])
    assert exit_code == 1
```

- [ ] **Step 6.2: Run tests to verify they fail**

```bash
py -3.10 -m pytest tests/test_cli.py -v -k bootstrap
```

Expected: argparse errors (subcommand 'bootstrap' not registered).

- [ ] **Step 6.3: Extend `src/piptastic/cli.py`**

Read the current file first to find where the existing subparsers are defined. Then make these changes:

1. Add at the top of the file (near the other imports):

```python
import hashlib
import shutil
from datetime import datetime
```

If `shutil` / `hashlib` / `datetime` are already imported, skip those lines.

Also add a new import block:

```python
from piptastic.bootstrap import find_venv, freeze_venv
```

2. In `build_parser()`, after the `update` subparser definition and before the `return parser` line, add:

```python
    # bootstrap
    boot = sub.add_parser(
        "bootstrap",
        help="Generate requirements.txt from a project's installed venv",
    )
    boot.add_argument("path", type=Path)
    boot.add_argument(
        "--venv",
        type=Path, default=None,
        help="Explicit venv directory (relative to PATH or absolute)",
    )
    boot.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing requirements.txt (creates a backup first)",
    )
    boot.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the requirements to stdout; do not write any file",
    )
```

3. In `main()`, find the dispatch block. After the existing handlers, add:

```python
        if args.command == "bootstrap":
            return _cmd_bootstrap(args)
```

4. Append the handler at the bottom of the file (alongside the other `_cmd_*` functions):

```python
def _cmd_bootstrap(args) -> int:
    project_path = args.path.resolve()
    if not project_path.is_dir():
        logger.error("not a directory: %s", project_path)
        return 1

    candidates, chosen = find_venv(project_path, explicit=args.venv)
    if not candidates:
        logger.error(
            "no venv found under %s; pass --venv to specify",
            project_path,
        )
        return 1
    if chosen is None:
        rel = ", ".join(str(c.relative_to(project_path)) for c in candidates)
        logger.error(
            "multiple venvs found (%s); pass --venv to disambiguate",
            rel,
        )
        return 1

    lines = freeze_venv(project_path, chosen)

    if args.dry_run:
        for line in lines:
            print(line)
        return 0

    target = project_path / "requirements.txt"
    if target.exists() and not args.force:
        logger.error(
            "%s already exists; pass --force to overwrite (a backup will be created)",
            target,
        )
        return 1

    if target.exists() and args.force:
        backup_dir = project_path / ".requirements_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()[:8]
        dest = backup_dir / f"requirements_{ts}_{digest}.txt"
        shutil.copy2(target, dest)
        print(f"piptastic: backed up existing requirements.txt to {dest}")

    try:
        target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    except OSError as e:
        logger.error("failed to write %s: %s", target, e)
        return 2

    plumbing_count = sum(1 for d in __import__("importlib").metadata.distributions(
        path=[str(__import__("piptastic.bootstrap", fromlist=["find_site_packages"]).find_site_packages(chosen))]
    ) if d.metadata["Name"] and __import__("piptastic.bootstrap", fromlist=["is_plumbing"]).is_plumbing(d.metadata["Name"]))
    # The plumbing count is informational; if anything goes wrong computing it, default to 0
    print(f"piptastic: wrote {target}")
    print(f"  captured {len(lines)} deps from {chosen}")
    if plumbing_count:
        print(f"  skipped {plumbing_count} plumbing distributions")
    return 0
```

NOTE on the `__import__` use: that's an ugly attempt at lazy access. Replace it with a clean import at the top of the file. Specifically, change the bootstrap import line to:

```python
from piptastic.bootstrap import find_site_packages, find_venv, freeze_venv, is_plumbing
import importlib.metadata
```

And rewrite the plumbing counter as:

```python
    sp = find_site_packages(chosen)
    if sp is not None:
        plumbing_count = sum(
            1 for d in importlib.metadata.distributions(path=[str(sp)])
            if d.metadata["Name"] and is_plumbing(d.metadata["Name"])
        )
    else:
        plumbing_count = 0
```

Use this cleaner version in the handler, not the `__import__` version.

- [ ] **Step 6.4: Run tests**

```bash
py -3.10 -m pytest tests/test_cli.py -v -k bootstrap
py -3.10 -m pytest -q
```

Expected: 6 bootstrap CLI tests pass; full suite at ~101 (95 prior + 6 new).

- [ ] **Step 6.5: Commit**

```bash
git add src/piptastic/cli.py tests/test_cli.py
git commit -m "feat(cli): bootstrap subcommand wires venv freeze to requirements.txt

- piptastic bootstrap <path> [--venv DIR] [--force] [--dry-run]
- Refuses to overwrite existing requirements.txt without --force
- --force backs up the old file to .requirements_backups/ first
- --dry-run prints to stdout, writes nothing
- Reports captured + skipped counts
- Exit codes: 0 ok, 1 no/ambiguous venv or refused overwrite, 2 IO error
"
```

---

## Task 7: Stats CLI subcommand

**Files:**
- Modify: `src/piptastic/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 7.1: Write the CLI smoke tests**

Append to `F:/laboratory/pyRequirements-manager/tests/test_cli.py`:

```python
def test_stats_subcommand_smoke(monkeypatch, capsys, tmp_path):
    """Stats produces a non-empty terminal report against fixtures with a
    monkey-patched PyPI client."""
    from piptastic import cli as cli_mod

    class FakeClient:
        def fetch_many(self, names):
            return {}
        def fetch_one(self, name):
            return None

    monkeypatch.setattr(cli_mod, "_build_client", lambda args: FakeClient())

    exit_code = main(["stats", str(FIXTURES / "req_only")])
    assert exit_code == 0
    captured = capsys.readouterr()
    # Smoke: stats header and some content present
    assert "piptastic stats" in captured.out
    assert "1 projects" in captured.out  # single-project audit produces a 1-project stats


def test_stats_json_smoke(monkeypatch, capsys):
    from piptastic import cli as cli_mod

    class FakeClient:
        def fetch_many(self, names): return {}
        def fetch_one(self, name): return None

    monkeypatch.setattr(cli_mod, "_build_client", lambda args: FakeClient())

    exit_code = main(["stats", str(FIXTURES), "--json"])
    assert exit_code == 0
    captured = capsys.readouterr()
    import json as _json
    payload = _json.loads(captured.out)
    assert payload["schema_version"] == 1
    assert payload["kind"] == "stats"
    assert payload["totals"]["project_count"] >= 1
```

- [ ] **Step 7.2: Run tests to verify they fail**

```bash
py -3.10 -m pytest tests/test_cli.py -v -k stats
```

Expected: argparse errors (subcommand 'stats' not registered).

- [ ] **Step 7.3: Extend `src/piptastic/cli.py`**

1. Add to imports near the top of the file:

```python
from piptastic.render import render_stats_json, render_stats_terminal
from piptastic.stats import compute_stats
```

2. In `build_parser()`, after the `update` subparser and BEFORE the `bootstrap` subparser added in Task 6, add:

```python
    # stats
    stats = sub.add_parser(
        "stats",
        help="Cross-project rollup (top packages, fragmentation, yanked, etc.)",
    )
    stats.add_argument("path", type=Path)
    stats.add_argument("--top", type=int, default=20, help="Top N packages (default: 20)")
    stats.add_argument("--json", action="store_true", help="Machine-readable JSON to stdout")
    stats.add_argument(
        "--exclude", action="append", default=[],
        help="Glob pattern matched against directory BASENAMES (not paths), repeatable",
    )
    stats.add_argument("--no-cache", action="store_true")
    stats.add_argument("--refresh-cache", action="store_true")
    stats.add_argument("--cache-ttl", type=int, default=3600)
    stats.add_argument("--concurrency", type=int, default=8)
```

3. In `main()`, after the `bootstrap` dispatch, add:

```python
        if args.command == "stats":
            return _cmd_stats(args)
```

4. Append the handler:

```python
def _cmd_stats(args) -> int:
    path = args.path.resolve()
    if not path.exists():
        logger.error("path does not exist: %s", path)
        return 1

    single = discover_one(path)
    if single is not None:
        projects = [single]
    else:
        projects = discover_tree(path, exclude=args.exclude)
    if not projects:
        logger.error("no Python projects found at %s", path)
        return 1

    client = _build_client(args)
    current_py = Version(".".join(str(x) for x in sys.version_info[:3]))
    audits = []
    for p in projects:
        try:
            audits.append(audit_project(p, client, current_python=current_py))
        except Exception as e:
            logger.warning("failed to audit %s: %s", p.name, e)

    report = compute_stats(audits, top=args.top, root=path)

    if args.json:
        print(render_stats_json(report))
    else:
        render_stats_terminal(report)
    return 0
```

5. While we're in `cli.py`, also tighten the existing `audit` subparser's `--exclude` help string for consistency:

Find:
```python
    audit.add_argument("--exclude", action="append", default=[], help="Glob pattern, repeatable")
```

Replace with:
```python
    audit.add_argument(
        "--exclude", action="append", default=[],
        help="Glob pattern matched against directory BASENAMES (not paths), repeatable",
    )
```

- [ ] **Step 7.4: Run tests**

```bash
py -3.10 -m pytest tests/test_cli.py -v -k stats
py -3.10 -m pytest -q
```

Expected: 2 stats CLI tests pass; full suite at ~103 (101 prior + 2 new).

- [ ] **Step 7.5: Commit**

```bash
git add src/piptastic/cli.py tests/test_cli.py
git commit -m "feat(cli): stats subcommand for cross-project rollup

- piptastic stats <tree> [--top N] [--json] [--exclude PAT] [cache flags]
- Reuses discover_tree + audit_project, then compute_stats + renderers
- Per-project audit failures isolated (one bad project doesn't kill stats)
- Also tightens audit's --exclude help string (clarifies glob is matched
  against directory basenames, not paths)
"
```

---

## Task 8: README polish + end-to-end real-world smoke

**Files:**
- Modify: `README.md`

- [ ] **Step 8.1: Run a real end-to-end smoke**

```bash
cd F:/laboratory/pyRequirements-manager
py -3.10 -m piptastic stats F:/laboratory --top 10 --exclude 'pyRequirements-manager' 2>/dev/null | head -50
```

Expected: a rich-rendered stats report with sections for top packages, fragmentation, drift histogram, etc. Confirm it doesn't crash.

```bash
py -3.10 -m piptastic stats F:/laboratory --top 5 --json --exclude 'pyRequirements-manager' 2>/dev/null | py -3.10 -c "import json, sys; d=json.load(sys.stdin); print('schema:', d['schema_version'], 'kind:', d['kind'], 'projects:', d['totals']['project_count'])"
```

Expected: `schema: 1 kind: stats projects: <some number>`.

If either run errors out, fix the issue rather than continuing.

- [ ] **Step 8.2: Bootstrap smoke on a real lab project that has no requirements.txt**

Pick any project in your lab with a `.venv/` but no `requirements.txt` (you mentioned several at 0% pin score — many of those may also lack a manifest). For example:

```bash
ls F:/laboratory/Arista-RAG/
# if there's a .venv/ and no requirements.txt, try:
py -3.10 -m piptastic bootstrap F:/laboratory/Arista-RAG --dry-run 2>/dev/null | head -20
```

If no venv exists, this will exit 1 — that's expected; just confirm the error message is clear.

- [ ] **Step 8.3: Expand `README.md`**

Read the current `README.md` first. Then find the "## Usage" section and add a new section AFTER it (before "## Output channels"):

```markdown
## Bootstrap and stats

For a project that has a working `.venv/` but no `requirements.txt`,
generate one from the venv's installed packages:

```bash
# Dry-run (print to stdout, write nothing)
piptastic bootstrap ./myproject --dry-run

# Write to <myproject>/requirements.txt (refuses to overwrite)
piptastic bootstrap ./myproject

# Overwrite with backup
piptastic bootstrap ./myproject --force
```

The output is `name==X.Y.Z` pins for every distribution in the venv,
excluding pip/setuptools/wheel/etc. plumbing and any editable install
of the project itself.

For a tree-wide rollup of dependency health:

```bash
# Terminal report: top packages, version fragmentation, yanked pins,
# unpinned projects, tree-wide drift/pin posture histograms
piptastic stats ~/projects

# JSON for dashboards (stable schema_version=1, kind='stats')
piptastic stats ~/projects --json > stats.json

# Limit top-N packages (default 20)
piptastic stats ~/projects --top 5
```

`stats` reuses the same discovery + audit pipeline as `audit`, so it
honors `--exclude`, the on-disk cache, and per-project Python version
detection.
```

- [ ] **Step 8.4: Final test pass with coverage**

```bash
py -3.10 -m pytest --cov=src/piptastic --cov-report=term-missing
```

Expected: all tests green; coverage on bootstrap.py + stats.py + render/*.py at or above 80%. (`update.py` and CLI handlers will still be in the 30-70% range — that's pre-existing.)

- [ ] **Step 8.5: Final commit**

```bash
git add README.md
git commit -m "docs: expand README with bootstrap + stats usage

- bootstrap: dry-run / write / --force / behaviors
- stats: terminal + JSON outputs, --top flag, --exclude inheritance
"
```

---

## Self-review checklist (executed)

**Spec coverage:**
- §3 Package layout → all 8 tasks together (file structure summary at top of plan)
- §4 Data model → Task 1
- §5 Bootstrap behavior → Tasks 5 + 6
- §6 Stats behavior → Tasks 2 + 3 + 4 + 7
- §7 Bug fixes folded in (cp1252, --exclude doc) → Task 4 (cp1252) + Task 7 (--exclude doc)
- §8 Testing → tests in Tasks 1-7; coverage in Task 8.4
- §10 Risks documented in spec, not addressed inline (e.g. plumbing list)
- §11 Deferred items are out of scope for this plan

**Placeholder scan:** no "TBD" / "TODO" / "fill in" markers. Every step has full code or exact command + expected output.

**Type consistency:**
- `PackageFrequency`, `VersionFragmentation`, `YankedFinding`, `StatsReport` spellings consistent across §1, §2, §3, §4, §7.
- `compute_stats(audits, *, top, root)` signature in §2 matches use in §7.
- `freeze_venv(project_path, venv_dir)` signature in §5 matches use in §6.
- `find_venv(project_path, *, explicit=None)` returns `(candidates, chosen)` — consistent in §5 (definition) and §6 (use).
- `render_stats_terminal(report, *, console=None)` signature in §4 matches use in §7.
- `render_stats_json(report) -> str` signature in §3 matches use in §7.
- `_make_console()` and `_stdout_is_utf8()` names consistent in §4.

**Scope:** Two related features in one v0.2.1 plan. They share renderer/CLI infra and ship together. Each is testable independently (bootstrap doesn't depend on stats and vice versa), so subagent-driven execution can parallelize them if desired.

---
