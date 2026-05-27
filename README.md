# piptastic

A read-only auditor for Python dependency files. Walks a directory tree,
finds every project that declares dependencies (`requirements*.txt`,
`pyproject.toml`, `Pipfile` / `Pipfile.lock`), resolves each declared
version against PyPI, and reports how stale and how strictly pinned the
declarations are.

Also writes (when explicitly asked): `update` rewrites `requirements.txt`
to the latest compatible pinned versions, `bootstrap` produces a
`requirements.txt` from an existing venv.

## Install

```bash
pip install .
# or, isolated install with the CLI on PATH:
pipx install .
```

Two entry points are installed: `piptastic` and `ptc` (short alias).
Both invoke the same `main()`. Without installing, run the package
directly: `python -m piptastic` (or `py -3.10 -m piptastic` on Windows).

Runtime requirements: Python 3.10+, `packaging`, `rich`. `tomli` is
pulled in on Python < 3.11.

## Commands

### `audit <path>`

Read-only. Discovers Python projects under `<path>` and reports each
dependency's pin posture and drift against PyPI.

| Flag | Effect |
| --- | --- |
| `--table` | Flat table view (default when `<path>` is a single project). |
| `--summary` | One row per project: drift histogram + pin score. |
| `--json` | JSON to stdout. See [JSON schema](#json-schema). |
| `--include-prereleases` | Consider pre-release versions as candidates for "latest". |
| `--exclude PATTERN` | Glob matched against directory **basename** (not full path). Repeatable. Layered on top of the built-in skip list. |
| `--no-cache` | Skip the on-disk PyPI cache for this run. |
| `--refresh-cache` | Force a fresh fetch and rewrite the cache. |
| `--cache-ttl SECONDS` | Override the default TTL (3600). |
| `--concurrency N` | PyPI fetch thread-pool size (default depends on dep count). |
| `--fail-on-drift {build,patch,minor,major,epoch}` | Exit 1 when any dep has drift ≥ this level. Pure CI gate; does not change output. |

Default view: tree (project → file → dep) for multi-project paths,
table for a single project.

### `list <path>`

Alias for `audit <path> --table` against a single project. Convenience
only — no extra behaviour.

### `update <path> [packages ...]`

Mutates `requirements.txt` in place. Resolves each pinned dep to the
latest compatible release (respecting the existing specifier — a `~=`
stays compatible-release, a `>=` floor stays a floor, etc.), writes a
backup alongside the file, runs a test install in a throwaway venv, and
rolls back if the install fails.

| Flag | Effect |
| --- | --- |
| `--no-test` | Skip the test-install step. |
| `--refresh` | Bypass the PyPI cache (equivalent to `--refresh-cache` on audit). |
| `--temp-test-env` | Use a freshly created temporary venv for the test install (default reuses a cached one under `~/.cache/piptastic/`). |

Pass package names as positional args to limit updates to those
distributions. Only `requirements.txt`-family files are mutated in v0.2;
`pyproject.toml` and `Pipfile` are not yet rewritten.

### `stats <path>`

Cross-project rollup over the same audit pipeline. Terminal output
includes: top N most-depended-upon packages, version fragmentation
(packages pinned to multiple versions across the tree), yanked pins,
unpinned projects, and tree-wide drift / pin-posture histograms.

| Flag | Effect |
| --- | --- |
| `--top N` | Top-N package list size (default 20). |
| `--json` | JSON to stdout. See [JSON schema](#json-schema). |
| `--exclude`, `--no-cache`, `--refresh-cache`, `--cache-ttl`, `--concurrency` | Same as `audit`. |

### `bootstrap <path>`

Generates a `requirements.txt` from the packages installed in a
project's venv. Output is sorted `name==X.Y.Z` lines, with venv
plumbing (`pip`, `setuptools`, `wheel`, `pkg_resources`, `distlib`,
`_distutils_hack`) and any editable self-install of the project itself
filtered out.

| Flag | Effect |
| --- | --- |
| `--venv PATH` | Explicit venv directory (relative to `<path>` or absolute). Required if `<path>` contains multiple venvs and you want to disambiguate. |
| `--force` | Overwrite an existing `requirements.txt`. The previous file is renamed to `requirements.txt.bak.<timestamp>` first. |
| `--dry-run` | Print to stdout; write nothing. |

Auto-discovery probes `.venv`, `venv`, `env`, `.env` under `<path>` for
a `pyvenv.cfg`. If none of those match, it scans the project's
top-level directories for any `pyvenv.cfg`. If zero or multiple
candidates are found and `--venv` was not given, the command fails
without writing.

## Project discovery

A directory is considered a Python project if it contains any of:

- `requirements*.txt` (including `requirements-dev.txt`, etc.)
- `pyproject.toml` with a `[project]` or `[tool.poetry]` table
- `Pipfile`

Walks are bounded by an internal skip list: `.git`, `.venv`, `venv`,
`env`, `.env`, `node_modules`, `__pycache__`, `site-packages`, `build`,
`dist`, `.tox`, `.nox`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`,
plus any directory containing a `pyvenv.cfg` (so the contents of a venv
are never treated as a project). `--exclude PATTERN` adds to this list
and accepts glob syntax matched against the directory **basename**.

Includes (`-r other.txt`, `-c constraints.txt`) inside requirements
files are followed, with cycle detection. The audit attributes each dep
to the file it was originally declared in, not the file that included
it.

## Dependency parsing

| Source | What's read |
| --- | --- |
| `requirements*.txt` family | PEP 508 specifiers; `-r` / `-c` includes followed with cycle detection. Environment markers honoured. URL / VCS / local-path requirements are surfaced as `URL` posture (no version comparison performed). |
| `pyproject.toml` (PEP 621) | `[project].dependencies` and every list under `[project.optional-dependencies]`. |
| `pyproject.toml` (Poetry) | `[tool.poetry.dependencies]` and `[tool.poetry.group.<name>.dependencies]`. Caret (`^1.2.3`) and tilde (`~1.2.3`) shorthands are expanded to PEP 440 ranges. `python` is excluded. |
| `Pipfile` | `[packages]` and `[dev-packages]`. |
| `Pipfile.lock` | Hashed pin lines from `default` and `develop` sections. |

## Drift classification

For each dep with a comparable installed/declared version, drift is
classified by which version component changed between the declared
version and the latest matching PyPI release:

| Tier | Meaning |
| --- | --- |
| `NONE` | Declared version equals the latest. |
| `BUILD` | Only the 4th+ segment moved (sometimes called "nano"). |
| `PATCH` | 3rd segment (the `Z` in `X.Y.Z`). |
| `MINOR` | 2nd segment. |
| `MAJOR` | 1st segment. |
| `EPOCH` | PEP 440 epoch (`N!X.Y.Z`) changed. Rare. |

`--fail-on-drift LEVEL` exits 1 when any dep has drift ≥ `LEVEL`.

## Pin posture

For each dep, the *specifier shape* (not the version value) determines
posture:

| Posture | Examples |
| --- | --- |
| `PINNED` | `flask==2.3.0`, `flask===2.3.0` |
| `COMPATIBLE` | `flask~=2.3.0` (PEP 440 compatible-release) |
| `RANGE` | `flask>=2.0,<3.0` |
| `FLOOR` | `flask>=2.0` (open upper bound) |
| `UNPINNED` | `flask` (no specifier at all) |
| `URL` | `flask @ git+https://...`, local paths, direct URLs |

**Pin score** is the percentage of a project's non-URL deps that are
`PINNED` or `COMPATIBLE`. A project whose deps are all `URL` reports
`n/a` instead of 0 — URL pinning depends on whether the URL pins a
ref, which the auditor can't reliably tell.

## JSON schema

Both `audit --json` and `stats --json` emit `schema_version: 1`. The
shape is intended to be stable across patch releases of piptastic;
breaking changes will bump `schema_version`. Top-level discriminator
is `kind`:

- `kind: "audit"` — emitted by `audit --json`.
- `kind: "stats"` — emitted by `stats --json`.

Diff the schema against your dashboard / CI consumer before upgrading
across a minor version bump.

## Caching

PyPI metadata is cached on disk. Default location:

- POSIX: `$XDG_CACHE_HOME/piptastic/pypi/`, falling back to
  `~/.cache/piptastic/pypi/`.
- Windows: `%LOCALAPPDATA%\piptastic\pypi\`.

Override with `PIPTASTIC_CACHE_DIR=<path>`. Default TTL is 3600s
(1h). Cache entries are per-distribution JSON blobs; safe to delete
the directory at any time.

## Logging

`-v` / `--verbose` flips the root logger to INFO. `-q` / `--quiet`
silences everything below ERROR. `--log-file PATH` mirrors records
into a file (the stderr stream is unaffected). Default is WARNING to
stderr.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Audit completed. Outdated deps in the output do **not** change the exit code by themselves — use `--fail-on-drift` for that. |
| `1` | Operational failure: path doesn't exist, no Python projects found, malformed input, PyPI totally unreachable, or `--fail-on-drift` threshold tripped. |
| `2` | `update` test-install failed; the requirements file was rolled back from its backup. |

## Not in v0.2

These are deferred. None are committed to a v0.3 release date.

- Watch/daemon mode.
- CVE / security advisory lookups (OSV.dev integration).
- Lockfile-drift detection (`Pipfile` ↔ `Pipfile.lock`, `poetry.lock`,
  `uv.lock`).
- HTML report output.
- `setup.py` / `setup.cfg` parsing.
- `update` for `pyproject.toml` and `Pipfile`.

## License

AGPL-3.0-or-later. Full text in [LICENSE](LICENSE).

Practical summary (not a substitute for reading the licence):

- Use, modify, and redistribute freely under the AGPL.
- A modified version offered as a network service must offer its
  corresponding source to the users of that service (the "SaaS
  clause" — this is what distinguishes AGPL from plain GPL).
- If those terms don't fit a commercial deployment, contact the
  maintainer about a commercial licence.

## Contributing

Issues and PRs welcome. By submitting a contribution you agree it will
be distributed under the project's licence (AGPL-3.0-or-later).
