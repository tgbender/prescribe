import json
import os
import sys
from pathlib import Path

import typer

from prescribe import __version__
from prescribe.core.result import OrchestrationResult
from prescribe.orchestrator import Orchestrator, condition_matches, condition_skip_reason
from prescribe.paths import default_state_path
from prescribe.rollback import ConflictResolver
from prescribe.spec import Spec, SpecError, SpecLoader
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
    tags: str | None = typer.Option(None, "--tags", help="Only apply targets with these tags (comma-separated)."),
    skip_tags: str | None = typer.Option(None, "--skip-tags", help="Skip targets with these tags (comma-separated)."),
    explain_skips: bool = typer.Option(False, "--explain-skips", help="Show why targets were skipped."),
) -> None:
    """Apply a spec file to its target config files, env vars, and shell blocks."""
    try:
        spec_obj = SpecLoader().load(spec)
    except SpecError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None

    tag_set = _parse_tags(tags)
    skip_set = _parse_tags(skip_tags)
    explain = _option_bool(explain_skips)
    orchestrator = Orchestrator(_make_store(state))
    if explain:
        results = orchestrator.run(spec_obj, dry_run=dry_run, tags=tag_set, skip_tags=skip_set, explain_skips=True)
    else:
        results = orchestrator.run(spec_obj, dry_run=dry_run, tags=tag_set, skip_tags=skip_set)

    if output_json:
        data = _results_to_json(spec_obj, results, display_status=False, explain_skips=explain)
        typer.echo(json.dumps(data, indent=2))
    else:
        _print_results(spec_obj, results, explain_skips=explain)

    if any(r.status in {"error", "conflict"} for r in results):
        raise typer.Exit(1)


@app.command()
def status(
    spec: Path = typer.Argument(..., help="Path to the spec TOML file."),
    output_json: bool = typer.Option(False, "--json", help="Output results as JSON."),
    state: Path | None = typer.Option(None, "--state", envvar="PRESCRIBE_STATE", help="Path to state database."),
    tags: str | None = typer.Option(None, "--tags", help="Only show targets with these tags (comma-separated)."),
    skip_tags: str | None = typer.Option(None, "--skip-tags", help="Skip targets with these tags (comma-separated)."),
    diff: bool = typer.Option(False, "--diff", help="Show unified diffs for files that would change."),
    explain_skips: bool = typer.Option(False, "--explain-skips", help="Show why targets were skipped."),
) -> None:
    """Show sync status of all targets without making changes."""
    try:
        spec_obj = SpecLoader().load(spec)
    except SpecError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None

    tag_set = _parse_tags(tags)
    skip_set = _parse_tags(skip_tags)
    explain = _option_bool(explain_skips)
    orchestrator = Orchestrator(_make_store(state))
    if explain:
        results = orchestrator.run(
            spec_obj, dry_run=True, tags=tag_set, skip_tags=skip_set, diff=diff, explain_skips=True
        )
    else:
        results = orchestrator.run(spec_obj, dry_run=True, tags=tag_set, skip_tags=skip_set, diff=diff)

    if output_json:
        data = _results_to_json(spec_obj, results, display_status=True, explain_skips=explain)
        typer.echo(json.dumps(data, indent=2))
    else:
        _print_results(spec_obj, results, explain_skips=explain)


@app.command()
def validate(
    spec: Path = typer.Argument(..., help="Path to the spec TOML file."),
    output_json: bool = typer.Option(False, "--json", help="Output validation summary as JSON."),
    tags: str | None = typer.Option(None, "--tags", help="Only consider targets with these tags (comma-separated)."),
    skip_tags: str | None = typer.Option(None, "--skip-tags", help="Skip targets with these tags (comma-separated)."),
    explain_skips: bool = typer.Option(False, "--explain-skips", help="Show why targets were skipped."),
) -> None:
    """Validate a spec and show which targets would be considered."""
    try:
        spec_obj = SpecLoader().load(spec)
    except SpecError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None

    tag_set = _parse_tags(tags)
    skip_set = _parse_tags(skip_tags)
    explain = _option_bool(explain_skips)
    targets = _validation_targets(spec_obj, tags=tag_set, skip_tags=skip_set, explain_skips=explain)

    if output_json:
        typer.echo(json.dumps({"valid": True, "targets": targets}, indent=2))
        return

    active = sum(1 for target in targets if target["active"])
    skipped = len(targets) - active
    typer.echo(f"valid: {spec}")
    typer.echo(f"targets: {active} active, {skipped} skipped")
    for target in targets:
        status = "active" if target["active"] else "skipped"
        label = typer.style(f"{target['type']:<6}", fg=typer.colors.BRIGHT_BLACK)
        detail = target.get("path") or target.get("name") or ""
        reason = f"  — {target['skip_reason']}" if explain and target.get("skip_reason") else ""
        typer.echo(f"{label}  {status:<7}  {detail}{reason}")


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


def _results_to_json(
    spec_obj: Spec,
    results: list[OrchestrationResult],
    *,
    display_status: bool = False,
    explain_skips: bool = False,
) -> list[dict[str, object]]:
    data: list[dict[str, object]] = []
    idx = 0

    for file_target in spec_obj.files:
        if idx >= len(results):
            break
        r = results[idx]
        idx += 1
        entry: dict[str, object] = {
            "type": "file",
            "path": str(file_target.path),
            "status": _display_status(r.status, r.changed) if display_status else r.status,
            "applied": r.applied,
            "changed": r.changed,
        }
        if r.error:
            entry["error"] = r.error
        if r.conflict:
            entry["conflict"] = r.conflict.reason
        if r.diff:
            entry["diff"] = r.diff
        if explain_skips and r.skip_reason:
            entry["skip_reason"] = r.skip_reason
        data.append(entry)

    # env entries don't produce individual results (merged into shell results).
    # Only the synthetic env result (when no files and no shells) consumes a slot.
    if spec_obj.env and not spec_obj.files and not spec_obj.shell:
        idx += 1

    for shell_target in spec_obj.shell:
        if idx >= len(results):
            break
        r = results[idx]
        idx += 1
        shell_entry: dict[str, object] = {
            "type": "shell",
            "path": str(shell_target.path),
            "status": _display_status(r.status, r.changed) if display_status else r.status,
            "applied": r.applied,
            "changed": r.changed,
        }
        if r.error:
            shell_entry["error"] = r.error
        if r.conflict:
            shell_entry["conflict"] = r.conflict.reason
        if r.diff:
            shell_entry["diff"] = r.diff
        if explain_skips and r.skip_reason:
            shell_entry["skip_reason"] = r.skip_reason
        if r.materialize_errors:
            shell_entry["materialize_errors"] = r.materialize_errors
        data.append(shell_entry)

    # Include materialize_errors from any result in the top-level output
    all_mat_errors: list[str] = []
    for r in results:
        if r.materialize_errors:
            for err in r.materialize_errors:
                if err not in all_mat_errors:
                    all_mat_errors.append(err)
    if all_mat_errors:
        data.append(
            {
                "type": "materialize",
                "errors": all_mat_errors,
            }
        )

    return data


def _print_results(spec_obj: Spec, results: list[OrchestrationResult], *, explain_skips: bool = False) -> None:
    idx = 0

    for file_target in spec_obj.files:
        if idx >= len(results):
            break
        result = results[idx]
        idx += 1
        detail = result.conflict.reason if result.conflict else result.error or None
        if explain_skips and result.skip_reason:
            detail = result.skip_reason
        typer.echo(_status_line(result.status, result.changed, str(file_target.path), detail))
        if result.diff:
            typer.echo(result.diff, nl=False)

    # Env entries are merged — show them as a summary.
    # Only the synthetic env result (when no files and no shells) consumes a slot.
    active_env = 0
    skipped_env = 0
    if spec_obj.env and not spec_obj.files and not spec_obj.shell:
        r = results[idx] if idx < len(results) else None
        if r is not None:
            idx += 1
            if r.skipped:
                skipped_env += 1
            else:
                active_env += 1

    if spec_obj.env:
        parts: list[str] = []
        if active_env:
            parts.append(f"{active_env} active")
        if skipped_env:
            parts.append(f"{skipped_env} skipped")
        label = ", ".join(parts) if parts else "none"
        typer.echo(typer.style("env            ", fg=typer.colors.BRIGHT_BLACK) + label)

    for shell_target in spec_obj.shell:
        if idx >= len(results):
            break
        result = results[idx]
        idx += 1
        detail = result.conflict.reason if result.conflict else result.error or None
        if explain_skips and result.skip_reason:
            detail = result.skip_reason
        typer.echo(_status_line(result.status, result.changed, str(shell_target.path), detail))
        if result.diff:
            typer.echo(result.diff, nl=False)

    # Surface materialize errors
    all_mat_errors: list[str] = []
    for r in results:
        if r.materialize_errors:
            for err in r.materialize_errors:
                if err not in all_mat_errors:
                    all_mat_errors.append(err)
    for err in all_mat_errors:
        typer.echo(
            typer.style("materialize      ", fg=typer.colors.YELLOW) + err,
            err=True,
        )


def main() -> None:
    app()


def _parse_tags(raw: str | None) -> set[str] | None:
    """Parse comma-separated tags string into a set, or None if empty."""
    if not raw:
        return None
    return {t.strip() for t in raw.split(",") if t.strip()}


def _option_bool(value: object) -> bool:
    return value if isinstance(value, bool) else False


def _validation_targets(
    spec_obj: Spec,
    *,
    tags: set[str] | None = None,
    skip_tags: set[str] | None = None,
    explain_skips: bool = False,
) -> list[dict[str, object]]:
    targets: list[dict[str, object]] = []
    for file_target in spec_obj.files:
        active = condition_matches(target=file_target, tags=tags, skip_tags=skip_tags)
        entry: dict[str, object] = {
            "type": "file",
            "path": str(file_target.path),
            "format": file_target.format,
            "active": active,
        }
        if explain_skips and not active:
            entry["skip_reason"] = condition_skip_reason(target=file_target, tags=tags, skip_tags=skip_tags)
        targets.append(entry)
    for env_target in spec_obj.env:
        active = condition_matches(target=env_target, tags=tags, skip_tags=skip_tags)
        entry = {"type": "env", "name": env_target.name, "active": active}
        if explain_skips and not active:
            entry["skip_reason"] = condition_skip_reason(target=env_target, tags=tags, skip_tags=skip_tags)
        targets.append(entry)
    for shell_target in spec_obj.shell:
        active = condition_matches(target=shell_target, tags=tags, skip_tags=skip_tags)
        entry = {"type": "shell", "path": str(shell_target.path), "shells": shell_target.shells, "active": active}
        if explain_skips and not active:
            entry["skip_reason"] = condition_skip_reason(target=shell_target, tags=tags, skip_tags=skip_tags)
        targets.append(entry)
    return targets
