"""Tests for piptastic bootstrap (venv → requirements.txt)."""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path

import pytest

from piptastic.bootstrap import (
    find_site_packages,
    find_venv,
    freeze_venv,
    is_plumbing,
    is_self_install,
)


def build_fake_venv(
    venv_root: Path,
    *,
    on_windows: bool = False,
    packages: dict[str, str] | None = None,
    editable_self: dict | None = None,
) -> Path:
    """Build a minimal venv directory tree.

    Args:
        venv_root: Path to create the venv at (must not yet exist).
        on_windows: when True, uses Windows-style 'Lib/site-packages'.
        packages: {canonical_name: version} to write as dist-info dirs.
        editable_self: optional {"package_name": ..., "project_path": ...}
            that writes a direct_url.json marking that package as editable
            from project_path.
    """
    if on_windows:
        site_packages = venv_root / "Lib" / "site-packages"
    else:
        # Pick a python version dir; real venvs use the actual interpreter version.
        site_packages = venv_root / "lib" / "python3.11" / "site-packages"
    site_packages.mkdir(parents=True, exist_ok=True)

    # Plant pyvenv.cfg so other tools can recognize it as a venv
    (venv_root / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")

    for name, version in (packages or {}).items():
        dist_info = site_packages / f"{name}-{version}.dist-info"
        dist_info.mkdir()
        (dist_info / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
            encoding="utf-8",
        )
        (dist_info / "RECORD").write_text("", encoding="utf-8")
        (dist_info / "WHEEL").write_text(
            "Wheel-Version: 1.0\nGenerator: bdist_wheel\n",
            encoding="utf-8",
        )

    if editable_self:
        pkg_name = editable_self["package_name"]
        project_path = Path(editable_self["project_path"])
        # Find which dist-info matches; if not present, create a stub
        match = list(site_packages.glob(f"{pkg_name}-*.dist-info"))
        if not match:
            stub = site_packages / f"{pkg_name}-0.0.0.dist-info"
            stub.mkdir()
            (stub / "METADATA").write_text(
                f"Metadata-Version: 2.1\nName: {pkg_name}\nVersion: 0.0.0\n",
                encoding="utf-8",
            )
            match = [stub]
        (match[0] / "direct_url.json").write_text(
            _json.dumps({"url": project_path.as_uri(), "dir_info": {"editable": True}}),
            encoding="utf-8",
        )

    return site_packages


# ---------- find_site_packages ----------

@pytest.mark.parametrize("on_windows", [True, False])
def test_find_site_packages(tmp_path, on_windows):
    venv = tmp_path / "venv"
    build_fake_venv(venv, on_windows=on_windows, packages={"flask": "3.0.2"})
    sp = find_site_packages(venv)
    assert sp is not None
    assert sp.is_dir()
    assert (sp / "flask-3.0.2.dist-info").is_dir()


def test_find_site_packages_returns_none_when_missing(tmp_path):
    empty = tmp_path / "not-a-venv"
    empty.mkdir()
    assert find_site_packages(empty) is None


# ---------- find_venv ----------

def test_find_venv_zero_candidates(tmp_path):
    candidates, chosen = find_venv(tmp_path)
    assert candidates == []
    assert chosen is None


def test_find_venv_explicit_path(tmp_path):
    venv = tmp_path / "custom-runtime"
    build_fake_venv(venv, packages={})
    candidates, chosen = find_venv(tmp_path, explicit=venv)
    assert chosen == venv


def test_find_venv_single_default(tmp_path):
    venv = tmp_path / ".venv"
    build_fake_venv(venv, packages={})
    candidates, chosen = find_venv(tmp_path)
    assert chosen == venv


def test_find_venv_multiple_returns_none_for_chosen(tmp_path):
    build_fake_venv(tmp_path / ".venv", packages={})
    build_fake_venv(tmp_path / "venv", packages={})
    candidates, chosen = find_venv(tmp_path)
    assert chosen is None
    assert len(candidates) == 2


# ---------- is_plumbing / is_self_install ----------

@pytest.mark.parametrize("name,expected", [
    ("pip", True),
    ("PIP", True),
    ("setuptools", True),
    ("wheel", True),
    ("pkg-resources", True),
    ("distlib", True),
    ("_distutils_hack", True),
    ("flask", False),
    ("requests", False),
    ("setuptools-scm", False),  # only the bare 'setuptools' is plumbing
])
def test_is_plumbing(name, expected):
    assert is_plumbing(name) is expected


def test_is_self_install_with_direct_url(tmp_path):
    project = tmp_path / "myproj"
    project.mkdir()
    venv = tmp_path / ".venv"
    build_fake_venv(
        venv,
        packages={"myproj": "0.0.0"},
        editable_self={"package_name": "myproj", "project_path": project},
    )
    import importlib.metadata
    sp = find_site_packages(venv)
    dist = next(d for d in importlib.metadata.distributions(path=[str(sp)]) if d.metadata["Name"] == "myproj")
    assert is_self_install(dist, project) is True


def test_is_self_install_false_for_normal_dep(tmp_path):
    project = tmp_path / "myproj"
    project.mkdir()
    venv = tmp_path / ".venv"
    build_fake_venv(venv, packages={"flask": "3.0.2"})
    import importlib.metadata
    sp = find_site_packages(venv)
    dist = next(d for d in importlib.metadata.distributions(path=[str(sp)]) if d.metadata["Name"] == "flask")
    assert is_self_install(dist, project) is False


# ---------- freeze_venv ----------

def test_freeze_venv_basic_pinning(tmp_path):
    project = tmp_path / "myproj"
    project.mkdir()
    venv = tmp_path / ".venv"
    build_fake_venv(venv, packages={
        "flask": "3.0.2",
        "requests": "2.32.2",
        "pip": "24.0",        # plumbing → skipped
        "setuptools": "70.0", # plumbing → skipped
        "wheel": "0.43",      # plumbing → skipped
    })
    lines = freeze_venv(project, venv)
    assert lines == ["flask==3.0.2", "requests==2.32.2"]


def test_freeze_venv_excludes_self(tmp_path):
    project = tmp_path / "myproj"
    project.mkdir()
    venv = tmp_path / ".venv"
    build_fake_venv(
        venv,
        packages={"flask": "3.0.2", "myproj": "0.0.0"},
        editable_self={"package_name": "myproj", "project_path": project},
    )
    lines = freeze_venv(project, venv)
    assert lines == ["flask==3.0.2"]


def test_freeze_venv_sorted_alphabetical(tmp_path):
    project = tmp_path / "myproj"
    project.mkdir()
    venv = tmp_path / ".venv"
    build_fake_venv(venv, packages={
        "ZZZ-late": "1.0",
        "aaa-early": "2.0",
        "mmm-mid": "3.0",
    })
    lines = freeze_venv(project, venv)
    # PEP 503 canonicalization lowercases names; sorted order should match
    assert lines == ["aaa-early==2.0", "mmm-mid==3.0", "zzz-late==1.0"]


def test_freeze_venv_empty(tmp_path):
    project = tmp_path / "myproj"
    project.mkdir()
    venv = tmp_path / ".venv"
    build_fake_venv(venv, packages={})
    lines = freeze_venv(project, venv)
    assert lines == []
