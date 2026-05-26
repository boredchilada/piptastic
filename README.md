# piptastic

Audit Python dependency posture across all projects in a tree. Read-only by
default — think Watchtower for your `requirements.txt` files.

## Install

```bash
pip install .
# or
pipx install .
```

Provides two commands: `piptastic` and `ptc` (short alias).

## Usage

```bash
piptastic audit ~/projects               # scan tree, pretty terminal report
piptastic audit . --json                 # same, machine-readable
piptastic list ./myproject               # one project, table view
piptastic update ./myproject             # mutates requirements.txt (with backup)
```

See `piptastic --help` for the full reference.

## Status

v0.2 — see `docs/superpowers/specs/` for the design.
