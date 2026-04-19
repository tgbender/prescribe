from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import typer

from config_helper.orchestrator import Orchestrator
from config_helper.spec import SpecError, SpecLoader
from config_helper.state import StateStore

app = typer.Typer(help="Manage declarative config file changes.", add_completion=False)

_STATUS_COLORS = {
    "applied": typer.colors.GREEN,
    "rolled-back": typer.colors.GREEN,
    "noop": typer.colors.BRIGHT_BLACK,
    "skipped": typer.colors.BRIGHT_BLACK,
    "dry-run": typer.colors.CYAN,
    "conflict": typer.colors.YELLOW,
    "error": typer.colors.RED,
}


def _make_store(state: Optional[Path]) -> StateStore:
    if state is not None:
        return StateStore(state)
    env = os.environ.get("CONFIG_HELPER_STATE")
    if env:
        return StateStore(env)
    return StateStore(Path.home() / ".local" / "share" / "config-helper" / "state.db")


def _status_line(status: str, path: str, detail: str | None = None) -> str:
    color = _STATUS_COLORS.get(status, typer.colors.WHITE)
    label = typer.style(f"{status:<12}", fg=color, bold=True)
    line = label + path
    if detail:
        line += f"  — {detail}"
    return line


@app.command()
def apply(
    spec: Path = typer.Argument(..., help="Path to the spec TOML file."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan changes without writing."),
    output_json: bool = typer.Option(False, "--json", help="Output results as JSON."),
    state: Optional[Path] = typer.Option(None, "--state", envvar="CONFIG_HELPER_STATE", help="Path to state database."),
) -> None:
    """Apply a spec file to its target config files."""
    try:
        spec_obj = SpecLoader().load(spec)
    except SpecError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)

    results = Orchestrator(_make_store(state)).run(spec, dry_run=dry_run)

    if output_json:
        data = []
        for target, result in zip(spec_obj.targets, results):
            entry: dict = {
                "path": str(target.path),
                "status": result.status,
                "applied": result.applied,
                "changed": result.changed,
            }
            if result.error:
                entry["error"] = result.error
            if result.conflict:
                entry["conflict"] = result.conflict.reason
            data.append(entry)
        typer.echo(json.dumps(data, indent=2))
    else:
        for target, result in zip(spec_obj.targets, results):
            detail = (
                result.conflict.reason if result.conflict
                else result.error or None
            )
            typer.echo(_status_line(result.status, str(target.path), detail))

    if any(r.status in {"error", "conflict"} for r in results):
        raise typer.Exit(1)


@app.command()
def rollback(
    path: Path = typer.Argument(..., help="Path to the config file to roll back."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be rolled back."),
    output_json: bool = typer.Option(False, "--json", help="Output result as JSON."),
    state: Optional[Path] = typer.Option(None, "--state", envvar="CONFIG_HELPER_STATE", help="Path to state database."),
) -> None:
    """Roll back managed changes to a config file."""
    result = Orchestrator(_make_store(state)).rollback(path, dry_run=dry_run)

    if output_json:
        data: dict = {
            "path": str(path),
            "status": result.status,
            "applied": result.applied,
            "changed": result.changed,
        }
        if result.error:
            data["error"] = result.error
        typer.echo(json.dumps(data, indent=2))
    else:
        typer.echo(_status_line(result.status, str(path), result.error))

    if result.status == "error":
        raise typer.Exit(1)


def main() -> None:
    app()
