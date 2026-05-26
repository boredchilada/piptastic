"""Parse dep sources into a uniform list of Dep objects."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from piptastic.logging import get_logger
from piptastic.models import Dep, DepSource, SourceKind

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
    line: str, *, source: DepSource, line_no: int
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
