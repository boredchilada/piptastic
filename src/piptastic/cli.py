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
from piptastic.render import render_json, render_stats_json, render_stats_terminal, render_terminal
from piptastic.stats import compute_stats
from piptastic.update import update_project

logger = get_logger(__name__)


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
        help="Exit non-zero if any dep has drift at or above this level",
    )

    # list = audit + --table on a single project (kept for muscle memory)
    lst = sub.add_parser("list", help="Alias for `audit <path> --table` on a single project")
    lst.add_argument("path", type=Path)
    lst.add_argument("--json", action="store_true")

    # update
    upd = sub.add_parser("update", help="Update requirements*.txt to latest pinned versions")
    upd.add_argument("path", type=Path)
    upd.add_argument("packages", nargs="*")
    upd.add_argument("--no-test", action="store_true")
    upd.add_argument("--refresh", action="store_true")
    upd.add_argument("--temp-test-env", action="store_true")

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
        return 0

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
        return 1
    return 0


# ---------- subcommand impls ----------

def _build_client(args) -> PyPIClient:
    if getattr(args, "no_cache", False):
        ttl = 0
    elif getattr(args, "refresh_cache", False):
        ttl = 0
    else:
        ttl = getattr(args, "cache_ttl", 3600)
    return PyPIClient(ttl_seconds=ttl, concurrency=getattr(args, "concurrency", 8))


def _cmd_audit(args) -> int:
    path = args.path.resolve()
    if not path.exists():
        logger.error("path does not exist: %s", path)
        return 1

    # If `path` is a single project (has dep sources directly), use discover_one.
    single = discover_one(path)
    if single is not None:
        projects = [single]
    else:
        projects = discover_tree(path, exclude=args.exclude)
    if not projects:
        logger.error("no Python projects found at %s", path)
        return 1

    client = _build_client(args)
    current_py = Version(".".join(str(x) for x in sys.version_info[:3]))
    include_pre = getattr(args, "include_prereleases", False)
    audits = []
    for p in projects:
        try:
            audits.append(
                audit_project(p, client, current_python=current_py, include_prereleases=include_pre)
            )
        except Exception as e:
            # One bad project must not kill the whole tree scan.
            logger.warning("failed to audit project %s: %s", p.name, e)

    if args.json:
        print(render_json(audits, root=path))
    else:
        mode = "summary" if args.summary else ("table" if (args.table or single is not None) else "tree")
        render_terminal(audits, mode=mode)

    if args.fail_on_drift:
        threshold = SemverDrift(args.fail_on_drift)
        if _exceeds_threshold(audits, threshold):
            return 1
    return 0


def _cmd_list(args) -> int:
    # Equivalent to `audit <path> --table`
    args.table = True
    args.summary = False
    args.include_prereleases = False
    args.exclude = []
    args.no_cache = False
    args.refresh_cache = False
    args.cache_ttl = 3600
    args.concurrency = 8
    args.fail_on_drift = None
    return _cmd_audit(args)


def _cmd_update(args) -> int:
    path = args.path.resolve()
    project = discover_one(path)
    if project is None:
        logger.error("no Python project at %s", path)
        return 1

    client = PyPIClient(ttl_seconds=0 if args.refresh else 3600)
    results = update_project(
        project,
        packages=args.packages or None,
        test=not args.no_test,
        refresh=args.refresh,
        use_temp_test_env=args.temp_test_env,
        client=client,
    )

    any_changes = False
    rollback = False
    for r in results:
        if r.changes:
            any_changes = True
            for name, old, new in r.changes:
                print(f"  {name}: {old or '(unpinned)'} -> {new}")
        if r.tested and not r.test_passed:
            rollback = True
            print(f"[piptastic] test install failed; rolled back {r.requirements_file}")

    if not any_changes:
        print("[piptastic] no changes")
    if rollback:
        return 2
    return 0


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


def _cmd_bootstrap(args) -> int:
    project_path = args.path.resolve()
    if not project_path.is_dir():
        logger.error("not a directory: %s", project_path)
        return 1

    candidates, chosen = find_venv(project_path, explicit=args.venv)
    if not candidates:
        logger.error(
            "no venv found under %s; pass --venv to specify",
            project_path,
        )
        return 1
    if chosen is None:
        rel = ", ".join(str(c.relative_to(project_path)) for c in candidates)
        logger.error(
            "multiple venvs found (%s); pass --venv to disambiguate",
            rel,
        )
        return 1

    lines = freeze_venv(project_path, chosen)

    if args.dry_run:
        for line in lines:
            print(line)
        return 0

    target = project_path / "requirements.txt"
    if target.exists() and not args.force:
        logger.error(
            "%s already exists; pass --force to overwrite (a backup will be created)",
            target,
        )
        return 1

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
        return 2

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
        return 1

    single = discover_one(path)
    if single is not None:
        projects = [single]
    else:
        projects = discover_tree(path, exclude=args.exclude)
    if not projects:
        logger.error("no Python projects found at %s", path)
        return 1

    client = _build_client(args)
    current_py = Version(".".join(str(x) for x in sys.version_info[:3]))
    audits = []
    for p in projects:
        try:
            audits.append(audit_project(p, client, current_python=current_py))
        except Exception as e:
            logger.warning("failed to audit %s: %s", p.name, e)

    report = compute_stats(audits, top=args.top, root=path)

    if args.json:
        print(render_stats_json(report))
    else:
        render_stats_terminal(report)
    return 0
