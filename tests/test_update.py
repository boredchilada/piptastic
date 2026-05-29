"""Tests for the requirements rewriter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from packaging.version import Version

from piptastic.discovery import discover_one
from piptastic.models import PackageMetadata, ReleaseInfo
from piptastic.update import update_project, _maybe_update_line


def _make_md(name: str, latest: str) -> PackageMetadata:
    from datetime import datetime, timezone
    return PackageMetadata(
        name=name,
        releases=(ReleaseInfo(
            version=Version(latest),
            yanked=False, yanked_reason=None,
            requires_python=None, upload_time=datetime.now(timezone.utc),
        ),),
        fetched_at=datetime.now(timezone.utc),
    )


class _FakePyPI:
    def __init__(self, latests: dict[str, str]):
        self._latests = latests
    def fetch_one(self, name):
        if name in self._latests:
            return _make_md(name, self._latests[name])
        return None
    def fetch_many(self, names):
        return {n: _make_md(n, self._latests[n]) for n in names if n in self._latests}


def _make_req_project(tmp_path: Path, content: str):
    (tmp_path / "requirements.txt").write_text(content, encoding="utf-8")
    return discover_one(tmp_path)


def test_dry_run_writes_nothing(tmp_path):
    """v0.4 P2: --dry-run computes changes without touching disk."""
    body = "flask==2.0.0\nrequests==2.31.0\n"
    project = _make_req_project(tmp_path, body)
    client = _FakePyPI({"flask": "3.0.4", "requests": "2.32.5"})

    results = update_project(
        project,
        client=client,
        test=False,
        apply_cve_floor=False,
        dry_run=True,
    )

    # Did NOT mutate the source file.
    assert (tmp_path / "requirements.txt").read_text(encoding="utf-8") == body
    # No backup directory was created.
    assert not (tmp_path / ".requirements_backups").exists()
    # Changes were still computed.
    r = results[0]
    assert r.backup_file is None
    assert r.tested is False
    assert {c[0] for c in r.changes} == {"flask", "requests"}


def test_dry_run_with_packages_filter(tmp_path):
    body = "flask==2.0.0\nrequests==2.31.0\n"
    project = _make_req_project(tmp_path, body)
    client = _FakePyPI({"flask": "3.0.4", "requests": "2.32.5"})

    results = update_project(
        project, client=client,
        packages=["flask"],
        test=False, apply_cve_floor=False, dry_run=True,
    )
    assert {c[0] for c in results[0].changes} == {"flask"}
    # Source unchanged
    assert "flask==2.0.0" in (tmp_path / "requirements.txt").read_text(encoding="utf-8")


def test_non_dry_run_still_writes(tmp_path):
    """Sanity: dry-run did not break the happy path."""
    body = "flask==2.0.0\n"
    project = _make_req_project(tmp_path, body)
    client = _FakePyPI({"flask": "3.0.4"})

    results = update_project(
        project, client=client,
        test=False, apply_cve_floor=False, dry_run=False,
    )
    assert "flask==3.0.4" in (tmp_path / "requirements.txt").read_text(encoding="utf-8")
    assert results[0].backup_file is not None
    assert results[0].backup_file.exists()
