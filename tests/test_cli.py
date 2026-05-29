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

    class FakeVulnClient:
        unreachable: list = []
        def fetch_for(self, pkgs):
            return {}

    monkeypatch.setattr(cli_mod, "_build_client", lambda args: FakeClient())
    monkeypatch.setattr(cli_mod, "_build_vuln_client", lambda args: FakeVulnClient())

    exit_code = main(["audit", str(FIXTURES / "req_only"), "--json"])
    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["schema_version"] == 3
    assert payload["projects"][0]["name"] == "req_only"


def test_audit_missing_path_returns_1(tmp_path):
    exit_code = main(["audit", str(tmp_path / "nope")])
    assert exit_code == 1


def test_list_alias_runs(tmp_path, monkeypatch, capsys):
    from piptastic import cli as cli_mod
    class FakeClient:
        def fetch_many(self, names): return {}
        def fetch_one(self, name): return None
    class FakeVulnClient:
        unreachable: list = []
        def fetch_for(self, pkgs): return {}
    monkeypatch.setattr(cli_mod, "_build_client", lambda args: FakeClient())
    monkeypatch.setattr(cli_mod, "_build_vuln_client", lambda args: FakeVulnClient())

    exit_code = main(["list", str(FIXTURES / "req_only")])
    assert exit_code == 0


def test_exit_code_constants_are_distinct():
    from piptastic.cli import EXIT_OK, EXIT_ERROR, EXIT_ROLLBACK, EXIT_GATE
    assert len({EXIT_OK, EXIT_ERROR, EXIT_ROLLBACK, EXIT_GATE}) == 4
    assert (EXIT_OK, EXIT_ERROR, EXIT_ROLLBACK, EXIT_GATE) == (0, 1, 2, 3)


def test_fail_on_drift_returns_gate_not_error(monkeypatch):
    """v0.4 contract: tripped --fail-on-drift returns EXIT_GATE (3), not EXIT_ERROR (1)."""
    from piptastic import cli as cli_mod
    from piptastic.cli import EXIT_GATE

    class FakeClient:
        def fetch_many(self, names): return {}
        def fetch_one(self, name): return None
    class FakeVulnClient:
        unreachable: list = []
        def fetch_for(self, pkgs): return {}
    monkeypatch.setattr(cli_mod, "_build_client", lambda args: FakeClient())
    monkeypatch.setattr(cli_mod, "_build_vuln_client", lambda args: FakeVulnClient())
    # Force the gate to trip by stubbing the threshold check.
    monkeypatch.setattr(cli_mod, "_exceeds_threshold", lambda audits, threshold: True)

    exit_code = main(["audit", str(FIXTURES / "req_only"), "--fail-on-drift", "patch"])
    assert exit_code == EXIT_GATE


def test_vuln_gate_trips_with_any(monkeypatch):
    """P1: --fail-on-vuln any returns EXIT_GATE when there's at least one CVE."""
    from piptastic import cli as cli_mod
    from piptastic.cli import EXIT_GATE

    class FakeClient:
        def fetch_many(self, names): return {}
        def fetch_one(self, name): return None
    class FakeVulnClient:
        unreachable: list = []
        def fetch_for(self, pkgs): return {}
    monkeypatch.setattr(cli_mod, "_build_client", lambda args: FakeClient())
    monkeypatch.setattr(cli_mod, "_build_vuln_client", lambda args: FakeVulnClient())
    # Stub the gate evaluator to "tripped".
    monkeypatch.setattr(cli_mod, "_vuln_gate_tripped", lambda *a, **k: True)

    exit_code = main(["audit", str(FIXTURES / "req_only"), "--fail-on-vuln", "any"])
    assert exit_code == EXIT_GATE


def test_vuln_gate_and_no_vulns_are_incompatible(monkeypatch, capsys):
    """P1+P6: --no-vulns + --fail-on-vuln must error out cleanly."""
    from piptastic.cli import EXIT_ERROR
    exit_code = main([
        "audit", str(FIXTURES / "req_only"),
        "--no-vulns", "--fail-on-vuln", "any",
    ])
    assert exit_code == EXIT_ERROR


def test_filter_helper_vulnerable_only_and_drift_min():
    """P5: _filter_audits drops non-matching deps and empty projects."""
    from datetime import datetime, timezone
    from packaging.specifiers import SpecifierSet
    from piptastic.cli import _filter_audits
    from piptastic.models import (
        Dep, DepAudit, DepSource, PinStatus, Project, ProjectAudit,
        SemverDrift, SourceKind, Vulnerability,
    )

    src = DepSource(kind=SourceKind.REQUIREMENTS_TXT, path=Path("r.txt"), group="default")
    proj = Project(name="p", path=Path("/p"), python_version=None,
                   python_source=None, python_constraints=None, dep_sources=(src,))
    def _dep(name):
        return Dep(name=name, raw_name=name, specifier=SpecifierSet(),
                   extras=frozenset(), marker=None, source=src, line_no=1, url=None)

    vuln_dep = DepAudit(
        dep=_dep("flask"),
        installed=None, latest=None, latest_including_prereleases=None,
        drift=SemverDrift.PATCH, pin_status=PinStatus.PINNED, yanked=False,
        warnings=(), vulnerabilities=(Vulnerability(id="X", aliases=(), fix_versions=(), description=""),),
    )
    clean_minor = DepAudit(
        dep=_dep("requests"),
        installed=None, latest=None, latest_including_prereleases=None,
        drift=SemverDrift.MINOR, pin_status=PinStatus.PINNED, yanked=False,
        warnings=(),
    )
    clean_none = DepAudit(
        dep=_dep("sqlalchemy"),
        installed=None, latest=None, latest_including_prereleases=None,
        drift=SemverDrift.NONE, pin_status=PinStatus.PINNED, yanked=False,
        warnings=(),
    )
    pa = ProjectAudit(project=proj, deps=[vuln_dep, clean_minor, clean_none], pinning_score=1.0)

    # vulnerable-only keeps only flask
    out = _filter_audits([pa], vulnerable_only=True, drift_min=None)
    assert len(out) == 1 and {d.dep.name for d in out[0].deps} == {"flask"}

    # drift-min minor keeps flask and requests
    out = _filter_audits([pa], vulnerable_only=False, drift_min="minor")
    assert {d.dep.name for d in out[0].deps} == {"requests"}  # flask drift is patch

    # combined: must satisfy both
    out = _filter_audits([pa], vulnerable_only=True, drift_min="minor")
    assert out == []  # no dep is both vulnerable AND minor+ drift


def test_no_vulns_skips_vuln_client_build(monkeypatch, capsys):
    """P6: --no-vulns must not even construct the vuln client (no pip-audit call)."""
    from piptastic import cli as cli_mod

    class FakeClient:
        def fetch_many(self, names): return {}
        def fetch_one(self, name): return None
    calls = {"vuln_built": 0}
    def boom(args):
        calls["vuln_built"] += 1
        raise AssertionError("vuln client should not be built with --no-vulns")
    monkeypatch.setattr(cli_mod, "_build_client", lambda args: FakeClient())
    monkeypatch.setattr(cli_mod, "_build_vuln_client", boom)

    exit_code = main(["audit", str(FIXTURES / "req_only"), "--no-vulns"])
    assert exit_code == 0
    assert calls["vuln_built"] == 0


def test_no_apply_cve_floor_flag_parses():
    """P10: --apply-cve-floor positive flag is gone; --no-apply-cve-floor remains."""
    parser = build_parser()
    args = parser.parse_args(["update", ".", "--no-apply-cve-floor"])
    assert args.apply_cve_floor is False
    # Default is True
    args = parser.parse_args(["update", "."])
    assert args.apply_cve_floor is True
    # Old positive flag now errors
    with pytest.raises(SystemExit):
        parser.parse_args(["update", ".", "--apply-cve-floor"])


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


def test_stats_subcommand_smoke(monkeypatch, capsys, tmp_path):
    """Stats produces a non-empty terminal report against fixtures with a
    monkey-patched PyPI client."""
    from piptastic import cli as cli_mod

    class FakeClient:
        def fetch_many(self, names):
            return {}
        def fetch_one(self, name):
            return None

    class FakeVulnClient:
        unreachable: list = []
        def fetch_for(self, pkgs):
            return {}

    monkeypatch.setattr(cli_mod, "_build_client", lambda args: FakeClient())
    monkeypatch.setattr(cli_mod, "_build_vuln_client", lambda args: FakeVulnClient())

    exit_code = main(["stats", str(FIXTURES / "req_only")])
    assert exit_code == 0
    captured = capsys.readouterr()
    # Smoke: stats header and some content present
    assert "piptastic stats" in captured.out
    assert "1 projects" in captured.out  # single-project audit produces a 1-project stats


def test_stats_json_smoke(monkeypatch, capsys):
    from piptastic import cli as cli_mod

    class FakeClient:
        def fetch_many(self, names): return {}
        def fetch_one(self, name): return None

    monkeypatch.setattr(cli_mod, "_build_client", lambda args: FakeClient())

    exit_code = main(["stats", str(FIXTURES), "--json"])
    assert exit_code == 0
    captured = capsys.readouterr()
    import json as _json
    payload = _json.loads(captured.out)
    assert payload["schema_version"] == 3
    assert payload["kind"] == "stats"
    assert payload["totals"]["project_count"] >= 1
