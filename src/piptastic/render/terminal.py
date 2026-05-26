"""Terminal renderer using `rich`. Three views: tree, table, summary."""

from __future__ import annotations

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


def render_terminal(
    audits: Iterable[ProjectAudit],
    *,
    mode: ViewMode = "tree",
    console: Console | None = None,
) -> None:
    """Render audits to the terminal. Default view is `tree`."""
    console = console or Console()
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
    table = Table(title="piptastic — summary", show_lines=False)
    table.add_column("Project")
    table.add_column("Py", justify="right")
    table.add_column("Pin score", justify="right")
    table.add_column("Major", justify="right", style="red")
    table.add_column("Minor", justify="right", style="orange3")
    table.add_column("Patch", justify="right", style="yellow")
    table.add_column("Yanked", justify="right", style="red")
    table.add_column("Deps", justify="right")

    for a in audits:
        table.add_row(
            a.project.name,
            a.project.python_version or "—",
            _format_pin_score(a.pinning_score),
            str(a.drift_summary.get(SemverDrift.MAJOR, 0)),
            str(a.drift_summary.get(SemverDrift.MINOR, 0)),
            str(a.drift_summary.get(SemverDrift.PATCH, 0)),
            str(a.yanked_count),
            str(len(a.deps)),
        )
    console.print(table)


def _render_table(audits: list[ProjectAudit], console: Console) -> None:
    table = Table(title="piptastic — packages", show_lines=False)
    table.add_column("Project")
    table.add_column("File")
    table.add_column("Group")
    table.add_column("Package")
    table.add_column("Current")
    table.add_column("Latest")
    table.add_column("Drift")
    table.add_column("Pin")
    table.add_column("Notes")

    for a in audits:
        for d in a.deps:
            drift_text = f"[{DRIFT_STYLE[d.drift]}]{d.drift.value}[/{DRIFT_STYLE[d.drift]}]"
            notes = ", ".join(d.warnings) if d.warnings else ""
            if d.yanked:
                notes = "yanked" + (f"; {notes}" if notes else "")
            current = _current_str(d)
            latest = str(d.latest) if d.latest else "—"
            table.add_row(
                a.project.name,
                d.dep.source.path.name,
                d.dep.source.group,
                d.dep.name,
                current,
                latest,
                drift_text,
                d.pin_status.value,
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
    latest = str(d.latest) if d.latest else "—"
    yanked_mark = " [red strike]yanked[/red strike]" if d.yanked else ""
    return (
        f"{d.dep.name:<25} "
        f"{current:<14} → {latest:<10}  "
        f"[{style}]{drift:<7}[/{style}]  "
        f"{d.pin_status.value}"
        f"{yanked_mark}"
    )


def _current_str(d: DepAudit) -> str:
    for clause in d.dep.specifier:
        if clause.operator == "==":
            return clause.version
    if d.installed is not None:
        return f"({d.installed})"
    return "—"
