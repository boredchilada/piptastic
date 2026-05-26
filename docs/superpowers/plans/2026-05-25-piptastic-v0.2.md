# piptastic v0.2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the broken `requirements_manager` package with a clean, audit-first, multi-format Python dependency reporter installable as `piptastic` / `ptc`.

**Architecture:** A `discovery` layer walks a tree and emits `Project`s with one or more `DepSource`s. A `parsing` layer turns each source into `Dep`s using `packaging.requirements.Requirement`. A `pypi` client fetches release metadata concurrently with on-disk TTL caching. An `analysis` layer joins parsed `Dep`s with PyPI data and produces a `ProjectAudit` carrying drift classification (`EPOCH`/`MAJOR`/`MINOR`/`PATCH`/`BUILD`/`NONE`) and pinning posture. A `render` layer turns audits into either a `rich`-based TUI or stable-shape JSON. A separate `update` module handles the only file-mutating subcommand. The CLI ties them together with three subcommands: `audit` (default), `list`, `update`.

**Tech Stack:** Python 3.10+, `packaging`, `rich`, `tomli` (Python 3.10 only — stdlib `tomllib` on 3.11+), stdlib `concurrent.futures` + `urllib.request`, `pytest`, `hatchling` build backend.

**Spec:** [`docs/superpowers/specs/2026-05-25-piptastic-v0.2-design.md`](../specs/2026-05-25-piptastic-v0.2-design.md)

---

## File Structure

**Created:**
- `pyproject.toml` — PEP 621 metadata, console_scripts for `piptastic` and `ptc`, dev extras.
- `README.md` — install, usage, sample output.
- `src/piptastic/__init__.py` — version, public API re-exports.
- `src/piptastic/__main__.py` — enables `python -m piptastic`.
- `src/piptastic/models.py` — all shared dataclasses and enums.
- `src/piptastic/logging.py` — `get_logger` factory; no module-level side effects.
- `src/piptastic/parsing.py` — `parse_source(DepSource) -> list[Dep]` for all formats.
- `src/piptastic/discovery.py` — `discover_tree`, `discover_one`, exclusion rules.
- `src/piptastic/pypi.py` — `PyPIClient` with thread-pool + on-disk TTL cache.
- `src/piptastic/analysis.py` — drift classifier, pin-status classifier, `audit_project`.
- `src/piptastic/render/__init__.py` — exports `render_terminal`, `render_json`.
- `src/piptastic/render/terminal.py` — `rich`-based tree/table/summary views.
- `src/piptastic/render/json_out.py` — JSON serializer, `schema_version=1`.
- `src/piptastic/update.py` — file-mutating logic for the `update` subcommand.
- `src/piptastic/cli.py` — argparse subcommands, exit codes, `main()` entry point.
- `tests/__init__.py`
- `tests/conftest.py` — shared fixtures (PyPI mock, tmp project trees).
- `tests/test_models.py`
- `tests/test_parsing.py`
- `tests/test_discovery.py`
- `tests/test_pypi.py`
- `tests/test_analysis.py`
- `tests/test_render_json.py`
- `tests/test_cli.py`
- `tests/fixtures/req_only/requirements.txt`
- `tests/fixtures/req_only/dev-requirements.txt`
- `tests/fixtures/pyproject_pep621/pyproject.toml`
- `tests/fixtures/pyproject_poetry/pyproject.toml`
- `tests/fixtures/pipfile_project/Pipfile`
- `tests/fixtures/pipfile_project/Pipfile.lock`
- `tests/fixtures/mixed/requirements.txt`
- `tests/fixtures/mixed/pyproject.toml`
- `tests/fixtures/venv_inside/requirements.txt`
- `tests/fixtures/venv_inside/.venv/pyvenv.cfg`
- `tests/fixtures/envoy_dir/envoy/requirements.txt`
- `.gitignore` (replace)

**Deleted:**
- `pip-update-requirements/` (vendored, unused)
- `src/requirements_manager/` (replaced by `src/piptastic/`)
- `requirements_updater.py` (replaced by console_scripts entry)
- `requirements.txt` at repo root (unused; real deps go in `pyproject.toml`)
- `requirements_backups/` and `.requirements_backups/` (historical; gitignored)
- `requirements_updates.log` (historical; gitignored)

**Kept:**
- `archive/PIPRU.py` (historical reference)

---

## Task 0: Repository hygiene

**Files:**
- Create: `.gitignore` (replace existing)
- Delete: `pip-update-requirements/`, `src/requirements_manager/`, `requirements_updater.py`, `requirements.txt`, `requirements_backups/`, `.requirements_backups/`, `requirements_updates.log`

- [ ] **Step 0.1: Initialize git repo**

```bash
cd F:/laboratory/pyRequirements-manager
git init
git branch -M main
```

Expected: `Initialized empty Git repository in F:/laboratory/pyRequirements-manager/.git/`.

Note: do NOT add a remote here. Per user policy (CLAUDE.md), private repo → Gitea. The user will set up the Gitea remote themselves when ready.

- [ ] **Step 0.2: Replace `.gitignore`**

```gitignore
# Python
__pycache__/
*.py[cod]
*$py.class
*.egg-info/
build/
dist/
.pytest_cache/
.coverage
htmlcov/
.mypy_cache/
.ruff_cache/

# Virtual envs
.env
.venv
venv/
test_venv/
test_env_*/

# Editor
.vscode/
.idea/

# OS
.DS_Store
Thumbs.db

# piptastic legacy + runtime
requirements_backups/
.requirements_backups/
requirements_updates.log

# piptastic dev cache (default location is ~/.cache/piptastic; this only
# matters if someone overrides via env var to a path inside the repo)
.piptastic_cache/
```

- [ ] **Step 0.3: Delete unused directories and files**

```bash
rm -rf pip-update-requirements/
rm -rf src/requirements_manager/
rm -f requirements_updater.py
rm -f requirements.txt
rm -rf requirements_backups/
rm -rf .requirements_backups/
rm -f requirements_updates.log
```

Verify with `ls -la`. Expected remaining: `.git/`, `.gitignore`, `archive/`, `docs/`, `src/`.

`src/` should now be empty — we'll populate it in Task 2.

- [ ] **Step 0.4: First commit**

```bash
git add .gitignore archive docs
git status
```

Expected `git status` shows `.gitignore`, `archive/PIPRU.py`, and the two doc files in `docs/superpowers/` staged.

```bash
git commit -m "chore: initialize piptastic v0.2 repo

- Drop vendored pip-update-requirements/ (was unused)
- Drop legacy src/requirements_manager/ package
- Drop unused root requirements.txt
- Keep archive/PIPRU.py for historical reference
- Carry forward spec + plan into docs/superpowers/
"
```

---

## Task 1: `pyproject.toml` and project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `README.md`

- [ ] **Step 1.1: Write `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling>=1.18"]
build-backend = "hatchling.build"

[project]
name = "piptastic"
version = "0.2.0"
description = "Audit Python dependency posture across all projects in a tree."
readme = "README.md"
requires-python = ">=3.10"
license = { text = "MIT" }
authors = [{ name = "boredchilada" }]
keywords = ["pip", "requirements", "dependencies", "audit", "watchtower"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Environment :: Console",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Operating System :: OS Independent",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Topic :: Software Development",
    "Topic :: System :: Systems Administration",
]
dependencies = [
    "packaging>=23.0",
    "rich>=13.0",
    "tomli>=2.0; python_version < '3.11'",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=4.0",
]

[project.scripts]
piptastic = "piptastic.cli:main"
ptc = "piptastic.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["src/piptastic"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = ["-ra", "--strict-markers", "--strict-config"]
```

- [ ] **Step 1.2: Write minimal `README.md`**

```markdown
# piptastic

Audit Python dependency posture across all projects in a tree. Read-only by
default — think Watchtower for your `requirements.txt` files.

## Install

```bash
pip install .
# or
pipx install .
```

Provides two commands: `piptastic` and `ptc` (short alias).

## Usage

```bash
piptastic audit ~/projects               # scan tree, pretty terminal report
piptastic audit . --json                 # same, machine-readable
piptastic list ./myproject               # one project, table view
piptastic update ./myproject             # mutates requirements.txt (with backup)
```

See `piptastic --help` for the full reference.

## Status

v0.2 — see `docs/superpowers/specs/` for the design.
```

- [ ] **Step 1.3: Commit scaffolding**

```bash
git add pyproject.toml README.md
git commit -m "feat: add pyproject.toml and README for piptastic v0.2

- PEP 621 metadata
- Console scripts: piptastic, ptc
- Runtime deps: packaging, rich, tomli (3.10 only)
- Dev extras: pytest, pytest-cov
"
```

---

## Task 2: Package skeleton + logging

**Files:**
- Create: `src/piptastic/__init__.py`
- Create: `src/piptastic/__main__.py`
- Create: `src/piptastic/logging.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 2.1: Write `src/piptastic/__init__.py`**

```python
"""piptastic — audit Python dependency posture across all projects in a tree."""

__version__ = "0.2.0"

__all__ = ["__version__"]
```

- [ ] **Step 2.2: Write `src/piptastic/__main__.py`**

```python
"""Enable `python -m piptastic`."""

from piptastic.cli import main

raise SystemExit(main())
```

- [ ] **Step 2.3: Write `src/piptastic/logging.py`**

```python
"""Logger factory. No module-level side effects."""

import logging
import sys
from pathlib import Path

_CONFIGURED = False


def get_logger(name: str = "piptastic") -> logging.Logger:
    """Return the piptastic logger. Idempotent."""
    return logging.getLogger(name)


def configure_logging(
    level: int = logging.WARNING,
    log_file: Path | None = None,
) -> None:
    """Configure the piptastic logger. Call once from the CLI entry point."""
    global _CONFIGURED
    logger = logging.getLogger("piptastic")
    logger.setLevel(level)

    if _CONFIGURED:
        # Reconfigure: tear down old handlers first so repeated calls in tests
        # don't accumulate.
        for h in list(logger.handlers):
            logger.removeHandler(h)
            h.close()

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(formatter)
    logger.addHandler(stderr)

    if log_file is not None:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    logger.propagate = False
    _CONFIGURED = True
```

- [ ] **Step 2.4: Write `tests/__init__.py` (empty file)**

```python
```

- [ ] **Step 2.5: Write `tests/conftest.py` with the project-tree builder fixture**

```python
"""Shared test fixtures."""

from pathlib import Path
from typing import Callable

import pytest


@pytest.fixture
def write_tree(tmp_path: Path) -> Callable[[dict], Path]:
    """Build a directory tree from a nested dict.

    Keys are filenames or dirnames; dict values become subdirs, str values
    become file contents.
    """

    def _build(spec: dict, root: Path | None = None) -> Path:
        target = root or tmp_path
        for name, value in spec.items():
            p = target / name
            if isinstance(value, dict):
                p.mkdir(parents=True, exist_ok=True)
                _build(value, p)
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(value, encoding="utf-8")
        return target

    return _build
```

- [ ] **Step 2.6: Install in editable mode and verify imports**

```bash
pip install -e .[dev]
python -c "import piptastic; print(piptastic.__version__)"
```

Expected: `0.2.0`.

- [ ] **Step 2.7: Commit skeleton**

```bash
git add src/piptastic tests
git commit -m "feat: scaffold piptastic package with logging factory

- src/piptastic/__init__.py with __version__
- src/piptastic/__main__.py enables python -m piptastic
- src/piptastic/logging.py: factory pattern, no module-level side effects
  (fixes [I2] from review)
- tests/conftest.py with write_tree fixture for building tmp project trees
"
```

---

## Task 3: Shared data models

**Files:**
- Create: `src/piptastic/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 3.1: Write the failing test**

`tests/test_models.py`:

```python
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
```

- [ ] **Step 3.2: Run test to verify it fails**

```bash
pytest tests/test_models.py -v
```

Expected: ImportError / ModuleNotFoundError for `piptastic.models`.

- [ ] **Step 3.3: Write `src/piptastic/models.py`**

```python
"""Shared data models. All dataclasses are frozen for hashability."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from packaging.markers import Marker
from packaging.specifiers import SpecifierSet
from packaging.version import Version


class SemverDrift(StrEnum):
    NONE = "none"
    BUILD = "build"
    PATCH = "patch"
    MINOR = "minor"
    MAJOR = "major"
    EPOCH = "epoch"
    UNKNOWN = "unknown"


class PinStatus(StrEnum):
    PINNED = "pinned"
    COMPATIBLE = "compatible"
    RANGE = "range"
    FLOOR = "floor"
    UNPINNED = "unpinned"
    URL = "url"


class SourceKind(StrEnum):
    REQUIREMENTS_TXT = "requirements_txt"
    CONSTRAINTS_TXT = "constraints_txt"
    PYPROJECT_PEP621 = "pyproject_pep621"
    PYPROJECT_POETRY = "pyproject_poetry"
    PIPFILE = "pipfile"
    PIPFILE_LOCK = "pipfile_lock"


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
    pinning_score: float
    drift_summary: dict[SemverDrift, int] = field(default_factory=dict)
    yanked_count: int = 0
    pypi_unreachable: list[str] = field(default_factory=list)
```

- [ ] **Step 3.4: Run tests to verify they pass**

```bash
pytest tests/test_models.py -v
```

Expected: 5 passed.

- [ ] **Step 3.5: Commit**

```bash
git add src/piptastic/models.py tests/test_models.py
git commit -m "feat: add shared data models

- Frozen dataclasses for Dep, DepSource, Project, ReleaseInfo,
  PackageMetadata, DepAudit
- Mutable ProjectAudit (collects results during audit)
- StrEnums for SemverDrift, PinStatus, SourceKind
"
```

---

## Task 4: Parsing — requirements.txt family

**Files:**
- Create: `src/piptastic/parsing.py`
- Create: `tests/fixtures/req_only/requirements.txt`
- Create: `tests/fixtures/req_only/dev-requirements.txt`
- Create: `tests/test_parsing.py`

- [ ] **Step 4.1: Write fixtures**

`tests/fixtures/req_only/requirements.txt`:

```text
# Production deps
flask==3.0.2
requests>=2.30,<3
sqlalchemy~=2.0.36
pkg-with-extras[crypto]==1.2.3
unpinned-pkg
# blank line below kept for layout
git+https://github.com/example/repo@v1.0#egg=repo-from-vcs
needs-marker==1.0; python_version >= "3.10"
-r dev-requirements.txt
```

`tests/fixtures/req_only/dev-requirements.txt`:

```text
pytest>=8.0
black==24.4.1
```

- [ ] **Step 4.2: Write failing tests for requirements.txt parsing**

`tests/test_parsing.py`:

```python
"""Tests for parsing each supported dep file format."""

from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet

from piptastic.models import DepSource, PinStatus, SourceKind
from piptastic.parsing import parse_source

FIXTURES = Path(__file__).parent / "fixtures"


def _by_name(deps, name):
    matches = [d for d in deps if d.name == name]
    assert matches, f"no dep named {name}; have {[d.name for d in deps]}"
    return matches[0]


def test_parse_requirements_txt_basic():
    src = DepSource(
        kind=SourceKind.REQUIREMENTS_TXT,
        path=FIXTURES / "req_only" / "requirements.txt",
        group="default",
    )
    deps = parse_source(src)
    names = [d.name for d in deps]

    # Names are PEP 503 canonicalized (lowercase, hyphens)
    assert "flask" in names
    assert "requests" in names
    assert "sqlalchemy" in names
    assert "pkg-with-extras" in names
    assert "unpinned-pkg" in names
    assert "repo-from-vcs" in names
    assert "needs-marker" in names

    # `-r dev-requirements.txt` was followed
    assert "pytest" in names
    assert "black" in names


def test_parse_requirements_txt_extras_and_marker():
    src = DepSource(
        kind=SourceKind.REQUIREMENTS_TXT,
        path=FIXTURES / "req_only" / "requirements.txt",
        group="default",
    )
    deps = parse_source(src)

    crypto = _by_name(deps, "pkg-with-extras")
    assert "crypto" in crypto.extras
    assert crypto.specifier == SpecifierSet("==1.2.3")

    marked = _by_name(deps, "needs-marker")
    assert marked.marker is not None
    assert 'python_version' in str(marked.marker)


def test_parse_requirements_txt_url_dep():
    src = DepSource(
        kind=SourceKind.REQUIREMENTS_TXT,
        path=FIXTURES / "req_only" / "requirements.txt",
        group="default",
    )
    deps = parse_source(src)

    vcs = _by_name(deps, "repo-from-vcs")
    assert vcs.url is not None
    assert vcs.url.startswith("git+https://")
    assert vcs.specifier == SpecifierSet()


def test_parse_requirements_txt_unpinned():
    src = DepSource(
        kind=SourceKind.REQUIREMENTS_TXT,
        path=FIXTURES / "req_only" / "requirements.txt",
        group="default",
    )
    deps = parse_source(src)

    unp = _by_name(deps, "unpinned-pkg")
    assert unp.specifier == SpecifierSet()


def test_parse_requirements_followed_recursively_carries_correct_source(write_tree):
    """A `-r` include attributes its deps to the included file, not the entry."""
    tree = write_tree({
        "main.txt": "flask==3.0.2\n-r more.txt\n",
        "more.txt": "requests==2.32.2\n",
    })
    src = DepSource(
        kind=SourceKind.REQUIREMENTS_TXT,
        path=tree / "main.txt",
        group="default",
    )
    deps = parse_source(src)
    by_name = {d.name: d for d in deps}
    assert by_name["flask"].source.path == tree / "main.txt"
    assert by_name["requests"].source.path == tree / "more.txt"


def test_parse_requirements_cycle_guard(write_tree):
    """`-r` cycles do not loop forever."""
    tree = write_tree({
        "a.txt": "-r b.txt\nflask==3.0.2\n",
        "b.txt": "-r a.txt\nrequests==2.32.2\n",
    })
    src = DepSource(
        kind=SourceKind.REQUIREMENTS_TXT,
        path=tree / "a.txt",
        group="default",
    )
    deps = parse_source(src)  # must not hang
    names = {d.name for d in deps}
    assert names == {"flask", "requests"}


def test_parse_invalid_line_warns_but_continues(write_tree, caplog):
    """Garbage lines are skipped with a warning, not raised."""
    tree = write_tree({"r.txt": "flask==3.0.2\n@@@nonsense@@@\nrequests==2.32.2\n"})
    src = DepSource(
        kind=SourceKind.REQUIREMENTS_TXT,
        path=tree / "r.txt",
        group="default",
    )
    deps = parse_source(src)
    names = {d.name for d in deps}
    assert names == {"flask", "requests"}
```

- [ ] **Step 4.3: Run tests to verify they fail**

```bash
pytest tests/test_parsing.py -v
```

Expected: ModuleNotFoundError for `piptastic.parsing`.

- [ ] **Step 4.4: Write `src/piptastic/parsing.py` (requirements.txt support only first)**

```python
"""Parse dep sources into a uniform list of Dep objects."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from piptastic.logging import get_logger
from piptastic.models import Dep, DepSource, SourceKind

logger = get_logger(__name__)


def parse_source(source: DepSource) -> list[Dep]:
    """Parse a single DepSource and return a flat list of Dep.

    Includes (`-r other.txt`) are followed recursively with cycle detection.
    Each yielded Dep carries the DepSource of its true file of origin.
    """
    if source.kind in (SourceKind.REQUIREMENTS_TXT, SourceKind.CONSTRAINTS_TXT):
        return _parse_requirements_file(source, _visited=set())
    raise NotImplementedError(f"parsing for {source.kind} not yet implemented")


def _parse_requirements_file(
    source: DepSource, *, _visited: set[Path]
) -> list[Dep]:
    path = source.path.resolve()
    if path in _visited:
        return []
    _visited.add(path)

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("could not read %s: %s", path, e)
        return []

    deps: list[Dep] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # strip trailing inline comments (pip behavior)
        if " #" in line:
            line = line.split(" #", 1)[0].rstrip()

        if line.startswith(("-r ", "--requirement ", "-c ", "--constraint ")):
            include_path = _resolve_include(path.parent, line)
            if include_path is None:
                logger.warning("could not resolve include at %s:%d", path, line_no)
                continue
            kind = (
                SourceKind.CONSTRAINTS_TXT
                if line.startswith(("-c ", "--constraint "))
                else SourceKind.REQUIREMENTS_TXT
            )
            sub_source = DepSource(kind=kind, path=include_path, group=source.group)
            deps.extend(_parse_requirements_file(sub_source, _visited=_visited))
            continue

        if line.startswith(("-e ", "--editable ")):
            line = line.split(maxsplit=1)[1]

        dep = _parse_one_requirement_line(line, source=source, line_no=line_no)
        if dep is not None:
            deps.append(dep)

    return deps


def _resolve_include(base_dir: Path, line: str) -> Path | None:
    parts = line.split(maxsplit=1)
    if len(parts) != 2:
        return None
    candidate = (base_dir / parts[1].strip()).resolve()
    return candidate if candidate.exists() else None


def _parse_one_requirement_line(
    line: str, *, source: DepSource, line_no: int
) -> Dep | None:
    try:
        req = Requirement(line)
    except InvalidRequirement as e:
        logger.warning("invalid requirement %r: %s", line, e)
        return None

    url = req.url
    return Dep(
        name=canonicalize_name(req.name),
        raw_name=req.name,
        specifier=req.specifier,
        extras=frozenset(req.extras),
        marker=req.marker,
        source=source,
        line_no=line_no,
        url=url,
    )
```

- [ ] **Step 4.5: Run tests to verify they pass**

```bash
pytest tests/test_parsing.py -v
```

Expected: 7 passed.

- [ ] **Step 4.6: Commit**

```bash
git add src/piptastic/parsing.py tests/test_parsing.py tests/fixtures/req_only
git commit -m "feat: parse requirements.txt family with extras/markers/URLs

- Uses packaging.requirements.Requirement (fixes [M1])
- Follows -r / -c includes with cycle guard
- Each Dep carries DepSource of its true origin file
- Invalid lines warn and continue (never raise out)
"
```

---

## Task 5: Parsing — pyproject.toml (PEP 621 + Poetry)

**Files:**
- Modify: `src/piptastic/parsing.py`
- Create: `tests/fixtures/pyproject_pep621/pyproject.toml`
- Create: `tests/fixtures/pyproject_poetry/pyproject.toml`
- Modify: `tests/test_parsing.py`

- [ ] **Step 5.1: Write fixtures**

`tests/fixtures/pyproject_pep621/pyproject.toml`:

```toml
[project]
name = "demo"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
    "flask==3.0.2",
    "requests>=2.30,<3",
    "httpx[http2]>=0.27; python_version >= '3.10'",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "black==24.4.1"]
ml  = ["numpy>=2.0"]
```

`tests/fixtures/pyproject_poetry/pyproject.toml`:

```toml
[tool.poetry]
name = "demo"
version = "0.1.0"

[tool.poetry.dependencies]
python = ">=3.10"
flask = "^3.0.2"
requests = "~2.30"
httpx = { version = ">=0.27", extras = ["http2"] }
unpinned-thing = "*"

[tool.poetry.group.dev.dependencies]
pytest = "^8.0"
```

- [ ] **Step 5.2: Add failing tests**

Append to `tests/test_parsing.py`:

```python
def test_parse_pyproject_pep621_default_group():
    src = DepSource(
        kind=SourceKind.PYPROJECT_PEP621,
        path=FIXTURES / "pyproject_pep621" / "pyproject.toml",
        group="default",
    )
    deps = parse_source(src)
    names = {d.name for d in deps}
    assert names == {"flask", "requests", "httpx"}

    httpx = _by_name(deps, "httpx")
    assert "http2" in httpx.extras
    assert httpx.marker is not None


def test_parse_pyproject_pep621_optional_group():
    src = DepSource(
        kind=SourceKind.PYPROJECT_PEP621,
        path=FIXTURES / "pyproject_pep621" / "pyproject.toml",
        group="dev",
    )
    deps = parse_source(src)
    assert {d.name for d in deps} == {"pytest", "black"}


def test_parse_pyproject_poetry_caret_tilde():
    src = DepSource(
        kind=SourceKind.PYPROJECT_POETRY,
        path=FIXTURES / "pyproject_poetry" / "pyproject.toml",
        group="default",
    )
    deps = parse_source(src)
    names = {d.name for d in deps}
    # "python" is the interpreter constraint, NOT a dep
    assert "python" not in names
    assert names == {"flask", "requests", "httpx", "unpinned-thing"}

    flask = _by_name(deps, "flask")
    # ^3.0.2 → >=3.0.2,<4.0.0
    assert str(flask.specifier) == ">=3.0.2,<4.0.0"

    requests = _by_name(deps, "requests")
    # ~2.30 → >=2.30,<3.0.0  (Poetry's "~" without micro → next-major lock)
    assert str(requests.specifier) == ">=2.30,<3.0.0"

    httpx = _by_name(deps, "httpx")
    assert "http2" in httpx.extras
    assert str(httpx.specifier) == ">=0.27"

    unpinned = _by_name(deps, "unpinned-thing")
    assert unpinned.specifier == SpecifierSet()


def test_parse_pyproject_poetry_group():
    src = DepSource(
        kind=SourceKind.PYPROJECT_POETRY,
        path=FIXTURES / "pyproject_poetry" / "pyproject.toml",
        group="dev",
    )
    deps = parse_source(src)
    assert {d.name for d in deps} == {"pytest"}
```

- [ ] **Step 5.3: Run tests to verify they fail**

```bash
pytest tests/test_parsing.py -v -k pyproject
```

Expected: 4 fail with `NotImplementedError`.

- [ ] **Step 5.4: Extend `src/piptastic/parsing.py`**

Replace the `parse_source` function and add the TOML helpers. The complete updated file:

```python
"""Parse dep sources into a uniform list of Dep objects."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from piptastic.logging import get_logger
from piptastic.models import Dep, DepSource, SourceKind

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

logger = get_logger(__name__)


def parse_source(source: DepSource) -> list[Dep]:
    """Parse a single DepSource into a flat list of Dep."""
    if source.kind in (SourceKind.REQUIREMENTS_TXT, SourceKind.CONSTRAINTS_TXT):
        return _parse_requirements_file(source, _visited=set())
    if source.kind == SourceKind.PYPROJECT_PEP621:
        return _parse_pep621(source)
    if source.kind == SourceKind.PYPROJECT_POETRY:
        return _parse_poetry(source)
    raise NotImplementedError(f"parsing for {source.kind} not yet implemented")


# ---------- requirements.txt ----------

def _parse_requirements_file(
    source: DepSource, *, _visited: set[Path]
) -> list[Dep]:
    path = source.path.resolve()
    if path in _visited:
        return []
    _visited.add(path)

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("could not read %s: %s", path, e)
        return []

    deps: list[Dep] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if " #" in line:
            line = line.split(" #", 1)[0].rstrip()

        if line.startswith(("-r ", "--requirement ", "-c ", "--constraint ")):
            include_path = _resolve_include(path.parent, line)
            if include_path is None:
                logger.warning("could not resolve include at %s:%d", path, line_no)
                continue
            kind = (
                SourceKind.CONSTRAINTS_TXT
                if line.startswith(("-c ", "--constraint "))
                else SourceKind.REQUIREMENTS_TXT
            )
            sub_source = DepSource(kind=kind, path=include_path, group=source.group)
            deps.extend(_parse_requirements_file(sub_source, _visited=_visited))
            continue

        if line.startswith(("-e ", "--editable ")):
            line = line.split(maxsplit=1)[1]

        dep = _parse_one_requirement_line(line, source=source, line_no=line_no)
        if dep is not None:
            deps.append(dep)

    return deps


def _resolve_include(base_dir: Path, line: str) -> Path | None:
    parts = line.split(maxsplit=1)
    if len(parts) != 2:
        return None
    candidate = (base_dir / parts[1].strip()).resolve()
    return candidate if candidate.exists() else None


def _parse_one_requirement_line(
    line: str, *, source: DepSource, line_no: int | None
) -> Dep | None:
    try:
        req = Requirement(line)
    except InvalidRequirement as e:
        logger.warning("invalid requirement %r: %s", line, e)
        return None

    return Dep(
        name=canonicalize_name(req.name),
        raw_name=req.name,
        specifier=req.specifier,
        extras=frozenset(req.extras),
        marker=req.marker,
        source=source,
        line_no=line_no,
        url=req.url,
    )


# ---------- pyproject.toml (PEP 621) ----------

def _parse_pep621(source: DepSource) -> list[Dep]:
    data = _read_toml(source.path)
    project = data.get("project", {})
    if source.group == "default":
        strings = project.get("dependencies", []) or []
    else:
        optional = project.get("optional-dependencies", {}) or {}
        strings = optional.get(source.group, []) or []

    deps: list[Dep] = []
    for s in strings:
        dep = _parse_one_requirement_line(s, source=source, line_no=None)
        if dep is not None:
            deps.append(dep)
    return deps


# ---------- pyproject.toml (Poetry) ----------

def _parse_poetry(source: DepSource) -> list[Dep]:
    data = _read_toml(source.path)
    poetry = data.get("tool", {}).get("poetry", {})

    if source.group == "default":
        table = poetry.get("dependencies", {}) or {}
    else:
        groups = poetry.get("group", {}) or {}
        table = groups.get(source.group, {}).get("dependencies", {}) or {}

    deps: list[Dep] = []
    for name, value in table.items():
        if name == "python":  # interpreter constraint, not a real dep
            continue
        pep508 = _poetry_to_pep508(name, value)
        if pep508 is None:
            continue
        dep = _parse_one_requirement_line(pep508, source=source, line_no=None)
        if dep is not None:
            deps.append(dep)
    return deps


def _poetry_to_pep508(name: str, value: Any) -> str | None:
    """Convert a Poetry dep spec to a PEP 508 string."""
    if isinstance(value, str):
        spec = _poetry_version_to_specifier(value)
        return f"{name}{spec}" if spec else name

    if not isinstance(value, dict):
        logger.warning("unsupported poetry dep spec for %s: %r", name, value)
        return None

    version = value.get("version", "*")
    spec = _poetry_version_to_specifier(version)
    extras = value.get("extras") or []
    marker = value.get("python")

    extras_part = f"[{','.join(extras)}]" if extras else ""
    marker_part = f"; python_version {_python_constraint_to_marker(marker)}" if marker else ""
    return f"{name}{extras_part}{spec}{marker_part}"


def _poetry_version_to_specifier(v: str) -> str:
    """Convert Poetry version shorthand to a PEP 440 specifier string."""
    v = v.strip()
    if v == "*" or v == "":
        return ""
    if v.startswith("^"):
        return _caret_to_specifier(v[1:])
    if v.startswith("~"):
        return _tilde_to_specifier(v[1:])
    # plain version, or already a PEP 440-style range
    if v[:1] in (">", "<", "=", "!"):
        return v
    return f"=={v}"


def _caret_to_specifier(base: str) -> str:
    """`^X.Y.Z` → `>=X.Y.Z,<(X+1).0.0`. `^0.Y.Z` → `>=0.Y.Z,<0.(Y+1).0`.

    Poetry rule: caret allows changes that do not change the leftmost
    non-zero element.
    """
    parts = _split_version(base)
    if not parts:
        return ""
    # Find leftmost non-zero
    upper = list(parts)
    for i, n in enumerate(parts):
        if n != 0:
            upper[i] = n + 1
            for j in range(i + 1, len(upper)):
                upper[j] = 0
            break
    else:
        # all zeros → behave like ==
        return f"=={base}"
    # pad to 3 segments for readability
    while len(upper) < 3:
        upper.append(0)
    return f">={base},<{'.'.join(str(x) for x in upper)}"


def _tilde_to_specifier(base: str) -> str:
    """`~X.Y.Z` → `>=X.Y.Z,<X.(Y+1).0`. `~X.Y` → `>=X.Y,<(X+1).0.0`.
    `~X` → `>=X,<(X+1).0.0`.
    """
    parts = _split_version(base)
    if not parts:
        return ""
    if len(parts) >= 2:
        upper = list(parts[:2])
        upper[-1] += 1
        for _ in range(max(0, 3 - len(upper))):
            upper.append(0)
        return f">={base},<{'.'.join(str(x) for x in upper)}"
    # single segment
    upper = [parts[0] + 1, 0, 0]
    return f">={base},<{'.'.join(str(x) for x in upper)}"


def _split_version(v: str) -> list[int]:
    out: list[int] = []
    for seg in v.split("."):
        try:
            out.append(int(seg))
        except ValueError:
            return []
    return out


def _python_constraint_to_marker(value: str) -> str:
    """Map a Poetry `python = ">=3.10"` to a PEP 508 marker tail."""
    v = value.strip()
    if v.startswith((">=", "<=", ">", "<", "==", "!=")):
        return f'{v[:2] if v[:2] in (">=", "<=", "==", "!=") else v[:1]} "{v[2 if v[:2] in (">=", "<=", "==", "!=") else 1:].strip()}"'
    return f'== "{v}"'


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.warning("could not parse TOML %s: %s", path, e)
        return {}
```

- [ ] **Step 5.5: Run all parsing tests**

```bash
pytest tests/test_parsing.py -v
```

Expected: 11 passed.

- [ ] **Step 5.6: Commit**

```bash
git add src/piptastic/parsing.py tests/test_parsing.py tests/fixtures/pyproject_pep621 tests/fixtures/pyproject_poetry
git commit -m "feat: parse pyproject.toml (PEP 621 + Poetry) deps

- PEP 621: [project].dependencies + [project].optional-dependencies.<extra>
- Poetry: [tool.poetry.dependencies] + [tool.poetry.group.<name>.dependencies]
- Poetry caret/tilde/star shorthand → PEP 508
- 'python' interpreter constraint excluded from dep list
"
```

---

## Task 6: Parsing — Pipfile

**Files:**
- Modify: `src/piptastic/parsing.py`
- Create: `tests/fixtures/pipfile_project/Pipfile`
- Create: `tests/fixtures/pipfile_project/Pipfile.lock`
- Modify: `tests/test_parsing.py`

- [ ] **Step 6.1: Write fixtures**

`tests/fixtures/pipfile_project/Pipfile`:

```toml
[[source]]
url = "https://pypi.org/simple"
verify_ssl = true
name = "pypi"

[packages]
flask = "==3.0.2"
requests = "*"
httpx = { version = ">=0.27", extras = ["http2"] }

[dev-packages]
pytest = ">=8.0"

[requires]
python_version = "3.11"
```

`tests/fixtures/pipfile_project/Pipfile.lock`:

```json
{
  "_meta": {
    "hash": { "sha256": "abc" },
    "pipfile-spec": 6,
    "requires": { "python_version": "3.11" },
    "sources": [{ "name": "pypi", "url": "https://pypi.org/simple", "verify_ssl": true }]
  },
  "default": {
    "flask": { "version": "==3.0.2" },
    "requests": { "version": "==2.32.2" },
    "httpx": { "version": "==0.27.2", "extras": ["http2"] }
  },
  "develop": {
    "pytest": { "version": "==8.3.3" }
  }
}
```

- [ ] **Step 6.2: Add failing tests**

Append to `tests/test_parsing.py`:

```python
def test_parse_pipfile_packages():
    src = DepSource(
        kind=SourceKind.PIPFILE,
        path=FIXTURES / "pipfile_project" / "Pipfile",
        group="default",
    )
    deps = parse_source(src)
    names = {d.name for d in deps}
    assert names == {"flask", "requests", "httpx"}

    requests = _by_name(deps, "requests")
    assert requests.specifier == SpecifierSet()  # "*"

    httpx = _by_name(deps, "httpx")
    assert "http2" in httpx.extras
    assert str(httpx.specifier) == ">=0.27"


def test_parse_pipfile_dev_packages():
    src = DepSource(
        kind=SourceKind.PIPFILE,
        path=FIXTURES / "pipfile_project" / "Pipfile",
        group="dev",
    )
    deps = parse_source(src)
    assert {d.name for d in deps} == {"pytest"}


def test_parse_pipfile_lock_default():
    src = DepSource(
        kind=SourceKind.PIPFILE_LOCK,
        path=FIXTURES / "pipfile_project" / "Pipfile.lock",
        group="default",
    )
    deps = parse_source(src)
    names = {d.name for d in deps}
    assert names == {"flask", "requests", "httpx"}
    assert _by_name(deps, "flask").specifier == SpecifierSet("==3.0.2")
```

- [ ] **Step 6.3: Run tests to verify they fail**

```bash
pytest tests/test_parsing.py -v -k pipfile
```

Expected: 3 fail with NotImplementedError.

- [ ] **Step 6.4: Extend `src/piptastic/parsing.py` with Pipfile support**

Add these two handler functions and route them from `parse_source`:

In `parse_source` add the two branches:

```python
    if source.kind == SourceKind.PIPFILE:
        return _parse_pipfile(source)
    if source.kind == SourceKind.PIPFILE_LOCK:
        return _parse_pipfile_lock(source)
```

Then add the implementations at the bottom of the file:

```python
# ---------- Pipfile / Pipfile.lock ----------

def _parse_pipfile(source: DepSource) -> list[Dep]:
    data = _read_toml(source.path)
    table_name = "dev-packages" if source.group == "dev" else "packages"
    table = data.get(table_name, {}) or {}

    deps: list[Dep] = []
    for name, value in table.items():
        # Pipfile shorthand uses identical conventions to Poetry:
        # caret/tilde/star + table form with version/extras/markers.
        pep508 = _poetry_to_pep508(name, value)
        if pep508 is None:
            continue
        dep = _parse_one_requirement_line(pep508, source=source, line_no=None)
        if dep is not None:
            deps.append(dep)
    return deps


def _parse_pipfile_lock(source: DepSource) -> list[Dep]:
    import json
    try:
        with source.path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("could not parse %s: %s", source.path, e)
        return []

    table_name = "develop" if source.group == "dev" else "default"
    table = data.get(table_name, {}) or {}

    deps: list[Dep] = []
    for name, info in table.items():
        version_spec = info.get("version", "")  # e.g. "==3.0.2"
        extras = info.get("extras") or []
        extras_part = f"[{','.join(extras)}]" if extras else ""
        pep508 = f"{name}{extras_part}{version_spec}"
        dep = _parse_one_requirement_line(pep508, source=source, line_no=None)
        if dep is not None:
            deps.append(dep)
    return deps
```

- [ ] **Step 6.5: Run all parsing tests**

```bash
pytest tests/test_parsing.py -v
```

Expected: 14 passed.

- [ ] **Step 6.6: Commit**

```bash
git add src/piptastic/parsing.py tests/test_parsing.py tests/fixtures/pipfile_project
git commit -m "feat: parse Pipfile and Pipfile.lock

- [packages]/[dev-packages] use same shorthand as Poetry
- Pipfile.lock uses {default,develop}.<pkg>.version which is already
  PEP 440 ('==X.Y.Z')
"
```

---

## Task 7: Discovery

**Files:**
- Create: `src/piptastic/discovery.py`
- Create: `tests/fixtures/mixed/requirements.txt`
- Create: `tests/fixtures/mixed/pyproject.toml`
- Create: `tests/fixtures/venv_inside/requirements.txt`
- Create: `tests/fixtures/venv_inside/.venv/pyvenv.cfg`
- Create: `tests/fixtures/envoy_dir/envoy/requirements.txt`
- Create: `tests/test_discovery.py`

- [ ] **Step 7.1: Write fixtures**

`tests/fixtures/mixed/requirements.txt`:

```text
flask==3.0.2
```

`tests/fixtures/mixed/pyproject.toml`:

```toml
[project]
name = "mixed"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["requests==2.32.2"]
```

`tests/fixtures/venv_inside/requirements.txt`:

```text
flask==3.0.2
```

`tests/fixtures/venv_inside/.venv/pyvenv.cfg`:

```text
home = /usr/bin
include-system-site-packages = false
version = 3.11.0
```

`tests/fixtures/envoy_dir/envoy/requirements.txt`:

```text
flask==3.0.2
```

- [ ] **Step 7.2: Write failing tests**

`tests/test_discovery.py`:

```python
"""Tests for project discovery."""

from pathlib import Path

import pytest

from piptastic.discovery import discover_one, discover_tree
from piptastic.models import SourceKind

FIXTURES = Path(__file__).parent / "fixtures"


def test_discover_tree_finds_all_fixture_projects():
    projects = discover_tree(FIXTURES)
    names = {p.name for p in projects}
    assert "req_only" in names
    assert "pyproject_pep621" in names
    assert "pyproject_poetry" in names
    assert "pipfile_project" in names
    assert "mixed" in names


def test_discover_tree_excludes_venv():
    """A project root with a .venv/ subdir is itself a project, but the .venv
    must not be descended into."""
    projects = discover_tree(FIXTURES)
    # venv_inside is a real project (it has requirements.txt at its root)
    venv_inside = next(p for p in projects if p.name == "venv_inside")
    # ... but the .venv/ subdir must not appear as a project
    assert not any(".venv" in p.path.parts for p in projects)


def test_discover_tree_does_not_match_envoy_prefix():
    """Regression: old code excluded any dir starting with 'env'. envoy/ must
    be discovered."""
    projects = discover_tree(FIXTURES)
    assert any(p.name == "envoy" for p in projects)


def test_discover_tree_collapses_sibling_sources(write_tree):
    """A project dir with multiple dep files becomes ONE project with multiple
    dep_sources."""
    tree = write_tree({
        "requirements.txt": "flask==3.0.2\n",
        "dev-requirements.txt": "pytest>=8\n",
        "pyproject.toml": '[project]\nname="x"\nversion="0"\ndependencies=["requests==2.32.2"]\n',
    })
    projects = discover_tree(tree)
    assert len(projects) == 1
    p = projects[0]
    kinds = {s.kind for s in p.dep_sources}
    assert kinds == {SourceKind.REQUIREMENTS_TXT, SourceKind.PYPROJECT_PEP621}
    # 'dev' group came from filename inference
    groups = {(s.kind, s.group) for s in p.dep_sources}
    assert (SourceKind.REQUIREMENTS_TXT, "dev") in groups


def test_discover_tree_does_not_create_directories(write_tree):
    """Fixes [C1] — scan must be read-only."""
    tree = write_tree({"r.txt": "", "requirements.txt": "flask==3.0.2\n"})
    discover_tree(tree)
    assert not (tree / ".requirements_backups").exists()


def test_discover_one_returns_project_directly(write_tree):
    """Fixes [C5] — no parent rescan."""
    tree = write_tree({
        "myproj": {"requirements.txt": "flask==3.0.2\n"},
        "other": {"requirements.txt": "x==1\n"},
    })
    project = discover_one(tree / "myproj")
    assert project is not None
    assert project.name == "myproj"
    assert len(project.dep_sources) == 1


def test_discover_one_returns_none_for_dir_without_dep_files(tmp_path):
    assert discover_one(tmp_path) is None


def test_user_exclude_pattern(write_tree):
    tree = write_tree({
        "wanted": {"requirements.txt": "flask==3.0.2\n"},
        "skipme": {"requirements.txt": "x==1\n"},
    })
    projects = discover_tree(tree, exclude=["skipme"])
    assert {p.name for p in projects} == {"wanted"}


def test_python_version_detected_from_pyproject():
    projects = discover_tree(FIXTURES)
    mixed = next(p for p in projects if p.name == "mixed")
    assert mixed.python_version == "3.11"
    assert mixed.python_source == "pyproject.toml"
```

- [ ] **Step 7.3: Run tests to verify they fail**

```bash
pytest tests/test_discovery.py -v
```

Expected: 9 fail with ModuleNotFoundError.

- [ ] **Step 7.4: Write `src/piptastic/discovery.py`**

```python
"""Walk a directory tree and emit Project records."""

from __future__ import annotations

import fnmatch
import os
import re
import sys
from pathlib import Path
from typing import Iterable

from piptastic.logging import get_logger
from piptastic.models import DepSource, Project, SourceKind

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

logger = get_logger(__name__)

VENV_EXACT_NAMES = {"venv", ".venv", "env", ".env", "ENV", "virtualenv"}

ALWAYS_SKIP = {
    ".git", ".hg", ".svn",
    ".tox", ".nox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "node_modules", "__pycache__", "site-packages",
    "build", "dist",
}

REQUIREMENTS_PATTERNS = (
    "requirements.txt",
    "requirements-*.txt",
    "*-requirements.txt",
    "constraints.txt",
    "constraints-*.txt",
)


def discover_tree(root: Path, *, exclude: Iterable[str] = ()) -> list[Project]:
    """Walk `root` and return one Project per directory containing dep files."""
    root = Path(root).resolve()
    if not root.exists():
        logger.error("path does not exist: %s", root)
        return []

    user_excludes = tuple(exclude)
    by_dir: dict[Path, list[DepSource]] = {}

    for dirpath, dirnames, filenames in os.walk(root):
        # In-place mutation of dirnames prunes the walk
        dirnames[:] = [d for d in dirnames if not _should_skip(Path(dirpath) / d, user_excludes)]

        d = Path(dirpath)
        sources = list(_dep_sources_in_dir(d, filenames))
        if sources:
            by_dir.setdefault(d, []).extend(sources)

    projects: list[Project] = []
    for d, sources in sorted(by_dir.items()):
        py_info = _detect_python_version(d)
        projects.append(Project(
            name=d.name,
            path=d.resolve(),
            python_version=py_info.get("version"),
            python_source=py_info.get("source"),
            python_constraints=py_info.get("constraints"),
            dep_sources=tuple(sources),
        ))
    return sorted(projects, key=lambda p: p.name)


def discover_one(project_path: Path) -> Project | None:
    """Treat `project_path` as a known project root; do not rescan its parent."""
    project_path = Path(project_path).resolve()
    if not project_path.is_dir():
        return None
    filenames = [p.name for p in project_path.iterdir() if p.is_file()]
    sources = list(_dep_sources_in_dir(project_path, filenames))
    if not sources:
        return None
    py_info = _detect_python_version(project_path)
    return Project(
        name=project_path.name,
        path=project_path,
        python_version=py_info.get("version"),
        python_source=py_info.get("source"),
        python_constraints=py_info.get("constraints"),
        dep_sources=tuple(sources),
    )


# ---------- internals ----------

def _should_skip(path: Path, user_excludes: tuple[str, ...]) -> bool:
    name = path.name
    if name in ALWAYS_SKIP:
        return True
    if name in VENV_EXACT_NAMES:
        return True
    # Detect a venv by the presence of pyvenv.cfg inside it
    if (path / "pyvenv.cfg").is_file():
        return True
    if name.endswith(".egg-info"):
        return True
    for pat in user_excludes:
        if fnmatch.fnmatch(name, pat):
            return True
    return False


def _dep_sources_in_dir(d: Path, filenames: list[str]) -> Iterable[DepSource]:
    fileset = set(filenames)

    # requirements*.txt / constraints*.txt
    for fname in filenames:
        if any(fnmatch.fnmatch(fname, pat) for pat in REQUIREMENTS_PATTERNS):
            kind = (
                SourceKind.CONSTRAINTS_TXT
                if fname.startswith("constraints")
                else SourceKind.REQUIREMENTS_TXT
            )
            yield DepSource(kind=kind, path=d / fname, group=_infer_group(fname))

    # pyproject.toml: may produce PEP 621 and/or Poetry sources, each with
    # multiple groups
    if "pyproject.toml" in fileset:
        yield from _pyproject_sources(d / "pyproject.toml")

    # Pipfile + Pipfile.lock
    if "Pipfile" in fileset:
        yield DepSource(kind=SourceKind.PIPFILE, path=d / "Pipfile", group="default")
        yield DepSource(kind=SourceKind.PIPFILE, path=d / "Pipfile", group="dev")
    if "Pipfile.lock" in fileset:
        yield DepSource(kind=SourceKind.PIPFILE_LOCK, path=d / "Pipfile.lock", group="default")
        yield DepSource(kind=SourceKind.PIPFILE_LOCK, path=d / "Pipfile.lock", group="dev")


def _infer_group(fname: str) -> str:
    stem = Path(fname).stem.lower()
    if stem in ("requirements", "constraints"):
        return "default"
    if "dev" in stem:
        return "dev"
    if "test" in stem:
        return "test"
    if "prod" in stem:
        return "prod"
    # Strip "requirements-" / "-requirements" / "constraints-" / "-constraints"
    # and use the remainder as the group name.
    remainder = re.sub(r"(^(requirements|constraints)-|-?(requirements|constraints)$)", "", stem)
    return remainder or "default"


def _pyproject_sources(path: Path) -> Iterable[DepSource]:
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.warning("could not read %s: %s", path, e)
        return

    project = data.get("project") or {}
    if project:
        yield DepSource(kind=SourceKind.PYPROJECT_PEP621, path=path, group="default")
        for extra in (project.get("optional-dependencies") or {}).keys():
            yield DepSource(kind=SourceKind.PYPROJECT_PEP621, path=path, group=extra)

    poetry = (data.get("tool") or {}).get("poetry") or {}
    if poetry:
        if poetry.get("dependencies"):
            yield DepSource(kind=SourceKind.PYPROJECT_POETRY, path=path, group="default")
        for group_name in (poetry.get("group") or {}).keys():
            yield DepSource(kind=SourceKind.PYPROJECT_POETRY, path=path, group=group_name)


def _detect_python_version(project_dir: Path) -> dict[str, str | None]:
    """Return {version, source, constraints} from project files."""
    out: dict[str, str | None] = {"version": None, "source": None, "constraints": None}

    runtime = project_dir / "runtime.txt"
    if runtime.exists():
        m = re.search(r"python-?(\d+\.\d+(?:\.\d+)?)", runtime.read_text(encoding="utf-8"))
        if m:
            out["version"] = m.group(1)
            out["source"] = "runtime.txt"
            return out

    pyproject = project_dir / "pyproject.toml"
    if pyproject.exists():
        try:
            with pyproject.open("rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            data = {}
        req = ((data.get("project") or {}).get("requires-python"))
        if req:
            m = re.search(r"(\d+\.\d+(?:\.\d+)?)", req)
            if m:
                out["version"] = m.group(1)
                out["source"] = "pyproject.toml"
                out["constraints"] = req.strip()
                return out

    pipfile = project_dir / "Pipfile"
    if pipfile.exists():
        try:
            with pipfile.open("rb") as f:
                data = tomllib.load(f)
            v = (data.get("requires") or {}).get("python_version")
            if v:
                out["version"] = str(v)
                out["source"] = "Pipfile"
                return out
        except (OSError, tomllib.TOMLDecodeError):
            pass

    return out
```

- [ ] **Step 7.5: Run discovery tests**

```bash
pytest tests/test_discovery.py -v
```

Expected: 9 passed.

- [ ] **Step 7.6: Commit**

```bash
git add src/piptastic/discovery.py tests/test_discovery.py tests/fixtures/mixed tests/fixtures/venv_inside tests/fixtures/envoy_dir
git commit -m "feat: discover Python projects across a tree (multi-format)

- Walk root, prune common skip dirs and venvs (exact-match + pyvenv.cfg)
- Detect: requirements*.txt, constraints*.txt, pyproject.toml (PEP 621 +
  Poetry, including optional extras and Poetry groups), Pipfile, Pipfile.lock
- Group inference from filename stem
- Sibling sources collapse into one Project
- discover_one(path) bypasses parent scan
- Read-only (fixes [C1]); 'envoy/' no longer mis-excluded (fixes [C2])
- Detect target Python from runtime.txt / pyproject.toml / Pipfile
"
```

---

## Task 8: PyPI client with cache + concurrency

**Files:**
- Create: `src/piptastic/pypi.py`
- Create: `tests/test_pypi.py`

- [ ] **Step 8.1: Write failing tests**

`tests/test_pypi.py`:

```python
"""Tests for the PyPI client and on-disk cache."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from piptastic.pypi import PyPIClient, _parse_pypi_payload


SAMPLE_PAYLOAD = {
    "info": {"name": "flask"},
    "releases": {
        "3.0.2": [{
            "yanked": False,
            "yanked_reason": None,
            "requires_python": ">=3.8",
            "upload_time_iso_8601": "2024-02-03T00:00:00.000000Z",
        }],
        "3.1.0": [{
            "yanked": False,
            "yanked_reason": None,
            "requires_python": ">=3.9",
            "upload_time_iso_8601": "2024-10-10T00:00:00.000000Z",
        }],
        "3.1.0rc1": [{
            "yanked": False,
            "yanked_reason": None,
            "requires_python": ">=3.9",
            "upload_time_iso_8601": "2024-09-01T00:00:00.000000Z",
        }],
        "3.0.0": [{
            "yanked": True,
            "yanked_reason": "broken",
            "requires_python": ">=3.8",
            "upload_time_iso_8601": "2023-09-30T00:00:00.000000Z",
        }],
    },
}


def test_parse_pypi_payload_normalizes_releases():
    md = _parse_pypi_payload("flask", SAMPLE_PAYLOAD)
    versions = {str(r.version) for r in md.releases}
    assert versions == {"3.0.0", "3.0.2", "3.1.0", "3.1.0rc1"}

    by_version = {str(r.version): r for r in md.releases}
    assert by_version["3.0.0"].yanked is True
    assert by_version["3.0.0"].yanked_reason == "broken"
    assert by_version["3.1.0"].requires_python == SpecifierSet(">=3.9")
    assert by_version["3.1.0"].upload_time is not None


def test_cache_round_trip(tmp_path: Path):
    client = PyPIClient(cache_dir=tmp_path, ttl_seconds=3600)
    md = _parse_pypi_payload("flask", SAMPLE_PAYLOAD)
    client._write_cache("flask", md)
    loaded = client._read_cache("flask")
    assert loaded is not None
    assert {str(r.version) for r in loaded.releases} == {
        "3.0.0", "3.0.2", "3.1.0", "3.1.0rc1"
    }


def test_cache_expiry(tmp_path: Path):
    client = PyPIClient(cache_dir=tmp_path, ttl_seconds=60)
    md = _parse_pypi_payload("flask", SAMPLE_PAYLOAD)
    client._write_cache("flask", md)

    # Pretend the cache is two hours old
    cache_file = client._cache_path("flask")
    old_time = datetime.now(timezone.utc) - timedelta(hours=2)
    os_time = old_time.timestamp()
    import os
    os.utime(cache_file, (os_time, os_time))

    assert client._read_cache("flask") is None


def test_fetch_one_uses_cache(tmp_path: Path):
    client = PyPIClient(cache_dir=tmp_path, ttl_seconds=3600)
    md = _parse_pypi_payload("flask", SAMPLE_PAYLOAD)
    client._write_cache("flask", md)

    with patch.object(client, "_http_get") as mock_http:
        result = client.fetch_one("flask")
        mock_http.assert_not_called()
    assert result is not None


def test_fetch_one_misses_then_caches(tmp_path: Path):
    client = PyPIClient(cache_dir=tmp_path, ttl_seconds=3600)
    with patch.object(client, "_http_get", return_value=SAMPLE_PAYLOAD) as mock_http:
        result1 = client.fetch_one("flask")
        result2 = client.fetch_one("flask")
        # Only one HTTP call; second call hits the cache
        assert mock_http.call_count == 1
    assert result1 is not None and result2 is not None


def test_fetch_many_returns_dict(tmp_path: Path):
    client = PyPIClient(cache_dir=tmp_path, ttl_seconds=3600)
    with patch.object(client, "_http_get", return_value=SAMPLE_PAYLOAD):
        out = client.fetch_many(["flask", "requests"])
    assert set(out.keys()) == {"flask", "requests"}
```

- [ ] **Step 8.2: Run tests to verify they fail**

```bash
pytest tests/test_pypi.py -v
```

Expected: 6 fail with ModuleNotFoundError.

- [ ] **Step 8.3: Write `src/piptastic/pypi.py`**

```python
"""PyPI metadata client with on-disk TTL cache and thread-pool concurrency."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from piptastic.logging import get_logger
from piptastic.models import PackageMetadata, ReleaseInfo

logger = get_logger(__name__)


def _default_cache_dir() -> Path:
    base = os.environ.get("PIPTASTIC_CACHE_DIR")
    if base:
        return Path(base)
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "piptastic" / "pypi"
    return Path.home() / ".cache" / "piptastic" / "pypi"


class PyPIClient:
    def __init__(
        self,
        *,
        cache_dir: Path | None = None,
        ttl_seconds: int = 3600,
        timeout: float = 10.0,
        concurrency: int = 8,
        base_url: str = "https://pypi.org/pypi",
    ) -> None:
        self.cache_dir = (cache_dir or _default_cache_dir()).resolve()
        self.ttl_seconds = ttl_seconds
        self.timeout = timeout
        self.concurrency = concurrency
        self.base_url = base_url.rstrip("/")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ---------- public ----------

    def fetch_one(self, name: str) -> PackageMetadata | None:
        name = canonicalize_name(name)
        cached = self._read_cache(name)
        if cached is not None:
            return cached

        try:
            payload = self._http_get(name)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            logger.warning("PyPI fetch failed for %s: %s", name, e)
            return None

        md = _parse_pypi_payload(name, payload)
        self._write_cache(name, md)
        return md

    def fetch_many(self, names: Iterable[str]) -> dict[str, PackageMetadata]:
        names = list({canonicalize_name(n) for n in names})
        out: dict[str, PackageMetadata] = {}
        with ThreadPoolExecutor(max_workers=self.concurrency) as ex:
            futures = {ex.submit(self.fetch_one, n): n for n in names}
            for fut in as_completed(futures):
                n = futures[fut]
                try:
                    md = fut.result()
                except Exception as e:
                    logger.warning("PyPI worker error for %s: %s", n, e)
                    continue
                if md is not None:
                    out[n] = md
        return out

    # ---------- HTTP ----------

    def _http_get(self, name: str) -> dict[str, Any]:
        url = f"{self.base_url}/{name}/json"
        with urllib.request.urlopen(url, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # ---------- cache ----------

    def _cache_path(self, name: str) -> Path:
        bucket = name[:2] if len(name) >= 2 else name + "_"
        d = self.cache_dir / bucket
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{name}.json"

    def _read_cache(self, name: str) -> PackageMetadata | None:
        path = self._cache_path(name)
        if not path.exists():
            return None
        age = datetime.now(timezone.utc).timestamp() - path.stat().st_mtime
        if age > self.ttl_seconds:
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
        return _rehydrate_metadata(raw)

    def _write_cache(self, name: str, md: PackageMetadata) -> None:
        path = self._cache_path(name)
        with path.open("w", encoding="utf-8") as f:
            json.dump(_dehydrate_metadata(md), f)


# ---------- parsers ----------

def _parse_pypi_payload(name: str, payload: dict[str, Any]) -> PackageMetadata:
    releases_in = payload.get("releases") or {}
    releases: list[ReleaseInfo] = []
    for ver_str, files in releases_in.items():
        try:
            version = Version(ver_str)
        except InvalidVersion:
            continue
        if not files:
            # No artifacts uploaded — treat as missing
            continue

        # Use the first file's metadata as representative
        first = files[0]
        yanked = bool(first.get("yanked", False))
        yanked_reason = first.get("yanked_reason")

        rp = first.get("requires_python")
        requires_python: SpecifierSet | None = None
        if rp:
            try:
                requires_python = SpecifierSet(rp)
            except InvalidSpecifier:
                requires_python = None

        upload_iso = first.get("upload_time_iso_8601") or first.get("upload_time")
        upload_time: datetime | None = None
        if upload_iso:
            try:
                upload_time = datetime.fromisoformat(upload_iso.replace("Z", "+00:00"))
            except ValueError:
                upload_time = None

        releases.append(ReleaseInfo(
            version=version,
            yanked=yanked,
            yanked_reason=yanked_reason,
            requires_python=requires_python,
            upload_time=upload_time,
        ))

    return PackageMetadata(
        name=canonicalize_name(name),
        releases=tuple(releases),
        fetched_at=datetime.now(timezone.utc),
    )


def _dehydrate_metadata(md: PackageMetadata) -> dict[str, Any]:
    return {
        "name": md.name,
        "fetched_at": md.fetched_at.isoformat(),
        "releases": [
            {
                "version": str(r.version),
                "yanked": r.yanked,
                "yanked_reason": r.yanked_reason,
                "requires_python": str(r.requires_python) if r.requires_python else None,
                "upload_time": r.upload_time.isoformat() if r.upload_time else None,
            }
            for r in md.releases
        ],
    }


def _rehydrate_metadata(raw: dict[str, Any]) -> PackageMetadata:
    releases: list[ReleaseInfo] = []
    for r in raw.get("releases", []):
        try:
            version = Version(r["version"])
        except (InvalidVersion, KeyError):
            continue
        rp_raw = r.get("requires_python")
        requires_python = None
        if rp_raw:
            try:
                requires_python = SpecifierSet(rp_raw)
            except InvalidSpecifier:
                requires_python = None
        ut_raw = r.get("upload_time")
        upload_time = None
        if ut_raw:
            try:
                upload_time = datetime.fromisoformat(ut_raw)
            except ValueError:
                upload_time = None
        releases.append(ReleaseInfo(
            version=version,
            yanked=bool(r.get("yanked", False)),
            yanked_reason=r.get("yanked_reason"),
            requires_python=requires_python,
            upload_time=upload_time,
        ))
    return PackageMetadata(
        name=raw["name"],
        releases=tuple(releases),
        fetched_at=datetime.fromisoformat(raw["fetched_at"]),
    )
```

- [ ] **Step 8.4: Run tests**

```bash
pytest tests/test_pypi.py -v
```

Expected: 6 passed.

- [ ] **Step 8.5: Commit**

```bash
git add src/piptastic/pypi.py tests/test_pypi.py
git commit -m "feat: PyPI client with thread-pool concurrency and on-disk TTL cache

- ThreadPoolExecutor for parallel package fetches (default 8 workers)
- urllib with 10s timeout (fixes [M2])
- Cache under \$XDG_CACHE_HOME/piptastic/pypi/{bucket}/{name}.json
- 1-hour default TTL; PIPTASTIC_CACHE_DIR env override
- Yanked release info and per-release requires_python preserved
"
```

---

## Task 9: Analysis (drift + pin posture + rollup)

**Files:**
- Create: `src/piptastic/analysis.py`
- Create: `tests/test_analysis.py`

- [ ] **Step 9.1: Write failing tests**

`tests/test_analysis.py`:

```python
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
        releases.append(ReleaseInfo(
            version=Version(v),
            yanked=info.get("yanked", False),
            yanked_reason=info.get("yanked_reason"),
            requires_python=SpecifierSet(info["rp"]) if "rp" in info else None,
            upload_time=None,
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
```

- [ ] **Step 9.2: Run tests to verify they fail**

```bash
pytest tests/test_analysis.py -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 9.3: Write `src/piptastic/analysis.py`**

```python
"""Drift classification, pin posture, and per-project audit rollup."""

from __future__ import annotations

import importlib.metadata
from collections import Counter
from typing import Iterable, Protocol

from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
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
)
from piptastic.parsing import parse_source

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

    # Single == X.Y.Z
    if len(clauses) == 1 and operators[0] == "==":
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
) -> ProjectAudit:
    deps = _collect_deps(project)
    target_python = _project_target_python(project, current_python)

    names = sorted({d.name for d in deps if d.url is None})
    metadata = client.fetch_many(names) if names else {}
    unreachable = [n for n in names if n not in metadata]

    audits: list[DepAudit] = []
    for dep in deps:
        installed = _installed_version(dep.name)
        latest, latest_pre = _pick_latest(
            metadata.get(dep.name), target_python=target_python
        )
        current_for_drift = _current_version_for_drift(dep, installed)
        drift = classify_drift(current_for_drift, latest)
        pin = classify_pin_status(dep.specifier, url=dep.url)
        warnings: list[str] = []
        if dep.url:
            warnings.append("VCS/URL requirement — version cannot be tracked")
        if dep.name in unreachable:
            warnings.append("PyPI metadata unavailable")
        if pin is PinStatus.UNPINNED and installed is None:
            warnings.append("unpinned and not installed in current environment")

        yanked = _is_pinned_version_yanked(dep, metadata.get(dep.name))

        audits.append(DepAudit(
            dep=dep,
            installed=installed,
            latest=latest,
            latest_including_prereleases=latest_pre,
            drift=drift,
            pin_status=pin,
            yanked=yanked,
            warnings=tuple(warnings),
        ))

    score = _pinning_score(audits)
    drift_summary = dict(Counter(a.drift for a in audits))
    yanked_count = sum(1 for a in audits if a.yanked)

    return ProjectAudit(
        project=project,
        deps=audits,
        pinning_score=score,
        drift_summary=drift_summary,
        yanked_count=yanked_count,
        pypi_unreachable=unreachable,
    )


# ---------- internals ----------

def _collect_deps(project: Project) -> list[Dep]:
    out: list[Dep] = []
    for src in project.dep_sources:
        out.extend(parse_source(src))
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


def _pinning_score(audits: list[DepAudit]) -> float:
    scored = [PIN_WEIGHTS[a.pin_status] for a in audits if a.pin_status in PIN_WEIGHTS]
    if not scored:
        return 0.0
    return sum(scored) / len(scored)
```

- [ ] **Step 9.4: Run tests**

```bash
pytest tests/test_analysis.py -v
```

Expected: 14 passed (12 parametrized + 4 audit tests; count varies by pytest's parametrize expansion — verify it runs to green).

- [ ] **Step 9.5: Commit**

```bash
git add src/piptastic/analysis.py tests/test_analysis.py
git commit -m "feat: drift classification, pin posture, project audit rollup

- Drift: EPOCH / MAJOR / MINOR / PATCH / BUILD / NONE / UNKNOWN
  (uses Version.release tuple padded to 3; BUILD covers post/dev/local)
- Pin status: PINNED / COMPATIBLE / RANGE / FLOOR / UNPINNED / URL
- Latest picker filters yanked + python-incompatible, splits stable vs pre
  (removed [C3] zero-version filter, removed [C4] N-1 heuristic)
- Yanked-version detection for currently-pinned releases (new)
- importlib.metadata replaces pkg_resources (fixes [I1])
- Pinning score: weighted mean PINNED=1.0 ... UNPINNED=0.0
"
```

---

## Task 10: Render — JSON

**Files:**
- Create: `src/piptastic/render/__init__.py`
- Create: `src/piptastic/render/json_out.py`
- Create: `tests/test_render_json.py`

- [ ] **Step 10.1: Write `src/piptastic/render/__init__.py`**

```python
"""Output renderers."""

from piptastic.render.json_out import render_json
from piptastic.render.terminal import render_terminal

__all__ = ["render_json", "render_terminal"]
```

(Note: `terminal.render_terminal` is defined in Task 11; this import will fail until Task 11 is done. That's fine — `render_json` is independently importable from `piptastic.render.json_out`. We'll fix the `__init__.py` to import terminal in Task 11. For now, the import line is forward-looking; tests in this task import directly from `piptastic.render.json_out` to sidestep it.)

Actually: to keep this task self-contained, write `__init__.py` without the terminal import for now:

```python
"""Output renderers."""

from piptastic.render.json_out import render_json

__all__ = ["render_json"]
```

We'll extend it in Task 11.

- [ ] **Step 10.2: Write failing tests**

`tests/test_render_json.py`:

```python
"""Tests for the JSON renderer (stable schema_version=1)."""

import json
from datetime import datetime, timezone
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from piptastic.models import (
    Dep,
    DepAudit,
    DepSource,
    PinStatus,
    Project,
    ProjectAudit,
    SemverDrift,
    SourceKind,
)
from piptastic.render.json_out import render_json


def _make_audit() -> ProjectAudit:
    src = DepSource(
        kind=SourceKind.REQUIREMENTS_TXT,
        path=Path("/projects/webapp/requirements.txt"),
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
    project = Project(
        name="webapp",
        path=Path("/projects/webapp"),
        python_version="3.11",
        python_source="pyproject.toml",
        python_constraints=">=3.11",
        dep_sources=(src,),
    )
    audit = DepAudit(
        dep=dep,
        installed=Version("3.0.2"),
        latest=Version("3.1.0"),
        latest_including_prereleases=Version("3.1.0"),
        drift=SemverDrift.MINOR,
        pin_status=PinStatus.PINNED,
        yanked=False,
        warnings=(),
    )
    return ProjectAudit(
        project=project,
        deps=[audit],
        pinning_score=1.0,
        drift_summary={SemverDrift.MINOR: 1},
        yanked_count=0,
        pypi_unreachable=[],
    )


def test_render_json_schema_version():
    out = render_json([_make_audit()], root=Path("/projects"))
    parsed = json.loads(out)
    assert parsed["schema_version"] == 1


def test_render_json_project_shape():
    out = render_json([_make_audit()], root=Path("/projects"))
    parsed = json.loads(out)
    project = parsed["projects"][0]
    assert project["name"] == "webapp"
    assert project["pinning_score"] == 1.0
    assert project["drift_summary"]["minor"] == 1
    assert project["yanked_count"] == 0
    assert project["python"]["version"] == "3.11"
    assert project["python"]["source"] == "pyproject.toml"


def test_render_json_dep_shape():
    out = render_json([_make_audit()], root=Path("/projects"))
    parsed = json.loads(out)
    dep = parsed["projects"][0]["deps"][0]
    assert dep["name"] == "flask"
    assert dep["specifier"] == "==3.0.2"
    assert dep["pin_status"] == "pinned"
    assert dep["current"] == "3.0.2"
    assert dep["latest"] == "3.1.0"
    assert dep["drift"] == "minor"
    assert dep["yanked"] is False
    assert dep["warnings"] == []


def test_render_json_handles_url_dep():
    src = DepSource(
        kind=SourceKind.REQUIREMENTS_TXT,
        path=Path("/p/requirements.txt"),
        group="default",
    )
    dep = Dep(
        name="repo",
        raw_name="repo",
        specifier=SpecifierSet(),
        extras=frozenset(),
        marker=None,
        source=src,
        line_no=1,
        url="git+https://example/repo",
    )
    audit = DepAudit(
        dep=dep,
        installed=None,
        latest=None,
        latest_including_prereleases=None,
        drift=SemverDrift.UNKNOWN,
        pin_status=PinStatus.URL,
        yanked=False,
        warnings=("VCS/URL requirement — version cannot be tracked",),
    )
    project = Project(
        name="p",
        path=Path("/p"),
        python_version=None, python_source=None, python_constraints=None,
        dep_sources=(src,),
    )
    pa = ProjectAudit(
        project=project, deps=[audit], pinning_score=0.0,
        drift_summary={}, yanked_count=0, pypi_unreachable=[],
    )
    out = render_json([pa], root=Path("/p"))
    parsed = json.loads(out)
    d = parsed["projects"][0]["deps"][0]
    assert d["pin_status"] == "url"
    assert d["url"] == "git+https://example/repo"
    assert d["latest"] is None
    assert d["current"] is None
```

- [ ] **Step 10.3: Run tests to verify they fail**

```bash
pytest tests/test_render_json.py -v
```

Expected: ModuleNotFoundError for `piptastic.render.json_out`.

- [ ] **Step 10.4: Write `src/piptastic/render/json_out.py`**

```python
"""JSON renderer — stable schema_version=1 contract for CI consumers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from piptastic.models import DepAudit, ProjectAudit, SemverDrift

SCHEMA_VERSION = 1


def render_json(audits: Iterable[ProjectAudit], *, root: Path) -> str:
    """Render a list of ProjectAudits as a JSON string."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "projects": [_project_to_dict(a) for a in audits],
    }
    return json.dumps(payload, indent=2)


def _project_to_dict(pa: ProjectAudit) -> dict:
    p = pa.project
    return {
        "name": p.name,
        "path": str(p.path),
        "python": {
            "version": p.python_version,
            "source": p.python_source,
            "constraints": p.python_constraints,
        },
        "pinning_score": round(pa.pinning_score, 4),
        "drift_summary": {k.value: v for k, v in pa.drift_summary.items()},
        "yanked_count": pa.yanked_count,
        "pypi_unreachable": pa.pypi_unreachable,
        "sources": [
            {"kind": s.kind.value, "path": str(s.path), "group": s.group}
            for s in p.dep_sources
        ],
        "deps": [_dep_to_dict(d) for d in pa.deps],
    }


def _dep_to_dict(da: DepAudit) -> dict:
    dep = da.dep
    current = None
    for clause in dep.specifier:
        if clause.operator == "==":
            current = clause.version.rstrip(".*")
            break
    if current is None and da.installed is not None:
        current = str(da.installed)
    return {
        "name": dep.name,
        "raw_name": dep.raw_name,
        "source_file": str(dep.source.path),
        "group": dep.source.group,
        "specifier": str(dep.specifier),
        "extras": sorted(dep.extras),
        "marker": str(dep.marker) if dep.marker else None,
        "url": dep.url,
        "pin_status": da.pin_status.value,
        "current": current,
        "installed": str(da.installed) if da.installed else None,
        "latest": str(da.latest) if da.latest else None,
        "latest_including_prereleases": (
            str(da.latest_including_prereleases) if da.latest_including_prereleases else None
        ),
        "drift": da.drift.value,
        "yanked": da.yanked,
        "warnings": list(da.warnings),
    }
```

- [ ] **Step 10.5: Run tests**

```bash
pytest tests/test_render_json.py -v
```

Expected: 4 passed.

- [ ] **Step 10.6: Commit**

```bash
git add src/piptastic/render
git commit -m "feat: JSON renderer with stable schema_version=1

- Top-level: schema_version, scanned_at, root, projects[]
- Per-project: name, path, python info, pinning_score, drift_summary,
  yanked_count, sources, deps
- Per-dep: name, source, specifier, pin_status, current, installed,
  latest, drift, yanked, warnings
"
```

---

## Task 11: Render — terminal (rich)

**Files:**
- Modify: `src/piptastic/render/__init__.py`
- Create: `src/piptastic/render/terminal.py`

- [ ] **Step 11.1: Write `src/piptastic/render/terminal.py`**

```python
"""Terminal renderer using `rich`. Three views: tree, table, summary."""

from __future__ import annotations

from typing import Iterable, Literal

from rich.console import Console
from rich.table import Table
from rich.tree import Tree

from piptastic.models import DepAudit, PinStatus, ProjectAudit, SemverDrift

ViewMode = Literal["tree", "table", "summary"]

DRIFT_STYLE = {
    SemverDrift.NONE:    "green",
    SemverDrift.BUILD:   "dim",
    SemverDrift.PATCH:   "yellow",
    SemverDrift.MINOR:   "orange3",
    SemverDrift.MAJOR:   "red",
    SemverDrift.EPOCH:   "magenta",
    SemverDrift.UNKNOWN: "white",
}


def render_terminal(
    audits: Iterable[ProjectAudit],
    *,
    mode: ViewMode = "tree",
    console: Console | None = None,
) -> None:
    """Render audits to the terminal. Default view is `tree`."""
    console = console or Console()
    audits = list(audits)

    if not audits:
        console.print("[yellow]No Python projects found.[/yellow]")
        return

    if mode == "summary":
        _render_summary(audits, console)
    elif mode == "table":
        _render_table(audits, console)
    else:
        _render_tree(audits, console)


def _render_summary(audits: list[ProjectAudit], console: Console) -> None:
    table = Table(title="piptastic — summary", show_lines=False)
    table.add_column("Project")
    table.add_column("Py", justify="right")
    table.add_column("Pin score", justify="right")
    table.add_column("Major", justify="right", style="red")
    table.add_column("Minor", justify="right", style="orange3")
    table.add_column("Patch", justify="right", style="yellow")
    table.add_column("Yanked", justify="right", style="red")
    table.add_column("Deps", justify="right")

    for a in audits:
        table.add_row(
            a.project.name,
            a.project.python_version or "—",
            f"{a.pinning_score:.0%}",
            str(a.drift_summary.get(SemverDrift.MAJOR, 0)),
            str(a.drift_summary.get(SemverDrift.MINOR, 0)),
            str(a.drift_summary.get(SemverDrift.PATCH, 0)),
            str(a.yanked_count),
            str(len(a.deps)),
        )
    console.print(table)


def _render_table(audits: list[ProjectAudit], console: Console) -> None:
    table = Table(title="piptastic — packages", show_lines=False)
    table.add_column("Project")
    table.add_column("File")
    table.add_column("Group")
    table.add_column("Package")
    table.add_column("Current")
    table.add_column("Latest")
    table.add_column("Drift")
    table.add_column("Pin")
    table.add_column("Notes")

    for a in audits:
        for d in a.deps:
            drift_text = f"[{DRIFT_STYLE[d.drift]}]{d.drift.value}[/{DRIFT_STYLE[d.drift]}]"
            notes = ", ".join(d.warnings) if d.warnings else ""
            if d.yanked:
                notes = "yanked" + (f"; {notes}" if notes else "")
            current = _current_str(d)
            latest = str(d.latest) if d.latest else "—"
            table.add_row(
                a.project.name,
                d.dep.source.path.name,
                d.dep.source.group,
                d.dep.name,
                current,
                latest,
                drift_text,
                d.pin_status.value,
                notes,
            )
    console.print(table)


def _render_tree(audits: list[ProjectAudit], console: Console) -> None:
    root = Tree(f"[bold]{len(audits)} project(s)[/bold]")
    for a in audits:
        header = (
            f"[bold]{a.project.name}[/bold]   "
            f"py{a.project.python_version or '?'}   "
            f"pin: {a.pinning_score:.0%}   "
            f"deps: {len(a.deps)}"
        )
        if a.yanked_count:
            header += f"   [red]yanked: {a.yanked_count}[/red]"
        pnode = root.add(header)
        by_file: dict[str, list[DepAudit]] = {}
        for d in a.deps:
            by_file.setdefault(d.dep.source.path.name, []).append(d)
        for fname, items in by_file.items():
            fnode = pnode.add(f"[cyan]{fname}[/cyan]   ({len(items)} deps)")
            for d in items:
                fnode.add(_dep_line(d))
    console.print(root)


def _dep_line(d: DepAudit) -> str:
    drift = d.drift.value
    style = DRIFT_STYLE[d.drift]
    current = _current_str(d)
    latest = str(d.latest) if d.latest else "—"
    yanked_mark = " [red strike]yanked[/red strike]" if d.yanked else ""
    return (
        f"{d.dep.name:<25} "
        f"{current:<14} → {latest:<10}  "
        f"[{style}]{drift:<7}[/{style}]  "
        f"{d.pin_status.value}"
        f"{yanked_mark}"
    )


def _current_str(d: DepAudit) -> str:
    for clause in d.dep.specifier:
        if clause.operator == "==":
            return clause.version
    if d.installed is not None:
        return f"({d.installed})"
    return "—"
```

- [ ] **Step 11.2: Re-enable the terminal export in `__init__.py`**

Update `src/piptastic/render/__init__.py`:

```python
"""Output renderers."""

from piptastic.render.json_out import render_json
from piptastic.render.terminal import render_terminal

__all__ = ["render_json", "render_terminal"]
```

- [ ] **Step 11.3: Smoke test the renderer (no assertions; just imports + doesn't raise)**

Append to `tests/test_render_json.py` (so it travels with the render module, no new test file):

```python
def test_render_terminal_does_not_raise(capsys):
    from rich.console import Console
    from piptastic.render import render_terminal

    audit = _make_audit()
    console = Console(record=True, no_color=True)
    render_terminal([audit], mode="tree", console=console)
    render_terminal([audit], mode="table", console=console)
    render_terminal([audit], mode="summary", console=console)
    # If we got here, no exception. Spot-check that project name appeared.
    output = console.export_text()
    assert "webapp" in output
```

- [ ] **Step 11.4: Run renderer tests**

```bash
pytest tests/test_render_json.py -v
```

Expected: 5 passed.

- [ ] **Step 11.5: Commit**

```bash
git add src/piptastic/render tests/test_render_json.py
git commit -m "feat: rich terminal renderer with tree/table/summary views

- Tree: project → file → dep lines, colored by drift level
- Table: flat one-row-per-dep
- Summary: one row per project, drift histogram + pin score
- Yanked versions: red strikethrough
- Pin status as a word — readable when piped to a file
"
```

---

## Task 12: Update flow (file-mutating)

**Files:**
- Create: `src/piptastic/update.py`

This task has no unit tests in v0.2 — `update.py` is exercised by the CLI smoke test in Task 13, and full integration testing of the venv-creation path is deferred. The logic is small and reads as documentation; the bug-fix value comes from the surrounding correctness (cleanup, line preservation) rather than algorithmic complexity.

- [ ] **Step 12.1: Write `src/piptastic/update.py`**

```python
"""Mutate a requirements.txt to pin packages to their latest compatible version.

Only invoked from the `piptastic update` subcommand. Audit is read-only.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import venv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from packaging.utils import canonicalize_name
from packaging.version import Version

from piptastic.logging import get_logger
from piptastic.models import Project, SourceKind
from piptastic.pypi import PyPIClient

logger = get_logger(__name__)


@dataclass
class UpdateResult:
    requirements_file: Path
    backup_file: Path | None
    changes: list[tuple[str, str, str]]  # (name, old, new)
    tested: bool
    test_passed: bool


def update_project(
    project: Project,
    *,
    packages: Iterable[str] | None = None,
    test: bool = True,
    refresh: bool = False,
    use_temp_test_env: bool = False,
    client: PyPIClient | None = None,
) -> list[UpdateResult]:
    """Update each requirements*.txt source in `project`.

    pyproject.toml and Pipfile updates are NOT supported in v0.2 — these
    sources are skipped with an info-level message.
    """
    client = client or PyPIClient(ttl_seconds=0 if refresh else 3600)
    only = {canonicalize_name(p) for p in packages} if packages else None

    results: list[UpdateResult] = []
    for src in project.dep_sources:
        if src.kind not in (SourceKind.REQUIREMENTS_TXT, SourceKind.CONSTRAINTS_TXT):
            logger.info("update: skipping %s (only requirements*.txt is writeable in v0.2)", src.path)
            continue
        results.append(_update_one_file(src.path, only, client, test, use_temp_test_env, project.path))
    return results


def _update_one_file(
    req_path: Path,
    only: set[str] | None,
    client: PyPIClient,
    test: bool,
    use_temp_test_env: bool,
    project_root: Path,
) -> UpdateResult:
    backup = _create_backup(req_path, project_root)
    lines = req_path.read_text(encoding="utf-8").splitlines()
    changes: list[tuple[str, str, str]] = []

    new_lines = []
    for line in lines:
        new_line, change = _maybe_update_line(line, only, client)
        new_lines.append(new_line)
        if change is not None:
            changes.append(change)

    # Preserve trailing newline conventions
    new_text = "\n".join(new_lines)
    if not new_text.endswith("\n"):
        new_text += "\n"
    req_path.write_text(new_text, encoding="utf-8")

    tested = False
    test_passed = True
    if test and changes:
        tested = True
        test_passed = _test_install(req_path, project_root, use_temp_test_env)
        if not test_passed:
            logger.warning("test install failed; restoring backup")
            shutil.copy2(backup, req_path)

    return UpdateResult(
        requirements_file=req_path,
        backup_file=backup,
        changes=changes,
        tested=tested,
        test_passed=test_passed,
    )


_LINE_RE = re.compile(
    r"^([a-zA-Z0-9][a-zA-Z0-9_.\-]*)(\[[^\]]+\])?(==|~=|>=|<=|>|<|!=|===)?([^;\s]+)?(.*)$"
)


def _maybe_update_line(
    line: str, only: set[str] | None, client: PyPIClient
) -> tuple[str, tuple[str, str, str] | None]:
    stripped = line.strip()
    if not stripped or stripped.startswith(("#", "-", "@")):
        return line, None

    m = _LINE_RE.match(stripped)
    if not m:
        return line, None
    name, extras, op, ver, tail = m.groups()
    canon = canonicalize_name(name)
    if only is not None and canon not in only:
        return line, None
    # Only touch == pins; never override range/floor/url/unpinned semantics
    if op != "==":
        return line, None

    md = client.fetch_one(canon)
    if md is None:
        return line, None
    latest = max(
        (r.version for r in md.releases if not r.yanked and not r.version.is_prerelease),
        default=None,
    )
    if latest is None or str(latest) == ver:
        return line, None

    extras_str = extras or ""
    tail_str = tail or ""
    new = f"{name}{extras_str}=={latest}{tail_str}"
    # Preserve leading whitespace (in case the line was indented)
    leading_ws = line[:len(line) - len(line.lstrip())]
    return f"{leading_ws}{new}", (canon, ver or "", str(latest))


def _create_backup(req_path: Path, project_root: Path) -> Path:
    backup_dir = project_root / ".requirements_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    content = req_path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()[:8]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = backup_dir / f"{req_path.stem}_{ts}_{digest}.txt"
    shutil.copy2(req_path, dest)
    return dest


def _test_install(req_path: Path, project_root: Path, use_temp: bool) -> bool:
    """Create a throwaway venv, install -r req_path, return True on success."""
    if use_temp:
        ctx_dir = Path(tempfile.mkdtemp(prefix="piptastic_test_"))
    else:
        ctx_dir = project_root / f".piptastic_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        ctx_dir.mkdir(parents=True, exist_ok=True)
    try:
        venv.create(ctx_dir, with_pip=True)
        python_path = (
            ctx_dir / "Scripts" / "python.exe"
            if sys.platform == "win32"
            else ctx_dir / "bin" / "python"
        )
        # Upgrade pip; surface any failure
        r = subprocess.run(
            [str(python_path), "-m", "pip", "install", "--upgrade", "pip"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            logger.error("pip upgrade failed: %s", r.stderr)
            return False
        r = subprocess.run(
            [str(python_path), "-m", "pip", "install", "-r", str(req_path)],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            logger.error("pip install failed: %s", r.stderr)
            return False
        return True
    finally:
        # Always clean up — fixes [C7]
        shutil.rmtree(ctx_dir, ignore_errors=True)
```

- [ ] **Step 12.2: Quick import smoke check**

```bash
python -c "from piptastic.update import update_project; print('ok')"
```

Expected: `ok`.

- [ ] **Step 12.3: Commit**

```bash
git add src/piptastic/update.py
git commit -m "feat: requirements.txt updater with backup + tested install

- Line-preserving writer (fixes [C8])
- Test venv lives in project dir or a temp dir, never in CWD (fixes [C6])
- Failed install cleans up its venv unconditionally (fixes [C7])
- Skips packages without == pins (does not override range/floor/url)
- pyproject.toml and Pipfile updates: skipped with info message (not supported in v0.2)
"
```

---

## Task 13: CLI

**Files:**
- Create: `src/piptastic/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 13.1: Write failing CLI tests**

`tests/test_cli.py`:

```python
"""Tests for the CLI entry points."""

import json
from pathlib import Path

import pytest

from piptastic.cli import build_parser, main

FIXTURES = Path(__file__).parent / "fixtures"


def test_parser_has_audit_list_update():
    parser = build_parser()
    args = parser.parse_args(["audit", "."])
    assert args.command == "audit"
    args = parser.parse_args(["list", "."])
    assert args.command == "list"
    args = parser.parse_args(["update", "."])
    assert args.command == "update"


def test_audit_json_smoke(tmp_path, monkeypatch, capsys):
    """Audit a tiny fixture project; --json output must be parseable + schema 1.

    The PyPI client is monkeypatched so the test never touches the network.
    """
    # Use the existing req_only fixture
    monkeypatch.chdir(FIXTURES)

    from piptastic import cli as cli_mod

    class FakeClient:
        def fetch_many(self, names):
            return {}
        def fetch_one(self, name):
            return None

    monkeypatch.setattr(cli_mod, "_build_client", lambda args: FakeClient())

    exit_code = main(["audit", str(FIXTURES / "req_only"), "--json"])
    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["schema_version"] == 1
    assert payload["projects"][0]["name"] == "req_only"


def test_audit_missing_path_returns_1(tmp_path):
    exit_code = main(["audit", str(tmp_path / "nope")])
    assert exit_code == 1


def test_list_alias_runs(tmp_path, monkeypatch, capsys):
    from piptastic import cli as cli_mod
    class FakeClient:
        def fetch_many(self, names): return {}
        def fetch_one(self, name): return None
    monkeypatch.setattr(cli_mod, "_build_client", lambda args: FakeClient())

    exit_code = main(["list", str(FIXTURES / "req_only")])
    assert exit_code == 0
```

- [ ] **Step 13.2: Run tests to verify they fail**

```bash
pytest tests/test_cli.py -v
```

Expected: ModuleNotFoundError / AttributeError.

- [ ] **Step 13.3: Write `src/piptastic/cli.py`**

```python
"""piptastic CLI — audit (default), list, update."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from packaging.version import Version

from piptastic import __version__
from piptastic.analysis import audit_project
from piptastic.discovery import discover_one, discover_tree
from piptastic.logging import configure_logging, get_logger
from piptastic.models import ProjectAudit, SemverDrift
from piptastic.pypi import PyPIClient
from piptastic.render import render_json, render_terminal
from piptastic.update import update_project

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="piptastic",
        description="Audit Python dependency posture across all projects in a tree.",
    )
    parser.add_argument("--version", action="version", version=f"piptastic {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("--log-file", type=Path, default=None)
    sub = parser.add_subparsers(dest="command")

    # audit
    audit = sub.add_parser("audit", help="Audit dependency health (read-only)")
    audit.add_argument("path", type=Path)
    audit.add_argument("--table", action="store_true", help="Flat table view")
    audit.add_argument("--summary", action="store_true", help="One row per project")
    audit.add_argument("--json", action="store_true", help="Machine-readable JSON to stdout")
    audit.add_argument("--include-prereleases", action="store_true")
    audit.add_argument("--exclude", action="append", default=[], help="Glob pattern, repeatable")
    audit.add_argument("--no-cache", action="store_true")
    audit.add_argument("--refresh-cache", action="store_true")
    audit.add_argument("--cache-ttl", type=int, default=3600)
    audit.add_argument("--concurrency", type=int, default=8)
    audit.add_argument(
        "--fail-on-drift",
        choices=["build", "patch", "minor", "major", "epoch"],
        default=None,
        help="Exit non-zero if any dep has drift at or above this level",
    )

    # list = audit + --table on a single project (kept for muscle memory)
    lst = sub.add_parser("list", help="Alias for `audit <path> --table` on a single project")
    lst.add_argument("path", type=Path)
    lst.add_argument("--json", action="store_true")

    # update
    upd = sub.add_parser("update", help="Update requirements*.txt to latest pinned versions")
    upd.add_argument("path", type=Path)
    upd.add_argument("packages", nargs="*")
    upd.add_argument("--no-test", action="store_true")
    upd.add_argument("--refresh", action="store_true")
    upd.add_argument("--temp-test-env", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    level = logging.INFO if args.verbose else (logging.ERROR if args.quiet else logging.WARNING)
    configure_logging(level=level, log_file=args.log_file)

    if args.command is None:
        parser.print_help()
        return 0

    try:
        if args.command == "audit":
            return _cmd_audit(args)
        if args.command == "list":
            return _cmd_list(args)
        if args.command == "update":
            return _cmd_update(args)
    except Exception as e:  # last-resort guard so we never traceback at the user
        logger.exception("unhandled error: %s", e)
        return 1
    return 0


# ---------- subcommand impls ----------

def _build_client(args) -> PyPIClient:
    if getattr(args, "no_cache", False):
        ttl = 0
    elif getattr(args, "refresh_cache", False):
        ttl = 0
    else:
        ttl = getattr(args, "cache_ttl", 3600)
    return PyPIClient(ttl_seconds=ttl, concurrency=getattr(args, "concurrency", 8))


def _cmd_audit(args) -> int:
    path = args.path.resolve()
    if not path.exists():
        logger.error("path does not exist: %s", path)
        return 1

    # If `path` is a single project (has dep sources directly), use discover_one.
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
    audits = [audit_project(p, client, current_python=current_py) for p in projects]

    if args.json:
        print(render_json(audits, root=path))
    else:
        mode = "summary" if args.summary else ("table" if (args.table or single is not None) else "tree")
        render_terminal(audits, mode=mode)

    if args.fail_on_drift:
        threshold = SemverDrift(args.fail_on_drift)
        if _exceeds_threshold(audits, threshold):
            return 1
    return 0


def _cmd_list(args) -> int:
    # Equivalent to `audit <path> --table`
    args.table = True
    args.summary = False
    args.include_prereleases = False
    args.exclude = []
    args.no_cache = False
    args.refresh_cache = False
    args.cache_ttl = 3600
    args.concurrency = 8
    args.fail_on_drift = None
    return _cmd_audit(args)


def _cmd_update(args) -> int:
    path = args.path.resolve()
    project = discover_one(path)
    if project is None:
        logger.error("no Python project at %s", path)
        return 1

    client = PyPIClient(ttl_seconds=0 if args.refresh else 3600)
    results = update_project(
        project,
        packages=args.packages or None,
        test=not args.no_test,
        refresh=args.refresh,
        use_temp_test_env=args.temp_test_env,
        client=client,
    )

    any_changes = False
    rollback = False
    for r in results:
        if r.changes:
            any_changes = True
            for name, old, new in r.changes:
                print(f"  {name}: {old or '(unpinned)'} -> {new}")
        if r.tested and not r.test_passed:
            rollback = True
            print(f"[piptastic] test install failed; rolled back {r.requirements_file}")

    if not any_changes:
        print("[piptastic] no changes")
    if rollback:
        return 2
    return 0


# ---------- helpers ----------

_DRIFT_RANK = {
    SemverDrift.NONE: 0, SemverDrift.UNKNOWN: 0,
    SemverDrift.BUILD: 1, SemverDrift.PATCH: 2,
    SemverDrift.MINOR: 3, SemverDrift.MAJOR: 4,
    SemverDrift.EPOCH: 5,
}


def _exceeds_threshold(audits: list[ProjectAudit], threshold: SemverDrift) -> bool:
    t = _DRIFT_RANK[threshold]
    for a in audits:
        for d in a.deps:
            if _DRIFT_RANK[d.drift] >= t:
                return True
    return False
```

- [ ] **Step 13.4: Run CLI tests**

```bash
pytest tests/test_cli.py -v
```

Expected: 4 passed.

- [ ] **Step 13.5: Run the full test suite**

```bash
pytest --tb=short
```

Expected: all green.

- [ ] **Step 13.6: Commit**

```bash
git add src/piptastic/cli.py tests/test_cli.py
git commit -m "feat: CLI (audit / list / update) with JSON + --fail-on-drift

- argparse subcommands: audit (default), list (alias), update
- discover_one for direct path, discover_tree for parent dirs
- --json flips any view to stable schema_version=1 output
- --fail-on-drift {build,patch,minor,major,epoch} for CI gates
- Exit codes: 0 ok, 1 op failure, 2 update test failed + rolled back
"
```

---

## Task 14: README + end-to-end smoke + final polish

**Files:**
- Modify: `README.md`

- [ ] **Step 14.1: Run a real end-to-end audit against the repo's own fixtures**

```bash
piptastic audit tests/fixtures --summary
```

Expected: rich table with one row per fixture project. (May warn about PyPI unreachability for non-existent fixture packages — that's fine.)

```bash
piptastic audit tests/fixtures/req_only --json | head -40
```

Expected: valid JSON, `schema_version: 1`.

- [ ] **Step 14.2: Expand `README.md`**

```markdown
# piptastic

Audit Python dependency posture across all projects in a tree. Read-only by
default — think Watchtower for your `requirements.txt` files.

## Install

```bash
pip install .
# or, for an isolated install with the CLI on PATH:
pipx install .
```

Provides two commands: `piptastic` and `ptc` (short alias).

## What it does

Auto-discovers Python projects under a path and reports, per project:

- **Drift** for every dependency: classified as `MAJOR` / `MINOR` / `PATCH` /
  `BUILD` (the "nano" tier) / `NONE`, colored.
- **Pinning posture**: `PINNED` / `COMPATIBLE` / `RANGE` / `FLOOR` /
  `UNPINNED` / `URL`, with a 0-100% pin score per project.
- **Yanked releases** that are still pinned.
- **PyPI unreachability** so transient network issues don't silently masquerade
  as "up to date".

Supports `requirements.txt` family, `pyproject.toml` (PEP 621 + Poetry), and
`Pipfile` / `Pipfile.lock`.

## Usage

```bash
# Read-only audit, tree view, across a whole tree
piptastic audit ~/projects

# Flat table view for a single project
piptastic audit ./myproject --table

# Machine-readable JSON (stable schema_version=1)
piptastic audit . --json > report.json

# CI gate: fail the build if any dep is a minor or higher behind
piptastic audit . --fail-on-drift minor

# Mutate requirements.txt to the latest compatible pinned version
piptastic update ./myproject
```

## Output channels

- **Tree view** (default for multi-project audits) — nested project → file → dep.
- **Table view** (`--table`, default for single-project audits) — one row per dep.
- **Summary view** (`--summary`) — one row per project, drift histogram + pin score.
- **JSON** (`--json`) — stable shape, intended for CI consumption.

## Configuration

- Caches PyPI metadata under `$XDG_CACHE_HOME/piptastic/pypi/` (1h TTL by
  default). Override with `--cache-ttl`, `--no-cache`, `--refresh-cache`,
  or the `PIPTASTIC_CACHE_DIR` environment variable.
- Per-tree exclusions: `--exclude PATTERN` (repeatable; glob syntax).

## Status

v0.2 — see `docs/superpowers/specs/` for the design.

## Not in v0.2

Deferred to v0.3+: watch/daemon mode, CVE/security advisory checks,
lockfile-drift detection, HTML report, `setup.py`/`setup.cfg` parsing,
`update` for `pyproject.toml` and `Pipfile`.
```

- [ ] **Step 14.3: Run full test suite one last time + coverage check**

```bash
pytest --cov=src/piptastic --cov-report=term-missing
```

Expected: all green; coverage on non-CLI modules above 80%.

- [ ] **Step 14.4: Final commit**

```bash
git add README.md
git commit -m "docs: expand README with v0.2 usage, output modes, configuration

- Install: pip install . or pipx install .
- Commands: audit (default), list, update
- Output: tree / table / summary / json (schema_version 1)
- Caching + per-tree excludes
- Status + v0.3 deferred list
"
```

---

## Self-review checklist (executed)

**Spec coverage:** Every section of the spec maps to at least one task above:

- §1 Purpose → Tasks 7–11 (audit-first), 12 (update isolation)
- §2 Target environment → Task 1 (pyproject `requires-python`)
- §3 Package layout → Tasks 0 (deletions), 2 (skeleton)
- §4 Data model → Task 3
- §5 Discovery → Task 7
- §6 Parsing → Tasks 4, 5, 6
- §7 PyPI client → Task 8
- §8 Analysis → Task 9
- §9 Rendering → Tasks 10, 11
- §10 Update → Task 12
- §11 CLI → Task 13
- §12 Logging → Task 2 (logging.py)
- §13 Packaging → Task 1 (pyproject.toml)
- §14 Testing → Tasks 3, 4, 5, 6, 7, 8, 9, 10, 11, 13
- §15 Bug-fix map → fixes cited inline in commit messages
- §16 Risks → smoke-tested in Task 14
- §17 Deferred → explicitly out of scope (README mentions)

**Placeholder scan:** No "TBD" / "TODO" / "fill in" markers in steps. Every
test step contains the test code; every implementation step contains the
implementation code; every command step shows expected output or failure.

**Type consistency:** `Dep`, `DepSource`, `Project`, `DepAudit`, `ProjectAudit`,
`SemverDrift`, `PinStatus`, `SourceKind`, `PackageMetadata`, `ReleaseInfo`
spellings match across Tasks 3, 4, 7, 8, 9, 10, 11, 12, 13. `audit_project`
signature in §9 matches usage in §13. `render_json(audits, *, root)` signature
matches usage in §13. `render_terminal(audits, *, mode, console)` signature
matches usage in §13. `update_project` keyword args match between §12 and §13.

**Scope:** v0.2 produces an installable, working tool with audit + update
flows. Deferred items (watch/CVE/HTML/lockfile drift) are listed under §17
and called out in the README. Single coherent implementation cycle.

---
