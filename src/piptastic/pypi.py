"""PyPI metadata client with on-disk TTL cache and thread-pool concurrency."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from piptastic.logging import get_logger
from piptastic.models import PackageMetadata, ReleaseInfo

logger = get_logger(__name__)


def _default_cache_dir() -> Path:
    base = os.environ.get("PIPTASTIC_CACHE_DIR")
    if base:
        return Path(base)
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "piptastic" / "pypi"
    return Path.home() / ".cache" / "piptastic" / "pypi"


class PyPIClient:
    def __init__(
        self,
        *,
        cache_dir: Path | None = None,
        ttl_seconds: int = 3600,
        timeout: float = 10.0,
        concurrency: int = 8,
        base_url: str = "https://pypi.org/pypi",
    ) -> None:
        self.cache_dir = (cache_dir or _default_cache_dir()).resolve()
        self.ttl_seconds = ttl_seconds
        self.timeout = timeout
        self.concurrency = concurrency
        self.base_url = base_url.rstrip("/")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ---------- public ----------

    def fetch_one(self, name: str) -> PackageMetadata | None:
        name = canonicalize_name(name)
        cached = self._read_cache(name)
        if cached is not None:
            return cached

        try:
            payload = self._http_get(name)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            logger.warning("PyPI fetch failed for %s: %s", name, e)
            return None

        md = _parse_pypi_payload(name, payload)
        self._write_cache(name, md)
        return md

    def fetch_many(self, names: Iterable[str]) -> dict[str, PackageMetadata]:
        names = list({canonicalize_name(n) for n in names})
        out: dict[str, PackageMetadata] = {}
        with ThreadPoolExecutor(max_workers=self.concurrency) as ex:
            futures = {ex.submit(self.fetch_one, n): n for n in names}
            for fut in as_completed(futures):
                n = futures[fut]
                try:
                    md = fut.result()
                except Exception as e:
                    logger.warning("PyPI worker error for %s: %s", n, e)
                    continue
                if md is not None:
                    out[n] = md
        return out

    # ---------- HTTP ----------

    def _http_get(self, name: str) -> dict[str, Any]:
        url = f"{self.base_url}/{name}/json"
        with urllib.request.urlopen(url, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # ---------- cache ----------

    def _cache_path(self, name: str) -> Path:
        bucket = name[:2] if len(name) >= 2 else name + "_"
        d = self.cache_dir / bucket
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{name}.json"

    def _read_cache(self, name: str) -> PackageMetadata | None:
        path = self._cache_path(name)
        if not path.exists():
            return None
        age = datetime.now(timezone.utc).timestamp() - path.stat().st_mtime
        if age > self.ttl_seconds:
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
        return _rehydrate_metadata(raw)

    def _write_cache(self, name: str, md: PackageMetadata) -> None:
        path = self._cache_path(name)
        with path.open("w", encoding="utf-8") as f:
            json.dump(_dehydrate_metadata(md), f)


# ---------- parsers ----------

def _parse_pypi_payload(name: str, payload: dict[str, Any]) -> PackageMetadata:
    releases_in = payload.get("releases") or {}
    releases: list[ReleaseInfo] = []
    for ver_str, files in releases_in.items():
        try:
            version = Version(ver_str)
        except InvalidVersion:
            continue
        if not files:
            # No artifacts uploaded — treat as missing
            continue

        # Use the first file's metadata as representative
        first = files[0]
        yanked = bool(first.get("yanked", False))
        yanked_reason = first.get("yanked_reason")

        rp = first.get("requires_python")
        requires_python: SpecifierSet | None = None
        if rp:
            try:
                requires_python = SpecifierSet(rp)
            except InvalidSpecifier:
                requires_python = None

        upload_iso = first.get("upload_time_iso_8601") or first.get("upload_time")
        upload_time: datetime | None = None
        if upload_iso:
            try:
                upload_time = datetime.fromisoformat(upload_iso.replace("Z", "+00:00"))
            except ValueError:
                upload_time = None

        releases.append(ReleaseInfo(
            version=version,
            yanked=yanked,
            yanked_reason=yanked_reason,
            requires_python=requires_python,
            upload_time=upload_time,
        ))

    return PackageMetadata(
        name=canonicalize_name(name),
        releases=tuple(releases),
        fetched_at=datetime.now(timezone.utc),
    )


def _dehydrate_metadata(md: PackageMetadata) -> dict[str, Any]:
    return {
        "name": md.name,
        "fetched_at": md.fetched_at.isoformat(),
        "releases": [
            {
                "version": str(r.version),
                "yanked": r.yanked,
                "yanked_reason": r.yanked_reason,
                "requires_python": str(r.requires_python) if r.requires_python else None,
                "upload_time": r.upload_time.isoformat() if r.upload_time else None,
            }
            for r in md.releases
        ],
    }


def _rehydrate_metadata(raw: dict[str, Any]) -> PackageMetadata:
    releases: list[ReleaseInfo] = []
    for r in raw.get("releases", []):
        try:
            version = Version(r["version"])
        except (InvalidVersion, KeyError):
            continue
        rp_raw = r.get("requires_python")
        requires_python = None
        if rp_raw:
            try:
                requires_python = SpecifierSet(rp_raw)
            except InvalidSpecifier:
                requires_python = None
        ut_raw = r.get("upload_time")
        upload_time = None
        if ut_raw:
            try:
                upload_time = datetime.fromisoformat(ut_raw)
            except ValueError:
                upload_time = None
        releases.append(ReleaseInfo(
            version=version,
            yanked=bool(r.get("yanked", False)),
            yanked_reason=r.get("yanked_reason"),
            requires_python=requires_python,
            upload_time=upload_time,
        ))
    return PackageMetadata(
        name=raw["name"],
        releases=tuple(releases),
        fetched_at=datetime.fromisoformat(raw["fetched_at"]),
    )
