# piptastic v0.2.1 — bootstrap + stats

**Status:** approved (brainstorm)
**Date:** 2026-05-26
**Author:** brainstorm session
**Extends:** `piptastic` v0.2 (commit `c3544c0`)

---

## 1. Purpose

Two new subcommands surfaced by real-world use of v0.2 on the user's
laboratory tree:

1. **`piptastic bootstrap`** — generate a `requirements.txt` for a project
   that has a working `.venv/` but no dep manifest. Captures the venv's
   installed state as `==X.Y.Z` pins so the project becomes reproducible
   and auditable by `piptastic audit`.
2. **`piptastic stats`** — cross-project rollup over a directory tree:
   most-required packages, version fragmentation, projects with yanked
   pins, projects with zero pinning. Built on `audit`'s data layer; no
   new IO.

Both features are read-only-by-default in spirit: `stats` never writes;
`bootstrap` writes only `requirements.txt` and only with explicit
acknowledgement when one already exists.

Out of scope for v0.2.1 (deferred to v0.3+): updating `pyproject.toml` /
`Pipfile`, scaffolding `pyproject.toml` from scratch, "top-level only"
dep detection in bootstrap, OSV/CVE integration, watch/daemon mode,
HTML reports.

---

## 2. Target environment

Same as v0.2: Python 3.10+, cross-platform (Windows dev, Linux prod),
`hatchling` build backend, installable as `piptastic` / `ptc`. No new
runtime dependencies.

---

## 3. Package layout (deltas only)

**Created:**

- `src/piptastic/bootstrap.py` — venv discovery + `freeze_venv` pure
  function.
- `src/piptastic/stats.py` — `compute_stats` pure aggregator.
- `tests/test_bootstrap.py`
- `tests/test_stats.py`

**Modified:**

- `src/piptastic/cli.py` — add `bootstrap` and `stats` subparsers and
  their handlers.
- `src/piptastic/models.py` — add `StatsReport`, `PackageFrequency`,
  `VersionFragmentation` dataclasses.
- `src/piptastic/render/terminal.py` — add `render_stats_terminal`. Also
  introduce a small `_make_console()` helper that returns a `Console`
  with `safe_box=True` when stdout's encoding is not UTF-8 (fixes the
  cp1252 `…` → `�` truncation issue surfaced by the final review).
- `src/piptastic/render/json_out.py` — add `render_stats_json`.
- `src/piptastic/render/__init__.py` — re-export the two new render
  functions.
- `tests/test_cli.py` — extend with smoke tests for both new subcommands.
- `README.md` — add a "Bootstrap and stats" section near the existing
  Usage block.

**Not changed:** parsing, discovery, analysis, pypi, update. The new
features build on existing primitives.

---

## 4. Data model (additions)

In `src/piptastic/models.py`:

```python
@dataclass(frozen=True)
class PackageFrequency:
    name: str
    project_count: int
    projects: tuple[str, ...]       # project names, sorted

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
    unpinned_projects: tuple[str, ...]   # project names with pin_score == 0.0 AND >= 5 deps
```

Frozen dataclasses for hashability + immutability; renderers consume
without mutating.

---

## 5. `bootstrap` subcommand

### 5.1 CLI surface

```
piptastic bootstrap <project_path>
  [--venv <relative-or-absolute-dir>]
  [--force]
  [--dry-run]
```

### 5.2 Behavior

1. **Locate venv.** Resolution order:
   - If `--venv` is given, use that path (resolved relative to `project_path` if not absolute).
   - Otherwise check, in order: `<project>/.venv`, `<project>/venv`, `<project>/env`, `<project>/.env`.
   - If none of those exist, scan top-level subdirs of `<project>` for one containing `pyvenv.cfg`.
   - 0 found → exit 1 with `"no venv found under <project>; pass --venv to specify"`.
   - 1 found → use it.
   - >1 found and `--venv` not given → exit 1 with `"multiple venvs found: <list>; pass --venv to disambiguate"`.

2. **Find `site-packages/`.** Cross-platform:
   - Windows: `<venv>/Lib/site-packages/`
   - POSIX: `<venv>/lib/python*/site-packages/` (glob the python version dir).
   - If not found → exit 1 with `"<venv> does not contain a site-packages directory"`.

3. **Enumerate installed distributions.**
   `importlib.metadata.distributions(path=[<site-packages>])` returns
   distribution objects readable from that path.

4. **Skip rules** (applied in order):
   - **Plumbing names** (case-insensitive canonical match): `pip`,
     `setuptools`, `wheel`, `pkg-resources`, `distlib`, `_distutils_hack`.
   - **Self-install detection**: if the distribution's
     `direct_url.json` file is present AND its `url` points back into
     `<project_path>`, skip it (the project is editable-installed into
     its own venv).

5. **Emit lines.** For each remaining distribution:
   - Canonicalize the name per PEP 503.
   - Format as `<canonical_name>==<version>` (no extras, no markers —
     bootstrap captures the resolved state, not the original spec).
   - Sort alphabetically.

6. **Write requirements.txt.**
   - Default target: `<project_path>/requirements.txt`.
   - If file exists AND `--force` not set → exit 1 with
     `"<path> already exists; pass --force to overwrite (a backup will be created)"`.
   - If file exists AND `--force` set → back up to
     `<project>/.requirements_backups/requirements_<ts>_<sha8>.txt`
     (same pattern as `update.py`), then overwrite.
   - With `--dry-run`, write to stdout instead. No filesystem mutation.

7. **Report on stdout** (when not `--dry-run`):
   ```
   piptastic: wrote <abs/path/requirements.txt>
     captured 23 deps from <venv>
     skipped 3 (pip, setuptools, wheel)
   ```

### 5.3 Exit codes

- `0` — success (file written, or dry-run printed)
- `1` — no venv found / ambiguous venv / file exists without `--force` /
  no site-packages
- `2` — IO error during write

### 5.4 Module API (pure, testable)

```python
def freeze_venv(
    project_path: Path,
    venv_dir: Path,
) -> list[str]:
    """Return a sorted list of 'name==version' lines for non-plumbing,
    non-self distributions installed in venv_dir."""

def find_venv(project_path: Path, *, explicit: Path | None = None) -> tuple[list[Path], Path | None]:
    """Return (all_candidates, chosen). chosen is None when caller must
    disambiguate."""

def find_site_packages(venv_dir: Path) -> Path | None: ...

def is_plumbing(canonical_name: str) -> bool: ...

def is_self_install(dist, project_path: Path) -> bool: ...
```

The CLI handler does file IO; the module functions return data.

---

## 6. `stats` subcommand

### 6.1 CLI surface

```
piptastic stats <tree>
  [--top N]                   # default 20
  [--json]
  [--exclude PATTERN]...      # repeatable glob, same as audit
  [--no-cache] [--refresh-cache] [--cache-ttl SECS]
```

### 6.2 Behavior

1. Reuses `discover_tree(tree, exclude=...)` and per-project
   `audit_project(...)` exactly as the `audit` subcommand does. Cache
   semantics inherited.
2. Calls `compute_stats(audits, top=N)` to produce a `StatsReport`.
3. Renders via `render_stats_terminal` (default) or `render_stats_json`.

### 6.3 Aggregation rules (`stats.py::compute_stats`)

For inputs `audits: list[ProjectAudit]` and `top: int`:

- **`top_packages`** — count distinct projects per canonical package
  name (one count per project, even if the package appears in multiple
  DepSources within the same project). Sort by descending count, then
  alphabetical for ties. Return top `top` entries.

- **`version_fragmentation`** — for each canonical package name where
  projects across the tree pin it to **2 or more distinct `==X.Y.Z`
  values**, emit a `VersionFragmentation` entry. (Packages pinned to
  the same version everywhere are not fragmented and don't appear.)
  Sort by count of distinct versions descending, then by name ascending.

- **`yanked_findings`** — one entry per (project, dep) where
  `DepAudit.yanked is True`. Include the latest non-yanked version
  (`DepAudit.latest`). Sort by project name then package name.

- **`unpinned_projects`** — project names where `pinning_score == 0.0`
  AND `len(deps) >= 5` (the threshold avoids noise from tiny stub
  projects). Sort alphabetical.

- **`drift_histogram`** and **`pin_status_histogram`** — sum across all
  audits.

- **`total_deps`** — sum of `len(audit.deps)` across all audits.

### 6.4 Terminal output (`render_stats_terminal`)

Five sections, each a `rich.Table`:

```
piptastic stats — F:/laboratory (164 projects, 2147 deps)

Top 20 most-required packages
  Package                Projects  Sample of projects
  requests                    42   cowrie, phishing_catcher, pyrdp, ...
  ...

Most version-fragmented packages
  Package        Distinct versions
  jsonschema     ==4.* (2), ==4.21.0 (3), ==4.25.1 (1)
  ...

Drift across the tree
  none: 442   build: 7   patch: 135   minor: 533   major: 512   unknown: 518

Pin posture across the tree
  pinned: ...   compatible: ...   range: ...   floor: ...   unpinned: 586   url: ...

Yanked pins (5)
  Project              Package              Pinned     Latest non-yanked
  phishing_catcher     python-levenshtein   ==0.12.0   0.27.3
  ...

Unpinned projects (deps >= 5)
  .ansible_hub, Arista-RAG, Blog-LLM, ...
```

### 6.5 JSON output (`render_stats_json`)

Stable shape with `schema_version: 1` (distinct from `audit`'s schema —
JSON consumers identify by both the top-level key set AND the
`schema_version`).

```json
{
  "schema_version": 1,
  "kind": "stats",
  "scanned_at": "2026-05-26T14:30:00Z",
  "root": "F:/laboratory",
  "totals": {
    "project_count": 164,
    "total_deps": 2147,
    "drift_histogram": {"major": 512, "minor": 533, ...},
    "pin_status_histogram": {"pinned": ..., "unpinned": 586, ...}
  },
  "top_packages": [
    {"name": "requests", "project_count": 42, "projects": ["cowrie", "..."]}
  ],
  "version_fragmentation": [
    {"name": "jsonschema", "versions": {"4.*": ["ynl", "..."], "4.21.0": ["..."]}}
  ],
  "yanked_findings": [
    {"project_name": "phishing_catcher", "project_path": "...", "package_name": "python-levenshtein", "pinned_version": "0.12.0", "latest_non_yanked": "0.27.3"}
  ],
  "unpinned_projects": [".ansible_hub", "Arista-RAG", "..."]
}
```

The `"kind": "stats"` discriminator distinguishes from `audit`'s shape
(which has `"projects": [...]`).

### 6.6 Exit codes

- `0` — stats produced successfully (always, even if PyPI was partially
  unreachable — that surfaces in the data, not the exit code).
- `1` — root path doesn't exist, or no Python projects found.

---

## 7. Bug fixes folded in

While we're modifying renderer code, we'll address two of the
final-review minor items:

- **cp1252 truncation** — introduce `_make_console()` in
  `render/terminal.py` that returns a `Console(safe_box=True)` when
  `sys.stdout.encoding` doesn't include `utf` (case-insensitive). All
  three view functions (`tree`, `table`, `summary`) and the new
  `render_stats_terminal` use this helper. Eliminates the `…` → `�`
  cosmetic issue on Windows default consoles.

- **`--exclude` doc clarification** — update the audit subparser's
  `--exclude` help string to read:
  `"Glob pattern matched against directory BASENAMES (not paths), repeatable"`.
  Mirror on the new `stats` subparser.

The `.requirements_backups/` first-time-user surprise is NOT folded in —
that's a structural decision (gitignore auto-emit vs. relocation to
XDG_STATE_HOME) that deserves its own discussion. Deferred to v0.3.

---

## 8. Testing

### 8.1 `tests/test_bootstrap.py`

Fixture helper builds a fake venv tree under `tmp_path`:

```python
def build_fake_venv(tmp_path: Path, *, on_windows: bool = False, packages: dict[str, str], editable_self: bool = False, project_root: Path | None = None) -> Path:
    """packages = {canonical_name: version}. Optionally adds a
    direct_url.json for a self-editable install."""
```

Tests:

- `freeze_venv` returns sorted `name==ver` for non-plumbing distributions
- Plumbing (`pip`, `setuptools`, `wheel`) is excluded
- Self-install (editable `.` pointing back at project) is excluded
- Cross-platform site-packages: parametrize over `on_windows=True/False`
- `find_venv` returns 0 candidates, 1 candidate, or >1 candidates as
  expected
- CLI smoke: write to disk, refuse overwrite, `--force` backs up,
  `--dry-run` writes nothing

### 8.2 `tests/test_stats.py`

Synthesize a small `list[ProjectAudit]` directly (no real PyPI):

- `top_packages` sorted correctly + tie-break alphabetical
- `version_fragmentation` excludes packages with only 1 distinct version
- `yanked_findings` triple is correct
- `unpinned_projects` filters by score AND dep-count threshold
- `drift_histogram` sums correctly
- JSON shape round-trips through `json.loads` and matches the spec
- Empty input → all sections empty, no crash

### 8.3 `tests/test_cli.py`

Extend with smoke tests:

- `bootstrap` against a fixture venv-only project
- `stats` against a fixture tree (monkeypatched PyPI client)
- Both subcommands' `--help` outputs are non-empty (cheap regression
  guard)

### 8.4 Target

Total tests: 67 → ~85. All on Python 3.10. Coverage targets unchanged.

---

## 9. Bug-fix mapping (for this delta)

| Review item | Fixed in |
|---|---|
| Final-review minor: cp1252 column truncation | §7 `_make_console()` helper |
| Final-review minor: `--exclude` glob doc ambiguity | §7 CLI help-string update |

---

## 10. Risks / open questions

- **Editable self-install detection** depends on `direct_url.json`,
  which is PEP 610 (relatively new). Some old pip versions may not
  produce it. Fallback: if the dist's name canonicalizes to the same
  thing as `project_path.name` (or its `pyproject.toml`'s `[project]
  name`), also treat as self. Document the fallback.

- **Fragmentation table cardinality** — a tree with 164 projects could
  produce a large fragmentation list. The current design has no
  pagination. If the terminal output gets unwieldy in practice, add a
  `--max-fragmentation N` flag later.

- **`stats` doesn't have a `--summary`-style condensed view** — only
  full sections or JSON. If the full view is too verbose for daily use,
  add a `--brief` flag in v0.3.

- **Plumbing skip-list could miss vendored stuff** — `_distutils_hack`
  and `pkg_resources` show up unpredictably across setuptools versions.
  The current list reflects current setuptools; if older or newer
  setuptools introduces a new shim, we'd silently include it. Acceptable
  for v0.2.1; revisit if user reports false positives.

---

## 11. Deferred (post v0.2.1)

- "Top-level only" bootstrap mode (requires dep-graph traversal).
- Bootstrap writing to `pyproject.toml` / `Pipfile`.
- `stats --brief` condensed view.
- `--max-fragmentation N` pagination.
- A `piptastic init` command for greenfield projects (different scope
  from bootstrap, which needs an existing venv).
- README expansion with stats/bootstrap recipes (will follow once
  there's real-use feedback).
- The `.requirements_backups/` first-time-user surprise (gitignore
  auto-emit vs. relocate to XDG_STATE_HOME — needs its own discussion).
