# piptastic

A dependency auditor for Python projects. Walks a tree, finds every project
that declares dependencies, and answers two questions per pin: *how stale is
it* and *is it vulnerable*. Optionally rewrites `requirements.txt` to the
latest compatible version while honoring CVE fix-version data from
[pip-audit](https://github.com/pypa/pip-audit).

[![License: AGPL v3+](https://img.shields.io/badge/license-AGPL--3.0--or--later-blue.svg)](LICENSE)
[![Python: 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![tests](https://github.com/boredchilada/piptastic/actions/workflows/test.yml/badge.svg)](https://github.com/boredchilada/piptastic/actions/workflows/test.yml)
[![JSON schema: v3](https://img.shields.io/badge/json--schema-v3-informational.svg)](#json-schema)

Single-shot CLI. No daemon, no server, no shared state. Runs against one
project or a tree of hundreds.

## Contents

- [Install](#install)
- [Quickstart](#quickstart)
- [Design notes](#design-notes)
- [Commands](#commands)
- [Workflows](#workflows)
- [Accepting known CVEs (suppressions)](#accepting-known-cves-suppressions)
- [Reference](#reference) — discovery, parsing, drift, pin posture, JSON
  schema, caching, logging, exit codes
- [Roadmap](#roadmap)
- [Release notes](#release-notes)
- [License](#license)
- [Contributing](#contributing)
- [Acknowledgements](#acknowledgements)

## Install

```bash
pip install git+https://github.com/boredchilada/piptastic
# or, isolated install with the CLI on PATH:
pipx install git+https://github.com/boredchilada/piptastic
```

Two entry points are installed: `piptastic` and `ptc` (short alias). Without
installing, run the package directly: `python -m piptastic`.

Runtime requirements: Python 3.10+, `packaging`, `rich`, `pip-audit`. `tomli`
is pulled in on Python < 3.11. `pip-audit` is invoked as
`python -m pip_audit` under the hood, so no PATH shim is required on Windows.

## Quickstart

Audit a single project:

```bash
piptastic audit .
```

Sample output (table view, default for a single project):

```
+-----------------------------------------------------------------------------------------+
| Project       | Package    | Current  | Latest | Age  | Min safe | Drift | Pin   | Vulns
|---------------+------------+----------+--------+------+----------+-------+-------+------
| my-flask-app  | flask      | 2.0.0    | 3.1.3  | 2mo  | 3.1.0    | major | pinned|   2
| my-flask-app  | requests   | 2.31.0   | 2.34.2 | 1mo  | 2.32.4   | minor | pinned|   1
| my-flask-app  | sqlalchemy | 2.0.30   | 2.0.43 | 3mo  | -        | patch | pinned|   -
+-----------------------------------------------------------------------------------------+
```

Audit a whole tree:

```bash
piptastic audit ~/code --summary
```

```
+-----------------------------------------------------------------------------------------+
| Project        |   Py | Pin score | Major | Minor | Patch | Other | Yanked | Vulns | Deps
|----------------+------+-----------+-------+-------+-------+-------+--------+-------+-----
| my-flask-app   | 3.11 |      100% |     1 |     1 |     1 |     0 |      0 |     3 |   3
| ingestion-svc  | 3.12 |       60% |     0 |     2 |     5 |     0 |      1 |     0 |  12
| legacy-cron    | 3.10 |        0% |     8 |     3 |     1 |     1 |      2 |    27 |  14
+-----------------------------------------------------------------------------------------+

3 projects | 29 deps | 27 CVEs across 1 project(s) | 3 yanked
```

Apply CVE-aware bumps to one project's `requirements.txt`:

```bash
piptastic update ~/code/legacy-cron
# 7 bumped, 2 CVE-driven
```

Preview without writing:

```bash
piptastic update ~/code/legacy-cron --dry-run
```

## Design notes

The shape of the codebase reflects a handful of explicit choices:

- **Stdlib `urllib` for PyPI, not `requests`.** One fewer runtime dependency.
  PyPI's JSON endpoint is stable enough not to need a heavyweight client.
- **`pip-audit` via subprocess, not Python import.** The library's public
  surface is the CLI. Importing internals would couple piptastic to a moving
  target.
- **Frozen dataclasses everywhere.** `Dep`, `DepAudit`, `ProjectAudit`,
  `Vulnerability` are all immutable. Mutate by reconstruction; new fields take
  defaults so older call sites keep working. Domain logic lives in
  `analysis.py`, not on the dataclasses.
- **Per-source-file caching.** PyPI metadata cached by distribution name;
  pip-audit results cached by `(name, version)` pair. Empty-vulns results are
  cached too — clean pins don't re-invoke the subprocess on subsequent runs.
- **Graceful degradation, never silent.** PyPI miss becomes `drift=unknown`
  and surfaces in `pypi_unreachable`. pip-audit miss surfaces in
  `vuln_unreachable` (not silently reported as clean). Per-project failures
  don't kill the tree scan.
- **Strict exit-code contract.** `0` clean, `1` operational error, `2`
  rewrite rolled back, `3` policy gate tripped. CI can distinguish "you
  misconfigured me" from "the gate worked."
- **Single output schema with versioning.** JSON output declares
  `schema_version`. Additive changes don't bump it; field renames/removals
  do. Documented under [schema version history](#schema-version-history).

## Commands

### `audit <path>`

Read-only. Discovers Python projects under `<path>` and reports each
dependency's pin posture, drift against PyPI, and known CVEs.

| Flag | Effect |
| --- | --- |
| `--table` | Flat table view (default for a single project). |
| `--summary` | One row per project: drift histogram (Major / Minor / Patch, plus an `Other` column folding in build + epoch drift) + pin score + CVE rollup. |
| `--json` | Machine-readable JSON to stdout. See [JSON schema](#json-schema). |
| `--sarif` | SARIF 2.1.0 output for GitHub Code Scanning. Mutually exclusive with `--json`. |
| `--include-prereleases` | Consider pre-release versions as candidates for "latest". |
| `--exclude PATTERN` | Glob matched against directory basename. Repeatable. Layered on top of the built-in skip list. |
| `--no-cache` | Skip the on-disk PyPI cache for this run. |
| `--refresh-cache` | Force a fresh fetch and rewrite the cache. |
| `--cache-ttl SECONDS` | Override the default TTL (3600). |
| `--concurrency N` | PyPI fetch thread-pool size. |
| `--no-vulns` | Skip the pip-audit CVE pass entirely. Mutually exclusive with `--fail-on-vuln`. |
| `--vulnerable-only` | Show only deps with non-suppressed CVEs. Projects with zero matches are dropped. |
| `--drift-min {build,patch,minor,major,epoch}` | Show only deps with drift ≥ this level. |
| `--direct-only` | Hide transitive lockfile deps from the output. Display-only — gates still evaluate the full resolved graph. |
| `--fail-on-drift {build,patch,minor,major,epoch}` | Exit `3` when any dep has drift ≥ this level. |
| `--fail-on-age DAYS` | Exit `3` when any dep's latest release is older than `DAYS`. Deps with an unknown release date (PyPI miss) never trip it. |
| `--fail-on-vuln any\|N` | Exit `3` when any dep has a non-suppressed CVE (`any`) or when tree-wide CVE count ≥ N. |
| `--strict-vuln-gate` | When `--fail-on-vuln` is set, also trip on `vuln_unreachable` packages. Default is fail-open with a warning. |

Default view: tree (project → file → dep) for multi-project paths, table for
a single project. When more than one project is shown, terminal output ends
with a one-line tally — project and dependency counts, plus CVE and yanked
totals when non-zero.

### `update <path> [packages ...]`

Mutates `requirements.txt` in place. Resolves each pinned dep to the latest
compatible release (a `~=` stays compatible-release, a `>=` floor stays a
floor), writes a backup, runs a test install in a throwaway venv, and rolls
back if the install fails.

By default, queries pip-audit for each `==` pin and lifts the bump target to
the minimum safe version if the current pin is covered by an open advisory.
CVE-driven bumps are annotated in the output:

```
flask: 2.0.0 -> 2.2.5  (CVE floor: PYSEC-2023-62)
```

| Flag | Effect |
| --- | --- |
| `--dry-run` | Compute would-be changes; do not write files, create backups, or run the test install. CVE-floor lookups still happen so the preview is accurate. |
| `--no-test` | Skip the test-install step. |
| `--refresh` | Bypass the PyPI and vuln caches (equivalent to `--refresh-cache` on audit). |
| `--temp-test-env` | Put the throwaway test-install venv under the OS temp dir instead of `.piptastic_test_<ts>/` next to the project. |
| `--no-apply-cve-floor` | Disable the CVE-aware floor; pick latest non-yanked release as usual. |

Positional `packages` limit updates to those distributions. Only
`requirements*.txt`-family files are mutated; `pyproject.toml` and `Pipfile`
rewriting is not yet implemented.

### `stats <path>`

Cross-project rollup over the same audit pipeline. Terminal output shows the
most-depended-upon packages, version fragmentation across the tree, yanked
pins, unpinned projects, and tree-wide drift / pin-posture histograms.

| Flag | Effect |
| --- | --- |
| `--top N` | Top-N package list size (default 20). |
| `--json` | Machine-readable JSON to stdout. |
| `--exclude`, `--no-cache`, `--refresh-cache`, `--cache-ttl`, `--concurrency` | Same as `audit`. |

### `bootstrap <path>`

Generates a `requirements.txt` from packages installed in a project's venv.
Output is sorted `name==X.Y.Z` lines, with venv plumbing (`pip`, `setuptools`,
`wheel`, `pkg_resources`, `distlib`, `_distutils_hack`) and any editable
self-install of the project filtered out.

| Flag | Effect |
| --- | --- |
| `--venv PATH` | Explicit venv directory. Required when multiple venvs are present and you want to disambiguate. |
| `--force` | Overwrite an existing `requirements.txt`. The previous file is copied to `.requirements_backups/requirements_<timestamp>_<digest>.txt` first. |
| `--dry-run` | Print to stdout; write nothing. |

Auto-discovery probes `.venv`, `venv`, `env`, `.env` for a `pyvenv.cfg`, then
falls back to scanning the project's top-level subdirectories. If zero or
multiple candidates are found and `--venv` was not given, the command exits
without writing.

## Workflows

### Find what's stale

```bash
piptastic audit .                  # full table for one project
piptastic audit ~/code --summary   # one row per project across a tree
```

The drift column tells you which segment of `X.Y.Z` moved. The `Age` column
surfaces packages that haven't shipped in years even when drift is `none`.

### Bump pins to the latest compatible version

```bash
piptastic update .
piptastic update . flask requests          # limit to specific packages
piptastic update . --no-test               # skip the test install
piptastic update . --dry-run               # preview without writing
piptastic update . --refresh               # bypass caches for a fresh fetch
```

For each `==` pin, picks the latest non-yanked stable release that the
existing specifier permits, then bumps further if pip-audit reports the
chosen version is still in a vulnerable range.

### Find projects with known CVEs

```bash
piptastic audit ~/code --summary --vulnerable-only
```

The summary's Vulns column rolls up advisories across each project. Drill
into one:

```bash
piptastic audit ~/code/legacy-cron --table --vulnerable-only
```

The Min safe column gives the lowest version that resolves every advisory
affecting the current pin. To apply:

```bash
piptastic update ~/code/legacy-cron
```

The CVE floor is on by default; pass `--no-apply-cve-floor` to disable.

For pipeline consumption, the JSON output includes the full
`vulnerabilities[]` array per dep:

```bash
piptastic audit ~/code --json | jq '
  .projects[]
  | {name, vuln_count,
     vulnerable_deps: [.deps[] | select(.vulnerabilities | length > 0)
       | {name, current, min_safe_version, vulns: [.vulnerabilities[].id]}]}'
```

### Block stale or vulnerable PRs in CI

```bash
piptastic audit . --fail-on-drift minor --fail-on-vuln any
```

Exits `3` if any dep has drift at or above `minor`, or any dep has a
non-suppressed CVE. Exit `1` is reserved for operational errors; exit `0`
means clean.

GitHub Actions:

```yaml
- name: Audit dependencies
  run: |
    pip install git+https://github.com/boredchilada/piptastic
    piptastic audit . --fail-on-drift minor --fail-on-vuln any
```

Or upload SARIF so findings render in the Security tab:

```yaml
- name: Audit dependencies (SARIF)
  run: |
    pip install git+https://github.com/boredchilada/piptastic
    piptastic audit . --sarif > piptastic.sarif
- uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: piptastic.sarif
```

### Recover a missing requirements.txt from a venv

```bash
piptastic bootstrap . --dry-run    # preview
piptastic bootstrap .              # write requirements.txt
piptastic bootstrap . --force      # overwrite (existing file is backed up)
piptastic bootstrap . --venv .venv # disambiguate when multiple venvs exist
```

### Survey dep health across a whole code folder

```bash
piptastic stats ~/code
piptastic stats ~/code --json > stats.json
```

Surfaces the most-depended-upon packages, version-fragmented packages (same
name pinned at different versions across projects), yanked pins still
shipping, and tree-wide histograms.

### Evaluate a third-party project before depending on it

```bash
piptastic audit /path/to/their/repo --summary
```

Pin score, drift counters, vuln count, and the latest-release age columns
give a fast read on maintenance posture.

## Accepting known CVEs (suppressions)

When an advisory affects you in name only — the vulnerable code path isn't
exercised, a mitigation is in place at a higher layer, the fix isn't
available yet — add a suppression rule to your project's `pyproject.toml`:

```toml
[tool.piptastic]
[[tool.piptastic.suppressions]]
package = "flask"
cve = "PYSEC-2023-62"          # also matches the advisory's aliases
reason = "we do not use sessions"
expires = "2026-12-31"          # required; past-expiry rules are ignored
```

All four fields are required. Past-expiry rules are ignored and logged so
they don't sit forever. A rule expiring within the next 30 days logs a
heads-up warning while it's still active, so an accepted CVE doesn't silently
re-activate (and trip `--fail-on-vuln`) the day it lapses. Each rule matches
against the canonical advisory id
or any alias pip-audit reports (GHSA / CVE / PYSEC). `package = "*"`
suppresses the CVE across every package in the project.

Projects without a `pyproject.toml` can use a sibling `.piptastic.toml` with
the rules at the root:

```toml
[[suppressions]]
package = "requests"
cve = "CVE-2024-1234"
reason = "patched at the proxy"
expires = "2099-01-01"
```

Effects:

- `vuln_count` and `--fail-on-vuln` count non-suppressed advisories only.
- `min_safe_version` is computed from non-suppressed advisories only; `update`
  will not lift the pin over an accepted CVE.
- The full advisory is still emitted in JSON (`suppressed: true` plus a
  `suppression` block) and SARIF (`suppressions: [{kind: "external"}]`), so
  external auditors can see it.

## Reference

### Vulnerability lookups

Every `audit` run queries pip-audit alongside PyPI metadata. pip-audit is
invoked as `python -m pip_audit -r <tempfile> --format json --no-deps
--disable-pip` against the `(name, version)` pairs piptastic already
resolved, so there is no separate dependency-resolution step.

Per-dep results:

- `vulnerabilities` — list of advisories: GHSA / PYSEC / CVE ids, aliases,
  fix versions, upstream description.
- `min_safe_version` — the maximum of the per-advisory minimum fix-versions
  newer than the installed pin. Bumping to this version resolves every known
  advisory. `null` when no advisory applies, or when no known fix is newer
  than the current pin.

Per-project rollup:

- `vuln_count` — count of non-suppressed advisories across all deps,
  deduplicated by advisory id (pip-audit may report the same advisory once per
  affected version range; those are collapsed so the count isn't inflated).
- `vuln_unreachable` — packages where pip-audit failed to return a status.
  Surfaced as "unknown," never silently treated as clean.
- `suppressed_count` — accepted-risk advisories from
  `[tool.piptastic.suppressions]`.

### Project discovery

A directory is a Python project if it contains any of:

- `requirements*.txt` (including `requirements-dev.txt`, etc.)
- `pyproject.toml` with a `[project]` or `[tool.poetry]` table
- `Pipfile`
- `uv.lock`, `poetry.lock`, or `pdm.lock`

Walks skip `.git`, `.venv`, `venv`, `env`, `.env`, `node_modules`,
`__pycache__`, `site-packages`, `build`, `dist`, `.tox`, `.nox`,
`.mypy_cache`, `.pytest_cache`, `.ruff_cache`, and any directory containing a
`pyvenv.cfg`. `--exclude PATTERN` adds to this list and accepts glob syntax
matched against the directory basename.

`-r other.txt` / `-c constraints.txt` includes inside requirements files are
followed with cycle detection. Each dep is attributed to the file it was
originally declared in, not the file that included it.

### Dependency parsing

| Source | What's read |
| --- | --- |
| `requirements*.txt` family | PEP 508 specifiers; `-r` / `-c` includes with cycle detection; environment markers honored; URL / VCS / local-path requirements surfaced as `URL` posture. A bare `git+https://…` line without `#egg=name` is named from the repo path so it's still surfaced. |
| `pyproject.toml` (PEP 621) | `[project].dependencies` and every list under `[project.optional-dependencies]`. |
| `pyproject.toml` (Poetry) | `[tool.poetry.dependencies]` and `[tool.poetry.group.<name>.dependencies]`. Caret (`^1.2.3`) and tilde (`~1.2.3`) shorthands expanded to PEP 440 ranges. `python` is excluded. Multiple-constraints dependencies (a list of `{version, markers}` tables for platform-specific pins) become one dep per entry, each with its own specifier and marker. |
| `Pipfile` | `[packages]` and `[dev-packages]`. |
| `Pipfile.lock` | Hashed pin lines from `default` and `develop` sections. |
| `uv.lock` / `poetry.lock` / `pdm.lock` | The full resolved graph — every `[[package]]` entry as an exact pin (direct **and** transitive). When a lockfile is present it supersedes its manifest (the matching `pyproject.toml` source is skipped to avoid double-counting); the manifest is still read to tag which entries are direct. Transitive entries are marked in the output and `direct: false` in JSON. The project's own editable/virtual entry is skipped. |

`requirements*.txt` files are decoded as UTF-8 (a UTF-8 BOM is tolerated). A
UTF-16 or UTF-32 byte-order mark is detected and decoded accordingly, so a
file written by PowerShell's `pip freeze > requirements.txt` (UTF-16-LE on
Windows) parses correctly rather than being silently dropped.

### Drift classification

For each dep with a comparable declared/latest version, drift is classified
by which segment changed between the declared version and the latest matching
PyPI release:

| Tier | Meaning |
| --- | --- |
| `NONE` | Declared equals latest. |
| `BUILD` | Only the 4th+ segment moved. |
| `PATCH` | 3rd segment (`Z` in `X.Y.Z`). |
| `MINOR` | 2nd segment. |
| `MAJOR` | 1st segment. |
| `EPOCH` | PEP 440 epoch (`N!X.Y.Z`) changed. Rare. |

### Pin posture

The specifier *shape* (not the version value) determines posture:

| Posture | Examples |
| --- | --- |
| `PINNED` | `flask==2.3.0`, `flask===2.3.0` |
| `COMPATIBLE` | `flask~=2.3.0` (PEP 440 compatible-release) |
| `RANGE` | `flask>=2.0,<3.0` |
| `FLOOR` | `flask>=2.0` (open upper bound) |
| `UNPINNED` | `flask` (no specifier) |
| `URL` | `flask @ git+https://...`, local paths, direct URLs |

**Pin score** is the percentage of a project's non-URL deps that are `PINNED`
or `COMPATIBLE`. A project whose deps are all `URL` reports `n/a` — URL
pinning depends on whether the URL pins a ref, which the auditor can't tell
reliably.

### JSON schema

`audit --json` and `stats --json` both emit `schema_version: 3`. Breaking
changes bump the version; additive changes don't. Top-level discriminator is
`kind`: `"audit"` for `audit --json`, `"stats"` for `stats --json`.

Per-dep fields (audit):

```json
{
  "name": "flask",
  "current": "2.0.0",
  "latest": "3.0.4",
  "drift": "major",
  "pin_status": "pinned",
  "yanked": false,
  "vulnerabilities": [
    {
      "id": "PYSEC-2023-62",
      "aliases": ["CVE-2023-30861", "GHSA-m2qf-hxjv-5gpq"],
      "fix_versions": ["2.2.5", "2.3.2"],
      "description": "Flask session cookie issue ...",
      "suppressed": false,
      "suppression": null
    }
  ],
  "min_safe_version": "2.2.5",
  "latest_release_date": "2024-09-10T00:00:00+00:00",
  "latest_release_age_days": 261,
  "warnings": ["1 vulnerability(ies): PYSEC-2023-62"]
}
```

Per-project fields (audit): `pinning_score`, `drift_summary`, `yanked_count`,
`pypi_unreachable`, `vuln_count`, `vuln_unreachable`, `suppressed_count`.

#### Schema version history

| Version | Released | Change |
| --- | --- | --- |
| `1` | v0.2.0 | Initial public schema. |
| `2` | v0.3.0 | Adds `vulnerabilities[]` and `min_safe_version` per dep; `vuln_count` and `vuln_unreachable` per project. |
| `3` | v0.4.0 | Adds `latest_release_date` and `latest_release_age_days` per dep; `suppressed` and optional `suppression` block per vuln; `suppressed_count` per project. All additive. |

Additive-since-v3 (no version bump): per-dep `direct` boolean (false for
transitive lockfile entries), added in v0.6.0.

### Caching

PyPI metadata and pip-audit results are cached on disk in separate
directories.

| Source | POSIX default | Windows default |
| --- | --- | --- |
| PyPI metadata | `$XDG_CACHE_HOME/piptastic/pypi/` → `~/.cache/piptastic/pypi/` | `%USERPROFILE%\.cache\piptastic\pypi\` |
| pip-audit results | `$XDG_CACHE_HOME/piptastic/vulns/` → `~/.cache/piptastic/vulns/` | `%USERPROFILE%\.cache\piptastic\vulns\` |

Override the parent with `PIPTASTIC_CACHE_DIR=<path>`. Default TTL is 3600s
(1h) for both. PyPI entries are per-distribution JSON; vuln entries are
per-`(name, version)` JSON, including the empty result for clean pins. Safe
to delete either directory at any time.

### Logging

`-v` / `--verbose` sets the root logger to INFO. `-q` / `--quiet` silences
everything below ERROR. `--log-file PATH` mirrors records into a file while
the stderr stream stays as-is. Default is WARNING to stderr.

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Audit completed; no gate tripped. Outdated or vulnerable deps do not change the exit code by themselves. |
| `1` | Operational failure: path doesn't exist, no Python projects found, malformed input, internal crash. PyPI / pip-audit unreachable does NOT bump this — both degrade gracefully. |
| `2` | `update` test-install failed; the requirements file was rolled back from its backup. |
| `3` | Policy gate tripped: `--fail-on-drift`, `--fail-on-vuln`, and/or `--fail-on-age`. The audit itself was successful. |

CI consumers that previously checked `==1` for a gate trip should switch to
`==3` (v0.4 contract change).

## Roadmap

Deferred to a future release. Order is rough priority, not a release plan:

- Lockfile-drift detection (`Pipfile` ↔ `Pipfile.lock`, `poetry.lock`,
  `uv.lock`).
- `update` for `pyproject.toml` and `Pipfile`.
- `setup.py` / `setup.cfg` parsing.
- HTML report output.
- Watch / daemon mode.
- Publishing to PyPI (install is from source / git for now).

## Release notes

[CHANGELOG.md](CHANGELOG.md) tracks every release. Schema bumps and breaking
flag changes are called out under "Breaking" headings.

## License

AGPL-3.0-or-later. Full text in [LICENSE](LICENSE).

The AGPL's network-service clause applies: if you offer a modified version
of piptastic as a network service, you must offer that modified version's
source to its users. For commercial deployments where the AGPL doesn't fit,
contact the maintainer about a commercial license.

## Contributing

Issues and PRs welcome. By submitting a contribution you agree it will be
distributed under the project's license (AGPL-3.0-or-later).

Engineering conventions (frozen dataclasses, I/O boundaries, JSON-schema
versioning rules, cross-platform constraints) are documented in
[CLAUDE.md](CLAUDE.md). Read it before opening a non-trivial PR; the same
conventions apply whether the contributor is human or an AI agent.

## Acknowledgements

piptastic builds on a handful of well-maintained libraries:

- [packaging](https://github.com/pypa/packaging) (PyPA) — PEP 440 version,
  specifier, and marker parsing. The drift and pin-posture model is built on
  its `Version` and `SpecifierSet`.
- [rich](https://github.com/Textualize/rich) — the terminal tables, trees,
  and progress bars.
- [pip-audit](https://github.com/pypa/pip-audit) (PyPA) — the CVE scan.
  piptastic invokes it as a subprocess rather than reimplementing advisory
  matching.
- [tomli](https://github.com/hukkin/tomli) — TOML parsing on Python 3.10
  (3.11+ uses the standard-library `tomllib`).
- [hatchling](https://github.com/pypa/hatch) — the build backend.
- [pytest](https://github.com/pytest-dev/pytest) — the test suite.

Vulnerability advisories surfaced in the audit come from pip-audit's data
sources — primarily the
[PyPI Advisory Database](https://github.com/pypa/advisory-database) and
[OSV](https://osv.dev/) — and package metadata comes from the
[PyPI JSON API](https://warehouse.pypa.io/api-reference/json.html). Thanks to
the maintainers of all of the above.
