# piptastic v0.2 — Design

**Status:** approved (brainstorm)
**Date:** 2026-05-25
**Author:** brainstorm session
**Replaces:** `requirements_manager` (current `src/requirements_manager/` package)

---

## 1. Purpose

A small, fast CLI tool a developer can run against a directory tree to get an
honest, read-only health report on their Python dependency posture across all
projects found. Think *Watchtower for Python deps* — but local and one-shot, not
a daemon.

Primary deliverables vs. the existing tool:

- **Audit-first.** Default behavior is a non-destructive report; no file is
  rewritten unless the user runs `piptastic update` explicitly.
- **Multi-format.** Discovers `requirements*.txt`, `pyproject.toml` (PEP 621 +
  Poetry), and `Pipfile`/`Pipfile.lock`.
- **Semver-aware drift.** Each outdated dep is classified as `EPOCH` / `MAJOR` /
  `MINOR` / `PATCH` / `BUILD` (the "nano" tier) — color-coded.
- **Pinning posture.** Classifies every dep as `PINNED` / `COMPATIBLE` / `RANGE`
  / `FLOOR` / `UNPINNED` / `URL`, and rolls a per-project score.
- **Rich terminal output + stable JSON.** TUI by default; `--json` for CI and
  scripts.
- **Installable.** `pip install .` exposes `piptastic` and `ptc` commands.

Out of scope for v0.2 (deferred to v0.3): watch/daemon mode, OSV/CVE
integration, lockfile-drift detection, HTML report, `setup.py`/`setup.cfg`
parsing.

---

## 2. Target environment

- **Python:** 3.10+. Uses `tomli` for TOML on 3.10, will fall through to stdlib
  `tomllib` on 3.11+ via a conditional import.
- **OS:** Cross-platform (Windows, Linux, macOS). Primary dev on Windows 11,
  production targets are Linux.
- **Distribution:** PEP 621 `pyproject.toml`, installable with `pip` / `pipx`.
  Repo lives on Gitea (per user policy); GitHub publication is a separate
  later decision.

---

## 3. Package layout

```
pyRequirements-manager/              (repo root — current name retained)
├── pyproject.toml                   (new — PEP 621 + console_scripts)
├── README.md                        (new — usage, install, sample output)
├── .gitignore                       (existing, updated)
├── src/
│   └── piptastic/                   (renamed from requirements_manager)
│       ├── __init__.py              (version + public API)
│       ├── cli.py                   (argparse subcommands)
│       ├── discovery.py             (tree walk → list[Project])
│       ├── parsing.py               (req.txt / pyproject / Pipfile → list[Dep])
│       ├── pypi.py                  (concurrent PyPI client + on-disk TTL cache)
│       ├── analysis.py              (drift classifier, pinning posture, rollup)
│       ├── render/
│       │   ├── __init__.py
│       │   ├── terminal.py          (rich-based tree/table/summary views)
│       │   └── json_out.py          (stable JSON shape, schema_version=1)
│       ├── update.py                (file-mutating logic; only used by `update`)
│       └── logging.py               (logger factory — no module-level side effects)
├── tests/
│   ├── fixtures/                    (sample projects of each format)
│   ├── test_parsing.py
│   ├── test_discovery.py
│   ├── test_analysis.py
│   ├── test_pypi.py
│   └── test_cli.py
└── archive/
    └── PIPRU.py                     (kept for historical reference)
```

**Removed from repo as part of v0.2:**

- `pip-update-requirements/` — vendored copy of the third-party `pur` library,
  not imported anywhere. Pure dead weight.
- `requirements_updater.py` — root entry-point script, replaced by the
  console_scripts entry.
- `src/requirements_manager/` — replaced by `src/piptastic/`.
- `requirements.txt` at the root — current contents (`requests`, `pandas`,
  `numpy`, `flask`, ...) are unused. Real runtime deps move into
  `pyproject.toml`.

**Kept:**

- `archive/PIPRU.py` — historical reference, harmless.
- `requirements_backups/` and `.requirements_backups/` — historical, ignored by
  `.gitignore`.

---

## 4. Data model

All shared types live in `piptastic/__init__.py` (re-exported) or in the module
they're closest to. Frozen dataclasses where ownership is clear; mutable only
where the design demands it (the audit aggregator).

```python
class SemverDrift(StrEnum):
    NONE = "none"
    BUILD = "build"     # the "nano" tier — post/dev/local segments only
    PATCH = "patch"
    MINOR = "minor"
    MAJOR = "major"
    EPOCH = "epoch"
    UNKNOWN = "unknown"

class PinStatus(StrEnum):
    PINNED = "pinned"           # ==X.Y.Z (single, exact)
    COMPATIBLE = "compatible"   # ~=X.Y  or  ==X.Y.*
    RANGE = "range"             # bounded on both sides
    FLOOR = "floor"             # >=X.Y only, no upper bound
    UNPINNED = "unpinned"       # empty specifier set
    URL = "url"                 # VCS/URL requirement

@dataclass(frozen=True)
class DepSource:
    kind: Literal["requirements_txt", "pyproject_pep621", "pyproject_poetry",
                  "pipfile", "pipfile_lock", "constraints_txt"]
    path: Path
    group: str           # "default", "dev", "test", "<extra>", etc.

@dataclass(frozen=True)
class Dep:
    name: str                       # canonicalized per PEP 503
    raw_name: str                   # as written
    specifier: SpecifierSet
    extras: frozenset[str]
    marker: Marker | None
    source: DepSource
    line_no: int | None             # for req.txt only
    url: str | None                 # for VCS/URL requirements

@dataclass(frozen=True)
class Project:
    name: str
    path: Path
    python_version: str | None
    python_source: str | None
    python_constraints: str | None
    dep_sources: list[DepSource]

@dataclass(frozen=True)
class DepAudit:
    dep: Dep
    installed: Version | None
    latest: Version | None
    latest_including_prereleases: Version | None
    drift: SemverDrift
    pin_status: PinStatus
    yanked: bool
    warnings: list[str]

@dataclass
class ProjectAudit:
    project: Project
    deps: list[DepAudit]
    pinning_score: float                       # 0.0–1.0
    drift_summary: dict[SemverDrift, int]
    yanked_count: int
    pypi_unreachable: list[str]
```

---

## 5. Discovery (`discovery.py`)

### 5.1 Public API

```python
def discover_tree(root: Path, *, exclude: list[str] = ()) -> list[Project]
def discover_one(project_path: Path) -> Project | None
```

`discover_one` exists so that `piptastic list <project_path>` and
`piptastic update <project_path>` can act on a known project without rescanning
its parent directory (fixes [C5] from the review).

### 5.2 Discovery rules

1. **File patterns:**
   - `requirements.txt`, `requirements-*.txt`, `*-requirements.txt`,
     `constraints.txt`, `constraints-*.txt` → `DepSource(kind="requirements_txt",
     group=<inferred>)`. Group inference, case-insensitive on the filename
     stem: contains `dev` → `"dev"`; contains `test` → `"test"`; contains
     `prod` → `"prod"`; bare `requirements.txt` or `constraints.txt` →
     `"default"`; anything else → the non-`requirements`/`constraints` token
     from the filename (`requirements-ml.txt` → `"ml"`).
   - `pyproject.toml` → one or more `DepSource(kind="pyproject_pep621"|
     "pyproject_poetry", group=<table-name>)` depending on which tables are
     present.
   - `Pipfile` → `DepSource(kind="pipfile", group="default"|"dev")`.
   - `Pipfile.lock` → `DepSource(kind="pipfile_lock", ...)` — parsed but only
     for cross-checking against `Pipfile` in v0.3; in v0.2 it's read for display
     only.

2. **Project boundary.** A "project" is rooted at the directory that contains
   one or more dep sources. Multiple sibling dep files in the same directory
   (e.g. `requirements.txt` + `dev-requirements.txt` + `pyproject.toml`)
   collapse into one `Project` with multiple `dep_sources`. Nested dep files
   in subdirectories produce separate `Project` entries — the tool does not
   try to "glue" a subdir's requirements file to a parent's `pyproject.toml`;
   if the user organizes that way intentionally, each gets its own report
   line.

3. **Exclusion rules (fixes [C2]):**
   - **Exact-match** venv directory names: `venv`, `.venv`, `env`, `.env`,
     `ENV`, `virtualenv`.
   - **Any directory containing `pyvenv.cfg`** (most reliable venv detection).
   - **Always-skip dirs:** `.git`, `.hg`, `.svn`, `.tox`, `.nox`,
     `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `node_modules`,
     `__pycache__`, `site-packages`, `build`, `dist`, `*.egg-info`.
   - User-extensible via `--exclude` CLI flag and `[tool.piptastic] exclude`
     in a `pyproject.toml` at the scan root.

4. **No filesystem side effects.** Discovery never creates directories.
   `.requirements_backups/` is created lazily by `update.py` only when an
   `update` actually runs. (Fixes [C1].)

---

## 6. Parsing (`parsing.py`)

### 6.1 Public API

```python
def parse_source(source: DepSource) -> list[Dep]
```

### 6.2 Per-format rules

**`requirements.txt` and friends:**

- Use `packaging.requirements.Requirement` instead of the homegrown regex.
  Handles extras, markers, `!=`, `===`, URL requirements, `-e` editable.
- `-r other.txt` and `-c constraints.txt` includes are followed recursively
  with a cycle guard (visited-set keyed by absolute path). Each yielded `Dep`
  carries `source` pointing at its true file of origin, not the entry file.
- Comments and blank lines are preserved by `update.py`'s writer, but
  `parse_source` returns only `Dep`s — formatting is the writer's problem.

**`pyproject.toml` PEP 621:**

- `[project].dependencies` → strings are already PEP 508; pass through
  `Requirement()`. Group = `"default"`.
- `[project].optional-dependencies.<extra>` → group = `<extra>`.

**`pyproject.toml` Poetry:**

- `[tool.poetry.dependencies]` and `[tool.poetry.group.<name>.dependencies]`.
- Convert Poetry's table syntax (`{version = "^1.2", extras = [...], python = ">=3.10"}`)
  and shorthand (`"^1.2"`, `"*"`) to PEP 508 strings:
  - `^X.Y.Z` → `>=X.Y.Z,<(X+1).0.0`
  - `~X.Y` → `~=X.Y`
  - `*` or omitted → empty specifier (UNPINNED)
- Convert to `Requirement()` after string assembly.

**`Pipfile`:**

- TOML, same shape as Poetry-style tables under `[packages]` / `[dev-packages]`.
- Caret/tilde resolved the same way as Poetry above.

**`Pipfile.lock`:**

- JSON. Read `default` and `develop` sections. Each entry has a `version`
  field (already pinned). Parsed but treated as informational in v0.2.

### 6.3 Edge cases

- **VCS / URL requirements** (`pkg @ git+https://…`, `-e git+…`) → `Dep` with
  empty specifier, populated `url`, `PinStatus.URL`. Audited as "unpinnable"
  rather than skipped.
- **Markers** preserved on the `Dep` and shown in output when present.
  Considered when matching against current Python.
- **Invalid lines** → logged as warnings, parser continues. Never raises out
  to the caller.

---

## 7. PyPI client (`pypi.py`)

### 7.1 Public API

```python
class PyPIClient:
    def __init__(self, *, cache_dir: Path | None = None, ttl_seconds: int = 3600,
                 timeout: float = 10.0, concurrency: int = 8): ...

    def fetch_many(self, names: Iterable[str]) -> dict[str, PackageMetadata]: ...
    def fetch_one(self, name: str) -> PackageMetadata | None: ...

@dataclass(frozen=True)
class PackageMetadata:
    name: str
    releases: dict[Version, ReleaseInfo]   # all known releases, parsed
    fetched_at: datetime

@dataclass(frozen=True)
class ReleaseInfo:
    version: Version
    yanked: bool
    yanked_reason: str | None
    requires_python: SpecifierSet | None
    upload_time: datetime | None
```

### 7.2 Concurrency

- `concurrent.futures.ThreadPoolExecutor` with `max_workers=concurrency`
  (default 8). `httpx` would be nicer but `urllib.request` keeps the dep tree
  small; threads are fine for I/O-bound PyPI calls.
- Per-request `timeout` defaulting to 10s (fixes [M2]).

### 7.3 Cache

- On-disk JSON cache under `~/.cache/piptastic/pypi/{first2chars}/{name}.json`
  (XDG_CACHE_HOME respected). Entries carry `fetched_at`; expired entries are
  treated as cache misses.
- `--no-cache` flag and `--refresh-cache` flag for power users.
- TTL default 1 hour. Configurable via `[tool.piptastic] cache_ttl_seconds`
  and `--cache-ttl`.

### 7.4 Release filtering for "latest"

- Skip prereleases unless `--include-prereleases` is set.
- Skip yanked releases (always; yanked-aware is the whole point).
- Skip releases whose `requires_python` does not satisfy the project's target
  Python (fall back to current Python when project doesn't declare one).
- **The `is_zero_version` filter from the old code is removed** (fixes [C3]).
- **The "stable = latest − 1" heuristic is removed.** `latest` means newest
  release that survived the filters above. (Fixes [C4]. A `--cautious` flag
  to opt into "newest minus one minor" is a deliberate non-goal for v0.2; can
  be added if real demand emerges.)

---

## 8. Analysis (`analysis.py`)

### 8.1 Public API

```python
def audit_project(project: Project, client: PyPIClient,
                  current_python: Version) -> ProjectAudit
```

### 8.2 Drift classification

For `current = Version(epoch, [major, minor, micro, *rest], ...)` versus
`latest`:

| Condition | Drift |
|---|---|
| `current == latest` | `NONE` |
| epoch differs | `EPOCH` |
| `major` differs | `MAJOR` |
| `minor` differs | `MINOR` |
| `micro` differs | `PATCH` |
| only post/dev/local segments differ | `BUILD` |
| `Version` parsing fails for either side | `UNKNOWN` |

When the dep is `UNPINNED`, we use the *installed* version (from
`importlib.metadata`) as `current` for drift purposes if available; otherwise
drift is `UNKNOWN` and a warning is attached.

### 8.3 Pin status

Determined from `dep.specifier`:

| Specifier shape | Status |
|---|---|
| Single `==X.Y.Z` clause | `PINNED` |
| Any `~=` clause, or single `==X.Y.*` clause | `COMPATIBLE` |
| Both lower and upper bounds present (`>=A,<B`) | `RANGE` |
| `>=` only, no upper | `FLOOR` |
| Empty specifier set | `UNPINNED` |
| `url` field set | `URL` |

### 8.4 Rollup

- `pinning_score = weighted_mean(PINNED=1.0, COMPATIBLE=0.8, RANGE=0.6,
  FLOOR=0.3, UNPINNED=0.0, URL=excluded_from_average)`.
- `drift_summary` is a `Counter` over `SemverDrift`.
- `yanked_count` counts deps whose currently-specified version is yanked.

### 8.5 Concurrency

`audit_project` calls `client.fetch_many(names)` once with all unique package
names from all sources, then maps results back. One PyPI round-trip per
unique package per cache window, regardless of how many groups/files it
appears in.

---

## 9. Rendering (`render/`)

### 9.1 Terminal (`render/terminal.py`)

Built on `rich`. Three views, all driven by the same `list[ProjectAudit]`:

- **Tree view** (default for multi-project audits): nested `rich.Tree` with
  project → source file → dep lines. Color per drift level; pin status as a
  word, not just a glyph. Project line shows pin-score and ✓/⚠/✗ counts.
- **Table view** (`--table` flag, default for single-project audits): flat
  `rich.Table` with columns `Project | File | Package | Current | Latest |
  Drift | Pin | Notes`.
- **Summary view** (`--summary`): one row per project, no per-dep detail.

Color map: `NONE`=green, `BUILD`=dim, `PATCH`=yellow, `MINOR`=orange,
`MAJOR`=red, `EPOCH`=magenta, `UNKNOWN`=white. Yanked versions: red
strikethrough.

Output detects whether stdout is a TTY; when piped to a file, `rich`
auto-disables color and ANSI markers — pin status words ensure the report
is still readable in that case.

### 9.2 JSON (`render/json_out.py`)

Stable shape with `schema_version: 1`. Full shape documented in §11 below;
consumers can rely on it not changing within v0.x. Breaking shape changes
bump `schema_version`.

```json
{
  "schema_version": 1,
  "scanned_at": "2026-05-25T14:30:00Z",
  "root": "F:/projects",
  "projects": [{
    "name": "webapp",
    "path": "F:/projects/webapp",
    "python": {"version": "3.11", "source": "pyproject.toml", "constraints": ">=3.11"},
    "pinning_score": 0.92,
    "drift_summary": {"major": 1, "minor": 2, "patch": 0, "build": 0, "none": 18},
    "yanked_count": 0,
    "sources": [
      {"kind": "requirements_txt", "path": "requirements.txt", "group": "default"},
      {"kind": "requirements_txt", "path": "dev-requirements.txt", "group": "dev"}
    ],
    "deps": [{
      "name": "flask",
      "source_file": "requirements.txt",
      "group": "default",
      "specifier": "==3.0.2",
      "pin_status": "pinned",
      "current": "3.0.2",
      "installed": "3.0.2",
      "latest": "3.1.0",
      "drift": "minor",
      "yanked": false,
      "warnings": []
    }]
  }]
}
```

JSON writes to stdout; redirect with `> file.json`.

---

## 10. Update flow (`update.py`)

Used only by the explicit `piptastic update` subcommand. Conceptually
unchanged from the current tool, but with the bugs fixed:

- **Test venv lives in the project directory** (or a temp dir if
  `--temp-test-env`), never in CWD. (Fixes [C6].)
- **Failed test cleans up its venv** before returning. (Fixes [C7].)
- **Writer preserves blank lines and comment blocks.** It works from the
  original file's line list, replacing only the version on lines that own a
  pinned `Dep`. Untouched lines pass through verbatim. (Fixes [C8].)
- **Constrained packages are not re-queried from PyPI** unless `--refresh`
  is set; the writer keeps the existing constraint. (Fixes [I3].)
- **`pyproject.toml` and `Pipfile` updates** are out of scope for v0.2 —
  `update` only mutates `requirements*.txt`. Attempting to update a TOML-only
  project produces a clear "not yet supported, edit by hand" error.

Backups: kept. `.requirements_backups/<filename>_<timestamp>_<hash8>.txt`
inside the project dir, created lazily on first update.

---

## 11. CLI (`cli.py`)

Subcommands:

```
piptastic audit <path> [--table | --summary] [--json] [--include-prereleases]
                       [--exclude PATTERN]... [--no-cache] [--refresh-cache]
                       [--cache-ttl SECS] [--concurrency N]
piptastic list  <project_path> [--json]
piptastic update <project_path> [packages...] [--no-test] [--refresh]
                                [--temp-test-env]
piptastic --version
piptastic --help
```

- `audit` is the headline command. `path` can be a project dir (single-project
  report) or a parent dir (multi-project tree report).
- `list` is an alias for `audit <project_path> --table` on a single project —
  kept for muscle memory from the previous tool.
- `update` is the only command that touches files. Always creates a backup
  first.
- `ptc` is an installed alias for `piptastic` (same entry point).

Exit codes:

- `0` — success (audit completed; even if outdated deps exist).
- `1` — operational failure (file not found, PyPI unreachable for all packages,
  malformed input).
- `2` — `update` test-install failed and the file was rolled back from backup.

Optional `--fail-on-drift LEVEL` (e.g. `--fail-on-drift major`) flips the
exit code to non-zero when audit finds drift at or above the given level —
useful for CI gates. Default behavior never fails the build just because of
outdated deps.

---

## 12. Logging (`logging.py`)

- `get_logger(name="piptastic")` factory. No module-level side effects.
  (Fixes [I2].)
- `--log-file PATH` and `--quiet` / `--verbose` flags on the CLI.
- Default: WARNING to stderr, no file log.

---

## 13. Packaging (`pyproject.toml`)

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "piptastic"
version = "0.2.0"
description = "Audit Python dependency posture across all projects in a tree."
requires-python = ">=3.10"
readme = "README.md"
license = { text = "MIT" }   # confirm with user
dependencies = [
  "packaging>=23.0",
  "rich>=13.0",
  "tomli>=2.0; python_version < '3.11'",
]

[project.scripts]
piptastic = "piptastic.cli:main"
ptc = "piptastic.cli:main"

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-cov>=4.0"]

[tool.hatch.build.targets.wheel]
packages = ["src/piptastic"]
```

`importlib.metadata` replaces `pkg_resources` throughout. (Fixes [I1].)

---

## 14. Testing

`pytest` with fixtures in `tests/fixtures/`:

- `req_only/` — single `requirements.txt` with mixed pinned / unpinned / constrained.
- `pyproject_pep621/` — modern PEP 621 layout.
- `pyproject_poetry/` — Poetry layout with groups.
- `pipfile/` — Pipfile + Pipfile.lock.
- `mixed/` — req.txt + pyproject.toml + dev-requirements.txt in one project.
- `venv_inside/` — project with a `.venv/` containing a fake site-packages
  with its own requirements.txt; must be excluded.
- `envoy_dir/` — directory literally named `envoy/` containing a project; must
  *not* be excluded (regression test for [C2]).

Unit tests cover:

- `parsing.py` — every format above, including `-r` cycles and URL deps.
- `discovery.py` — exclusion rules, project boundary, `discover_one`.
- `analysis.py` — drift classification truth table, pin-status truth table,
  rollup math.
- `pypi.py` — cache hit/miss/expiry; concurrency; mocked HTTP responses
  including yanked releases.
- `cli.py` — argparse smoke tests + JSON shape matches `schema_version: 1`
  contract.

`pytest-cov` target: 85% on non-CLI modules.

---

## 15. Bug-fix mapping

| Review ID | Fixed in |
|---|---|
| [C1] scan side-effects | §5.4 discovery is read-only; `update.py` creates backup dir lazily |
| [C2] broken venv exclusion | §5.3 exact-match + `pyvenv.cfg` detection |
| [C3] `is_zero_version` filter | §7.4 removed |
| [C4] "stable = N-1" heuristic | §7.4 removed |
| [C5] re-scan parent on list/update | §5.1 `discover_one` |
| [C6] test venv in CWD | §10 test venv in project dir |
| [C7] failed test leaks venv | §10 cleanup on failure |
| [C8] writer destroys blank lines | §10 line-preserving writer |
| [I1] `pkg_resources` deprecated | §13 `importlib.metadata` |
| [I2] logger side-effect on import | §12 factory pattern |
| [I3] PyPI queried for constrained pkgs | §10 skip unless `--refresh` |
| [I4] entry point depends on CWD | §13 `console_scripts` entry |
| [I5] vendored `pur` library | §3 deleted |
| [I6] root `requirements.txt` wrong | §3 deleted, deps move to pyproject |
| [I7] no tests | §14 test suite |
| [M1] homegrown regex | §6 `packaging.requirements.Requirement` |
| [M2] no urlopen timeout | §7.2 default 10s timeout |
| [M3] CLI usage hint wrong | obsoleted by console_scripts entry point |

---

## 16. Risks / open questions

- **PyPI rate limiting.** PyPI's JSON API is generous but uncached scans of
  large monorepos could hit limits. The on-disk cache mitigates this; if it
  becomes a real problem, swap to the simple-API + per-project ETag handling.
- **Poetry version-spec edge cases.** The caret/tilde converter is best-effort;
  edge cases like `~1` (which Poetry treats as `>=1.0.0,<2.0.0`, *not* `~=1.0`)
  need explicit unit tests.
- **Cross-platform path handling.** `pyvenv.cfg` detection is reliable on
  both platforms, but file-watching, symlinks, and case-insensitive paths
  on Windows need to be tested explicitly.
- **License choice for the package** — design assumes MIT to keep parity with
  most Python ecosystem tools; confirm before publishing.

---

## 17. Deferred to v0.3+

- Watch / daemon mode (periodic poll, summary report, webhook/email output).
- OSV.dev / PyPI advisory-DB integration for CVE checks.
- Lockfile-drift detection (Pipfile vs Pipfile.lock, future `poetry.lock`,
  `uv.lock`).
- HTML report output.
- `setup.py` / `setup.cfg` parsing (legacy; revisit if asked).
- `update` for `pyproject.toml` and `Pipfile`.
- A `--cautious` flag that picks `latest − 1` if the "always latest" default
  turns out to be too aggressive in practice.
