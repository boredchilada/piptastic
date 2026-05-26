"""Tests for the PyPI client and on-disk cache."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from piptastic.pypi import PyPIClient, _parse_pypi_payload


SAMPLE_PAYLOAD = {
    "info": {"name": "flask"},
    "releases": {
        "3.0.2": [{
            "yanked": False,
            "yanked_reason": None,
            "requires_python": ">=3.8",
            "upload_time_iso_8601": "2024-02-03T00:00:00.000000Z",
        }],
        "3.1.0": [{
            "yanked": False,
            "yanked_reason": None,
            "requires_python": ">=3.9",
            "upload_time_iso_8601": "2024-10-10T00:00:00.000000Z",
        }],
        "3.1.0rc1": [{
            "yanked": False,
            "yanked_reason": None,
            "requires_python": ">=3.9",
            "upload_time_iso_8601": "2024-09-01T00:00:00.000000Z",
        }],
        "3.0.0": [{
            "yanked": True,
            "yanked_reason": "broken",
            "requires_python": ">=3.8",
            "upload_time_iso_8601": "2023-09-30T00:00:00.000000Z",
        }],
    },
}


def test_parse_pypi_payload_normalizes_releases():
    md = _parse_pypi_payload("flask", SAMPLE_PAYLOAD)
    versions = {str(r.version) for r in md.releases}
    assert versions == {"3.0.0", "3.0.2", "3.1.0", "3.1.0rc1"}

    by_version = {str(r.version): r for r in md.releases}
    assert by_version["3.0.0"].yanked is True
    assert by_version["3.0.0"].yanked_reason == "broken"
    assert by_version["3.1.0"].requires_python == SpecifierSet(">=3.9")
    assert by_version["3.1.0"].upload_time is not None


def test_cache_round_trip(tmp_path: Path):
    client = PyPIClient(cache_dir=tmp_path, ttl_seconds=3600)
    md = _parse_pypi_payload("flask", SAMPLE_PAYLOAD)
    client._write_cache("flask", md)
    loaded = client._read_cache("flask")
    assert loaded is not None
    assert {str(r.version) for r in loaded.releases} == {
        "3.0.0", "3.0.2", "3.1.0", "3.1.0rc1"
    }


def test_cache_expiry(tmp_path: Path):
    client = PyPIClient(cache_dir=tmp_path, ttl_seconds=60)
    md = _parse_pypi_payload("flask", SAMPLE_PAYLOAD)
    client._write_cache("flask", md)

    # Pretend the cache is two hours old
    cache_file = client._cache_path("flask")
    old_time = datetime.now(timezone.utc) - timedelta(hours=2)
    os_time = old_time.timestamp()
    import os
    os.utime(cache_file, (os_time, os_time))

    assert client._read_cache("flask") is None


def test_fetch_one_uses_cache(tmp_path: Path):
    client = PyPIClient(cache_dir=tmp_path, ttl_seconds=3600)
    md = _parse_pypi_payload("flask", SAMPLE_PAYLOAD)
    client._write_cache("flask", md)

    with patch.object(client, "_http_get") as mock_http:
        result = client.fetch_one("flask")
        mock_http.assert_not_called()
    assert result is not None


def test_fetch_one_misses_then_caches(tmp_path: Path):
    client = PyPIClient(cache_dir=tmp_path, ttl_seconds=3600)
    with patch.object(client, "_http_get", return_value=SAMPLE_PAYLOAD) as mock_http:
        result1 = client.fetch_one("flask")
        result2 = client.fetch_one("flask")
        # Only one HTTP call; second call hits the cache
        assert mock_http.call_count == 1
    assert result1 is not None and result2 is not None


def test_fetch_many_returns_dict(tmp_path: Path):
    client = PyPIClient(cache_dir=tmp_path, ttl_seconds=3600)
    with patch.object(client, "_http_get", return_value=SAMPLE_PAYLOAD):
        out = client.fetch_many(["flask", "requests"])
    assert set(out.keys()) == {"flask", "requests"}
