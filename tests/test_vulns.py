"""Tests for the pip-audit-backed vulnerability client and cache."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from packaging.version import Version

from piptastic.models import Vulnerability
from piptastic.vulns import (
    VulnClient,
    _parse_pip_audit_payload,
    compute_min_safe_version,
)


SAMPLE_AUDIT_JSON = {
    "dependencies": [
        {
            "name": "flask",
            "version": "2.0.0",
            "vulns": [
                {
                    "id": "PYSEC-2023-62",
                    "aliases": ["CVE-2023-30861", "GHSA-m2qf-hxjv-5gpq"],
                    "fix_versions": ["2.2.5", "2.3.2"],
                    "description": "Flask session cookie issue.",
                }
            ],
        },
        {
            "name": "requests",
            "version": "2.31.0",
            "vulns": [],
        },
    ]
}


def test_parse_pip_audit_payload_extracts_vulns():
    parsed = _parse_pip_audit_payload(SAMPLE_AUDIT_JSON)
    flask_key = ("flask", "2.0.0")
    requests_key = ("requests", "2.31.0")
    assert flask_key in parsed
    assert requests_key in parsed
    assert parsed[requests_key] == ()

    vulns = parsed[flask_key]
    assert len(vulns) == 1
    v = vulns[0]
    assert v.id == "PYSEC-2023-62"
    assert "CVE-2023-30861" in v.aliases
    assert Version("2.2.5") in v.fix_versions
    assert Version("2.3.2") in v.fix_versions


def test_parse_pip_audit_payload_dedupes_by_id_merging_fixes():
    """pip-audit/OSV can list the same advisory id multiple times (one record
    per affected range). They must collapse to one Vulnerability with
    fix_versions and aliases unioned, so vuln_count isn't inflated and
    min_safe stays correct."""
    payload = {
        "dependencies": [{
            "name": "aiohttp", "version": "3.9.1",
            "vulns": [
                {"id": "PYSEC-2024-24", "aliases": ["CVE-2024-1"],
                 "fix_versions": ["3.9.2"], "description": "first"},
                {"id": "PYSEC-2024-24", "aliases": ["GHSA-xxxx"],
                 "fix_versions": ["3.9.4"], "description": ""},
                {"id": "PYSEC-2024-99", "aliases": [],
                 "fix_versions": ["3.10.0"], "description": "other"},
            ],
        }]
    }
    vulns = _parse_pip_audit_payload(payload)[("aiohttp", "3.9.1")]
    assert [v.id for v in vulns] == ["PYSEC-2024-24", "PYSEC-2024-99"]  # deduped, ordered
    dup = next(v for v in vulns if v.id == "PYSEC-2024-24")
    assert Version("3.9.2") in dup.fix_versions and Version("3.9.4") in dup.fix_versions
    assert "CVE-2024-1" in dup.aliases and "GHSA-xxxx" in dup.aliases  # aliases unioned
    assert dup.description == "first"  # first non-empty description kept


def test_read_cache_dedupes_stale_duplicate_ids(tmp_path: Path):
    """Caches written before the dedup fix may hold duplicate ids; they must be
    deduped on read so old caches don't keep inflating counts (no --refresh)."""
    import json
    client = VulnClient(cache_dir=tmp_path, ttl_seconds=3600)
    key = ("aiohttp", "3.9.1")
    path = client._cache_path(key)
    path.write_text(json.dumps({
        "name": key[0], "version": key[1], "fetched_at": "2026-01-01T00:00:00+00:00",
        "vulnerabilities": [
            {"id": "PYSEC-2024-24", "aliases": [], "fix_versions": ["3.9.2"], "description": ""},
            {"id": "PYSEC-2024-24", "aliases": [], "fix_versions": ["3.9.4"], "description": ""},
        ],
    }), encoding="utf-8")
    loaded = client._read_cache(key)
    assert [v.id for v in loaded] == ["PYSEC-2024-24"]
    assert Version("3.9.2") in loaded[0].fix_versions
    assert Version("3.9.4") in loaded[0].fix_versions


def test_compute_min_safe_version_picks_lowest_higher_fix():
    v = Vulnerability(
        id="X",
        aliases=(),
        fix_versions=(Version("2.2.5"), Version("2.3.2")),
        description="",
    )
    assert compute_min_safe_version(Version("2.0.0"), [v]) == Version("2.2.5")


def test_compute_min_safe_version_no_higher_fix():
    v = Vulnerability(
        id="X",
        aliases=(),
        fix_versions=(Version("1.0.0"),),
        description="",
    )
    assert compute_min_safe_version(Version("2.0.0"), [v]) is None


def test_compute_min_safe_version_no_vulns():
    assert compute_min_safe_version(Version("2.0.0"), []) is None


def test_compute_min_safe_version_multiple_advisories_takes_max():
    # Two advisories: lowest-fix-above-installed differs between them.
    # min_safe must satisfy *every* advisory, so it's the max of the per-advisory mins.
    v1 = Vulnerability(id="A", aliases=(), fix_versions=(Version("2.0.5"),), description="")
    v2 = Vulnerability(id="B", aliases=(), fix_versions=(Version("2.1.0"),), description="")
    assert compute_min_safe_version(Version("2.0.0"), [v1, v2]) == Version("2.1.0")


def test_cache_round_trip(tmp_path: Path):
    client = VulnClient(cache_dir=tmp_path, ttl_seconds=3600)
    key = ("flask", "2.0.0")
    vulns = (
        Vulnerability(
            id="PYSEC-2023-62",
            aliases=("CVE-2023-30861",),
            fix_versions=(Version("2.2.5"),),
            description="x",
        ),
    )
    client._write_cache(key, vulns)
    loaded = client._read_cache(key)
    assert loaded is not None
    assert len(loaded) == 1
    assert loaded[0].id == "PYSEC-2023-62"
    assert loaded[0].fix_versions == (Version("2.2.5"),)


def test_cache_expiry(tmp_path: Path):
    client = VulnClient(cache_dir=tmp_path, ttl_seconds=60)
    key = ("flask", "2.0.0")
    client._write_cache(key, ())
    cache_file = client._cache_path(key)
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    os.utime(cache_file, (old.timestamp(), old.timestamp()))
    assert client._read_cache(key) is None


def test_fetch_for_uses_cache(tmp_path: Path):
    client = VulnClient(cache_dir=tmp_path, ttl_seconds=3600)
    key = ("flask", "2.0.0")
    client._write_cache(key, ())

    with patch.object(client, "_run_pip_audit") as mock_run:
        result = client.fetch_for([("flask", Version("2.0.0"))])
        mock_run.assert_not_called()
    assert result[key] == ()


def test_fetch_for_miss_then_caches(tmp_path: Path):
    client = VulnClient(cache_dir=tmp_path, ttl_seconds=3600)
    with patch.object(client, "_run_pip_audit", return_value=SAMPLE_AUDIT_JSON) as mock_run:
        r1 = client.fetch_for([
            ("flask", Version("2.0.0")),
            ("requests", Version("2.31.0")),
        ])
        # Second call must come from cache.
        r2 = client.fetch_for([
            ("flask", Version("2.0.0")),
            ("requests", Version("2.31.0")),
        ])
        assert mock_run.call_count == 1
    assert r1[("flask", "2.0.0")][0].id == "PYSEC-2023-62"
    assert r2[("flask", "2.0.0")][0].id == "PYSEC-2023-62"
    assert r1[("requests", "2.31.0")] == ()


def test_fetch_for_missing_pip_audit(tmp_path: Path):
    client = VulnClient(
        cache_dir=tmp_path, ttl_seconds=3600,
        pip_audit_cmd="definitely-not-on-path-12345",
    )
    out = client.fetch_for([("flask", Version("2.0.0"))])
    # Returns nothing for unknown packages and records unreachable.
    assert out == {}
    assert "flask" in client.unreachable
