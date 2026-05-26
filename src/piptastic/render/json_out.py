"""JSON renderer — stable schema_version=1 contract for CI consumers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from piptastic.models import DepAudit, ProjectAudit, SemverDrift

SCHEMA_VERSION = 1


def render_json(audits: Iterable[ProjectAudit], *, root: Path) -> str:
    """Render a list of ProjectAudits as a JSON string."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "projects": [_project_to_dict(a) for a in audits],
    }
    return json.dumps(payload, indent=2)


def _project_to_dict(pa: ProjectAudit) -> dict:
    p = pa.project
    return {
        "name": p.name,
        "path": str(p.path),
        "python": {
            "version": p.python_version,
            "source": p.python_source,
            "constraints": p.python_constraints,
        },
        "pinning_score": round(pa.pinning_score, 4) if pa.pinning_score is not None else None,
        "drift_summary": {k.value: v for k, v in pa.drift_summary.items()},
        "yanked_count": pa.yanked_count,
        "pypi_unreachable": pa.pypi_unreachable,
        "sources": [
            {"kind": s.kind.value, "path": str(s.path), "group": s.group}
            for s in p.dep_sources
        ],
        "deps": [_dep_to_dict(d) for d in pa.deps],
    }


def _dep_to_dict(da: DepAudit) -> dict:
    dep = da.dep
    current = None
    for clause in dep.specifier:
        if clause.operator == "==":
            current = clause.version.rstrip(".*")
            break
    if current is None and da.installed is not None:
        current = str(da.installed)
    return {
        "name": dep.name,
        "raw_name": dep.raw_name,
        "source_file": str(dep.source.path),
        "group": dep.source.group,
        "specifier": str(dep.specifier),
        "extras": sorted(dep.extras),
        "marker": str(dep.marker) if dep.marker else None,
        "url": dep.url,
        "pin_status": da.pin_status.value,
        "current": current,
        "installed": str(da.installed) if da.installed else None,
        "latest": str(da.latest) if da.latest else None,
        "latest_including_prereleases": (
            str(da.latest_including_prereleases) if da.latest_including_prereleases else None
        ),
        "drift": da.drift.value,
        "yanked": da.yanked,
        "warnings": list(da.warnings),
    }
