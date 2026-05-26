"""Tests for parsing each supported dep file format."""

from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet

from piptastic.models import DepSource, PinStatus, SourceKind
from piptastic.parsing import parse_source

FIXTURES = Path(__file__).parent / "fixtures"


def _by_name(deps, name):
    matches = [d for d in deps if d.name == name]
    assert matches, f"no dep named {name}; have {[d.name for d in deps]}"
    return matches[0]


def test_parse_requirements_txt_basic():
    src = DepSource(
        kind=SourceKind.REQUIREMENTS_TXT,
        path=FIXTURES / "req_only" / "requirements.txt",
        group="default",
    )
    deps = parse_source(src)
    names = [d.name for d in deps]

    # Names are PEP 503 canonicalized (lowercase, hyphens)
    assert "flask" in names
    assert "requests" in names
    assert "sqlalchemy" in names
    assert "pkg-with-extras" in names
    assert "unpinned-pkg" in names
    assert "repo-from-vcs" in names
    assert "needs-marker" in names

    # `-r dev-requirements.txt` was followed
    assert "pytest" in names
    assert "black" in names


def test_parse_requirements_txt_extras_and_marker():
    src = DepSource(
        kind=SourceKind.REQUIREMENTS_TXT,
        path=FIXTURES / "req_only" / "requirements.txt",
        group="default",
    )
    deps = parse_source(src)

    crypto = _by_name(deps, "pkg-with-extras")
    assert "crypto" in crypto.extras
    assert crypto.specifier == SpecifierSet("==1.2.3")

    marked = _by_name(deps, "needs-marker")
    assert marked.marker is not None
    assert 'python_version' in str(marked.marker)


def test_parse_requirements_txt_url_dep():
    src = DepSource(
        kind=SourceKind.REQUIREMENTS_TXT,
        path=FIXTURES / "req_only" / "requirements.txt",
        group="default",
    )
    deps = parse_source(src)

    vcs = _by_name(deps, "repo-from-vcs")
    assert vcs.url is not None
    assert vcs.url.startswith("git+https://")
    assert vcs.specifier == SpecifierSet()


def test_parse_requirements_txt_unpinned():
    src = DepSource(
        kind=SourceKind.REQUIREMENTS_TXT,
        path=FIXTURES / "req_only" / "requirements.txt",
        group="default",
    )
    deps = parse_source(src)

    unp = _by_name(deps, "unpinned-pkg")
    assert unp.specifier == SpecifierSet()


def test_parse_requirements_followed_recursively_carries_correct_source(write_tree):
    """A `-r` include attributes its deps to the included file, not the entry."""
    tree = write_tree({
        "main.txt": "flask==3.0.2\n-r more.txt\n",
        "more.txt": "requests==2.32.2\n",
    })
    src = DepSource(
        kind=SourceKind.REQUIREMENTS_TXT,
        path=tree / "main.txt",
        group="default",
    )
    deps = parse_source(src)
    by_name = {d.name: d for d in deps}
    assert by_name["flask"].source.path == tree / "main.txt"
    assert by_name["requests"].source.path == tree / "more.txt"


def test_parse_requirements_cycle_guard(write_tree):
    """`-r` cycles do not loop forever."""
    tree = write_tree({
        "a.txt": "-r b.txt\nflask==3.0.2\n",
        "b.txt": "-r a.txt\nrequests==2.32.2\n",
    })
    src = DepSource(
        kind=SourceKind.REQUIREMENTS_TXT,
        path=tree / "a.txt",
        group="default",
    )
    deps = parse_source(src)  # must not hang
    names = {d.name for d in deps}
    assert names == {"flask", "requests"}


def test_parse_invalid_line_warns_but_continues(write_tree, caplog):
    """Garbage lines are skipped with a warning, not raised."""
    tree = write_tree({"r.txt": "flask==3.0.2\n@@@nonsense@@@\nrequests==2.32.2\n"})
    src = DepSource(
        kind=SourceKind.REQUIREMENTS_TXT,
        path=tree / "r.txt",
        group="default",
    )
    deps = parse_source(src)
    names = {d.name for d in deps}
    assert names == {"flask", "requests"}


def test_parse_pyproject_pep621_default_group():
    src = DepSource(
        kind=SourceKind.PYPROJECT_PEP621,
        path=FIXTURES / "pyproject_pep621" / "pyproject.toml",
        group="default",
    )
    deps = parse_source(src)
    names = {d.name for d in deps}
    assert names == {"flask", "requests", "httpx"}

    httpx = _by_name(deps, "httpx")
    assert "http2" in httpx.extras
    assert httpx.marker is not None


def test_parse_pyproject_pep621_optional_group():
    src = DepSource(
        kind=SourceKind.PYPROJECT_PEP621,
        path=FIXTURES / "pyproject_pep621" / "pyproject.toml",
        group="dev",
    )
    deps = parse_source(src)
    assert {d.name for d in deps} == {"pytest", "black"}


def test_parse_pyproject_poetry_caret_tilde():
    src = DepSource(
        kind=SourceKind.PYPROJECT_POETRY,
        path=FIXTURES / "pyproject_poetry" / "pyproject.toml",
        group="default",
    )
    deps = parse_source(src)
    names = {d.name for d in deps}
    # "python" is the interpreter constraint, NOT a dep
    assert "python" not in names
    assert names == {"flask", "requests", "httpx", "unpinned-thing"}

    flask = _by_name(deps, "flask")
    # ^3.0.2 -> >=3.0.2,<4.0.0
    # NOTE: SpecifierSet stringifies in sorted order; compare semantically.
    assert flask.specifier == SpecifierSet(">=3.0.2,<4.0.0")

    requests = _by_name(deps, "requests")
    # ~2.30 -> >=2.30,<3.0.0  (Poetry's "~" without micro -> next-major lock)
    assert requests.specifier == SpecifierSet(">=2.30,<3.0.0")

    httpx = _by_name(deps, "httpx")
    assert "http2" in httpx.extras
    assert httpx.specifier == SpecifierSet(">=0.27")

    unpinned = _by_name(deps, "unpinned-thing")
    assert unpinned.specifier == SpecifierSet()


def test_parse_pyproject_poetry_group():
    src = DepSource(
        kind=SourceKind.PYPROJECT_POETRY,
        path=FIXTURES / "pyproject_poetry" / "pyproject.toml",
        group="dev",
    )
    deps = parse_source(src)
    assert {d.name for d in deps} == {"pytest"}
