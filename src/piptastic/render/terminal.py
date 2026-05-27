# SPDX-License-Identifier: AGPL-3.0-or-later
"""Terminal renderer using `rich`. Three views: tree, table, summary."""

from __future__ import annotations

import sys
from typing import Iterable, Literal

from rich.console import Console
from rich.table import Table
from rich.tree import Tree

from piptastic.models import DepAudit, PinStatus, ProjectAudit, SemverDrift

ViewMode = Literal["tree", "table", "summary"]

DRIFT_STYLE = {
    SemverDrift.NONE:    "green",
    SemverDrift.BUILD:   "dim",
    SemverDrift.PATCH:   "yellow",
    SemverDrift.MINOR:   "orange3",
    SemverDrift.MAJOR:   "red",
    SemverDrift.EPOCH:   "magenta",
    SemverDrift.UNKNOWN: "white",
}


def _stdout_is_utf8() -> bool:
    """Return True when sys.stdout.encoding is UTF-8 family."""
    enc = (getattr(sys.stdout, "encoding", "") or "").lower()
    return "utf" in enc


def _make_console() -> Console:
    """Construct a Console with safe_box=True when stdout cannot encode the
    fancy box-drawing + ellipsis characters rich uses by default (e.g.
    Windows cp1252)."""
    return Console(safe_box=not _stdout_is_utf8())


def render_terminal(
    audits: Iterable[ProjectAudit],
    *,
    mode: ViewMode = "tree",
    console: Console | None = None,
) -> None:
    """Render audits to the terminal. Default view is `tree`."""
    console = console or _make_console()
    audits = list(audits)

    if not audits:
        console.print("[yellow]No Python projects found.[/yellow]")
        return

    if mode == "summary":
        _render_summary(audits, console)
    elif mode == "table":
        _render_table(audits, console)
    else:
        _render_tree(audits, console)


def _format_pin_score(score: float | None) -> str:
    if score is None:
        return "n/a"
    return f"{score:.0%}"


def _render_summary(audits: list[ProjectAudit], console: Console) -> None:
    table = Table(title="piptastic - summary", show_lines=False)
    table.add_column("Project")
    table.add_column("Py", justify="right")
    table.add_column("Pin score", justify="right")
    table.add_column("Major", justify="right", style="red")
    table.add_column("Minor", justify="right", style="orange3")
    table.add_column("Patch", justify="right", style="yellow")
    table.add_column("Yanked", justify="right", style="red")
    table.add_column("Vulns", justify="right", style="bold red")
    table.add_column("Deps", justify="right")

    for a in audits:
        table.add_row(
            a.project.name,
            a.project.python_version or "-",
            _format_pin_score(a.pinning_score),
            str(a.drift_summary.get(SemverDrift.MAJOR, 0)),
            str(a.drift_summary.get(SemverDrift.MINOR, 0)),
            str(a.drift_summary.get(SemverDrift.PATCH, 0)),
            str(a.yanked_count),
            str(a.vuln_count),
            str(len(a.deps)),
        )
    console.print(table)


def _render_table(audits: list[ProjectAudit], console: Console) -> None:
    table = Table(title="piptastic - packages", show_lines=False)
    table.add_column("Project")
    table.add_column("File")
    table.add_column("Group")
    table.add_column("Package")
    table.add_column("Current")
    table.add_column("Latest")
    table.add_column("Min safe")
    table.add_column("Drift")
    table.add_column("Pin")
    table.add_column("Vulns", justify="right")
    table.add_column("Notes")

    for a in audits:
        for d in a.deps:
            drift_text = f"[{DRIFT_STYLE[d.drift]}]{d.drift.value}[/{DRIFT_STYLE[d.drift]}]"
            notes = ", ".join(d.warnings) if d.warnings else ""
            if d.yanked:
                notes = "yanked" + (f"; {notes}" if notes else "")
            current = _current_str(d)
            latest = str(d.latest) if d.latest else "-"
            min_safe = str(d.min_safe_version) if d.min_safe_version else "-"
            vuln_n = len(d.vulnerabilities)
            vuln_cell = f"[bold red]{vuln_n}[/bold red]" if vuln_n else "-"
            table.add_row(
                a.project.name,
                d.dep.source.path.name,
                d.dep.source.group,
                d.dep.name,
                current,
                latest,
                min_safe,
                drift_text,
                d.pin_status.value,
                vuln_cell,
                notes,
            )
    console.print(table)


def _render_tree(audits: list[ProjectAudit], console: Console) -> None:
    root = Tree(f"[bold]{len(audits)} project(s)[/bold]")
    for a in audits:
        header = (
            f"[bold]{a.project.name}[/bold]   "
            f"py{a.project.python_version or '?'}   "
            f"pin: {_format_pin_score(a.pinning_score)}   "
            f"deps: {len(a.deps)}"
        )
        if a.yanked_count:
            header += f"   [red]yanked: {a.yanked_count}[/red]"
        if a.vuln_count:
            header += f"   [bold red]vulns: {a.vuln_count}[/bold red]"
        pnode = root.add(header)
        by_file: dict[str, list[DepAudit]] = {}
        for d in a.deps:
            by_file.setdefault(d.dep.source.path.name, []).append(d)
        for fname, items in by_file.items():
            fnode = pnode.add(f"[cyan]{fname}[/cyan]   ({len(items)} deps)")
            for d in items:
                fnode.add(_dep_line(d))
    console.print(root)


def _dep_line(d: DepAudit) -> str:
    drift = d.drift.value
    style = DRIFT_STYLE[d.drift]
    current = _current_str(d)
    latest = str(d.latest) if d.latest else "-"
    yanked_mark = " [red strike]yanked[/red strike]" if d.yanked else ""
    vuln_mark = ""
    if d.vulnerabilities:
        safe = f" min-safe {d.min_safe_version}" if d.min_safe_version else ""
        vuln_mark = f"  [bold red]vulns: {len(d.vulnerabilities)}[/bold red]{safe}"
    return (
        f"{d.dep.name:<25} "
        f"{current:<14} -> {latest:<10}  "
        f"[{style}]{drift:<7}[/{style}]  "
        f"{d.pin_status.value}"
        f"{yanked_mark}"
        f"{vuln_mark}"
    )


def _current_str(d: DepAudit) -> str:
    for clause in d.dep.specifier:
        if clause.operator == "==":
            return clause.version
    if d.installed is not None:
        return f"({d.installed})"
    return "-"


# ---------- stats renderer ----------

def render_stats_terminal(report, *, console: Console | None = None) -> None:
    """Render a StatsReport to the terminal as a series of rich Tables."""
    console = console or _make_console()
    root_label = str(report.root)
    console.print(
        f"[bold]piptastic stats[/bold] - {root_label} "
        f"({report.project_count} projects, {report.total_deps} deps)\n"
    )

    # Top packages
    if report.top_packages:
        t = Table(title=f"Top {len(report.top_packages)} most-required packages", show_lines=False)
        t.add_column("Package")
        t.add_column("Projects", justify="right")
        t.add_column("Sample of projects")
        for p in report.top_packages:
            sample = ", ".join(p.projects[:5])
            if len(p.projects) > 5:
                sample += f", ... (+{len(p.projects) - 5})"
            t.add_row(p.name, str(p.project_count), sample)
        console.print(t)

    # Version fragmentation
    if report.version_fragmentation:
        t = Table(title="Most version-fragmented packages", show_lines=False)
        t.add_column("Package")
        t.add_column("Distinct versions")
        for vf in report.version_fragmentation:
            pieces = []
            for ver, projs in vf.versions.items():
                pieces.append(f"=={ver} ({len(projs)})")
            t.add_row(vf.name, ", ".join(pieces))
        console.print(t)

    # Drift histogram
    drift_pieces = []
    for level in (SemverDrift.NONE, SemverDrift.BUILD, SemverDrift.PATCH,
                  SemverDrift.MINOR, SemverDrift.MAJOR, SemverDrift.EPOCH,
                  SemverDrift.UNKNOWN):
        count = report.drift_histogram.get(level, 0)
        if count:
            style = DRIFT_STYLE.get(level, "white")
            drift_pieces.append(f"[{style}]{level.value}: {count}[/{style}]")
    if drift_pieces:
        console.print("Drift across the tree:  " + "  ".join(drift_pieces))

    # Pin posture histogram
    pin_pieces = []
    for status in (PinStatus.PINNED, PinStatus.COMPATIBLE, PinStatus.RANGE,
                   PinStatus.FLOOR, PinStatus.UNPINNED, PinStatus.URL):
        count = report.pin_status_histogram.get(status, 0)
        if count:
            pin_pieces.append(f"{status.value}: {count}")
    if pin_pieces:
        console.print("Pin posture across the tree:  " + "  ".join(pin_pieces))

    # Yanked findings
    if report.yanked_findings:
        t = Table(title=f"Yanked pins ({len(report.yanked_findings)})", show_lines=False)
        t.add_column("Project")
        t.add_column("Package")
        t.add_column("Pinned")
        t.add_column("Latest non-yanked")
        for y in report.yanked_findings:
            t.add_row(
                y.project_name, y.package_name,
                f"=={y.pinned_version}" if y.pinned_version else "-",
                y.latest_non_yanked or "-",
            )
        console.print(t)

    # Unpinned projects
    if report.unpinned_projects:
        console.print(
            f"\nUnpinned projects (deps >= 5):  "
            + ", ".join(report.unpinned_projects)
        )

    # Footer with the project count even when empty
    if report.project_count == 0:
        console.print("[dim]0 projects in audit.[/dim]")
