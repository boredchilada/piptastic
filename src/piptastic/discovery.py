# SPDX-License-Identifier: AGPL-3.0-or-later
"""Walk a directory tree and emit Project records."""

from __future__ import annotations

import fnmatch
import os
import re
import sys
from pathlib import Path
from typing import Iterable

from piptastic.logging import get_logger
from piptastic.models import DepSource, Project, SourceKind

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

logger = get_logger(__name__)

VENV_EXACT_NAMES = {"venv", ".venv", "env", ".env", "ENV", "virtualenv"}

ALWAYS_SKIP = {
    ".git", ".hg", ".svn",
    ".tox", ".nox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "node_modules", "__pycache__", "site-packages",
    "build", "dist",
}

REQUIREMENTS_PATTERNS = (
    "requirements.txt",
    "requirements-*.txt",
    "*-requirements.txt",
    "constraints.txt",
    "constraints-*.txt",
)


def discover_tree(root: Path, *, exclude: Iterable[str] = ()) -> list[Project]:
    """Walk `root` and return one Project per directory containing dep files."""
    root = Path(root).resolve()
    if not root.exists():
        logger.error("path does not exist: %s", root)
        return []

    user_excludes = tuple(exclude)
    by_dir: dict[Path, list[DepSource]] = {}

    for dirpath, dirnames, filenames in os.walk(root):
        # In-place mutation of dirnames prunes the walk
        dirnames[:] = [d for d in dirnames if not _should_skip(Path(dirpath) / d, user_excludes)]

        d = Path(dirpath)
        sources = list(_dep_sources_in_dir(d, filenames))
        if sources:
            by_dir.setdefault(d, []).extend(sources)

    projects: list[Project] = []
    for d, sources in sorted(by_dir.items()):
        py_info = _detect_python_version(d)
        projects.append(Project(
            name=d.name,
            path=d.resolve(),
            python_version=py_info.get("version"),
            python_source=py_info.get("source"),
            python_constraints=py_info.get("constraints"),
            dep_sources=tuple(sources),
        ))
    return sorted(projects, key=lambda p: p.name)


def discover_one(project_path: Path) -> Project | None:
    """Treat `project_path` as a known project root; do not rescan its parent."""
    project_path = Path(project_path).resolve()
    if not project_path.is_dir():
        return None
    filenames = [p.name for p in project_path.iterdir() if p.is_file()]
    sources = list(_dep_sources_in_dir(project_path, filenames))
    if not sources:
        return None
    py_info = _detect_python_version(project_path)
    return Project(
        name=project_path.name,
        path=project_path,
        python_version=py_info.get("version"),
        python_source=py_info.get("source"),
        python_constraints=py_info.get("constraints"),
        dep_sources=tuple(sources),
    )


# ---------- internals ----------

def _should_skip(path: Path, user_excludes: tuple[str, ...]) -> bool:
    name = path.name
    if name in ALWAYS_SKIP:
        return True
    if name in VENV_EXACT_NAMES:
        return True
    # Detect a venv by the presence of pyvenv.cfg inside it
    if (path / "pyvenv.cfg").is_file():
        return True
    if name.endswith(".egg-info"):
        return True
    for pat in user_excludes:
        if fnmatch.fnmatch(name, pat):
            return True
    return False


def _dep_sources_in_dir(d: Path, filenames: list[str]) -> Iterable[DepSource]:
    fileset = set(filenames)

    # requirements*.txt / constraints*.txt
    for fname in filenames:
        if any(fnmatch.fnmatch(fname, pat) for pat in REQUIREMENTS_PATTERNS):
            kind = (
                SourceKind.CONSTRAINTS_TXT
                if fname.startswith("constraints")
                else SourceKind.REQUIREMENTS_TXT
            )
            yield DepSource(kind=kind, path=d / fname, group=_infer_group(fname))

    # pyproject.toml: may produce PEP 621 and/or Poetry sources, each with
    # multiple groups
    if "pyproject.toml" in fileset:
        yield from _pyproject_sources(d / "pyproject.toml")

    # Pipfile + Pipfile.lock
    if "Pipfile" in fileset:
        yield DepSource(kind=SourceKind.PIPFILE, path=d / "Pipfile", group="default")
        yield DepSource(kind=SourceKind.PIPFILE, path=d / "Pipfile", group="dev")
    if "Pipfile.lock" in fileset:
        yield DepSource(kind=SourceKind.PIPFILE_LOCK, path=d / "Pipfile.lock", group="default")
        yield DepSource(kind=SourceKind.PIPFILE_LOCK, path=d / "Pipfile.lock", group="dev")


def _infer_group(fname: str) -> str:
    stem = Path(fname).stem.lower()
    if stem in ("requirements", "constraints"):
        return "default"
    if "dev" in stem:
        return "dev"
    if "test" in stem:
        return "test"
    if "prod" in stem:
        return "prod"
    # Strip "requirements-" / "-requirements" / "constraints-" / "-constraints"
    # and use the remainder as the group name.
    remainder = re.sub(r"(^(requirements|constraints)-|-?(requirements|constraints)$)", "", stem)
    return remainder or "default"


def _pyproject_sources(path: Path) -> Iterable[DepSource]:
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.warning("could not read %s: %s", path, e)
        return

    project = data.get("project") or {}
    if project:
        yield DepSource(kind=SourceKind.PYPROJECT_PEP621, path=path, group="default")
        for extra in (project.get("optional-dependencies") or {}).keys():
            yield DepSource(kind=SourceKind.PYPROJECT_PEP621, path=path, group=extra)

    poetry = (data.get("tool") or {}).get("poetry") or {}
    if poetry:
        if poetry.get("dependencies"):
            yield DepSource(kind=SourceKind.PYPROJECT_POETRY, path=path, group="default")
        for group_name in (poetry.get("group") or {}).keys():
            yield DepSource(kind=SourceKind.PYPROJECT_POETRY, path=path, group=group_name)


def _detect_python_version(project_dir: Path) -> dict[str, str | None]:
    """Return {version, source, constraints} from project files."""
    out: dict[str, str | None] = {"version": None, "source": None, "constraints": None}

    runtime = project_dir / "runtime.txt"
    if runtime.exists():
        m = re.search(r"python-?(\d+\.\d+(?:\.\d+)?)", runtime.read_text(encoding="utf-8"))
        if m:
            out["version"] = m.group(1)
            out["source"] = "runtime.txt"
            return out

    pyproject = project_dir / "pyproject.toml"
    if pyproject.exists():
        try:
            with pyproject.open("rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            data = {}
        req = ((data.get("project") or {}).get("requires-python"))
        if req:
            m = re.search(r"(\d+\.\d+(?:\.\d+)?)", req)
            if m:
                out["version"] = m.group(1)
                out["source"] = "pyproject.toml"
                out["constraints"] = req.strip()
                return out

    pipfile = project_dir / "Pipfile"
    if pipfile.exists():
        try:
            with pipfile.open("rb") as f:
                data = tomllib.load(f)
            v = (data.get("requires") or {}).get("python_version")
            if v:
                out["version"] = str(v)
                out["source"] = "Pipfile"
                return out
        except (OSError, tomllib.TOMLDecodeError):
            pass

    return out
