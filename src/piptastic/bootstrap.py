# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bootstrap a requirements.txt from a project's venv.

Pure-data helpers here; the CLI handler does the file IO.
"""

from __future__ import annotations

import importlib.metadata
import json
import sys
from pathlib import Path
from typing import Iterable

from packaging.utils import canonicalize_name

from piptastic.logging import get_logger

logger = get_logger(__name__)


# Distributions that are part of the venv plumbing rather than the
# project's actual dependencies. Skipped during freeze.
_PLUMBING_NAMES = frozenset({
    "pip",
    "setuptools",
    "wheel",
    "pkg-resources",
    "distlib",
    "_distutils_hack",
})


# Default venv directory names to probe under a project root, in order.
_DEFAULT_VENV_NAMES = (".venv", "venv", "env", ".env")


def is_plumbing(name: str) -> bool:
    """True if `name` is a venv-plumbing distribution that bootstrap should
    skip. Match is case-insensitive on the PEP 503 canonical form, AND
    on the literal underscore-style names that some plumbing uses."""
    canon = canonicalize_name(name)
    if canon in _PLUMBING_NAMES:
        return True
    # _distutils_hack canonicalizes to '-distutils-hack' which is not the
    # bare canonical form, so also check the lowercase raw name.
    if name.lower() in _PLUMBING_NAMES:
        return True
    return False


def is_self_install(dist, project_path: Path) -> bool:
    """True if `dist` is an editable install pointing back at the project."""
    try:
        text = dist.read_text("direct_url.json")
    except (FileNotFoundError, OSError):
        return False
    if text is None:
        return False
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False
    url = data.get("url", "")
    if not url:
        return False
    # url is typically a file:// URI; compare resolved paths
    try:
        from urllib.parse import urlparse, unquote
        parsed = urlparse(url)
        if parsed.scheme != "file":
            return False
        # urlparse gives us a path like '/C:/path/...' on Windows
        path_str = unquote(parsed.path).lstrip("/")
        # On POSIX the leading / was real; restore it
        if not path_str.startswith(("/", ".")) and ":" not in path_str[:3]:
            path_str = "/" + path_str
        return Path(path_str).resolve() == project_path.resolve()
    except Exception:
        return False


def find_site_packages(venv_dir: Path) -> Path | None:
    """Return the venv's site-packages directory, cross-platform.

    Windows: <venv>/Lib/site-packages
    POSIX:   <venv>/lib/python*/site-packages
    """
    win = venv_dir / "Lib" / "site-packages"
    if win.is_dir():
        return win
    lib = venv_dir / "lib"
    if lib.is_dir():
        for py_dir in sorted(lib.glob("python*")):
            sp = py_dir / "site-packages"
            if sp.is_dir():
                return sp
    return None


def find_venv(
    project_path: Path,
    *,
    explicit: Path | None = None,
) -> tuple[list[Path], Path | None]:
    """Return (all_candidates, chosen).

    - If `explicit` is given, returns it as the only candidate (and chosen).
    - Otherwise probes the default venv names + scans for pyvenv.cfg.
    - chosen is None when 0 candidates OR multiple candidates were found.
    """
    if explicit is not None:
        path = explicit if explicit.is_absolute() else (project_path / explicit)
        return [path], path

    candidates: list[Path] = []

    for name in _DEFAULT_VENV_NAMES:
        candidate = project_path / name
        if candidate.is_dir() and (candidate / "pyvenv.cfg").is_file():
            candidates.append(candidate)

    # Also scan any other top-level subdir containing pyvenv.cfg, but only if
    # we didn't already find one of the canonical names. This catches
    # arbitrarily-named venvs.
    if not candidates:
        for child in sorted(project_path.iterdir() if project_path.is_dir() else []):
            if child.is_dir() and (child / "pyvenv.cfg").is_file():
                candidates.append(child)

    if len(candidates) == 1:
        return candidates, candidates[0]
    return candidates, None


def freeze_venv(project_path: Path, venv_dir: Path) -> list[str]:
    """Return sorted 'name==version' lines for non-plumbing, non-self
    distributions installed in venv_dir."""
    site_packages = find_site_packages(venv_dir)
    if site_packages is None:
        return []

    out: list[tuple[str, str]] = []
    for dist in importlib.metadata.distributions(path=[str(site_packages)]):
        raw_name = dist.metadata["Name"]
        if raw_name is None:
            continue
        if is_plumbing(raw_name):
            continue
        if is_self_install(dist, project_path):
            continue
        canon = canonicalize_name(raw_name)
        out.append((canon, dist.version))

    out.sort(key=lambda pair: pair[0])
    return [f"{name}=={version}" for name, version in out]
