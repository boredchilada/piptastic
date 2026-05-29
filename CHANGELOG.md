# Changelog

All notable changes to piptastic. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[SemVer](https://semver.org/spec/v2.0.0.html). The JSON output has its own
`schema_version` field, which bumps independently on breaking changes to the
JSON shape.

## [Unreleased]

### Fixed

- **Duplicate advisories no longer inflate `vuln_count`.** pip-audit / OSV can
  report the same advisory id more than once for a package (one record per
  affected version range). Those are now collapsed to a single advisory, with
  fix_versions and aliases unioned, so `vuln_count`, the tree-wide CVE total,
  the `--summary` footer, SARIF results, and the `update` CVE notes stop
  double-counting. Deduping also runs on cache reads, so caches written before
  this fix self-correct without `--refresh`. `min_safe_version` is unaffected
  (the union preserves every known fix version).

## [0.4.1] — 2026-05-29

**In plain English:** you can now fail a CI build when a dependency hasn't
shipped a release in too long (not just when it's outdated). Scanning a folder
of projects ends with a one-line tally so you see the big picture at a glance.
The summary table stopped hiding the less-common kinds of version drift. And
if you've told piptastic to accept a known security issue until a certain
date, it now warns you a month before that acceptance runs out — so the alert
doesn't silently come back and break your build. Plus smaller polish: clearer
messages when a filter matches nothing, a progress bar on `stats`, and help
text for options that were missing it.

### Added

- `--fail-on-age DAYS` CI gate on `audit`. Exits `3` when any dep's latest
  release is older than `DAYS`, reusing the `latest_release_age_days` signal.
  Deps with an unknown release date never trip it (fail-open on missing data).
- Aggregate footer after a multi-project terminal render: project / dep
  counts plus CVE and yanked totals when non-zero. Single-project output is
  unchanged.
- `Other` column on the `--summary` view, summing `build` + `epoch` drift so
  a project whose only drift is in those tiers no longer reads as `0/0/0`.

### Changed

- Empty display-filter results (`--vulnerable-only` / `--drift-min` matching
  nothing) now report "No deps matched … across N project(s) scanned" instead
  of the misleading "No Python projects found."
- Suppression rules expiring within 30 days now log a heads-up warning so an
  accepted CVE doesn't silently re-activate. Past-expiry handling is unchanged.
- `audit --table --summary` now warns that `--summary` wins instead of
  silently picking one.
- `--help` text filled in for the `update` / `stats` / `bootstrap` positional
  arguments and the `update --no-test` / `--refresh` / `--temp-test-env` flags.
- `stats` shows the same progress bar as `audit` on multi-project scans
  (stderr-only, hidden for `--json` / non-TTY / `--quiet`).

## [0.4.0] — 2026-05-28

**In plain English:** adds CI gating on known security advisories
(`--fail-on-vuln`), with the lowest safe version to bump to surfaced per
dependency. Advisories that don't apply can be formally accepted, with a
reason and an expiry date, so they stop being reported. `update` gains a
`--dry-run` to preview changes before anything is written. Reports can be
filtered to just the vulnerable or just the stale dependencies, emitted as
SARIF for GitHub's Security tab, and show a progress bar on large scans.
Packages that haven't shipped a release in years are now flagged even when
they're technically up to date. Note for scripts: the exit code for tripped
policy gates changed from `1` to `3`; `1` is now reserved for operational
errors.

### Breaking

- **Exit-code contract split.** `--fail-on-drift` and the new
  `--fail-on-vuln` now return `3` (policy gate tripped) instead of `1`. `1`
  is reserved for operational errors (bad path, no project found, internal
  crash). `2` remains `update` test-install rollback. CI consumers comparing
  to `==1` should switch to `==3` for gate checks. See README "Exit codes"
  for the full table.
- **`update --apply-cve-floor` removed** (the positive form). The CVE floor
  is on by default; only `--no-apply-cve-floor` is needed. Scripts passing
  the positive flag will get an argparse error.
- **JSON `schema_version` bumped 2 → 3.** Adds per-dep `latest_release_date`
  and `latest_release_age_days`; per-vuln `suppressed` boolean and optional
  `suppression` block; per-project `suppressed_count`. All additive; existing
  fields unchanged.

### Added

- `--fail-on-vuln {any,N}` on `audit`. Exits `3` when any dep has a
  non-suppressed advisory (`any`), or when the tree-wide non-suppressed CVE
  count is at least `N`. Defaults to fail-open on `vuln_unreachable`
  packages (logs a warning); `--strict-vuln-gate` flips to fail-closed.
- `update --dry-run` previews changes without writing files, creating
  backups, or running the test install. CVE-floor lookups still happen so
  the preview is accurate.
- `--vulnerable-only` and `--drift-min LEVEL` filters on `audit` output.
  Drop deps that don't match; drop projects that end up empty. Apply to
  terminal and JSON identically.
- `--no-vulns` on `audit` skips the pip-audit pass entirely. Mutually
  exclusive with `--fail-on-vuln` and `--strict-vuln-gate`.
- `--sarif` on `audit` emits SARIF 2.1.0 for GitHub Code Scanning. Suppressed
  CVEs are emitted with `suppressionStates: ["suppressedExternally"]`.
  Mutually exclusive with `--json`.
- Progress bar during tree audits (rich `Progress`). Auto-hidden when stdout
  is not a TTY, when `--json` / `--sarif` is set, or with `--quiet`.
- Latest-release-date / age signal. Terminal table gains an `Age` column;
  JSON gains `latest_release_date` and `latest_release_age_days` per dep.
  Surfaces abandoned packages even when drift is `none`.
- CVE suppression config under `[tool.piptastic.suppressions]` in a
  project's `pyproject.toml` (or root-level `.piptastic.toml`). Each rule
  requires `package`, `cve`, `reason`, and an `expires` date. Past-due rules
  are ignored with a warning. Matches CVE / GHSA / PYSEC ids and pip-audit
  aliases. `package = "*"` suppresses tree-wide.
- End-of-run summary line on `update`: `[piptastic] 7 bumped, 2 CVE-driven`.
  Dry-run runs show "would bump" instead.
- CHANGELOG.md (this file).

### Deprecated

- `piptastic list` is hidden from `--help` and slated for removal in v0.5.
  Use `piptastic audit <path> --table` instead. Still functional in v0.4
  with a one-line deprecation warning on use.

### Fixed

- README `--temp-test-env` description corrected. The default puts the
  throwaway venv at `.piptastic_test_<ts>/` next to the project; the flag
  flips it to the OS temp directory.
- README bootstrap-backup description corrected. The previous file is
  copied to `.requirements_backups/requirements_<ts>_<digest>.txt`, not
  `*.bak.<ts>`.

## [0.3.0] — 2026-05-27

### Added

- pip-audit integration. Every `audit` run queries `python -m pip_audit`
  against resolved `(name, version)` pairs and attaches per-dep
  `vulnerabilities[]` and `min_safe_version`.
- CVE-aware updates. `update` lifts bump targets past vulnerable ranges by
  default; opt out with `--no-apply-cve-floor`. Bumps driven by an advisory
  are annotated in the change line.
- Terminal renderer gains a `Min safe` column on the table view and `Vulns`
  counts on table, summary, and tree views.
- New `VulnClient` with per-`(name, version)` on-disk JSON cache at
  `~/.cache/piptastic/vulns/`.
- New runtime dependency: `pip-audit>=2.7`. Invoked as
  `python -m pip_audit` so no PATH shim is required on Windows.

### Changed

- JSON `schema_version` bumped 1 → 2. Per dep: `vulnerabilities[]` and
  `min_safe_version`. Per project: `vuln_count` and `vuln_unreachable`.
- pip-audit invocations are split into batches with one version per package
  name. pip-audit rejects requirements files with duplicate pin names even
  at different versions; chunking sidesteps that.

## [0.2.1] — 2026-05-26

### Added

- `bootstrap` subcommand reconstructs `requirements.txt` from an existing
  venv. Skips plumbing (`pip`, `setuptools`, `wheel`, `pkg_resources`,
  `distlib`, `_distutils_hack`) and editable self-installs. Auto-discovers
  `.venv` / `venv` / `env` / `.env` or any directory with a `pyvenv.cfg`.
  `--force` writes a backup before overwriting; `--dry-run` prints to
  stdout.
- `stats` subcommand for cross-project rollup: top packages, version
  fragmentation, yanked pins, and tree-wide drift / pin-posture histograms.
  Reuses the audit pipeline.

## [0.2.0] — 2026-05-26

### Added

- `audit` subcommand: per-dep drift, pin posture, and yanked detection
  against PyPI. Three views: table, tree, summary. JSON output with stable
  `schema_version=1`.
- `update` subcommand: rewrite `requirements*.txt` to latest compatible
  pins, with backup, test install, and rollback on failure.
- `list` subcommand: alias for `audit --table` on a single project.
  (Deprecated in v0.4; will be removed in v0.5.)
- Parsers for `requirements*.txt` (with `-r`/`-c` include chains),
  `pyproject.toml` (PEP 621 and Poetry), `Pipfile`, and `Pipfile.lock`.
- PyPI client with on-disk TTL cache and thread-pool concurrency.
- `--fail-on-drift LEVEL` CI gate.
- rich-powered terminal renderer with cp1252-safe fallback for Windows
  consoles.
- Cross-platform line endings pinned via `.gitattributes`.
- AGPL-3.0-or-later license.

[Unreleased]: https://github.com/boredchilada/piptastic/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/boredchilada/piptastic/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/boredchilada/piptastic/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/boredchilada/piptastic/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/boredchilada/piptastic/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/boredchilada/piptastic/releases/tag/v0.2.0
