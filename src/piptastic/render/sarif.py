# SPDX-License-Identifier: AGPL-3.0-or-later
"""SARIF 2.1.0 renderer for upload to GitHub Code Scanning (or any SARIF
consumer). Emits one rule per distinct CVE seen and one result per
(dep, vulnerability) pair. Suppressed CVEs are emitted with
suppressionStates so they show as accepted-risk findings, not silently
omitted.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from piptastic import __version__
from piptastic.models import DepAudit, ProjectAudit, Vulnerability

SARIF_SCHEMA = "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json"
SARIF_VERSION = "2.1.0"
TOOL_URI = "https://github.com/boredchilada/piptastic"


def render_sarif(audits: Iterable[ProjectAudit], *, root: Path) -> str:
    audits = list(audits)
    rules: dict[str, dict] = {}
    results: list[dict] = []

    for pa in audits:
        # Per-project "vuln_unreachable" packages become note-level results
        # so operators can see the coverage gap in GH Security.
        for name in pa.vuln_unreachable:
            results.append({
                "ruleId": "piptastic.unreachable",
                "level": "note",
                "message": {"text": f"piptastic could not determine the vulnerability status of {name} (pip-audit failed for this package)."},
                "locations": [_loc_for_project(pa, root)],
            })

        for dep in pa.deps:
            for vuln in dep.vulnerabilities:
                rules.setdefault(vuln.id, _rule_from_vuln(vuln))
                results.append(_result_from(dep, vuln, pa, root))

    # Always include the "unreachable" rule when used.
    if any(r["ruleId"] == "piptastic.unreachable" for r in results):
        rules.setdefault("piptastic.unreachable", {
            "id": "piptastic.unreachable",
            "name": "VulnerabilityStatusUnknown",
            "shortDescription": {"text": "Vulnerability status unknown"},
            "fullDescription": {"text": "pip-audit could not return a vulnerability status for this package. The dependency may or may not be affected by known advisories."},
            "defaultConfiguration": {"level": "note"},
        })

    sarif = {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [{
            "tool": {
                "driver": {
                    "name": "piptastic",
                    "version": __version__,
                    "informationUri": TOOL_URI,
                    "rules": list(rules.values()),
                }
            },
            "results": results,
        }],
    }
    return json.dumps(sarif, indent=2)


def _rule_from_vuln(v: Vulnerability) -> dict:
    return {
        "id": v.id,
        "name": v.id,
        "shortDescription": {"text": v.id + (f" ({v.aliases[0]})" if v.aliases else "")},
        "fullDescription": {"text": v.description or v.id},
        "helpUri": _help_uri_for(v.id, v.aliases),
        "defaultConfiguration": {"level": "error" if v.fix_versions else "warning"},
    }


def _help_uri_for(vid: str, aliases: tuple) -> str:
    """Return a sensible help URL for an advisory id."""
    for candidate in (vid,) + tuple(aliases):
        if candidate.startswith("GHSA-"):
            return f"https://github.com/advisories/{candidate}"
        if candidate.startswith("CVE-"):
            return f"https://www.cve.org/CVERecord?id={candidate}"
        if candidate.startswith("PYSEC-"):
            return f"https://osv.dev/vulnerability/{candidate}"
    return TOOL_URI


def _result_from(
    dep: DepAudit, vuln: Vulnerability, pa: ProjectAudit, root: Path,
) -> dict:
    current = _current_version_str(dep)
    pkg = dep.dep.name
    msg = f"{pkg}=={current} is affected by {vuln.id}"
    if vuln.aliases:
        msg += f" ({', '.join(vuln.aliases[:3])})"
    if dep.min_safe_version:
        msg += f"; bump to {dep.min_safe_version}"
    elif vuln.fix_versions:
        msg += f"; known fix versions: {', '.join(str(f) for f in vuln.fix_versions)}"
    else:
        msg += " (no fix version known)"

    result = {
        "ruleId": vuln.id,
        "level": "error" if (vuln.fix_versions and not vuln.suppressed) else "warning",
        "message": {"text": msg},
        "locations": [_loc_for_dep(dep, root)],
    }
    # Suggested fix block — only when we have a concrete safe version.
    if dep.min_safe_version and not vuln.suppressed:
        result["fixes"] = [{
            "description": {"text": f"Bump {pkg} to >= {dep.min_safe_version}"},
        }]
    if vuln.suppressed:
        # SARIF 2.1.0 suppressions for accepted-risk advisories.
        result["suppressions"] = [{
            "kind": "external",
            "justification": vuln.suppression_reason or "accepted via [tool.piptastic.suppressions]",
        }]
    return result


def _loc_for_dep(dep: DepAudit, root: Path) -> dict:
    return {
        "physicalLocation": {
            "artifactLocation": {"uri": _rel_uri(dep.dep.source.path, root)},
            **({"region": {"startLine": dep.dep.line_no}} if dep.dep.line_no else {}),
        }
    }


def _loc_for_project(pa: ProjectAudit, root: Path) -> dict:
    """Used for tool-level results (unreachable) that don't bind to a
    specific line."""
    if pa.project.dep_sources:
        return {
            "physicalLocation": {
                "artifactLocation": {"uri": _rel_uri(pa.project.dep_sources[0].path, root)},
            }
        }
    return {"physicalLocation": {"artifactLocation": {"uri": _rel_uri(pa.project.path, root)}}}


def _rel_uri(path: Path, root: Path) -> str:
    """SARIF wants forward-slash relative paths. Falls back to absolute
    posix path when `path` isn't under `root` (e.g. tree audits)."""
    try:
        rel = path.resolve().relative_to(root.resolve())
        return rel.as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _current_version_str(dep: DepAudit) -> str:
    for clause in dep.dep.specifier:
        if clause.operator == "==":
            return clause.version
    return str(dep.installed) if dep.installed else "?"
