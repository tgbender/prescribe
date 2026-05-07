import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import typer

from prescribe import __version__
from prescribe.claims import (
    ClaimConflict,
    ClaimResolver,
    compute_claims,
    detect_internal_claim_conflicts,
)
from prescribe.core.result import OrchestrationResult
from prescribe.materialize import detect_platform
from prescribe.orchestrator import Orchestrator, condition_matches, condition_skip_reason
from prescribe.paths import config_dir, data_dir, default_state_path
from prescribe.presets import Presets
from prescribe.rollback import ConflictResolver
from prescribe.spec import AssetTarget, EnvTarget, FileTarget, ShellTarget, Spec, SpecError, SpecLoader
from prescribe.state import NetworkStatePathError, StateStore

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


def _make_store(state: Path | None, *, allow_network_state: bool = False) -> StateStore:
    try:
        if state is not None:
            return StateStore(state, allow_network_state=allow_network_state)
        env = os.environ.get("PRESCRIBE_STATE")
        if env:
            return StateStore(env, allow_network_state=allow_network_state)
        return StateStore(default_state_path(), allow_network_state=allow_network_state)
    except NetworkStatePathError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None


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
    allow_network_state: bool = typer.Option(
        False,
        "--allow-network-state",
        help="Allow the SQLite state database on a network filesystem.",
    ),
    on_claim_conflict: str | None = typer.Option(
        None,
        "--on-claim-conflict",
        help="Behavior when another spec owns a claim: prompt (TTY default), fail (non-TTY default), or take.",
    ),
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
    orchestrator = Orchestrator(_make_store(state, allow_network_state=allow_network_state))
    claim_resolver = _make_claim_resolver(on_claim_conflict)
    if explain:
        if claim_resolver is None:
            results = orchestrator.run(spec_obj, dry_run=dry_run, tags=tag_set, skip_tags=skip_set, explain_skips=True)
        else:
            results = orchestrator.run(
                spec_obj,
                dry_run=dry_run,
                tags=tag_set,
                skip_tags=skip_set,
                explain_skips=True,
                claim_resolver=claim_resolver,
            )
    elif claim_resolver is None:
        results = orchestrator.run(spec_obj, dry_run=dry_run, tags=tag_set, skip_tags=skip_set)
    else:
        results = orchestrator.run(
            spec_obj,
            dry_run=dry_run,
            tags=tag_set,
            skip_tags=skip_set,
            claim_resolver=claim_resolver,
        )

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
    plan: bool = typer.Option(False, "--plan", help="Show whether active targets would change."),
    diff: bool = typer.Option(False, "--diff", help="Include diffs when used with --plan."),
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
    claim_conflicts = detect_internal_claim_conflicts(
        compute_claims(
            spec_obj,
            include=lambda target: _claim_target_matches(target, tags=tag_set, skip_tags=skip_set),
        )
    )
    if claim_conflicts:
        message = _claim_conflict_message(claim_conflicts[0])
        if output_json:
            typer.echo(json.dumps({"valid": False, "error": message, "targets": []}, indent=2))
        else:
            typer.echo(f"error: {message}", err=True)
        raise typer.Exit(1)

    targets = _validation_targets(
        spec_obj,
        tags=tag_set,
        skip_tags=skip_set,
        explain_skips=explain,
        plan=_option_bool(plan),
        diff=_option_bool(diff),
    )
    had_plan_error = _targets_have_plan_errors(targets)

    if output_json:
        typer.echo(json.dumps({"valid": not had_plan_error, "targets": targets}, indent=2))
        if had_plan_error:
            raise typer.Exit(1)
        return

    active = sum(1 for target in targets if target["active"])
    skipped = len(targets) - active
    typer.echo(f"{'error' if had_plan_error else 'valid'}: {spec}")
    typer.echo(f"targets: {active} active, {skipped} skipped")
    for target in targets:
        status = "active" if target["active"] else "skipped"
        label = typer.style(f"{target['type']:<6}", fg=typer.colors.BRIGHT_BLACK)
        detail = target.get("path") or target.get("name") or ""
        if plan and target.get("status"):
            status = str(target["status"])
        if plan and target.get("error"):
            detail = f"{detail}  — {target['error']}"
        reason = f"  — {target['skip_reason']}" if explain and target.get("skip_reason") else ""
        typer.echo(f"{label}  {status:<7}  {detail}{reason}")
        if plan and target.get("diff"):
            typer.echo(str(target["diff"]), nl=False)
    if had_plan_error:
        raise typer.Exit(1)


@app.command("list-specs")
def list_specs_cmd(
    directory: Path = typer.Argument(..., help="Directory containing top-level spec TOML files."),
    output_json: bool = typer.Option(False, "--json", help="Output discovered specs as JSON."),
) -> None:
    """List top-level specs in deterministic apply order."""
    specs = Presets().list_specs(directory)
    if output_json:
        typer.echo(json.dumps([str(spec) for spec in specs], indent=2))
        return
    if not specs:
        typer.echo("no spec files")
        return
    for spec in specs:
        typer.echo(str(spec))


@app.command("apply-dir")
def apply_dir(
    directory: Path = typer.Argument(..., help="Directory containing top-level spec TOML files."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan changes without writing."),
    output_json: bool = typer.Option(False, "--json", help="Output results as JSON."),
    state: Path | None = typer.Option(None, "--state", envvar="PRESCRIBE_STATE", help="Path to state database."),
    tags: str | None = typer.Option(None, "--tags", help="Only apply targets with these tags (comma-separated)."),
    skip_tags: str | None = typer.Option(None, "--skip-tags", help="Skip targets with these tags (comma-separated)."),
    explain_skips: bool = typer.Option(False, "--explain-skips", help="Show why targets were skipped."),
    allow_network_state: bool = typer.Option(
        False,
        "--allow-network-state",
        help="Allow the SQLite state database on a network filesystem.",
    ),
    on_claim_conflict: str | None = typer.Option(
        None,
        "--on-claim-conflict",
        help="Behavior when another spec owns a claim: prompt (TTY default), fail (non-TTY default), or take.",
    ),
) -> None:
    """Apply all top-level spec TOML files in a directory."""
    _run_spec_directory(
        directory,
        dry_run=dry_run,
        output_json=output_json,
        state=state,
        tags=tags,
        skip_tags=skip_tags,
        explain_skips=explain_skips,
        allow_network_state=allow_network_state,
        diff=False,
        display_status=dry_run,
        claim_resolver=_make_claim_resolver(on_claim_conflict),
    )


@app.command("status-dir")
def status_dir(
    directory: Path = typer.Argument(..., help="Directory containing top-level spec TOML files."),
    output_json: bool = typer.Option(False, "--json", help="Output results as JSON."),
    state: Path | None = typer.Option(None, "--state", envvar="PRESCRIBE_STATE", help="Path to state database."),
    tags: str | None = typer.Option(None, "--tags", help="Only show targets with these tags (comma-separated)."),
    skip_tags: str | None = typer.Option(None, "--skip-tags", help="Skip targets with these tags (comma-separated)."),
    diff: bool = typer.Option(False, "--diff", help="Show unified diffs for files that would change."),
    explain_skips: bool = typer.Option(False, "--explain-skips", help="Show why targets were skipped."),
) -> None:
    """Show status for all top-level spec TOML files in a directory."""
    _run_spec_directory(
        directory,
        dry_run=True,
        output_json=output_json,
        state=state,
        tags=tags,
        skip_tags=skip_tags,
        explain_skips=explain_skips,
        diff=diff,
        display_status=True,
    )


@app.command("validate-dir")
def validate_dir(
    directory: Path = typer.Argument(..., help="Directory containing top-level spec TOML files."),
    output_json: bool = typer.Option(False, "--json", help="Output validation summary as JSON."),
    tags: str | None = typer.Option(None, "--tags", help="Only consider targets with these tags (comma-separated)."),
    skip_tags: str | None = typer.Option(None, "--skip-tags", help="Skip targets with these tags (comma-separated)."),
    explain_skips: bool = typer.Option(False, "--explain-skips", help="Show why targets were skipped."),
    plan: bool = typer.Option(False, "--plan", help="Show whether active targets would change."),
    diff: bool = typer.Option(False, "--diff", help="Include diffs when used with --plan."),
) -> None:
    """Validate all top-level spec TOML files in a directory."""
    tag_set = _parse_tags(tags)
    skip_set = _parse_tags(skip_tags)
    explain = _option_bool(explain_skips)
    specs = Presets().list_specs(directory)
    payload: list[dict[str, object]] = []
    had_error = False
    all_claims = []
    for spec_path in specs:
        try:
            spec_obj = SpecLoader().load(spec_path)
            all_claims.extend(
                compute_claims(
                    spec_obj,
                    include=lambda target: _claim_target_matches(target, tags=tag_set, skip_tags=skip_set),
                )
            )
            targets = _validation_targets(
                spec_obj,
                tags=tag_set,
                skip_tags=skip_set,
                explain_skips=explain,
                plan=_option_bool(plan),
                diff=_option_bool(diff),
            )
            plan_error = _targets_have_plan_errors(targets)
            had_error = had_error or plan_error
            item: dict[str, object] = {"spec": str(spec_path), "valid": not plan_error, "targets": targets}
            if plan_error:
                item["error"] = _first_target_error(targets) or "validation plan failed"
            payload.append(item)
        except SpecError as exc:
            had_error = True
            payload.append({"spec": str(spec_path), "valid": False, "error": str(exc), "targets": []})
    claim_conflicts = detect_internal_claim_conflicts(all_claims)
    if claim_conflicts:
        had_error = True
        for conflict in claim_conflicts:
            payload.append(
                {
                    "spec": conflict.claim.spec_path or "",
                    "valid": False,
                    "error": _claim_conflict_message(conflict),
                    "targets": [],
                }
            )

    if output_json:
        typer.echo(json.dumps({"valid": not had_error, "specs": payload}, indent=2))
    else:
        for item in payload:
            typer.echo(f"{'valid' if item['valid'] else 'error'}: {item['spec']}")
            if not item["valid"]:
                typer.echo(f"  {item['error']}")
                continue
            targets_obj = item["targets"]
            if not isinstance(targets_obj, list):
                raise RuntimeError("validation directory payload has unexpected shape")
            active = sum(1 for target in targets_obj if isinstance(target, dict) and target["active"])
            skipped = len(targets_obj) - active
            typer.echo(f"  targets: {active} active, {skipped} skipped")
            for target in targets_obj:
                if not isinstance(target, dict):
                    raise RuntimeError("validation target has unexpected shape")
                status = "active" if target["active"] else "skipped"
                if plan and target.get("status"):
                    status = str(target["status"])
                detail = target.get("path") or target.get("dest") or target.get("name") or ""
                if plan and target.get("error"):
                    detail = f"{detail}  — {target['error']}"
                reason = f"  — {target['skip_reason']}" if explain and target.get("skip_reason") else ""
                typer.echo(f"  {str(target['type']):<6}  {status:<7}  {detail}{reason}")
                if plan and target.get("diff"):
                    typer.echo(str(target["diff"]), nl=False)

    if had_error:
        raise typer.Exit(1)


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


@app.command()
def doctor(
    output_json: bool = typer.Option(False, "--json", help="Output diagnostics as JSON."),
    state: Path | None = typer.Option(None, "--state", envvar="PRESCRIBE_STATE", help="Path to state database."),
) -> None:
    """Report platform, path, shell, and materialization diagnostics."""
    data = _doctor_data(state)
    if output_json:
        typer.echo(json.dumps(data, indent=2))
        return

    typer.echo(f"prescribe: {data['version']}")
    typer.echo(f"platform: {data['platform']}")
    typer.echo(f"materialize backend: {data['materialize_backend']}")
    typer.echo(f"state path: {data['state_path']}")
    typer.echo(f"data dir: {data['data_dir']}")
    typer.echo(f"config dir: {data['config_dir']}")
    typer.echo(f"path separator: {data['path_separator']}")
    shells = data["available_shells"]
    tools = data["tools"]
    if not isinstance(shells, list) or not isinstance(tools, dict):
        raise RuntimeError("doctor diagnostics have unexpected shape")
    typer.echo(f"available shells: {', '.join(str(shell) for shell in shells)}")
    typer.echo(f"uv: {tools.get('uv') or 'not found'}")
    typer.echo(f"mise: {tools.get('mise') or 'not found'}")


def _make_conflict_resolver(on_conflict: str | None) -> ConflictResolver:
    effective = on_conflict or ("prompt" if sys.stdin.isatty() else "ignore")
    if effective == "revert":
        return lambda key: True
    if effective == "prompt":
        return lambda key: typer.confirm(f"  '{key}' was externally modified. Revert anyway?")
    return None


def _make_claim_resolver(on_claim_conflict: str | None) -> ClaimResolver | None:
    effective = on_claim_conflict or ("prompt" if sys.stdin.isatty() else "fail")
    if effective == "take":
        return lambda _conflict: True
    if effective == "prompt":
        return lambda conflict: typer.confirm(f"{_claim_conflict_message(conflict)}. Take ownership?")
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
    allow_network_state: bool = typer.Option(
        False,
        "--allow-network-state",
        help="Allow the SQLite state database on a network filesystem.",
    ),
) -> None:
    """Roll back managed changes to a config file."""
    result = Orchestrator(_make_store(state, allow_network_state=allow_network_state)).rollback(
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
    # Only the synthetic env result (when no files, shell, or assets) consumes a slot.
    if spec_obj.env and not spec_obj.files and not spec_obj.shell and not spec_obj.assets:
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

    for asset_target in spec_obj.assets:
        if idx >= len(results):
            break
        r = results[idx]
        idx += 1
        asset_entry: dict[str, object] = {
            "type": "asset",
            "source": asset_target.source,
            "dest": str(asset_target.dest),
            "mode": asset_target.mode,
            "replace": asset_target.replace,
            "status": _display_status(r.status, r.changed) if display_status else r.status,
            "applied": r.applied,
            "changed": r.changed,
        }
        if r.error:
            asset_entry["error"] = r.error
        if r.conflict:
            asset_entry["conflict"] = r.conflict.reason
        if r.diff:
            asset_entry["diff"] = r.diff
        if explain_skips and r.skip_reason:
            asset_entry["skip_reason"] = r.skip_reason
        data.append(asset_entry)

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
    # Only the synthetic env result (when no files, shell, or assets) consumes a slot.
    active_env = 0
    skipped_env = 0
    if spec_obj.env and not spec_obj.files and not spec_obj.shell and not spec_obj.assets:
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

    for asset_target in spec_obj.assets:
        if idx >= len(results):
            break
        result = results[idx]
        idx += 1
        detail = result.conflict.reason if result.conflict else result.error or asset_target.source
        if explain_skips and result.skip_reason:
            detail = result.skip_reason
        typer.echo(_status_line(result.status, result.changed, str(asset_target.dest), detail))
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


def _run_spec_directory(
    directory: Path,
    *,
    dry_run: bool,
    output_json: bool,
    state: Path | None,
    tags: str | None,
    skip_tags: str | None,
    explain_skips: bool,
    diff: bool,
    display_status: bool,
    allow_network_state: bool = False,
    claim_resolver: ClaimResolver | None = None,
) -> None:
    tag_set = _parse_tags(tags)
    skip_set = _parse_tags(skip_tags)
    explain = _option_bool(explain_skips)
    store = _make_store(state, allow_network_state=allow_network_state)
    loader = SpecLoader()
    specs = Presets(state_store=store, loader=loader).list_specs(directory)
    payload: list[dict[str, object]] = []
    display_items: list[tuple[Path, Spec | None, list[OrchestrationResult], str | None]] = []
    had_error = False
    loaded_specs: list[tuple[Path, Spec]] = []
    for spec_path in specs:
        try:
            spec_obj = loader.load(spec_path)
        except SpecError as exc:
            had_error = True
            payload.append({"spec": str(spec_path), "error": str(exc), "results": []})
            display_items.append((spec_path, None, [], str(exc)))
            continue
        loaded_specs.append((spec_path, spec_obj))
    claim_conflicts = detect_internal_claim_conflicts(
        [
            claim
            for _, spec_obj in loaded_specs
            for claim in compute_claims(
                spec_obj,
                include=lambda target: _claim_target_matches(target, tags=tag_set, skip_tags=skip_set),
            )
        ]
    )
    if claim_conflicts:
        had_error = True
        for conflict in claim_conflicts:
            payload.append(
                {"spec": conflict.claim.spec_path or "", "error": _claim_conflict_message(conflict), "results": []}
            )
            display_items.append((Path(conflict.claim.spec_path or ""), None, [], _claim_conflict_message(conflict)))

    if not had_error:
        for spec_path, spec_obj in loaded_specs:
            results = Orchestrator(store).run(
                spec_obj,
                dry_run=dry_run,
                tags=tag_set,
                skip_tags=skip_set,
                diff=diff,
                explain_skips=explain,
                claim_resolver=claim_resolver,
            )
            if any(r.status in {"error", "conflict"} for r in results):
                had_error = True
            payload.append(
                {
                    "spec": str(spec_path),
                    "results": _results_to_json(
                        spec_obj,
                        results,
                        display_status=display_status,
                        explain_skips=explain,
                    ),
                }
            )
            display_items.append((spec_path, spec_obj, results, None))

    if output_json:
        typer.echo(json.dumps(payload, indent=2))
    else:
        if not payload:
            typer.echo("no spec files")
        for spec_path, display_spec, results, error in display_items:
            typer.echo(f"spec: {spec_path}")
            if error is not None:
                typer.echo(_status_line("error", False, str(spec_path), error))
                continue
            if display_spec is None:
                raise RuntimeError("directory display payload is missing spec object")
            _print_results(display_spec, results, explain_skips=explain)

    if had_error:
        raise typer.Exit(1)


def _option_bool(value: object) -> bool:
    return value if isinstance(value, bool) else False


def _claim_target_matches(target: object, *, tags: set[str] | None, skip_tags: set[str] | None) -> bool:
    if not isinstance(target, (FileTarget, EnvTarget, ShellTarget, AssetTarget)):
        return False
    return condition_matches(target=target, tags=tags, skip_tags=skip_tags)


def _claim_conflict_message(conflict: ClaimConflict) -> str:
    if conflict.message is not None:
        return conflict.message
    claim = conflict.claim
    return (
        f"claim conflict: {claim.target_type} {claim.subject} {claim.address} is managed by {conflict.existing_owner}"
    )


def _validation_targets(
    spec_obj: Spec,
    *,
    tags: set[str] | None = None,
    skip_tags: set[str] | None = None,
    explain_skips: bool = False,
    plan: bool = False,
    diff: bool = False,
) -> list[dict[str, object]]:
    plan_results = (
        _validation_plan_results(spec_obj, tags=tags, skip_tags=skip_tags, diff=diff, explain_skips=explain_skips)
        if plan
        else []
    )
    result_idx = 0
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
        if plan and result_idx < len(plan_results):
            result = plan_results[result_idx]
            result_idx += 1
            entry["status"] = _display_status(result.status, result.changed)
            entry["changed"] = result.changed
            if result.error:
                entry["error"] = result.error
            elif result.conflict is not None:
                entry["error"] = result.conflict.reason
            if result.diff:
                entry["diff"] = result.diff
        targets.append(entry)
    if spec_obj.env and not spec_obj.files and not spec_obj.shell and not spec_obj.assets and plan:
        result_idx += 1
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
        if plan and result_idx < len(plan_results):
            result = plan_results[result_idx]
            result_idx += 1
            entry["status"] = _display_status(result.status, result.changed)
            entry["changed"] = result.changed
            if result.error:
                entry["error"] = result.error
            elif result.conflict is not None:
                entry["error"] = result.conflict.reason
            if result.diff:
                entry["diff"] = result.diff
        targets.append(entry)
    for asset_target in spec_obj.assets:
        active = condition_matches(target=asset_target, tags=tags, skip_tags=skip_tags)
        entry = {
            "type": "asset",
            "source": asset_target.source,
            "dest": str(asset_target.dest),
            "mode": asset_target.mode,
            "replace": asset_target.replace,
            "active": active,
        }
        if explain_skips and not active:
            entry["skip_reason"] = condition_skip_reason(target=asset_target, tags=tags, skip_tags=skip_tags)
        if plan and result_idx < len(plan_results):
            result = plan_results[result_idx]
            result_idx += 1
            entry["status"] = _display_status(result.status, result.changed)
            entry["changed"] = result.changed
            if result.error:
                entry["error"] = result.error
            elif result.conflict is not None:
                entry["error"] = result.conflict.reason
            if result.diff:
                entry["diff"] = result.diff
        targets.append(entry)
    return targets


def _targets_have_plan_errors(targets: list[dict[str, object]]) -> bool:
    return any(target.get("active") and target.get("status") in {"error", "conflict"} for target in targets)


def _first_target_error(targets: list[dict[str, object]]) -> str | None:
    for target in targets:
        error = target.get("error")
        if isinstance(error, str):
            return error
    return None


def _validation_plan_results(
    spec_obj: Spec,
    *,
    tags: set[str] | None,
    skip_tags: set[str] | None,
    diff: bool,
    explain_skips: bool,
) -> list[OrchestrationResult]:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        store = StateStore(Path(tmpdir) / "state.db")
        return Orchestrator(store).run(
            spec_obj, dry_run=True, tags=tags, skip_tags=skip_tags, diff=diff, explain_skips=explain_skips
        )


def _doctor_data(state: Path | None) -> dict[str, object]:
    try:
        materialize_backend = detect_platform()
    except Exception as exc:
        materialize_backend = f"unavailable: {exc}"
    return {
        "version": __version__,
        "platform": sys.platform,
        "materialize_backend": materialize_backend,
        "state_path": str(_make_store(state).path),
        "data_dir": str(data_dir()),
        "config_dir": str(config_dir()),
        "path_separator": os.pathsep,
        "available_shells": ["bash", "zsh", "fish", "nu", "xonsh", "pwsh", "cmd"],
        "tools": {
            "uv": shutil.which("uv"),
            "mise": shutil.which("mise"),
        },
    }
