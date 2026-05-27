# CLAUDE.md

Guidance for Claude Code (and other coding agents) working in this
repo. Humans can read it too — it's a high-level map of how the
project is structured and the conventions to follow.

## What this project is

`piptastic` is a Python dependency auditor. It walks a directory tree,
finds Python projects, and reports per-dep:

- pin posture (`pinned` / `compatible` / `range` / `floor` /
  `unpinned` / `url`)
- drift against PyPI (`none` → `epoch`)
- known CVEs and minimum safe versions via [pip-audit](https://github.com/pypa/pip-audit)

Read-only by default. `update` rewrites `requirements*.txt` (with
backups + tested install + CVE-aware floor). `bootstrap` reconstructs
`requirements.txt` from an existing venv. `stats` rolls up across a
whole tree.

End users invoke `piptastic` (or `ptc` / `python -m piptastic`).
There's no daemon, no server, no shared state — every run is
self-contained.

## Source layout

```
src/piptastic/
  __main__.py        Thin entry point → cli.main
  cli.py             argparse subcommand wiring + dispatch
  discovery.py       Walk the tree, find Python projects
  parsing.py         Parse requirements*.txt / pyproject / Pipfile
  models.py          Frozen dataclasses (Dep, DepAudit, ProjectAudit, ...)
  pypi.py            PyPIClient: urllib + on-disk TTL cache + threadpool
  vulns.py           VulnClient: pip-audit subprocess + per-(name,version) cache
  analysis.py        audit_project — the core read pipeline
  update.py          Rewrite requirements.txt with backups + test install
  bootstrap.py       Freeze a venv to requirements.txt
  stats.py           Cross-project rollup
  render/
    json_out.py      Stable schema_version=2 JSON output
    terminal.py      rich tables / trees / summaries
```

Tests mirror sources under `tests/`. Fixtures live in
`tests/fixtures/` and exercise every supported source-file shape.

## Conventions

### Dataclasses
- All model dataclasses are **frozen**. Mutate by constructing a new
  instance. New fields go on the dataclass definition with a default
  so older callers don't break.
- Don't add domain logic to models — keep them dumb containers.
  Behavior lives in `analysis.py`, `update.py`, `stats.py`.

### I/O boundaries
- Never call `requests` / `httpx` — `pypi.py` uses stdlib `urllib` on
  purpose (one less runtime dep). pip-audit access goes through
  `vulns.VulnClient` (subprocess), never by importing pip-audit's
  internal API.
- All network/subprocess clients follow the same shape: ctor with
  `cache_dir`, `ttl_seconds`, `timeout`, `concurrency`; on-disk JSON
  cache; swallow-and-log failures; return `None` / `()` on miss.
- Cache layout is content-bucketed by name prefix (PyPI) or sha1
  (vulns). Don't change either without bumping `schema_version`.

### JSON schema
- `render/json_out.py` exports `SCHEMA_VERSION`. **Any field
  rename / removal / type change requires bumping it.** Additive
  changes (new optional keys) don't, but document the addition in
  README's schema-version-history table.
- Keep `kind: "audit"` and `kind: "stats"` as the discriminator.
  Don't introduce a third top-level shape without discussion.

### CLI
- argparse with subparsers — no click, no typer.
- Shared cache flags live on each subparser (`--no-cache`,
  `--refresh-cache`, `--cache-ttl`, `--concurrency`). When adding a
  new subcommand that does PyPI/vuln lookups, copy that block.
- `audit` and `stats` are read-only. `update` and `bootstrap` are the
  only write paths. Any new write path needs explicit user opt-in
  flag + a backup mechanism + a test-install rollback if it mutates
  install state.

### Logging
- Use `from piptastic.logging import get_logger` — never `logging`
  directly. The custom factory wires stderr formatting + the
  optional `--log-file` mirror.
- WARNING for recoverable issues (PyPI miss, pip-audit failed for a
  batch). INFO for narrative ("skipping pyproject.toml in update").
  ERROR only when the run is about to return non-zero.

### Errors
- Per-project failures must NOT kill the tree scan. Catch broadly in
  `cli.py::_cmd_audit` and log a warning.
- Network failures degrade gracefully: PyPI miss → drift becomes
  `UNKNOWN`; pip-audit miss → package goes into `vuln_unreachable`
  (never silently reported as clean).
- Never `print()` from library code. `cli.py` owns stdout.

### Testing
- Pytest. Every test file mirrors a source file.
- Mock the HTTP / subprocess boundary, not the cache. Pattern:
  `patch.object(client, "_http_get", return_value=SAMPLE_PAYLOAD)`
  for PyPI; `patch.object(client, "_run_pip_audit",
  return_value=SAMPLE_AUDIT_JSON)` for vulns. Cache round-trip + TTL
  expiry tests use `os.utime` to backdate cache files.
- Fixtures under `tests/fixtures/` are real directory trees, not
  monkey-patches. Add a fixture rather than building a tree inline
  if the shape will be reused.
- All 122+ tests must pass before any commit.

### Cross-platform
- Production runs on Linux. Development is Windows. Code must work
  on both: no shell-only assumptions, no Windows-only path tricks.
- `.gitattributes` pins line endings — don't override.
- pip-audit must be invoked as `python -m pip_audit`, not the script
  entry point. The script is not on Windows PATH after `pip install`
  by default.
- Terminal renderer falls back to `safe_box=True` when stdout is
  cp1252 (Windows default). Don't add box-drawing characters that
  break that fallback.

## How to add a feature

1. Read [docs/superpowers/specs/](docs/superpowers/specs/) and
   [docs/superpowers/plans/](docs/superpowers/plans/) for any
   ongoing design.
2. If the feature touches the audit pipeline, sketch how `DepAudit`
   / `ProjectAudit` need to grow first. Frozen dataclasses force you
   to commit to the shape early.
3. Network or subprocess access? Mirror the `PyPIClient` /
   `VulnClient` shape. Don't invent a new pattern.
4. CLI surface? Add to `cli.build_parser()`. New write paths need
   opt-in flag.
5. JSON output? Update `render/json_out.py` AND the schema-version
   history table in README if it's a breaking change.
6. Terminal output? Update `render/terminal.py` — table view,
   summary view, AND tree view.
7. Tests: cache round-trip + TTL + happy path + error path.

## How to make a release

1. Bump `version` in `pyproject.toml`.
2. Update README's "Not in v0.X" section.
3. Verify `pytest tests/` all green.
4. Commit. (See "Commit hygiene" below.)
5. `git tag vX.Y.Z` and push the tag.
6. `gh release create vX.Y.Z --generate-notes` (add `--prerelease`
   for pre-1.0 cuts).

## Commit hygiene

- **No `Co-Authored-By: Claude` or similar attribution trailers.**
  Commits should be authored by the contributor running them. If
  Claude helped, that's fine — it doesn't go in the commit message.
- Subjects under ~70 chars. Conventional prefix when natural: `feat(…)`,
  `fix(…)`, `docs(…)`, `chore(…)`, `test(…)`, `refactor(…)`.
- Body explains the *why*, not the diff. Reviewers can read the diff.
- Don't commit `.requirements_backups/`, `.piptastic_test_*`,
  `htmlcov/`, `.pytest_cache/`. The `.gitignore` covers these.
- Never commit secrets — there shouldn't be any, but if you see one,
  stop and flag it.

## License

AGPL-3.0-or-later. Any new file should carry the SPDX header:

```python
# SPDX-License-Identifier: AGPL-3.0-or-later
```

Tests don't need the header — they're considered part of the same
work and inherit the project license.
