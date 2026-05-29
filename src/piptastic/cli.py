# SPDX-License-Identifier: AGPL-3.0-or-later
"""piptastic CLI — audit (default), list, update."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

from packaging.version import Version

from piptastic import __version__
from piptastic.analysis import audit_project
from piptastic.bootstrap import find_site_packages, find_venv, freeze_venv, is_plumbing
from piptastic.discovery import discover_one, discover_tree
from piptastic.logging import configure_logging, get_logger
from piptastic.models import ProjectAudit, SemverDrift
from piptastic.pypi import PyPIClient
from piptastic.render import (
    render_json,
    render_sarif,
    render_stats_json,
    render_stats_terminal,
    render_terminal,
)
from piptastic.stats import compute_stats
from piptastic.update import update_project
from piptastic.vulns import VulnClient

logger = get_logger(__name__)


# Exit-code contract (see README "Exit codes" section). Changed in v0.4:
# previously --fail-on-drift returned 1 (collided with operational errors).
EXIT_OK = 0
EXIT_ERROR = 1       # operational failure (bad input, no project, crash)
EXIT_ROLLBACK = 2    # update test-install failed; backup restored
EXIT_GATE = 3        # --fail-on-drift or --fail-on-vuln tripped


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
    audit.add_argument(
        "--sarif", action="store_true",
        help="SARIF 2.1.0 output for GitHub Code Scanning (mutually exclusive with --json)",
    )
    audit.add_argument("--include-prereleases", action="store_true")
    audit.add_argument(
        "--exclude", action="append", default=[],
        help="Glob pattern matched against directory BASENAMES (not paths), repeatable",
    )
    audit.add_argument("--no-cache", action="store_true")
    audit.add_argument("--refresh-cache", action="store_true")
    audit.add_argument("--cache-ttl", type=int, default=3600)
    audit.add_argument("--concurrency", type=int, default=8)
    audit.add_argument(
        "--fail-on-drift",
        choices=["build", "patch", "minor", "major", "epoch"],
        default=None,
        help="Exit 3 (gate) if any dep has drift at or above this level",
    )
    audit.add_argument(
        "--no-vulns",
        action="store_true",
        help="Skip the pip-audit CVE pass entirely (fast audit). Incompatible with --fail-on-vuln.",
    )
    audit.add_argument(
        "--vulnerable-only",
        action="store_true",
        help="Show only deps with non-suppressed CVEs. Empty projects are dropped.",
    )
    audit.add_argument(
        "--drift-min",
        choices=["build", "patch", "minor", "major", "epoch"],
        default=None,
        help="Show only deps with drift at or above this level. Empty projects are dropped.",
    )
    audit.add_argument(
        "--fail-on-vuln",
        metavar="any|N",
        default=None,
        help=(
            "Exit 3 (gate) when any dep has a non-suppressed CVE (`any`) or "
            "when tree-wide non-suppressed CVE count >= N. Incompatible with --no-vulns."
        ),
    )
    audit.add_argument(
        "--strict-vuln-gate",
        action="store_true",
        help=(
            "When --fail-on-vuln is set, also trip the gate if any package is "
            "vuln_unreachable (pip-audit could not return a status). Default: fail-open."
        ),
    )

    # list = audit + --table on a single project. Deprecated in v0.4, kept
    # for muscle memory; hidden from --help. Slated for removal in v0.5.
    lst = sub.add_parser("list", help=argparse.SUPPRESS)
    lst.add_argument("path", type=Path)
    lst.add_argument("--json", action="store_true")

    # update
    upd = sub.add_parser("update", help="Update requirements*.txt to latest pinned versions")
    upd.add_argument("path", type=Path)
    upd.add_argument("packages", nargs="*")
    upd.add_argument("--no-test", action="store_true")
    upd.add_argument("--refresh", action="store_true")
    upd.add_argument("--temp-test-env", action="store_true")
    upd.add_argument(
        "--no-apply-cve-floor",
        dest="apply_cve_floor",
        action="store_false",
        default=True,
        help="Disable the CVE-aware floor; pick latest non-yanked release as usual (default: floor is on)",
    )
    upd.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute would-be changes and print them; do not write files, create backups, or run the test install",
    )

    # stats
    stats = sub.add_parser(
        "stats",
        help="Cross-project rollup (top packages, fragmentation, yanked, etc.)",
    )
    stats.add_argument("path", type=Path)
    stats.add_argument("--top", type=int, default=20, help="Top N packages (default: 20)")
    stats.add_argument("--json", action="store_true", help="Machine-readable JSON to stdout")
    stats.add_argument(
        "--exclude", action="append", default=[],
        help="Glob pattern matched against directory BASENAMES (not paths), repeatable",
    )
    stats.add_argument("--no-cache", action="store_true")
    stats.add_argument("--refresh-cache", action="store_true")
    stats.add_argument("--cache-ttl", type=int, default=3600)
    stats.add_argument("--concurrency", type=int, default=8)

    # bootstrap
    boot = sub.add_parser(
        "bootstrap",
        help="Generate requirements.txt from a project's installed venv",
    )
    boot.add_argument("path", type=Path)
    boot.add_argument(
        "--venv",
        type=Path, default=None,
        help="Explicit venv directory (relative to PATH or absolute)",
    )
    boot.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing requirements.txt (creates a backup first)",
    )
    boot.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the requirements to stdout; do not write any file",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    level = logging.INFO if args.verbose else (logging.ERROR if args.quiet else logging.WARNING)
    configure_logging(level=level, log_file=args.log_file)

    if args.command is None:
        parser.print_help()
        return EXIT_OK

    try:
        if args.command == "audit":
            return _cmd_audit(args)
        if args.command == "list":
            return _cmd_list(args)
        if args.command == "update":
            return _cmd_update(args)
        if args.command == "bootstrap":
            return _cmd_bootstrap(args)
        if args.command == "stats":
            return _cmd_stats(args)
    except Exception as e:  # last-resort guard so we never traceback at the user
        logger.exception("unhandled error: %s", e)
        return EXIT_ERROR
    return EXIT_OK


# ---------- subcommand impls ----------

def _build_client(args) -> PyPIClient:
    if getattr(args, "no_cache", False):
        ttl = 0
    elif getattr(args, "refresh_cache", False):
        ttl = 0
    else:
        ttl = getattr(args, "cache_ttl", 3600)
    return PyPIClient(ttl_seconds=ttl, concurrency=getattr(args, "concurrency", 8))


def _make_progress():
    """Return a rich.progress.Progress writing to stderr so it doesn't
    contaminate JSON/SARIF stdout output. Caller manages context."""
    from rich.console import Console
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
    )
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=Console(stderr=True),
        transient=True,
    )


def _build_vuln_client(args) -> VulnClient:
    if getattr(args, "no_cache", False):
        ttl = 0
    elif getattr(args, "refresh_cache", False):
        ttl = 0
    else:
        ttl = getattr(args, "cache_ttl", 3600)
    return VulnClient(ttl_seconds=ttl, concurrency=getattr(args, "concurrency", 8))


def _cmd_audit(args) -> int:
    path = args.path.resolve()
    if not path.exists():
        logger.error("path does not exist: %s", path)
        return EXIT_ERROR

    # Incompatible-flags check (cannot use mutually_exclusive_group because
    # --fail-on-vuln takes a value and we want a clear, custom message).
    no_vulns = getattr(args, "no_vulns", False)
    fail_on_vuln = getattr(args, "fail_on_vuln", None)
    if no_vulns and fail_on_vuln is not None:
        logger.error("--no-vulns and --fail-on-vuln are incompatible (no data to gate on)")
        return EXIT_ERROR
    if no_vulns and getattr(args, "strict_vuln_gate", False):
        logger.error("--no-vulns and --strict-vuln-gate are incompatible")
        return EXIT_ERROR
    if args.json and getattr(args, "sarif", False):
        logger.error("--json and --sarif are mutually exclusive")
        return EXIT_ERROR
    if fail_on_vuln is not None:
        # Validate value early so we don't run the whole audit on a typo.
        if fail_on_vuln != "any":
            try:
                int(fail_on_vuln)
            except ValueError:
                logger.error("--fail-on-vuln must be 'any' or an integer, got %r", fail_on_vuln)
                return EXIT_ERROR

    # If `path` is a single project (has dep sources directly), use discover_one.
    single = discover_one(path)
    if single is not None:
        projects = [single]
    else:
        projects = discover_tree(path, exclude=args.exclude)
    if not projects:
        logger.error("no Python projects found at %s", path)
        return EXIT_ERROR

    client = _build_client(args)
    vuln_client = None if no_vulns else _build_vuln_client(args)
    current_py = Version(".".join(str(x) for x in sys.version_info[:3]))
    include_pre = getattr(args, "include_prereleases", False)
    audits = []

    # Project-level progress bar — only when output is interactive and the
    # caller isn't asking for machine-readable output. Always disabled in
    # --quiet mode.
    show_progress = (
        len(projects) > 1
        and sys.stdout.isatty()
        and not args.json
        and not getattr(args, "sarif", False)
        and not args.quiet
    )
    progress_ctx = _make_progress() if show_progress else None
    task_id = None
    if progress_ctx is not None:
        progress_ctx.__enter__()
        task_id = progress_ctx.add_task(f"auditing {len(projects)} project(s)", total=len(projects))
    try:
        for p in projects:
            try:
                audits.append(
                    audit_project(
                        p,
                        client,
                        current_python=current_py,
                        include_prereleases=include_pre,
                        vuln_client=vuln_client,
                    )
                )
            except Exception as e:
                # One bad project must not kill the whole tree scan.
                logger.warning("failed to audit project %s: %s", p.name, e)
            if progress_ctx is not None:
                progress_ctx.advance(task_id)
    finally:
        if progress_ctx is not None:
            progress_ctx.__exit__(None, None, None)

    filtered = _filter_audits(
        audits,
        vulnerable_only=getattr(args, "vulnerable_only", False),
        drift_min=getattr(args, "drift_min", None),
    )

    if args.json:
        print(render_json(filtered, root=path))
    elif getattr(args, "sarif", False):
        print(render_sarif(filtered, root=path))
    else:
        mode = "summary" if args.summary else ("table" if (args.table or single is not None) else "tree")
        render_terminal(filtered, mode=mode)

    # Gate evaluation runs on the UNFILTERED audits — filters are display-only.
    gate_tripped = False
    if args.fail_on_drift:
        threshold = SemverDrift(args.fail_on_drift)
        if _exceeds_threshold(audits, threshold):
            logger.warning("--fail-on-drift %s threshold tripped", args.fail_on_drift)
            gate_tripped = True
    if fail_on_vuln is not None:
        if _vuln_gate_tripped(audits, fail_on_vuln, strict=getattr(args, "strict_vuln_gate", False)):
            gate_tripped = True
    if gate_tripped:
        return EXIT_GATE
    return EXIT_OK


def _vuln_gate_tripped(
    audits: list[ProjectAudit], spec: str, *, strict: bool
) -> bool:
    """Evaluate --fail-on-vuln. Counts non-suppressed advisories.

    `spec` is either 'any' or an integer threshold.
    `strict` makes vuln_unreachable count as "unknown == tripped" instead of
    fail-open.
    """
    # vuln_unreachable handling: warn always; trip only when --strict.
    unreachable = sorted({n for a in audits for n in a.vuln_unreachable})
    if unreachable:
        if strict:
            logger.warning(
                "--strict-vuln-gate: %d package(s) vuln_unreachable -> gate trips: %s",
                len(unreachable), ", ".join(unreachable[:5]) + ("..." if len(unreachable) > 5 else ""),
            )
            return True
        logger.warning(
            "vuln_unreachable for %d package(s); not tripping gate (pass --strict-vuln-gate to flip)",
            len(unreachable),
        )

    total = sum(a.vuln_count for a in audits)  # already non-suppressed
    if spec == "any":
        tripped = total > 0
        if tripped:
            logger.warning("--fail-on-vuln any: %d non-suppressed advisory(ies) found", total)
        return tripped
    threshold = int(spec)
    tripped = total >= threshold
    if tripped:
        logger.warning(
            "--fail-on-vuln %d: %d non-suppressed advisory(ies) (>= threshold)",
            threshold, total,
        )
    return tripped


def _cmd_list(args) -> int:
    # Deprecated alias for `audit <path> --table`. See CHANGELOG v0.4 — will
    # be removed in v0.5. One-line warning so it doesn't drown the output.
    logger.warning(
        "`piptastic list` is deprecated and will be removed in v0.5; "
        "use `piptastic audit <path> --table` instead"
    )
    args.table = True
    args.summary = False
    args.include_prereleases = False
    args.exclude = []
    args.no_cache = False
    args.refresh_cache = False
    args.cache_ttl = 3600
    args.concurrency = 8
    args.fail_on_drift = None
    args.no_vulns = False
    args.vulnerable_only = False
    args.drift_min = None
    args.fail_on_vuln = None
    args.strict_vuln_gate = False
    args.sarif = False
    return _cmd_audit(args)


def _cmd_update(args) -> int:
    path = args.path.resolve()
    project = discover_one(path)
    if project is None:
        logger.error("no Python project at %s", path)
        return EXIT_ERROR

    client = PyPIClient(ttl_seconds=0 if args.refresh else 3600)
    vuln_client = VulnClient(ttl_seconds=0 if args.refresh else 3600) if args.apply_cve_floor else None
    results = update_project(
        project,
        packages=args.packages or None,
        test=not args.no_test,
        refresh=args.refresh,
        use_temp_test_env=args.temp_test_env,
        client=client,
        vuln_client=vuln_client,
        apply_cve_floor=args.apply_cve_floor,
        dry_run=args.dry_run,
    )

    any_changes = False
    rollback = False
    total_bumped = 0
    cve_driven = 0
    for r in results:
        if r.changes:
            any_changes = True
            for change in r.changes:
                name, old, new = change[0], change[1], change[2]
                note = change[3] if len(change) > 3 else ""
                if note.startswith("CVE floor"):
                    cve_driven += 1
                total_bumped += 1
                suffix = f"  ({note})" if note else ""
                print(f"  {name}: {old or '(unpinned)'} -> {new}{suffix}")
        if r.tested and not r.test_passed:
            rollback = True
            print(f"[piptastic] test install failed; rolled back {r.requirements_file}")

    if not any_changes:
        print("[piptastic] no changes" + (" (dry-run)" if args.dry_run else ""))
    else:
        verb = "would bump" if args.dry_run else "bumped"
        parts = [f"{total_bumped} {verb}"]
        if cve_driven:
            parts.append(f"{cve_driven} CVE-driven")
        suffix = " (dry-run, no files changed)" if args.dry_run else ""
        print(f"[piptastic] {', '.join(parts)}{suffix}")
    if rollback:
        return EXIT_ROLLBACK
    return EXIT_OK


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


def _filter_audits(
    audits: list[ProjectAudit],
    *,
    vulnerable_only: bool,
    drift_min: str | None,
) -> list[ProjectAudit]:
    """Drop deps that don't match the filter; drop projects that end up empty.

    Per-project counters (drift_summary, vuln_count, etc.) are preserved as-is
    — they reflect the project as a whole, not the filtered view. Only the
    `deps` list shrinks.
    """
    if not vulnerable_only and drift_min is None:
        return audits
    drift_threshold = _DRIFT_RANK[SemverDrift(drift_min)] if drift_min else 0
    out: list[ProjectAudit] = []
    for a in audits:
        keep: list = []
        for d in a.deps:
            if vulnerable_only and not d.vulnerabilities:
                continue
            if drift_min and _DRIFT_RANK[d.drift] < drift_threshold:
                continue
            keep.append(d)
        if not keep:
            continue
        # Shallow copy with replaced deps. ProjectAudit is not frozen, so
        # mutate-safe construction works.
        out.append(ProjectAudit(
            project=a.project,
            deps=keep,
            pinning_score=a.pinning_score,
            drift_summary=a.drift_summary,
            yanked_count=a.yanked_count,
            pypi_unreachable=a.pypi_unreachable,
            vuln_count=a.vuln_count,
            vuln_unreachable=a.vuln_unreachable,
        ))
    return out


def _cmd_bootstrap(args) -> int:
    project_path = args.path.resolve()
    if not project_path.is_dir():
        logger.error("not a directory: %s", project_path)
        return EXIT_ERROR

    candidates, chosen = find_venv(project_path, explicit=args.venv)
    if not candidates:
        logger.error(
            "no venv found under %s; pass --venv to specify",
            project_path,
        )
        return EXIT_ERROR
    if chosen is None:
        rel = ", ".join(str(c.relative_to(project_path)) for c in candidates)
        logger.error(
            "multiple venvs found (%s); pass --venv to disambiguate",
            rel,
        )
        return EXIT_ERROR

    lines = freeze_venv(project_path, chosen)

    if args.dry_run:
        for line in lines:
            print(line)
        return EXIT_OK

    target = project_path / "requirements.txt"
    if target.exists() and not args.force:
        logger.error(
            "%s already exists; pass --force to overwrite (a backup will be created)",
            target,
        )
        return EXIT_ERROR

    if target.exists() and args.force:
        backup_dir = project_path / ".requirements_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()[:8]
        dest = backup_dir / f"requirements_{ts}_{digest}.txt"
        shutil.copy2(target, dest)
        print(f"piptastic: backed up existing requirements.txt to {dest}")

    try:
        target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    except OSError as e:
        logger.error("failed to write %s: %s", target, e)
        return EXIT_ERROR

    sp = find_site_packages(chosen)
    if sp is not None:
        plumbing_count = sum(
            1 for d in importlib.metadata.distributions(path=[str(sp)])
            if d.metadata["Name"] and is_plumbing(d.metadata["Name"])
        )
    else:
        plumbing_count = 0

    print(f"piptastic: wrote {target}")
    print(f"  captured {len(lines)} deps from {chosen}")
    if plumbing_count:
        print(f"  skipped {plumbing_count} plumbing distributions")
    return 0


def _cmd_stats(args) -> int:
    path = args.path.resolve()
    if not path.exists():
        logger.error("path does not exist: %s", path)
        return EXIT_ERROR

    single = discover_one(path)
    if single is not None:
        projects = [single]
    else:
        projects = discover_tree(path, exclude=args.exclude)
    if not projects:
        logger.error("no Python projects found at %s", path)
        return EXIT_ERROR

    client = _build_client(args)
    current_py = Version(".".join(str(x) for x in sys.version_info[:3]))
    audits = []
    for p in projects:
        try:
            # stats does not need vuln data — keep it fast.
            audits.append(audit_project(p, client, current_python=current_py))
        except Exception as e:
            logger.warning("failed to audit %s: %s", p.name, e)

    report = compute_stats(audits, top=args.top, root=path)

    if args.json:
        print(render_stats_json(report))
    else:
        render_stats_terminal(report)
    return EXIT_OK
