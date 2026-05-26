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
