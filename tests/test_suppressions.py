"""Tests for [tool.piptastic.suppressions] config loading and matching."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from packaging.version import Version

from piptastic.models import Vulnerability
from piptastic.suppressions import (
    SuppressionRule,
    WILDCARD,
    find_rule,
    load_suppressions,
)


def _v(id_: str, *aliases: str) -> Vulnerability:
    return Vulnerability(
        id=id_, aliases=tuple(aliases),
        fix_versions=(Version("1.0.0"),), description="",
    )


def test_load_from_pyproject_basic(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.piptastic]\n'
        '[[tool.piptastic.suppressions]]\n'
        'package = "flask"\n'
        'cve = "PYSEC-2023-62"\n'
        'reason = "we do not use sessions"\n'
        'expires = "2099-12-31"\n',
        encoding="utf-8",
    )
    rules = load_suppressions(tmp_path)
    assert len(rules) == 1
    r = rules[0]
    assert r.package == "flask"
    assert r.cve == "PYSEC-2023-62"


def test_load_from_piptastic_toml_fallback(tmp_path: Path):
    (tmp_path / ".piptastic.toml").write_text(
        '[[suppressions]]\n'
        'package = "requests"\n'
        'cve = "CVE-2024-1234"\n'
        'reason = "patched at the proxy"\n'
        'expires = "2099-01-01"\n',
        encoding="utf-8",
    )
    rules = load_suppressions(tmp_path)
    assert len(rules) == 1
    assert rules[0].package == "requests"


def test_expired_rule_ignored(tmp_path: Path, caplog):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.piptastic]\n'
        f'[[tool.piptastic.suppressions]]\n'
        f'package = "flask"\n'
        f'cve = "CVE-OLD"\n'
        f'reason = "stale"\n'
        f'expires = "{yesterday}"\n',
        encoding="utf-8",
    )
    rules = load_suppressions(tmp_path)
    assert rules == []


def test_missing_required_fields_ignored(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.piptastic]\n'
        '[[tool.piptastic.suppressions]]\n'
        'package = "flask"\n'
        'cve = "X"\n'
        # missing reason + expires
        ,
        encoding="utf-8",
    )
    rules = load_suppressions(tmp_path)
    assert rules == []


def test_alias_match():
    r = SuppressionRule(
        package="flask", cve="CVE-2023-30861",
        reason="x", expires=date(2099, 1, 1),
    )
    v = _v("PYSEC-2023-62", "CVE-2023-30861", "GHSA-m2qf-hxjv-5gpq")
    assert r.matches("flask", v.id, v.aliases) is True


def test_wildcard_package():
    r = SuppressionRule(
        package=WILDCARD, cve="PYSEC-2024-1",
        reason="cross-project accepted", expires=date(2099, 1, 1),
    )
    assert r.matches("flask", "PYSEC-2024-1") is True
    assert r.matches("requests", "PYSEC-2024-1") is True
    assert r.matches("flask", "OTHER") is False


def test_package_canonicalized():
    r = SuppressionRule(
        package="Flask", cve="X", reason="r", expires=date(2099, 1, 1),
    )
    # SuppressionRule stores canonical form, but matches() also normalizes
    rule = SuppressionRule(
        package="flask", cve="X", reason="r", expires=date(2099, 1, 1),
    )
    assert rule.matches("FLASK", "X")
    assert rule.matches("flask", "X")


def test_find_rule_returns_first_match():
    rules = [
        SuppressionRule(package="flask", cve="A", reason="r1", expires=date(2099, 1, 1)),
        SuppressionRule(package="flask", cve="B", reason="r2", expires=date(2099, 1, 1)),
    ]
    v = _v("B")
    found = find_rule(rules, package="flask", vuln=v)
    assert found is not None and found.reason == "r2"


def test_load_returns_empty_when_no_config(tmp_path: Path):
    assert load_suppressions(tmp_path) == []


def test_malformed_toml_is_ignored(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("this is [not valid", encoding="utf-8")
    rules = load_suppressions(tmp_path)
    assert rules == []
