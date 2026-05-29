# SPDX-License-Identifier: AGPL-3.0-or-later
"""Mutate a requirements.txt to pin packages to their latest compatible version.

Only invoked from the `piptastic update` subcommand. Audit is read-only.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import venv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from packaging.utils import canonicalize_name
from packaging.version import Version

from piptastic.logging import get_logger
from piptastic.models import Project, SourceKind
from piptastic.pypi import PyPIClient
from piptastic.suppressions import find_rule, load_suppressions
from piptastic.vulns import VulnClient, compute_min_safe_version

logger = get_logger(__name__)


@dataclass
class UpdateResult:
    requirements_file: Path
    backup_file: Path | None
    # (name, old, new) or (name, old, new, note) when a CVE drove the bump.
    changes: list[tuple]
    tested: bool
    test_passed: bool


def update_project(
    project: Project,
    *,
    packages: Iterable[str] | None = None,
    test: bool = True,
    refresh: bool = False,
    use_temp_test_env: bool = False,
    client: PyPIClient | None = None,
    vuln_client: VulnClient | None = None,
    apply_cve_floor: bool = True,
    dry_run: bool = False,
) -> list[UpdateResult]:
    """Update each requirements*.txt source in `project`.

    pyproject.toml and Pipfile updates are NOT supported — these sources
    are skipped with an info-level message.

    When `dry_run=True`, computes would-be changes (including the CVE
    floor lookup) but writes nothing, creates no backup, and skips the
    test install. The returned `UpdateResult.backup_file` is None and
    `tested` is False.
    """
    client = client or PyPIClient(ttl_seconds=0 if refresh else 3600)
    if apply_cve_floor and vuln_client is None:
        vuln_client = VulnClient(ttl_seconds=0 if refresh else 3600)
    only = {canonicalize_name(p) for p in packages} if packages else None

    # Load suppressions once for this project — passed through so the CVE
    # floor doesn't lift over an advisory the project has already accepted.
    suppression_rules = load_suppressions(project.path) if apply_cve_floor else []

    results: list[UpdateResult] = []
    for src in project.dep_sources:
        if src.kind not in (SourceKind.REQUIREMENTS_TXT, SourceKind.CONSTRAINTS_TXT):
            logger.info("update: skipping %s (only requirements*.txt is writeable)", src.path)
            continue
        results.append(_update_one_file(
            src.path, only, client, test, use_temp_test_env, project.path,
            vuln_client if apply_cve_floor else None,
            suppression_rules=suppression_rules,
            dry_run=dry_run,
        ))
    return results


def _update_one_file(
    req_path: Path,
    only: set[str] | None,
    client: PyPIClient,
    test: bool,
    use_temp_test_env: bool,
    project_root: Path,
    vuln_client: VulnClient | None,
    *,
    suppression_rules: Iterable = (),
    dry_run: bool = False,
) -> UpdateResult:
    lines = req_path.read_text(encoding="utf-8").splitlines()
    changes: list[tuple] = []

    new_lines = []
    for line in lines:
        new_line, change = _maybe_update_line(
            line, only, client, vuln_client,
            suppression_rules=list(suppression_rules),
        )
        new_lines.append(new_line)
        if change is not None:
            changes.append(change)

    if dry_run:
        # Preview only — no backup, no write, no test install.
        return UpdateResult(
            requirements_file=req_path,
            backup_file=None,
            changes=changes,
            tested=False,
            test_passed=True,
        )

    backup = _create_backup(req_path, project_root)
    # Preserve trailing newline conventions
    new_text = "\n".join(new_lines)
    if not new_text.endswith("\n"):
        new_text += "\n"
    req_path.write_text(new_text, encoding="utf-8")

    tested = False
    test_passed = True
    if test and changes:
        tested = True
        test_passed = _test_install(req_path, project_root, use_temp_test_env)
        if not test_passed:
            logger.warning("test install failed; restoring backup")
            shutil.copy2(backup, req_path)

    return UpdateResult(
        requirements_file=req_path,
        backup_file=backup,
        changes=changes,
        tested=tested,
        test_passed=test_passed,
    )


_LINE_RE = re.compile(
    r"^([a-zA-Z0-9][a-zA-Z0-9_.\-]*)(\[[^\]]+\])?(==|~=|>=|<=|>|<|!=|===)?([^;\s]+)?(.*)$"
)


def _maybe_update_line(
    line: str,
    only: set[str] | None,
    client: PyPIClient,
    vuln_client: VulnClient | None = None,
    *,
    suppression_rules: list = None,
) -> tuple[str, tuple | None]:
    suppression_rules = suppression_rules or []
    stripped = line.strip()
    if not stripped or stripped.startswith(("#", "-", "@")):
        return line, None

    m = _LINE_RE.match(stripped)
    if not m:
        return line, None
    name, extras, op, ver, tail = m.groups()
    canon = canonicalize_name(name)
    if only is not None and canon not in only:
        return line, None
    # Only touch == pins; never override range/floor/url/unpinned semantics
    if op != "==":
        return line, None

    md = client.fetch_one(canon)
    if md is None:
        return line, None
    latest = max(
        (r.version for r in md.releases if not r.yanked and not r.version.is_prerelease),
        default=None,
    )
    if latest is None:
        return line, None

    note = ""
    target = latest

    # CVE floor: if the current pin has known vulnerabilities, force the
    # target up to min_safe_version when one exists.
    if vuln_client is not None and ver:
        try:
            current_v = Version(ver)
        except Exception:
            current_v = None
        if current_v is not None:
            results = vuln_client.fetch_for([(canon, current_v)])
            all_vulns = results.get((canon, str(current_v)), ())
            # Apply suppressions: accepted-risk CVEs do not drive a bump.
            active_vulns = tuple(
                v for v in all_vulns
                if find_rule(suppression_rules, package=canon, vuln=v) is None
            )
            if active_vulns:
                min_safe = compute_min_safe_version(current_v, active_vulns)
                ids = ", ".join(v.id for v in active_vulns[:2])
                if len(active_vulns) > 2:
                    ids += f", +{len(active_vulns) - 2}"
                if min_safe is not None and min_safe > latest:
                    target = min_safe
                    note = f"CVE floor: {ids}"
                elif min_safe is not None:
                    note = f"CVE: {ids}"
                else:
                    note = f"CVE (no fix known): {ids}"

    if str(target) == ver:
        return line, None

    extras_str = extras or ""
    tail_str = tail or ""
    new = f"{name}{extras_str}=={target}{tail_str}"
    leading_ws = line[:len(line) - len(line.lstrip())]
    change: tuple = (canon, ver or "", str(target))
    if note:
        change = (canon, ver or "", str(target), note)
    return f"{leading_ws}{new}", change


def _create_backup(req_path: Path, project_root: Path) -> Path:
    backup_dir = project_root / ".requirements_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    content = req_path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()[:8]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = backup_dir / f"{req_path.stem}_{ts}_{digest}.txt"
    shutil.copy2(req_path, dest)
    return dest


def _test_install(req_path: Path, project_root: Path, use_temp: bool) -> bool:
    """Create a throwaway venv, install -r req_path, return True on success."""
    if use_temp:
        ctx_dir = Path(tempfile.mkdtemp(prefix="piptastic_test_"))
    else:
        ctx_dir = project_root / f".piptastic_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        ctx_dir.mkdir(parents=True, exist_ok=True)
    try:
        venv.create(ctx_dir, with_pip=True)
        python_path = (
            ctx_dir / "Scripts" / "python.exe"
            if sys.platform == "win32"
            else ctx_dir / "bin" / "python"
        )
        # Upgrade pip; surface any failure
        r = subprocess.run(
            [str(python_path), "-m", "pip", "install", "--upgrade", "pip"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            logger.error("pip upgrade failed: %s", r.stderr)
            return False
        r = subprocess.run(
            [str(python_path), "-m", "pip", "install", "-r", str(req_path)],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            logger.error("pip install failed: %s", r.stderr)
            return False
        return True
    finally:
        # Always clean up — fixes [C7]
        shutil.rmtree(ctx_dir, ignore_errors=True)
