<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# CLAUDE.technical.md — developer reference

Deep technical companion to [CLAUDE.md](CLAUDE.md). `CLAUDE.md` is the
contributor contract (conventions, do/don't); this file explains *how the
thing actually works* so you can navigate and extend it without spelunking.
Read `CLAUDE.md` first for the rules, then this for the map.

Everything here describes the codebase only — no machine- or deployment-
specific setup. Audience: a developer (human or agent) opening the repo cold.

---

## 1. What runs when you type `piptastic audit <path>`

```
cli.main
  └─ _cmd_audit
       ├─ discover_one(path)              # single project?  (discovery.py)
       │   └─ else discover_tree(path)    # walk a tree, prune venvs/junk
       ├─ PyPIClient / VulnClient         # built once, shared across projects
       └─ for each Project:
            audit_project(project, pypi_client, vuln_client, current_python)   # analysis.py
              ├─ _collect_deps(project)            # parsing.py → list[Dep]
              ├─ pypi_client.fetch_many(names)     # PyPI metadata (urllib + cache)
              ├─ _pick_latest / classify_drift     # per dep
              ├─ classify_pin_status               # per dep
              ├─ vuln_client.fetch_for(pairs)      # pip-audit subprocess + cache
              ├─ suppressions.load_suppressions    # accepted-risk filter
              └─ → ProjectAudit (frozen DepAudits + per-project rollups)
       └─ render_* (terminal tree/table/summary | json | sarif)   # render/
       └─ gate evaluation → exit code
```

The pipeline is **read-only**. `update` and `bootstrap` are the only write
paths and live in their own modules.

Key property: **one bad project never kills the scan.** `_cmd_audit` catches
broadly per project and logs a warning; network/subprocess misses degrade to
`UNKNOWN` / `*_unreachable` rather than raising.

---

## 2. Module map (responsibilities + key entry points)

| Module | Owns | Entry points |
| --- | --- | --- |
| `cli.py` | argparse wiring, dispatch, gates, exit codes, filters, progress | `main`, `build_parser`, `_cmd_*`, `_exceeds_threshold`, `_exceeds_age_threshold`, `_vuln_gate_tripped`, `_filter_audits` |
| `discovery.py` | walk tree, find projects, prune venvs/junk, detect python version | `discover_one`, `discover_tree` |
| `parsing.py` | parse every dep-file format → `list[Dep]` | `parse_source`, `_collect_deps` (via analysis) |
| `models.py` | frozen dataclasses + enums; **no logic** | `Dep`, `DepAudit`, `ProjectAudit`, `SemverDrift`, `PinStatus`, `Vulnerability`, … |
| `pypi.py` | PyPI JSON metadata, urllib + on-disk TTL cache + threadpool | `PyPIClient.fetch_one/fetch_many`, `_parse_pypi_payload` |
| `vulns.py` | pip-audit subprocess, per-`(name,version)` cache, advisory dedup | `VulnClient.fetch_for`, `_parse_pip_audit_payload`, `compute_min_safe_version`, `_dedupe_vulns` |
| `suppressions.py` | `[tool.piptastic.suppressions]` loader + matcher | `load_suppressions`, `find_rule`, `SuppressionRule` |
| `analysis.py` | the core read pipeline — turns a Project into a ProjectAudit | `audit_project`, `classify_drift`, `classify_pin_status`, `_pick_latest`, `_upload_time_for`, `_pinning_score` |
| `update.py` | rewrite `requirements*.txt` with backup + test install + rollback | `update_project` |
| `bootstrap.py` | freeze a venv to `requirements.txt` | `find_venv`, `freeze_venv`, `find_site_packages`, `is_plumbing` |
| `stats.py` | cross-project rollup | `compute_stats` |
| `render/terminal.py` | rich tree/table/summary + multi-project footer | `render_terminal`, `render_stats_terminal` |
| `render/json_out.py` | stable `schema_version` JSON | `render_json`, `render_stats_json`, `SCHEMA_VERSION` |
| `render/sarif.py` | SARIF 2.1.0 for GitHub Code Scanning | `render_sarif` |
| `logging.py` | logger factory (stderr + optional file mirror) | `get_logger`, `configure_logging` |

Tests mirror sources 1:1 under `tests/`; fixtures are **real directory trees**
under `tests/fixtures/`.

---

## 3. Data model & invariants (`models.py`)

All model dataclasses are **frozen** except `ProjectAudit` (mutated only by
`_filter_audits` reconstruction). Mutate by constructing a new instance; new
fields get defaults so older call sites keep working.

Core shapes:

- `Dep` — a parsed requirement: `name` (canonicalized), `raw_name`,
  `specifier` (`SpecifierSet`), `extras`, `marker`, `source` (`DepSource`),
  `url`. Dumb container.
- `DepAudit` — `Dep` + resolved facts: `latest`, `latest_including_prereleases`,
  `drift`, `pin_status`, `yanked`, `vulnerabilities`, `min_safe_version`,
  `latest_release_date`, `warnings`.
- `ProjectAudit` — `project`, `deps`, `pinning_score`, `drift_summary`,
  `yanked_count`, `pypi_unreachable`, `vuln_count`, `vuln_unreachable`,
  `suppressed_count`.

**Invariants worth preserving** (there are tests that assert these; keep them
true):

1. `ProjectAudit.vuln_count` == number of **non-suppressed** advisories summed
   over `deps`. (Tree-wide CVE totals are `sum(vuln_count)`.)
2. No `DepAudit.vulnerabilities` list contains duplicate advisory ids — they
   are deduped in `vulns.py` (see §6).
3. `min_safe_version`, when set, is one of the advisories' fix versions and is
   strictly greater than the current pin.
4. `latest_release_date` and `latest_release_age_days` are both-or-neither.
5. `pin_status`/`drift` values are always valid enum members.
6. Output is deterministic given the same inputs (projects sorted by name,
   dedup preserves first-seen order, `_pick_latest` is order-independent).
7. `pinning_score` is computed over **direct** deps only (`a.dep.direct`), so a
   lockfile's always-pinned transitive graph doesn't force every locked project
   to ~100%.

---

## 4. Discovery (`discovery.py`)

`discover_tree` walks with `os.walk`, pruning in place: `ALWAYS_SKIP` dirs,
exact venv names, any dir containing `pyvenv.cfg`, `*.egg-info`, plus
user `--exclude` globs (matched against **basenames**, not full paths).

A directory is a project if it has `requirements*.txt` / `constraints*.txt`,
a `pyproject.toml` with `[project]` or `[tool.poetry]`, or a `Pipfile`. Each
`DepSource` records its `kind`, `path`, and `group` (e.g. `default`, `dev`,
an extras name, a poetry group). Python version is sniffed from `runtime.txt`,
`requires-python`, or Pipfile `[requires]`.

`discover_one` treats the path as a known project root (no parent rescan) —
this is why `audit <single-project>` defaults to the table view.

---

## 5. Parsing (`parsing.py`)

`parse_source(DepSource)` dispatches on `kind`. Notable internals:

- **Encoding**: requirements files are read as **bytes**, then decoded by
  `_decode_requirements`: UTF-32 BOM → UTF-16 BOM → `utf-8-sig` → latin-1 last
  resort. The BOM checks matter — Windows `pip freeze >` writes UTF-16-LE, and
  the latin-1 fallback would otherwise turn it into `F\x00l\x00…` garbage and
  silently drop every dep.
- **Includes**: `-r other.txt` / `-c constraints.txt` are followed recursively
  with a `_visited` set for cycle detection; each `Dep` keeps the `DepSource`
  of its *true* origin file.
- **PEP 508 via `packaging.Requirement`**. Bare direct-URL / VCS lines aren't
  PEP 508, so `_rewrite_bare_url` rewrites them to `name @ url`: it prefers an
  explicit `#egg=name`, else derives the name from the repo path
  (`_name_from_vcs_url`: strip trailing `@ref` and `.git`). Result is `URL`
  posture.
- **Poetry / Pipfile shorthand** share `_poetry_to_pep508`, which **returns a
  list** (Poetry multiple-constraints deps are a list of `{version, markers}`
  tables → one PEP 508 string per entry). It expands caret/tilde, honors a raw
  `markers` string and/or the `python` shorthand (parenthesized + AND-joined).
  Both the Poetry and Pipfile callers iterate the returned list — if you change
  its return type, update both.
- **Lockfiles** (`uv.lock` / `poetry.lock` / `pdm.lock`) go through
  `_parse_lockfile`: all three are TOML with `[[package]]` arrays, so it reads
  `data["package"]` and emits each as an exact `==` pin (the **full resolved
  graph**, direct + transitive). `_lockfile_direct_names` reads the sibling
  `pyproject.toml` to compute the direct-dep set and tags each `Dep.direct`
  accordingly (returns `None` = "unknown" when there's no manifest, so entries
  degrade to direct). The local project's own editable/virtual entry is
  skipped. **Discovery suppresses the matching manifest source** when a lock is
  present (`discovery._dep_sources_in_dir`: poetry.lock → skip Poetry source;
  uv/pdm.lock → skip PEP 621 source) so packages aren't double-counted.

---

## 6. PyPI + vuln clients (`pypi.py`, `vulns.py`)

Both follow the same shape (a deliberate convention — mirror it for any new
network/subprocess client): constructor takes `cache_dir`, `ttl_seconds`,
`timeout`, `concurrency`; on-disk JSON cache; swallow-and-log on failure;
return `None`/`()` on miss.

**PyPIClient** — stdlib `urllib` only (never `requests`/`httpx`). Parses the
JSON `releases` map into `ReleaseInfo` per version (`version`, `yanked`,
`requires_python`, `upload_time`). Cache is per-distribution JSON, bucketed by
name.

**VulnClient** — invokes `python -m pip_audit` (not the `pip-audit` script;
the script isn't on Windows PATH after install) as a subprocess against a temp
requirements file. Important details:

- `_chunk_unique_by_name` splits pins into batches with at most one version per
  package name — pip-audit rejects requirement files with duplicate names even
  at different versions.
- `_parse_pip_audit_payload` → `_dedupe_vulns`: pip-audit/OSV emits the same
  advisory id once per affected version range. `_dedupe_vulns` collapses by id,
  **unioning** `fix_versions` and `aliases` (first non-empty description wins).
  Dedup also runs in `_rehydrate_vulns` so pre-fix caches self-correct on read.
- pip-audit exit code `1` means "vulns found" (expected); only `!= 0 and != 1`
  is treated as failure. On failure the package goes to `unreachable`, never
  silently "clean".
- Cache key is `(canonical_name, version)`, file bucketed by `sha1[:2]`. Empty
  results are cached too, so clean pins don't re-spawn the subprocess.

`compute_min_safe_version(installed, vulns)` = max over advisories of the
lowest fix strictly greater than `installed`; `None` if any advisory has no
known newer fix (can't recommend one).

---

## 7. Analysis (`analysis.py`) — the heart

`audit_project` orchestrates everything above into a `ProjectAudit`.

- **`_pick_latest(md, target_python)`** returns `(latest_stable, latest_incl_pre)`,
  **excluding yanked and python-incompatible** releases. `--include-prereleases`
  swaps in the prerelease candidate as the effective latest.
- **`classify_drift(current, latest)`** → `SemverDrift` by which segment moved:
  `NONE` < `BUILD` < `PATCH` < `MINOR` < `MAJOR` < `EPOCH`; `UNKNOWN` when
  either side is missing (PyPI miss, unpinned, URL).
- **`classify_pin_status(specifier, url)`** → posture from the specifier
  *shape* (not value): `PINNED`/`COMPATIBLE`/`RANGE`/`FLOOR`/`UNPINNED`/`URL`.
- **`_pinning_score`** = fraction of non-URL deps that are `PINNED`/`COMPATIBLE`;
  `None` (→ `n/a`) when every dep is `URL`.
- **Age signal**: `_upload_time_for(md, effective_latest)` attaches the upload
  time of the *selected latest version* to `latest_release_date`. This is what
  `--fail-on-age` and the `Age` column read; it surfaces alive-vs-abandoned
  independent of drift.
- **Suppressions** are applied here: a matched `SuppressionRule` marks an
  advisory `suppressed=True` (excluded from `vuln_count`, `--fail-on-vuln`, and
  the update CVE floor) but the advisory is still emitted in JSON/SARIF.

---

## 8. CLI contract (`cli.py`)

- **Exit codes are named constants** — never write literal `1`/`2`/`3` outside
  this module: `EXIT_OK=0`, `EXIT_ERROR=1` (operational), `EXIT_ROLLBACK=2`
  (`update` test-install rolled back), `EXIT_GATE=3` (policy gate tripped).
- **Gates** run on the *unfiltered* audits (filters are display-only):
  `--fail-on-drift LEVEL`, `--fail-on-vuln any|N`, `--fail-on-age DAYS`.
  `_exceeds_age_threshold` uses strict `>` and fails open on unknown dates.
- **Filters** (`--vulnerable-only`, `--drift-min`) shrink the displayed `deps`
  via `_filter_audits`, dropping emptied projects; per-project counters are
  preserved. When a filter empties everything, the terminal branch reports
  "No deps matched … across N project(s) scanned" rather than "no projects".
- **Progress** bar is shown only when `len(projects) > 1 and stdout.isatty()
  and not json/sarif and not quiet`. It writes to **stderr** so stdout stays
  clean for `--json`/`--sarif`. Both `audit` and `stats` use `_make_progress`.
- argparse with subparsers — no click/typer. Shared cache flags
  (`--no-cache`, `--refresh-cache`, `--cache-ttl`, `--concurrency`) are copied
  onto each subcommand that does lookups.

---

## 9. Rendering (`render/`)

- **terminal**: three views — `tree` (default multi-project), `table` (default
  single project), `summary` (one row per project; the `Other` column folds
  build+epoch drift so it isn't hidden). A multi-project render ends with an
  aggregate footer (project/dep counts + CVE/yanked totals when non-zero).
  Color is paired with text labels everywhere (no color-only signaling), and
  `_make_console` sets `safe_box=True` when stdout can't encode box-drawing
  (cp1252). rich honors `NO_COLOR`.
- **json_out**: exports `SCHEMA_VERSION`. **Field rename/removal/type change ⇒
  bump it**; additive keys don't (but record them in the README schema-version
  table). Top-level discriminator is `kind` (`audit`/`stats`).
- **sarif**: one rule per advisory id, one result per (dep, advisory);
  suppressed advisories carry `suppressions: [{kind: "external"}]`. Mutually
  exclusive with `--json`.

---

## 10. Caching

| Source | Layout | Key |
| --- | --- | --- |
| PyPI | `<cache>/piptastic/pypi/…` | per distribution name |
| vulns | `<cache>/piptastic/vulns/<sha1[:2]>/<sha1>.json` | `(name, version)` |

Parent overridable with `PIPTASTIC_CACHE_DIR`; else `XDG_CACHE_HOME` else
`~/.cache`. Default TTL 3600s. `--no-cache`/`--refresh-cache` both set TTL=0.
Cache entries round-trip through `_dehydrate_*`/`_rehydrate_*`; **preserve
timezone-aware datetimes** across the round-trip (the age signal depends on
it). Don't change the on-disk layout without considering existing user caches.

---

## 11. Testing strategy & gotchas

- **Mock the HTTP/subprocess boundary, not the cache.** PyPI:
  `patch.object(client, "_http_get", ...)` or inject a `FakeClient` with
  `fetch_many/fetch_one`. Vulns: `patch.object(client, "_run_pip_audit", ...)`
  or a `FakeVulnClient` with `fetch_for` + `unreachable`.
- **Fixtures are real trees** under `tests/fixtures/`. Add one rather than
  building a tree inline when the shape will be reused. Encoding-specific
  cases (UTF-16) are better written to `tmp_path` as bytes to dodge
  `.gitattributes` EOL normalization.
- **`caplog` does NOT capture piptastic logs by default.** `logging.py` sets
  `logger.propagate = False`, so `caplog` (which listens on root) sees nothing
  once `configure_logging` has run. Either assert on stderr via `capsys`
  (after `main()` configures the stderr handler) or attach `caplog.handler`
  directly to the `piptastic` logger in the test.
- **"Test passes" ≠ "code ran."** The progress-bar branch only executes under
  `stdout.isatty()`, which is false under `capsys`; a naive `isatty`
  monkeypatch is ignored. Force it with a tty-proxy stdout
  (`isatty()->True`, delegating writes) and confirm via coverage that the
  intended lines were hit.
- Run the **full** suite before committing — shared helpers bite. (Example:
  changing `_poetry_to_pep508`'s return type broke the Pipfile parser, which
  reuses it.) All tests must pass; new behavior needs a test that would fail
  without the change.

---

## 12. Cross-platform notes

- Production is Linux; primary dev is Windows. No shell-only assumptions, no
  Windows-only path tricks; `.gitattributes` pins line endings.
- pip-audit is invoked as `python -m pip_audit` (the script isn't on Windows
  PATH after `pip install`).
- Terminal renderer falls back to `safe_box=True` on cp1252 stdout. Don't add
  box-drawing/ellipsis characters that break that fallback.
- Requirements files may be UTF-16 (Windows `pip freeze >`); the parser
  detects the BOM (§5).

---

## 13. How to extend (recipes)

**Add a new dep-file format**
1. Add a `SourceKind`; emit `DepSource`s for it in `discovery.py`.
2. Add a `_parse_<format>` in `parsing.py`; dispatch it in `parse_source`.
3. Normalize to PEP 508 where possible and reuse `_parse_one_requirement_line`.
4. Fixture tree + parsing tests.

**Add a CI gate**
1. Add the flag in `build_parser`; validate its value early in `_cmd_audit`.
2. Add an `_exceeds_*`/`*_gate_tripped` helper evaluating the **unfiltered**
   audits; set `gate_tripped` and return `EXIT_GATE`.
3. Document in README flag table + Exit codes; CHANGELOG; test the trip and the
   clean (fail-open) path.

**Add a JSON field**
1. Add it in `render/json_out.py`. Additive ⇒ no schema bump but record it in
   the README schema-version-history table. Rename/remove/retype ⇒ bump
   `SCHEMA_VERSION`.
2. If terminal-relevant, update all three terminal views and SARIF.

**Add a subcommand**
1. New subparser in `build_parser`; copy the shared cache-flag block if it does
   lookups. New write paths need an opt-in flag, a backup, and a rollback.
2. `_cmd_<name>` dispatched from `main`; reuse `EXIT_*`.

---

## 14. Dev workflow

```bash
pip install -e ".[dev]"
pytest tests/            # all green before any commit
piptastic audit .        # dogfood on this repo (or a tree of projects)
```

Release steps live in [CLAUDE.md](CLAUDE.md) ("How to make a release"): bump
`pyproject.toml` + `__init__.__version__` in lockstep, move CHANGELOG
`[Unreleased]` into a dated section, tag, push, cut a GitHub release. Commit
hygiene (no attribution trailers, conventional subjects, SPDX headers on new
source files) is also in `CLAUDE.md`.
