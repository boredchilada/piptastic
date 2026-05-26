"""Tests for the CLI entry points."""

import json
from pathlib import Path

import pytest

from piptastic.cli import build_parser, main

FIXTURES = Path(__file__).parent / "fixtures"


def test_parser_has_audit_list_update():
    parser = build_parser()
    args = parser.parse_args(["audit", "."])
    assert args.command == "audit"
    args = parser.parse_args(["list", "."])
    assert args.command == "list"
    args = parser.parse_args(["update", "."])
    assert args.command == "update"


def test_audit_json_smoke(tmp_path, monkeypatch, capsys):
    """Audit a tiny fixture project; --json output must be parseable + schema 1.

    The PyPI client is monkeypatched so the test never touches the network.
    """
    # Use the existing req_only fixture
    monkeypatch.chdir(FIXTURES)

    from piptastic import cli as cli_mod

    class FakeClient:
        def fetch_many(self, names):
            return {}
        def fetch_one(self, name):
            return None

    monkeypatch.setattr(cli_mod, "_build_client", lambda args: FakeClient())

    exit_code = main(["audit", str(FIXTURES / "req_only"), "--json"])
    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["schema_version"] == 1
    assert payload["projects"][0]["name"] == "req_only"


def test_audit_missing_path_returns_1(tmp_path):
    exit_code = main(["audit", str(tmp_path / "nope")])
    assert exit_code == 1


def test_list_alias_runs(tmp_path, monkeypatch, capsys):
    from piptastic import cli as cli_mod
    class FakeClient:
        def fetch_many(self, names): return {}
        def fetch_one(self, name): return None
    monkeypatch.setattr(cli_mod, "_build_client", lambda args: FakeClient())

    exit_code = main(["list", str(FIXTURES / "req_only")])
    assert exit_code == 0


from tests.test_bootstrap import build_fake_venv


def test_bootstrap_writes_requirements(tmp_path, monkeypatch, capsys):
    project = tmp_path / "myproj"
    project.mkdir()
    build_fake_venv(project / ".venv", packages={"flask": "3.0.2", "pip": "24.0"})

    exit_code = main(["bootstrap", str(project)])
    captured = capsys.readouterr()
    assert exit_code == 0
    req_file = project / "requirements.txt"
    assert req_file.is_file()
    content = req_file.read_text(encoding="utf-8")
    assert "flask==3.0.2" in content
    assert "pip==" not in content  # plumbing skipped


def test_bootstrap_refuses_overwrite_without_force(tmp_path, capsys):
    project = tmp_path / "myproj"
    project.mkdir()
    build_fake_venv(project / ".venv", packages={"flask": "3.0.2"})
    (project / "requirements.txt").write_text("preexisting==1.0\n", encoding="utf-8")

    exit_code = main(["bootstrap", str(project)])
    assert exit_code == 1
    # Original content preserved
    assert (project / "requirements.txt").read_text(encoding="utf-8") == "preexisting==1.0\n"


def test_bootstrap_force_backs_up_then_overwrites(tmp_path):
    project = tmp_path / "myproj"
    project.mkdir()
    build_fake_venv(project / ".venv", packages={"flask": "3.0.2"})
    (project / "requirements.txt").write_text("preexisting==1.0\n", encoding="utf-8")

    exit_code = main(["bootstrap", str(project), "--force"])
    assert exit_code == 0
    new_content = (project / "requirements.txt").read_text(encoding="utf-8")
    assert "flask==3.0.2" in new_content
    assert "preexisting==1.0" not in new_content
    # Backup exists
    backups = list((project / ".requirements_backups").glob("requirements_*.txt"))
    assert len(backups) == 1
    assert "preexisting==1.0" in backups[0].read_text(encoding="utf-8")


def test_bootstrap_dry_run(tmp_path, capsys):
    project = tmp_path / "myproj"
    project.mkdir()
    build_fake_venv(project / ".venv", packages={"flask": "3.0.2"})

    exit_code = main(["bootstrap", str(project), "--dry-run"])
    assert exit_code == 0
    assert not (project / "requirements.txt").exists()
    captured = capsys.readouterr()
    assert "flask==3.0.2" in captured.out


def test_bootstrap_no_venv(tmp_path):
    project = tmp_path / "myproj"
    project.mkdir()
    exit_code = main(["bootstrap", str(project)])
    assert exit_code == 1


def test_bootstrap_ambiguous_venv(tmp_path):
    project = tmp_path / "myproj"
    project.mkdir()
    build_fake_venv(project / ".venv", packages={"flask": "3.0.2"})
    build_fake_venv(project / "venv", packages={"flask": "3.0.2"})
    exit_code = main(["bootstrap", str(project)])
    assert exit_code == 1
