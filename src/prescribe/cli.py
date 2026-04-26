import json
import os
import sys
from pathlib import Path

import typer

from prescribe import __version__
from prescribe.orchestrator import Orchestrator
from prescribe.paths import default_state_path
from prescribe.rollback import ConflictResolver
from prescribe.spec import SpecError, SpecLoader
from prescribe.state import StateStore

app = typer.Typer(help="Manage declarative config file changes.", add_completion=False)

_STATUS_COLORS = {
    "applied": typer.colors.GREEN,
    "rolled-back": typer.colors.GREEN,
    "in sync": typer.colors.BRIGHT_BLACK,
    "skipped": typer.colors.BRIGHT_BLACK,
    "would change": typer.colors.CYAN,
    "conflict": typer.colors.YELLOW,
    "error": typer.colors.RED,
}


def _display_status(status: str, changed: bool) -> str:
    if status in {"noop", "dry-run"}:
        return "would change" if changed else "in sync"
    return status


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"prescribe {__version__}")
        raise typer.Exit()


@app.callback()
def _callback(
    version: bool = typer.Option(
        None,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    pass


def _make_store(state: Path | None) -> StateStore:
    if state is not None:
        return StateStore(state)
    env = os.environ.get("PRESCRIBE_STATE")
    if env:
        return StateStore(env)
    return StateStore(default_state_path())


def _status_line(status: str, changed: bool, path: str, detail: str | None = None) -> str:
    display = _display_status(status, changed)
    color = _STATUS_COLORS.get(display, typer.colors.WHITE)
    label = typer.style(f"{display:<14}", fg=color, bold=True)
    line = label + path
    if detail:
        line += f"  — {detail}"
    return line


@app.command()
def apply(
    spec: Path = typer.Argument(..., help="Path to the spec TOML file."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan changes without writing."),
    output_json: bool = typer.Option(False, "--json", help="Output results as JSON."),
    state: Path | None = typer.Option(None, "--state", envvar="PRESCRIBE_STATE", help="Path to state database."),
) -> None:
    """Apply a spec file to its target config files."""
    try:
        spec_obj = SpecLoader().load(spec)
    except SpecError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None

    results = Orchestrator(_make_store(state)).run(spec, dry_run=dry_run)

    if output_json:
        data = []
        for target, result in zip(spec_obj.targets, results, strict=True):
            entry: dict[str, object] = {
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
        for target, result in zip(spec_obj.targets, results, strict=True):
            detail = result.conflict.reason if result.conflict else result.error or None
            typer.echo(_status_line(result.status, result.changed, str(target.path), detail))

    if any(r.status in {"error", "conflict"} for r in results):
        raise typer.Exit(1)


@app.command()
def status(
    spec: Path = typer.Argument(..., help="Path to the spec TOML file."),
    output_json: bool = typer.Option(False, "--json", help="Output results as JSON."),
    state: Path | None = typer.Option(None, "--state", envvar="PRESCRIBE_STATE", help="Path to state database."),
) -> None:
    """Show sync status of all targets without making changes."""
    try:
        spec_obj = SpecLoader().load(spec)
    except SpecError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None

    results = Orchestrator(_make_store(state)).run(spec, dry_run=True)

    if output_json:
        data = []
        for target, result in zip(spec_obj.targets, results, strict=True):
            entry: dict[str, object] = {
                "path": str(target.path),
                "status": _display_status(result.status, result.changed),
                "changed": result.changed,
            }
            if result.error:
                entry["error"] = result.error
            data.append(entry)
        typer.echo(json.dumps(data, indent=2))
    else:
        for target, result in zip(spec_obj.targets, results, strict=True):
            typer.echo(
                _status_line(
                    result.status,
                    result.changed,
                    str(target.path),
                    result.error or None,
                )
            )


@app.command(name="list")
def list_managed(
    output_json: bool = typer.Option(False, "--json", help="Output results as JSON."),
    state: Path | None = typer.Option(None, "--state", envvar="PRESCRIBE_STATE", help="Path to state database."),
) -> None:
    """List all config files managed by prescribe."""
    store = _make_store(state)
    if not store.path.exists():
        if output_json:
            typer.echo("[]")
        else:
            typer.echo("no managed files", err=True)
        return

    store.initialize()
    records = store.list_managed()

    if output_json:
        typer.echo(
            json.dumps(
                [
                    {
                        "path": str(r.path),
                        "format": r.format,
                        "last_applied_at": r.last_applied_at.isoformat(),
                        "exists": r.path.exists(),
                    }
                    for r in records
                ],
                indent=2,
            )
        )
        return

    if not records:
        typer.echo("no managed files")
        return

    for r in records:
        fmt = typer.style(f"{r.format or '?':<8}", fg=typer.colors.BRIGHT_BLACK)
        date = r.last_applied_at.strftime("%Y-%m-%d %H:%M")
        missing = typer.style("  (deleted)", fg=typer.colors.RED) if not r.path.exists() else ""
        typer.echo(f"{fmt}  {date}  {r.path}{missing}")


def _make_conflict_resolver(on_conflict: str | None) -> ConflictResolver:
    effective = on_conflict or ("prompt" if sys.stdin.isatty() else "ignore")
    if effective == "revert":
        return lambda key: True
    if effective == "prompt":
        return lambda key: typer.confirm(f"  '{key}' was externally modified. Revert anyway?")
    return None


@app.command()
def rollback(
    path: Path = typer.Argument(..., help="Path to the config file to roll back."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be rolled back."),
    output_json: bool = typer.Option(False, "--json", help="Output result as JSON."),
    state: Path | None = typer.Option(None, "--state", envvar="PRESCRIBE_STATE", help="Path to state database."),
    on_conflict: str | None = typer.Option(
        None,
        "--on-conflict",
        help="Behavior when a key was externally modified: prompt (default when TTY), revert, or ignore.",
    ),
    original: bool = typer.Option(False, "--original", help="Restore to pre-prescribe state (before first apply)."),
) -> None:
    """Roll back managed changes to a config file."""
    result = Orchestrator(_make_store(state)).rollback(
        path, dry_run=dry_run, original=original, conflict_resolver=_make_conflict_resolver(on_conflict)
    )

    if output_json:
        data: dict[str, object] = {
            "path": str(path),
            "status": result.status,
            "applied": result.applied,
            "changed": result.changed,
        }
        if result.error:
            data["error"] = result.error
        typer.echo(json.dumps(data, indent=2))
    else:
        typer.echo(_status_line(result.status, result.changed, str(path), result.error))

    if result.status == "error":
        raise typer.Exit(1)


def main() -> None:
    app()
