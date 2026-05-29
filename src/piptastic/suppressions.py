# SPDX-License-Identifier: AGPL-3.0-or-later
"""CVE suppression rules — accepted-risk allowlist per project.

Loaded from a project's `pyproject.toml` under `[tool.piptastic]` /
`[[tool.piptastic.suppressions]]`, or from a sibling `.piptastic.toml`
when no pyproject is present. Each rule is `(package, cve, reason,
expires)`; all four fields are required. Past-expiry rules are
ignored and logged so they don't rot silently.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

from packaging.utils import canonicalize_name

from piptastic.logging import get_logger
from piptastic.models import Vulnerability

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10 fallback (declared in pyproject)
    import tomli as tomllib  # type: ignore[no-redef]

logger = get_logger(__name__)

# Wildcard token for tree-wide suppressions.
WILDCARD = "*"


@dataclass(frozen=True)
class SuppressionRule:
    package: str  # canonical name, or WILDCARD
    cve: str  # exact id (CVE-..., GHSA-..., PYSEC-...) — also matched against aliases
    reason: str
    expires: date

    def matches(self, package: str, cve_id: str, aliases: Iterable[str] = ()) -> bool:
        if self.package != WILDCARD and canonicalize_name(self.package) != canonicalize_name(package):
            return False
        if self.cve == cve_id:
            return True
        for alias in aliases:
            if self.cve == alias:
                return True
        return False


def load_suppressions(project_path: Path) -> list[SuppressionRule]:
    """Read suppressions for a project. Returns [] when no config is present.

    Looks first at `<project>/pyproject.toml` under `[tool.piptastic]`, then
    falls back to `<project>/.piptastic.toml` (root-level table). Parsing
    failures and invalid rules are logged as warnings and skipped — they
    must never break the audit.
    """
    candidates: list[tuple[Path, str]] = [
        (project_path / "pyproject.toml", "tool.piptastic"),
        (project_path / ".piptastic.toml", ""),
    ]
    for path, table_path in candidates:
        if not path.is_file():
            continue
        try:
            with path.open("rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError) as e:
            logger.warning("suppressions: failed to parse %s: %s", path, e)
            continue

        block = data
        if table_path:
            for key in table_path.split("."):
                block = block.get(key) if isinstance(block, dict) else None
                if block is None:
                    break
        if not isinstance(block, dict):
            continue
        raw_rules = block.get("suppressions")
        if not isinstance(raw_rules, list):
            continue
        return _parse_rules(raw_rules, source=str(path))
    return []


def _parse_rules(raw_rules: list, *, source: str) -> list[SuppressionRule]:
    rules: list[SuppressionRule] = []
    today = date.today()
    for i, r in enumerate(raw_rules):
        if not isinstance(r, dict):
            logger.warning("suppressions: entry %d in %s is not a table; ignored", i, source)
            continue
        package = r.get("package")
        cve = r.get("cve")
        reason = r.get("reason")
        expires_raw = r.get("expires")
        missing = [k for k, v in (("package", package), ("cve", cve), ("reason", reason), ("expires", expires_raw)) if not v]
        if missing:
            logger.warning(
                "suppressions: entry %d in %s missing required fields %s; ignored",
                i, source, ", ".join(missing),
            )
            continue
        try:
            expires = _coerce_date(expires_raw)
        except ValueError as e:
            logger.warning(
                "suppressions: entry %d in %s has invalid expires=%r: %s",
                i, source, expires_raw, e,
            )
            continue
        if expires < today:
            logger.warning(
                "suppressions: rule %s/%s in %s is past expiry (%s); ignored — remove or refresh",
                package, cve, source, expires.isoformat(),
            )
            continue
        rules.append(SuppressionRule(
            package=package if package == WILDCARD else canonicalize_name(package),
            cve=cve,
            reason=reason,
            expires=expires,
        ))
    return rules


def _coerce_date(v) -> date:
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        # ISO 8601 YYYY-MM-DD
        return date.fromisoformat(v)
    raise ValueError(f"expected ISO date string or date value, got {type(v).__name__}")


def find_rule(
    rules: Iterable[SuppressionRule],
    *,
    package: str,
    vuln: Vulnerability,
) -> SuppressionRule | None:
    """Return the first rule matching this (package, vulnerability), or None."""
    for rule in rules:
        if rule.matches(package, vuln.id, vuln.aliases):
            return rule
    return None
