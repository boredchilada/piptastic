"""Shared test fixtures."""

from pathlib import Path
from typing import Callable

import pytest


@pytest.fixture
def write_tree(tmp_path: Path) -> Callable[[dict], Path]:
    """Build a directory tree from a nested dict.

    Keys are filenames or dirnames; dict values become subdirs, str values
    become file contents.
    """

    def _build(spec: dict, root: Path | None = None) -> Path:
        target = root or tmp_path
        for name, value in spec.items():
            p = target / name
            if isinstance(value, dict):
                p.mkdir(parents=True, exist_ok=True)
                _build(value, p)
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(value, encoding="utf-8")
        return target

    return _build
