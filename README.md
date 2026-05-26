# piptastic

Audit Python dependency posture across all projects in a tree. Read-only by
default — think Watchtower for your `requirements.txt` files.

## Install

```bash
pip install .
# or, for an isolated install with the CLI on PATH:
pipx install .
```

Provides two commands: `piptastic` and `ptc` (short alias). Both are equivalent
entry points — `ptc` is just a typing shortcut for ad-hoc shell use.

If you're running directly from a cloned repo without installing, use
`python -m piptastic` (or `py -3.10 -m piptastic` on Windows) in place of the
`piptastic` command shown in the examples below.

## What it does

Auto-discovers Python projects under a path and reports, per project:

- **Drift** for every dependency: classified as `MAJOR` / `MINOR` / `PATCH` /
  `BUILD` (the "nano" tier) / `NONE`, colored.
- **Pinning posture**: `PINNED` / `COMPATIBLE` / `RANGE` / `FLOOR` /
  `UNPINNED` / `URL`, with a 0-100% pin score per project (or `n/a` when a
  project has only URL deps, which are deliberately excluded from the score
  because URL pinning posture depends on the git ref).
- **Yanked releases** that are still pinned.
- **PyPI unreachability** so transient network issues don't silently masquerade
  as "up to date".

Supports `requirements.txt` family (including `-r` / `-c` includes with cycle
detection), `pyproject.toml` (PEP 621 + Poetry, with caret/tilde shorthand
expansion), and `Pipfile` / `Pipfile.lock`.

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

# Update only specific packages
piptastic update ./myproject flask requests

# Update without running the test-install step
piptastic update ./myproject --no-test
```

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

## Output channels

- **Tree view** (default for multi-project audits) — nested project → file → dep.
- **Table view** (`--table`, default for single-project audits) — one row per dep.
- **Summary view** (`--summary`) — one row per project, drift histogram + pin score.
- **JSON** (`--json`) — stable shape, intended for CI consumption. Writes to
  stdout; redirect with `> file.json`.

The terminal renderer auto-detects whether stdout is a TTY and disables color
when piping to a file.

## Configuration

- Caches PyPI metadata under `$XDG_CACHE_HOME/piptastic/pypi/` (1h TTL by
  default). Override with `--cache-ttl`, `--no-cache`, `--refresh-cache`,
  or the `PIPTASTIC_CACHE_DIR` environment variable.
- Per-tree exclusions: `--exclude PATTERN` (repeatable; glob syntax). On top of
  the built-in exclusion list (`.git`, `.venv`, `node_modules`, `__pycache__`,
  `site-packages`, `build`, `dist`, any directory containing `pyvenv.cfg`,
  etc.).
- Logging: `--verbose` for INFO, `--quiet` for ERROR only, `--log-file PATH`
  for a separate file log. Default is WARNING to stderr.

## Exit codes

- `0` — audit completed successfully (even if outdated deps were found)
- `1` — operational failure (path not found, no Python projects, PyPI
  totally unreachable, malformed input)
- `2` — `update` test-install failed and the requirements file was rolled
  back from the backup

Use `--fail-on-drift {build,patch,minor,major,epoch}` to make exit code 1
also fire when drift at or above the given level is found — useful for CI
gates on dependency staleness.

## Status

v0.2 — see `docs/superpowers/specs/` for the full design and
`docs/superpowers/plans/` for the implementation plan.

## Not in v0.2

Deferred to v0.3+: watch/daemon mode, CVE/security advisory checks via
OSV.dev, lockfile-drift detection (Pipfile vs Pipfile.lock, poetry.lock,
uv.lock), HTML report output, `setup.py` / `setup.cfg` parsing, `update`
for `pyproject.toml` and `Pipfile`.
