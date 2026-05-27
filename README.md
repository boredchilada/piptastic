# piptastic

A read-only auditor for Python dependency files. Walks a directory tree,
finds every project that declares dependencies (`requirements*.txt`,
`pyproject.toml`, `Pipfile` / `Pipfile.lock`), resolves each declared
version against PyPI, and reports:

- how stale and how strictly pinned the declarations are
- which pinned versions have **known CVEs**, with the minimum safe
  version to bump to (via [pip-audit](https://github.com/pypa/pip-audit))

Also writes (when explicitly asked): `update` rewrites `requirements.txt`
to the latest compatible pinned versions and, by default, lifts past any
vulnerable ranges using pip-audit's fix-version data. `bootstrap`
produces a `requirements.txt` from an existing venv.

## Install

```bash
pip install .
# or, isolated install with the CLI on PATH:
pipx install .
```

Two entry points are installed: `piptastic` and `ptc` (short alias).
Both invoke the same `main()`. Without installing, run the package
directly: `python -m piptastic` (or `py -3.10 -m piptastic` on Windows).

Runtime requirements: Python 3.10+, `packaging`, `rich`, `pip-audit`.
`tomli` is pulled in on Python < 3.11. `pip-audit` is invoked as
`python -m pip_audit` under the hood, so no shim or PATH entry is
required.

## Quickstart

Point it at a single project:

```bash
cd ~/code/my-flask-app
piptastic audit .
```

You'll see a table with one row per declared dependency:

```
+------------------------------------------------------------------------------+
| Project       | File           | Group   | Package    | Current  | Latest |
|---------------+----------------+---------+------------+----------+--------|
| my-flask-app  | requirements.. | default | flask      | (2.3.0)  | 3.0.4  |
| my-flask-app  | requirements.. | default | requests   | (2.31.0) | 2.32.5 |
| my-flask-app  | requirements.. | default | sqlalchemy | (2.0.30) | 2.0.43 |
+------------------------------------------------------------------------------+
   Min safe | Drift | Pin    | Vulns | Notes
   ---------+-------+--------+-------+----------------------------
   3.1.0    | major | pinned |   2   | 2 vulnerability(ies): ...
   2.32.4   | minor | pinned |   1   | 1 vulnerability(ies): ...
   -        | patch | pinned |   0   |
```

How to read each column:

- **Current** — the version your project declares (parens mean it was
  parsed from a specifier like `flask==2.3.0`, not a lockfile).
- **Latest** — newest non-prerelease on PyPI. Add `--include-prereleases`
  to include alphas/betas/rcs.
- **Min safe** — the lowest fix-version (per pip-audit) that resolves
  every advisory affecting the pinned version. `-` when no CVEs apply.
- **Drift** — how far behind: `none`, `build`, `patch`, `minor`,
  `major`, or `epoch`. Colour-coded in your terminal.
- **Pin** — the *shape* of the specifier: `pinned` (`==`),
  `compatible` (`~=`), `range` (`>=,<`), `floor` (`>=`), `unpinned`
  (no specifier), or `url`. See [Pin posture](#pin-posture).
- **Vulns** — count of known CVEs / advisories affecting the pinned
  version. Bold red when non-zero.
- **Notes** — yanked-release warnings, PyPI fetch errors, and an
  inline CVE summary (`N vulnerability(ies): GHSA-..., CVE-...`).

Point it at a whole tree of projects:

```bash
piptastic audit ~/code
```

Default view becomes a tree (project → file → dep). For a one-line-per-project
overview instead:

```bash
piptastic audit ~/code --summary
```

```
+----------------------------------------------------------------------------------+
| Project        |   Py | Pin score | Major | Minor | Patch | Yanked | Vulns | Deps
|----------------+------+-----------+-------+-------+-------+--------+-------+-----
| my-flask-app   | 3.11 |      100% |     1 |     1 |     1 |      0 |     3 |   3
| ingestion-svc  | 3.12 |       60% |     0 |     2 |     5 |      1 |     0 |  12
| legacy-cron    | 3.10 |        0% |     8 |     3 |     1 |      2 |    27 |  14
+----------------------------------------------------------------------------------+
```

**Pin score** is the percent of non-URL deps that are `pinned` or
`compatible`. Higher = stricter pinning. **Vulns** is the rollup of
known CVEs across all of a project's deps. Use the drift columns to
spot which projects are dragging and the Vulns column to find security
debt.

That's the core loop: `audit` to look, `update` to act on a single
project, `stats` to roll up across many, `bootstrap` to recover from a
lost `requirements.txt`. The Workflows section below has full recipes.

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

By default, also queries pip-audit for each `==` pin and lifts the bump
target up to the **minimum safe version** if the current pin is
covered by an open advisory. Bumps driven by a CVE are annotated in
the output:

```
flask: 2.0.0 -> 2.2.5  (CVE floor: PYSEC-2023-62)
```

| Flag | Effect |
| --- | --- |
| `--no-test` | Skip the test-install step. |
| `--refresh` | Bypass the PyPI and vuln caches (equivalent to `--refresh-cache` on audit). |
| `--temp-test-env` | Use a freshly created temporary venv for the test install (default reuses a cached one under `~/.cache/piptastic/`). |
| `--no-apply-cve-floor` | Don't consult pip-audit during update; pick latest non-yanked as usual. The CVE floor is **on by default**. |

Pass package names as positional args to limit updates to those
distributions. Only `requirements.txt`-family files are mutated in
v0.3; `pyproject.toml` and `Pipfile` are not yet rewritten.

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

## Workflows

### "Is anything in this project stale?"

```bash
piptastic audit .
```

Read the **Drift** column. `none` everywhere = you're current. Anything
`minor` or `major` is worth investigating — it usually means you've
pinned to an older release and missed feature work or breaking
changes upstream.

If you just want a yes/no:

```bash
piptastic audit . --summary
```

The `Major` / `Minor` / `Patch` counters tell you the shape of the
debt at a glance.

### "Bump my project's pins to the latest compatible versions"

```bash
piptastic update .
```

For each pinned dep in `requirements.txt`, this resolves the latest
release compatible with the existing specifier, rewrites the file,
keeps a `.bak` next to it, then runs a test install in a throwaway
venv. If the test install fails, the file is rolled back.

Common variations:

```bash
# Only update specific packages
piptastic update . flask requests

# Skip the test install (faster, but no safety net)
piptastic update . --no-test

# Force a fresh PyPI fetch (don't trust the local cache)
piptastic update . --refresh
```

`update` only touches `requirements*.txt`-family files in v0.2.
`pyproject.toml` and `Pipfile` are not rewritten yet.

### "Which of my projects have known CVEs?"

```bash
piptastic audit ~/code --summary
```

The **Vulns** column on the summary rolls up every advisory pip-audit
knows about for the pinned versions in each project. Drill into a
specific project to see which packages are affected and what to bump
to:

```bash
piptastic audit ~/code/legacy-cron --table
```

Look at the **Min safe** column — that's the lowest version that
resolves every advisory affecting the current pin. To actually apply
those bumps:

```bash
piptastic update ~/code/legacy-cron
```

The CVE floor is on by default. Pass `--no-apply-cve-floor` if you
want pure "latest non-yanked" behavior instead.

For pipeline consumption, the JSON output includes the full
`vulnerabilities[]` array per dep:

```bash
piptastic audit ~/code --json | \
  jq '.projects[] | {name, vuln_count, vulnerable_deps: [.deps[] | select(.vulnerabilities | length > 0) | {name, current, min_safe_version, vulns: [.vulnerabilities[].id]}]}'
```

### "Block stale-dep PRs in CI"

```bash
piptastic audit . --fail-on-drift minor
```

Exits 1 if any dep has drift at or above `minor`. Drop it into a CI
step; the build fails when someone's pinned to something that's
fallen too far behind. Tighter gate: `--fail-on-drift patch`. Looser:
`--fail-on-drift major`.

The output is the normal table — your CI logs show exactly which
packages tripped the gate.

Example GitHub Actions step:

```yaml
- name: Check dependency staleness
  run: |
    pip install piptastic
    piptastic audit . --fail-on-drift minor
```

### "I have a venv but no requirements.txt"

You inherited a project, or you blew away `requirements.txt` at some
point and kept developing inside the venv. Reconstruct from what's
actually installed:

```bash
# See what would be written
piptastic bootstrap . --dry-run

# Write it
piptastic bootstrap .
```

Output is sorted `name==X.Y.Z` lines. Plumbing (`pip`, `setuptools`,
`wheel`, etc.) and editable installs of the project itself are
filtered out. If the project has multiple venvs (`.venv`, `venv`,
`env`...), pass `--venv .venv` to disambiguate.

To overwrite an existing `requirements.txt` (a backup is made first):

```bash
piptastic bootstrap . --force
```

### "What's the dep health across my whole code folder?"

```bash
piptastic stats ~/code
```

Cross-project rollup. Shows the most-depended-upon packages, version
fragmentation (the same package pinned to different versions across
your projects — a refactor smell), yanked pins that still ship, and
tree-wide drift / pin-posture histograms.

Useful for finding the "we should standardise on one version of X"
problem before it bites you in production.

For dashboards or further processing:

```bash
piptastic stats ~/code --json > stats.json
```

The schema is stable (`schema_version=2` as of v0.3) — safe to parse
from scripts.

### "Audit a project before installing it"

You're evaluating a dependency or contractor's codebase. Don't trust
their README; look at their actual pinning hygiene:

```bash
piptastic audit /path/to/their/repo --summary
```

A project with 0% pin score and 30 major-drift deps tells you what
you need to know about their maintenance posture.

### "Plug the output into something else"

All views support `--json`. The schema is documented under
[JSON schema](#json-schema). Both `audit --json` and `stats --json`
write to stdout, so redirect with `> file.json` or pipe directly:

```bash
piptastic audit ~/code --json | jq '.projects[] | select(.pin_score < 50)'
```

## Vulnerability lookups

Every `audit` run queries [pip-audit](https://github.com/pypa/pip-audit)
in addition to PyPI metadata. pip-audit is invoked as `python -m
pip_audit -r <tempfile> --format json --no-deps --disable-pip` against
the resolved `(name, version)` pairs piptastic already knows about, so
there is no separate dependency-resolution step and no network round
trip per package — one subprocess call per audit (or two, if some
packages have multiple pinned versions across files within the tree).

Results attach to each dep:

- `vulnerabilities` — list of advisories from pip-audit, including
  GHSA / PYSEC / CVE identifiers, aliases, fix versions, and the
  upstream description.
- `min_safe_version` — the **maximum** of the per-advisory minimum
  fix-versions newer than the installed pin. Bumping to this version
  resolves every known advisory. `None` when there are no advisories
  or when no known fix is newer than the current pin.

Per-project rollup on `ProjectAudit`:

- `vuln_count` — total advisories across all deps.
- `vuln_unreachable` — packages where pip-audit failed to return a
  status. These are surfaced as "unknown" rather than silently treated
  as clean. If pip-audit is not installed or not callable as `python
  -m pip_audit`, every dep ends up here.

The vuln cache lives at `~/.cache/piptastic/vulns/` (overridable via
`PIPTASTIC_CACHE_DIR`). Keys are `sha1(name|version)`; the empty-vulns
result for a clean package is cached too, so subsequent audits don't
re-invoke pip-audit for known-good pins.

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

Both `audit --json` and `stats --json` emit `schema_version: 2`. The
shape is intended to be stable across patch releases of piptastic;
breaking changes will bump `schema_version`. Top-level discriminator
is `kind`:

- `kind: "audit"` — emitted by `audit --json`.
- `kind: "stats"` — emitted by `stats --json`.

### Per-dep fields (audit)

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
      "description": "Flask session cookie issue ..."
    }
  ],
  "min_safe_version": "2.2.5",
  "warnings": ["1 vulnerability(ies): PYSEC-2023-62"]
}
```

### Per-project fields (audit)

`pinning_score`, `drift_summary`, `yanked_count`, `pypi_unreachable`,
**`vuln_count`**, **`vuln_unreachable`**.

### Schema version history

| Version | Change |
| --- | --- |
| `1` | Initial public schema (v0.2). |
| `2` | Adds `vulnerabilities[]` and `min_safe_version` per dep; adds `vuln_count` and `vuln_unreachable` per project. |

Diff the schema against your dashboard / CI consumer before upgrading
across a minor version bump.

## Caching

PyPI metadata and pip-audit results are cached on disk in separate
directories.

| Source | POSIX default | Windows default |
| --- | --- | --- |
| PyPI metadata | `$XDG_CACHE_HOME/piptastic/pypi/` → `~/.cache/piptastic/pypi/` | `%USERPROFILE%\.cache\piptastic\pypi\` |
| pip-audit results | `$XDG_CACHE_HOME/piptastic/vulns/` → `~/.cache/piptastic/vulns/` | `%USERPROFILE%\.cache\piptastic\vulns\` |

Override the parent with `PIPTASTIC_CACHE_DIR=<path>`. Default TTL is
3600s (1h) for both. PyPI entries are per-distribution JSON; vuln
entries are per-`(name, version)` JSON (including the empty result for
clean pins). Safe to delete either directory at any time.

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

## Not in v0.3

These are deferred. None are committed to a future release date.

- Watch/daemon mode.
- Lockfile-drift detection (`Pipfile` ↔ `Pipfile.lock`, `poetry.lock`,
  `uv.lock`).
- HTML report output.
- `setup.py` / `setup.cfg` parsing.
- `update` for `pyproject.toml` and `Pipfile`.
- `--fail-on-vuln` CI gate (currently `--fail-on-drift` only).

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
