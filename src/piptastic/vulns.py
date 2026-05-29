# SPDX-License-Identifier: AGPL-3.0-or-later
"""Vulnerability lookup via pip-audit, with per-(name, version) on-disk cache.

Mirrors the shape of `piptastic.pypi.PyPIClient`: same cache_dir / ttl_seconds
construction, same fetch_one / fetch_for entry points, same swallow-and-log
error policy. The transport is a `pip-audit` subprocess invocation rather than
a direct HTTP call — pip-audit is the documented source of truth per project
policy.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from piptastic.logging import get_logger
from piptastic.models import Vulnerability

logger = get_logger(__name__)


def _default_cache_dir() -> Path:
    base = os.environ.get("PIPTASTIC_CACHE_DIR")
    if base:
        return Path(base) / "vulns"
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "piptastic" / "vulns"
    return Path.home() / ".cache" / "piptastic" / "vulns"


class VulnClient:
    def __init__(
        self,
        *,
        cache_dir: Path | None = None,
        ttl_seconds: int = 3600,
        timeout: float = 60.0,
        concurrency: int = 8,
        pip_audit_cmd: list[str] | str | None = None,
    ) -> None:
        self.cache_dir = (cache_dir or _default_cache_dir()).resolve()
        self.ttl_seconds = ttl_seconds
        self.timeout = timeout
        self.concurrency = concurrency  # unused; kept symmetric with PyPIClient
        # Default to `python -m pip_audit` so we don't depend on the script
        # entry point being on PATH (it isn't, by default, on Windows).
        if pip_audit_cmd is None:
            self.pip_audit_cmd: list[str] = [sys.executable, "-m", "pip_audit"]
        elif isinstance(pip_audit_cmd, str):
            self.pip_audit_cmd = [pip_audit_cmd]
        else:
            self.pip_audit_cmd = list(pip_audit_cmd)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Names we tried to resolve but pip-audit could not report on.
        self.unreachable: list[str] = []

    # ---------- public ----------

    def fetch_for(
        self, pkgs: Iterable[tuple[str, Version]]
    ) -> dict[tuple[str, str], tuple[Vulnerability, ...]]:
        """Look up vulnerabilities for each (name, version) pair.

        Returns a dict keyed by `(canonical_name, str(version))`. Packages with
        no known advisories map to an empty tuple (and that empty result is
        cached, so subsequent runs are cheap).
        """
        pairs = []
        for name, version in pkgs:
            canon = canonicalize_name(name)
            ver_str = str(version)
            pairs.append((canon, ver_str))
        # De-dup
        pairs = list(dict.fromkeys(pairs))

        out: dict[tuple[str, str], tuple[Vulnerability, ...]] = {}
        misses: list[tuple[str, str]] = []
        for key in pairs:
            cached = self._read_cache(key)
            if cached is not None:
                out[key] = cached
            else:
                misses.append(key)

        if misses:
            fetched = self._run_pip_audit_for(misses)
            if fetched is None:
                # Subprocess failed; do NOT cache and do NOT fabricate clean
                # results. Mark each missing pkg as unreachable so callers can
                # surface that the status is unknown.
                for name, _ver in misses:
                    if name not in self.unreachable:
                        self.unreachable.append(name)
            else:
                for key in misses:
                    vulns = fetched.get(key, ())
                    out[key] = vulns
                    self._write_cache(key, vulns)

        return out

    # ---------- subprocess ----------

    def _run_pip_audit_for(
        self, pairs: list[tuple[str, str]]
    ) -> dict[tuple[str, str], tuple[Vulnerability, ...]] | None:
        """Return the full mapping for `pairs` on success, or None on failure.

        pip-audit refuses requirement files with duplicate package names even
        when the versions differ, so we split into multiple invocations: each
        batch contains at most one pin per package name. Failure of any single
        batch is treated as overall failure (returns None).
        """
        batches = _chunk_unique_by_name(pairs)
        combined: dict[tuple[str, str], tuple[Vulnerability, ...]] = {}
        for batch in batches:
            payload = self._invoke_pip_audit_once(batch)
            if payload is None:
                return None
            combined.update(_parse_pip_audit_payload(payload))
        # Ensure every requested pair is present (pip-audit may omit clean
        # packages); fill defaults so callers don't mistake "no entry" for
        # "unreachable".
        for key in pairs:
            combined.setdefault(key, ())
        return combined

    def _invoke_pip_audit_once(
        self, pairs: list[tuple[str, str]]
    ) -> dict[str, Any] | None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as fh:
            tmp_path = Path(fh.name)
            for name, ver in pairs:
                fh.write(f"{name}=={ver}\n")
        try:
            try:
                return self._run_pip_audit(tmp_path)
            except (subprocess.SubprocessError, OSError, json.JSONDecodeError, FileNotFoundError) as e:
                logger.warning("pip-audit invocation failed: %s", e)
                return None
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

    def _run_pip_audit(self, req_file: Path) -> dict[str, Any]:
        cmd = [
            *self.pip_audit_cmd,
            "--requirement", str(req_file),
            "--format", "json",
            "--disable-pip",
            "--progress-spinner", "off",
            "--no-deps",
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )
        # pip-audit exits 1 when vulnerabilities are present — that's expected.
        # Only exit codes != 0 and != 1 indicate a real failure. But the JSON
        # is on stdout in both cases.
        if proc.returncode not in (0, 1):
            logger.warning(
                "pip-audit exited %d: %s",
                proc.returncode,
                (proc.stderr or "").strip()[:500],
            )
            raise subprocess.SubprocessError(f"pip-audit exit {proc.returncode}")
        if not proc.stdout.strip():
            raise json.JSONDecodeError("empty stdout from pip-audit", "", 0)
        return json.loads(proc.stdout)

    # ---------- cache ----------

    def _cache_path(self, key: tuple[str, str]) -> Path:
        digest = hashlib.sha1(f"{key[0]}|{key[1]}".encode("utf-8")).hexdigest()
        bucket = digest[:2]
        d = self.cache_dir / bucket
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{digest}.json"

    def _read_cache(self, key: tuple[str, str]) -> tuple[Vulnerability, ...] | None:
        path = self._cache_path(key)
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
        return _rehydrate_vulns(raw.get("vulnerabilities", []))

    def _write_cache(self, key: tuple[str, str], vulns: tuple[Vulnerability, ...]) -> None:
        path = self._cache_path(key)
        payload = {
            "name": key[0],
            "version": key[1],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "vulnerabilities": [_dehydrate_vuln(v) for v in vulns],
        }
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f)


# ---------- chunking ----------

def _chunk_unique_by_name(
    pairs: list[tuple[str, str]],
) -> list[list[tuple[str, str]]]:
    """Split `pairs` into batches such that each batch has at most one pin per
    package name. Required because pip-audit rejects requirements files with
    duplicate package names even when versions differ.
    """
    remaining: dict[str, list[str]] = {}
    for name, ver in pairs:
        remaining.setdefault(name, []).append(ver)
    batches: list[list[tuple[str, str]]] = []
    while remaining:
        batch: list[tuple[str, str]] = []
        empties: list[str] = []
        for name, versions in remaining.items():
            batch.append((name, versions[0]))
            del versions[0]
            if not versions:
                empties.append(name)
        for name in empties:
            del remaining[name]
        batches.append(batch)
    return batches


# ---------- parsers ----------

def _parse_pip_audit_payload(
    payload: dict[str, Any],
) -> dict[tuple[str, str], tuple[Vulnerability, ...]]:
    out: dict[tuple[str, str], tuple[Vulnerability, ...]] = {}
    deps = payload.get("dependencies") or []
    for entry in deps:
        name = canonicalize_name(entry.get("name", ""))
        ver = entry.get("version", "")
        if not name or not ver:
            continue
        vulns_raw = entry.get("vulns") or []
        vulns: list[Vulnerability] = []
        for v in vulns_raw:
            vid = v.get("id", "")
            if not vid:
                continue
            aliases = tuple(v.get("aliases") or ())
            fixes: list[Version] = []
            for f in v.get("fix_versions") or []:
                try:
                    fixes.append(Version(f))
                except InvalidVersion:
                    continue
            vulns.append(Vulnerability(
                id=vid,
                aliases=aliases,
                fix_versions=tuple(sorted(fixes)),
                description=v.get("description", "") or "",
            ))
        out[(name, ver)] = _dedupe_vulns(vulns)
    return out


def _dedupe_vulns(vulns: Iterable[Vulnerability]) -> tuple[Vulnerability, ...]:
    """Collapse advisories that share an `id` into one entry, unioning their
    fix_versions and aliases (the first non-empty description wins).

    pip-audit / OSV can emit the same advisory id multiple times for a single
    package — typically one record per affected version range. Left as-is those
    duplicates inflate vuln_count, the tree-wide CVE total, SARIF results, and
    the `update` CVE notes. Order is preserved (first occurrence wins its slot).
    """
    by_id: dict[str, Vulnerability] = {}
    for v in vulns:
        prev = by_id.get(v.id)
        if prev is None:
            by_id[v.id] = v
            continue
        by_id[v.id] = Vulnerability(
            id=v.id,
            aliases=tuple(dict.fromkeys(prev.aliases + v.aliases)),
            fix_versions=tuple(sorted(set(prev.fix_versions) | set(v.fix_versions))),
            description=prev.description or v.description,
            suppressed=prev.suppressed or v.suppressed,
            suppression_reason=prev.suppression_reason or v.suppression_reason,
            suppression_expires=prev.suppression_expires or v.suppression_expires,
        )
    return tuple(by_id.values())


def _dehydrate_vuln(v: Vulnerability) -> dict[str, Any]:
    return {
        "id": v.id,
        "aliases": list(v.aliases),
        "fix_versions": [str(f) for f in v.fix_versions],
        "description": v.description,
    }


def _rehydrate_vulns(raw: list[dict[str, Any]]) -> tuple[Vulnerability, ...]:
    out: list[Vulnerability] = []
    for r in raw:
        fixes: list[Version] = []
        for f in r.get("fix_versions") or []:
            try:
                fixes.append(Version(f))
            except InvalidVersion:
                continue
        out.append(Vulnerability(
            id=r.get("id", ""),
            aliases=tuple(r.get("aliases") or ()),
            fix_versions=tuple(fixes),
            description=r.get("description", "") or "",
        ))
    # Dedupe on read as well, so caches written before this fix self-correct
    # without requiring --refresh.
    return _dedupe_vulns(out)


def compute_min_safe_version(
    installed: Version | None,
    vulns: Iterable[Vulnerability],
) -> Version | None:
    """Return the lowest fix-version strictly greater than `installed` that
    covers every advisory affecting this pin. Returns None when there are no
    vulns, no installed version, or no fix is known.
    """
    if installed is None:
        return None
    vuln_list = [v for v in vulns if v.fix_versions]
    if not vuln_list:
        return None
    # For each vuln, the lowest fix that's strictly newer than installed.
    candidates: list[Version] = []
    for v in vuln_list:
        higher = [f for f in v.fix_versions if f > installed]
        if not higher:
            # No known fix is newer than installed; we cannot recommend one.
            return None
        candidates.append(min(higher))
    if not candidates:
        return None
    return max(candidates)
