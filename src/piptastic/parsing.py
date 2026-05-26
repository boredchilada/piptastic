"""Parse dep sources into a uniform list of Dep objects."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from piptastic.logging import get_logger
from piptastic.models import Dep, DepSource, SourceKind

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

logger = get_logger(__name__)

# Schemes pip treats as direct-URL requirements. VCS schemes use the
# `vcs+transport` form (git+https, hg+http, bzr+ssh, svn+https, ...).
_DIRECT_URL_SCHEMES = (
    "http", "https", "file", "ftp",
    "git", "git+http", "git+https", "git+ssh", "git+file", "git+git",
    "hg", "hg+http", "hg+https", "hg+ssh", "hg+file", "hg+static-http",
    "bzr", "bzr+http", "bzr+https", "bzr+ssh", "bzr+sftp", "bzr+ftp", "bzr+lp", "bzr+file",
    "svn", "svn+http", "svn+https", "svn+ssh", "svn+svn", "svn+file",
)

_EGG_FRAGMENT_RE = re.compile(r"[#&]egg=([^&]+)")


def parse_source(source: DepSource) -> list[Dep]:
    """Parse a single DepSource and return a flat list of Dep.

    Includes (`-r other.txt`) are followed recursively with cycle detection.
    Each yielded Dep carries the DepSource of its true file of origin.
    """
    if source.kind in (SourceKind.REQUIREMENTS_TXT, SourceKind.CONSTRAINTS_TXT):
        return _parse_requirements_file(source, _visited=set())
    if source.kind == SourceKind.PYPROJECT_PEP621:
        return _parse_pep621(source)
    if source.kind == SourceKind.PYPROJECT_POETRY:
        return _parse_poetry(source)
    raise NotImplementedError(f"parsing for {source.kind} not yet implemented")


def _parse_requirements_file(
    source: DepSource, *, _visited: set[Path]
) -> list[Dep]:
    path = source.path.resolve()
    if path in _visited:
        return []
    _visited.add(path)

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("could not read %s: %s", path, e)
        return []

    deps: list[Dep] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # strip trailing inline comments (pip behavior)
        if " #" in line:
            line = line.split(" #", 1)[0].rstrip()

        if line.startswith(("-r ", "--requirement ", "-c ", "--constraint ")):
            include_path = _resolve_include(path.parent, line)
            if include_path is None:
                logger.warning("could not resolve include at %s:%d", path, line_no)
                continue
            kind = (
                SourceKind.CONSTRAINTS_TXT
                if line.startswith(("-c ", "--constraint "))
                else SourceKind.REQUIREMENTS_TXT
            )
            sub_source = DepSource(kind=kind, path=include_path, group=source.group)
            deps.extend(_parse_requirements_file(sub_source, _visited=_visited))
            continue

        if line.startswith(("-e ", "--editable ")):
            line = line.split(maxsplit=1)[1]

        dep = _parse_one_requirement_line(line, source=source, line_no=line_no)
        if dep is not None:
            deps.append(dep)

    return deps


def _resolve_include(base_dir: Path, line: str) -> Path | None:
    parts = line.split(maxsplit=1)
    if len(parts) != 2:
        return None
    candidate = (base_dir / parts[1].strip()).resolve()
    return candidate if candidate.exists() else None


def _parse_one_requirement_line(
    line: str, *, source: DepSource, line_no: int | None
) -> Dep | None:
    # Pip allows bare direct-URL / VCS requirements (e.g.
    # `git+https://github.com/x/y@v1#egg=name`) without a `name @ ` prefix.
    # `packaging.Requirement` is strict PEP 508 and rejects those. Rewrite them
    # to `name @ url` form using the `#egg=` fragment before parsing.
    rewritten = _rewrite_bare_url(line)

    try:
        req = Requirement(rewritten)
    except InvalidRequirement as e:
        logger.warning("invalid requirement %r: %s", line, e)
        return None

    url = req.url
    return Dep(
        name=canonicalize_name(req.name),
        raw_name=req.name,
        specifier=req.specifier,
        extras=frozenset(req.extras),
        marker=req.marker,
        source=source,
        line_no=line_no,
        url=url,
    )


def _rewrite_bare_url(line: str) -> str:
    """Convert a bare URL/VCS requirement into PEP 508 `name @ url` form.

    If the line is already PEP 508 (`name @ url`, or just a name with
    specifiers), return it unchanged. If it starts with a recognized direct-URL
    or VCS scheme and carries an `#egg=name` fragment, prepend `name @ `.
    """
    scheme = urlsplit(line).scheme
    if not scheme or scheme not in _DIRECT_URL_SCHEMES:
        return line
    m = _EGG_FRAGMENT_RE.search(line)
    if not m:
        return line
    name = m.group(1)
    # Strip subdirectory/etc. tokens after the egg name if any leaked in.
    name = name.split("&", 1)[0]
    return f"{name} @ {line}"


# ---------- pyproject.toml (PEP 621) ----------

def _parse_pep621(source: DepSource) -> list[Dep]:
    data = _read_toml(source.path)
    project = data.get("project", {})
    if source.group == "default":
        strings = project.get("dependencies", []) or []
    else:
        optional = project.get("optional-dependencies", {}) or {}
        strings = optional.get(source.group, []) or []

    deps: list[Dep] = []
    for s in strings:
        dep = _parse_one_requirement_line(s, source=source, line_no=None)
        if dep is not None:
            deps.append(dep)
    return deps


# ---------- pyproject.toml (Poetry) ----------

def _parse_poetry(source: DepSource) -> list[Dep]:
    data = _read_toml(source.path)
    poetry = data.get("tool", {}).get("poetry", {})

    if source.group == "default":
        table = poetry.get("dependencies", {}) or {}
    else:
        groups = poetry.get("group", {}) or {}
        table = groups.get(source.group, {}).get("dependencies", {}) or {}

    deps: list[Dep] = []
    for name, value in table.items():
        if name == "python":  # interpreter constraint, not a real dep
            continue
        pep508 = _poetry_to_pep508(name, value)
        if pep508 is None:
            continue
        dep = _parse_one_requirement_line(pep508, source=source, line_no=None)
        if dep is not None:
            deps.append(dep)
    return deps


def _poetry_to_pep508(name: str, value: Any) -> str | None:
    """Convert a Poetry dep spec to a PEP 508 string."""
    if isinstance(value, str):
        spec = _poetry_version_to_specifier(value)
        return f"{name}{spec}" if spec else name

    if not isinstance(value, dict):
        logger.warning("unsupported poetry dep spec for %s: %r", name, value)
        return None

    version = value.get("version", "*")
    spec = _poetry_version_to_specifier(version)
    extras = value.get("extras") or []
    marker = value.get("python")

    extras_part = f"[{','.join(extras)}]" if extras else ""
    marker_part = (
        f"; python_version {_python_constraint_to_marker(marker)}" if marker else ""
    )
    return f"{name}{extras_part}{spec}{marker_part}"


def _poetry_version_to_specifier(v: str) -> str:
    """Convert Poetry version shorthand to a PEP 440 specifier string."""
    v = v.strip()
    if v == "*" or v == "":
        return ""
    if v.startswith("^"):
        return _caret_to_specifier(v[1:])
    if v.startswith("~"):
        return _tilde_to_specifier(v[1:])
    # plain version, or already a PEP 440-style range
    if v[:1] in (">", "<", "=", "!"):
        return v
    return f"=={v}"


def _caret_to_specifier(base: str) -> str:
    """`^X.Y.Z` -> `>=X.Y.Z,<(X+1).0.0`. `^0.Y.Z` -> `>=0.Y.Z,<0.(Y+1).0`."""
    parts = _split_version(base)
    if not parts:
        return ""
    upper = list(parts)
    for i, n in enumerate(parts):
        if n != 0:
            upper[i] = n + 1
            for j in range(i + 1, len(upper)):
                upper[j] = 0
            break
    else:
        # all zeros -> behave like ==
        return f"=={base}"
    while len(upper) < 3:
        upper.append(0)
    return f">={base},<{'.'.join(str(x) for x in upper)}"


def _tilde_to_specifier(base: str) -> str:
    """`~X.Y.Z` -> `>=X.Y.Z,<X.(Y+1).0`. `~X.Y` -> `>=X.Y,<(X+1).0.0`.
    `~X` -> `>=X,<(X+1).0.0`.

    Poetry's tilde: when the patch component is given, lock to the same
    minor (next-minor upper bound). When only major.minor or major is given,
    lock to the same major (next-major upper bound).
    """
    parts = _split_version(base)
    if not parts:
        return ""
    if len(parts) >= 3:
        # ~X.Y.Z -> >=X.Y.Z,<X.(Y+1).0
        upper = [parts[0], parts[1] + 1, 0]
        return f">={base},<{'.'.join(str(x) for x in upper)}"
    # ~X or ~X.Y -> >=base,<(X+1).0.0
    upper = [parts[0] + 1, 0, 0]
    return f">={base},<{'.'.join(str(x) for x in upper)}"


def _split_version(v: str) -> list[int]:
    """Split a dotted version into a list of ints. Returns [] on any non-int part."""
    out: list[int] = []
    for seg in v.split("."):
        try:
            out.append(int(seg))
        except ValueError:
            return []
    return out


def _python_constraint_to_marker(value: str) -> str:
    """Map a Poetry `python = ">=3.10"` to a PEP 508 marker tail.

    Returns the comparison + quoted version, e.g. `>= "3.10"`. The caller
    prefixes this with `python_version `.
    """
    v = value.strip()
    if v.startswith((">=", "<=", "==", "!=")):
        return f'{v[:2]} "{v[2:].strip()}"'
    if v.startswith((">", "<")):
        return f'{v[:1]} "{v[1:].strip()}"'
    return f'== "{v}"'


def _read_toml(path: Path) -> dict[str, Any]:
    """Read and parse a TOML file. Returns {} on any read/parse error (logged)."""
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.warning("could not parse TOML %s: %s", path, e)
        return {}
