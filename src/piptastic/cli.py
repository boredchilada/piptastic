"""piptastic CLI — audit (default), list, update."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from packaging.version import Version

from piptastic import __version__
from piptastic.analysis import audit_project
from piptastic.discovery import discover_one, discover_tree
from piptastic.logging import configure_logging, get_logger
from piptastic.models import ProjectAudit, SemverDrift
from piptastic.pypi import PyPIClient
from piptastic.render import render_json, render_terminal
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
    audit.add_argument("--exclude", action="append", default=[], help="Glob pattern, repeatable")
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
    audits = [
        audit_project(p, client, current_python=current_py, include_prereleases=include_pre)
        for p in projects
    ]

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
