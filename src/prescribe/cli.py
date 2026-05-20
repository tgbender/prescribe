import json
import os
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Callable
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

app = typer.Typer(
    help=(
        "Manage files, environment variables, shell startup blocks, and assets from TOML specs. "
        "Start with validate, status, then apply; use docs for terminology."
    ),
    add_completion=False,
)

DIRECT_CHILD_SPEC_HELP = "Directory containing direct child *.toml specs; nested directories are ignored."
DOCS_TOPIC_HELP = "Topic to explain. Run without a topic to list available docs."

_STATUS_COLORS = {
    "applied": typer.colors.GREEN,
    "rolled-back": typer.colors.GREEN,
    "in sync": typer.colors.BRIGHT_BLACK,
    "skipped": typer.colors.BRIGHT_BLACK,
    "would change": typer.colors.CYAN,
    "conflict": typer.colors.YELLOW,
    "error": typer.colors.RED,
}

def _text(*parts: str) -> str:
    return " ".join(parts)


_DOC_TOPICS: dict[str, dict[str, object]] = {
    "overview": {
        "title": "Overview",
        "summary": _text(
            "Prescribe applies declarative TOML specs to files, shell startup blocks,",
            "assets, and environment state.",
        ),
        "body": [
            _text(
                "A spec describes desired changes. Prescribe plans those changes, writes them,",
                "and records enough state to detect drift and undo managed changes later.",
            ),
            _text(
                "The normal workflow is: validate a spec, inspect status or a plan, apply it,",
                "then use status, list, backups, or rollback when you need to inspect or recover state.",
            ),
            "`SPEC` means a TOML spec file path. `TARGET` means a managed filesystem path recorded in state.",
            "Command help is intentionally short. Use `prescribe docs TOPIC` for examples and terminology.",
        ],
        "examples": [
            "prescribe validate dotfiles.toml --plan",
            "prescribe status dotfiles.toml --diff",
            "prescribe apply dotfiles.toml",
            "prescribe docs rollback",
        ],
    },
    "specs": {
        "title": "Specs And Targets",
        "summary": _text(
            "Specs are TOML files. Targets are the files, env vars, shell blocks,",
            "or assets selected from a spec.",
        ),
        "body": [
            _text(
                "`SPEC` is a filesystem path to one TOML spec. Relative paths are resolved",
                "by the shell and spec loader from the current working directory.",
            ),
            _text(
                "A matching target is a target that passes filters such as tags, platform,",
                "architecture, machine, command checks, and env checks.",
            ),
            _text(
                "Selectors are conjunctive: platform/machine/command/env checks must pass,",
                "`--tags` must match when provided, and `--skip-tags` removes matching targets.",
            ),
            "Tags are comma-separated on the CLI: `--tags base,work`. Repeating `--tags` is not the intended syntax.",
            "File targets manage structured config keys or whole managed text blocks.",
            _text(
                "Structured file targets update only the managed keys they name. Managed text blocks replace only",
                "the fenced block with the same block id.",
            ),
            "Env targets describe environment variables for shell rendering or supported OS-level materialization.",
            "Shell targets write managed startup blocks, such as a fenced block in a shell rc file.",
            "Asset targets materialize repo-owned files. Mirror assets copy a source tree into a destination root.",
        ],
        "examples": [
            "prescribe validate ./prescribe.toml --explain-skips",
            "prescribe apply ./prescribe.toml --tags base,work",
            "prescribe status ./prescribe.toml --skip-tags experimental",
        ],
    },
    "target": {
        "title": "Target Paths",
        "summary": "`TARGET` means a filesystem path recorded in Prescribe state, not a spec id or system PATH.",
        "body": [
            _text(
                "`TARGET` appears on commands such as `rollback TARGET` and `backups TARGET`.",
                "It can be absolute or relative; Prescribe normalizes it before matching recorded state.",
            ),
            _text(
                "For rollback, a target can be a managed config file, a managed asset file,",
                "a displaced extra file, or an asset mirror destination root.",
            ),
            _text(
                "For backups, a target filters recovery backup records by the file path that was",
                "about to be overwritten or deleted.",
            ),
            "`TARGET` is unrelated to the shell `PATH` variable and is not the name of a target inside a TOML spec.",
            "Use `list` to see paths Prescribe has recorded in the selected state database.",
        ],
        "examples": [
            "prescribe list",
            "prescribe rollback ~/.gitconfig --dry-run",
            "prescribe backups ~/.gitconfig",
            "prescribe rollback ~/.codex/skills --original",
        ],
    },
    "selectors": {
        "title": "Selectors",
        "summary": "Selectors decide whether a target participates in apply, status, validate, and directory runs.",
        "body": [
            _text(
                "Selectors are conjunctive. A target must pass its platform, architecture, machine,",
                "command, env, tag, and skip-tag checks to be active.",
            ),
            "Common platform names are `windows`, `linux`, `macos`, and `wsl`.",
            "Common architecture names are `x86_64`, `arm64`, `x86`, `armv7`, and `all`.",
            _text(
                "Machine selectors compare against the hostname, or against `PRESCRIBE_MACHINE`",
                "when that environment variable is set.",
            ),
            "`if_command_exists` checks whether a command is discoverable for the current process.",
            "`if_env_missing` selects a target only when the named environment variable is absent.",
            _text(
                "`--tags` is comma-separated, such as `--tags base,work`.",
                "`--skip-tags` removes matching targets even when other selectors pass.",
            ),
            "`--explain-skips` reports which selector or priority rule skipped a target.",
        ],
        "examples": [
            "prescribe validate dotfiles.toml --tags base,work --explain-skips",
            "prescribe status-dir specs --skip-tags experimental",
            "PRESCRIBE_MACHINE=laptop prescribe apply dotfiles.toml",
        ],
    },
    "dirs": {
        "title": "Directory Commands",
        "summary": "Directory commands process direct child *.toml specs in lexicographic path order.",
        "body": [
            "`DIRECTORY` is scanned only for direct child `*.toml` files. Nested directories are ignored.",
            _text(
                "`list-specs DIRECTORY` shows the exact specs and order.",
                "The order is Python's default lexicographic path sort for the discovered direct child specs.",
            ),
            "`apply-dir` first preflights the directory so conflicts can stop the run before writes begin.",
            "Directory specs are processed as one coordinated run for claim checks and preflight conflict detection.",
        ],
        "examples": [
            "prescribe list-specs specs",
            "prescribe validate-dir specs --plan --explain-skips",
            "prescribe apply-dir specs --dry-run",
            "prescribe apply-dir specs",
        ],
    },
    "list": {
        "title": "List",
        "summary": "List reports paths that have recorded Prescribe change history in the state database.",
        "body": [
            "`list` reads managed state. It does not parse a spec and it does not discover unmanaged files.",
            "`list` is a managed-path index: it reports paths with surviving Prescribe records in state.",
            _text(
                "The output can include paths that no longer exist, because Prescribe still has history",
                "for rollback, backups, claims, or auditing.",
            ),
            "`--json` returns machine-readable records with path metadata from the selected state database.",
            "`--state PATH` lists records from a specific SQLite state database.",
        ],
        "examples": [
            "prescribe list",
            "prescribe list --json",
            "prescribe list --state .prescribe/state.db",
        ],
    },
    "apply": {
        "title": "Apply",
        "summary": "Apply writes the desired state from a spec and records ownership, snapshots, and undo history.",
        "body": [
            _text(
                "`apply SPEC` processes one spec.",
                "`apply-dir DIRECTORY` processes every direct child spec in that directory.",
            ),
            _text(
                "`--dry-run` plans without writing. `--tags` and `--skip-tags` filter targets.",
                "`--explain-skips` shows why skipped targets did not run.",
            ),
            _text(
                "`--on-claim-conflict take` changes the durable ownership record for overlapping",
                "managed keys, blocks, or paths. It does not by itself mean overwrite external file drift.",
            ),
            _text(
                "Apply records ownership, file snapshots, change batches for rollback, target run results,",
                "and recovery backups before overwriting existing files.",
            ),
            "Accepted `--on-claim-conflict` values are `prompt`, `fail`, and `take`.",
        ],
        "examples": [
            "prescribe apply dotfiles.toml --dry-run",
            "prescribe apply dotfiles.toml --tags base",
            "prescribe apply-dir specs --on-claim-conflict prompt",
        ],
    },
    "status": {
        "title": "Status",
        "summary": "Status compares the current system to a spec without writing.",
        "body": [
            _text(
                "`status SPEC` is a read-only sync check. It reports whether each target is in sync,",
                "would change, is skipped, or has a conflict.",
            ),
            "`--diff` includes unified diffs for file-like targets that would change.",
            "Use status when you want to know what apply would do against the real state database.",
        ],
        "examples": [
            "prescribe status dotfiles.toml",
            "prescribe status dotfiles.toml --diff",
            "prescribe status-dir specs --tags windows --explain-skips",
        ],
    },
    "statuses": {
        "title": "Result Statuses",
        "summary": "Result statuses describe whether a target changed, would change, skipped, conflicted, or failed.",
        "body": [
            "`applied` means Prescribe wrote the requested change.",
            "`in sync` means the target already matches the requested state.",
            "`would change` means a read-only command or dry run found a change that would be written.",
            _text(
                "`skipped` means selectors, tags, priority, or duplicate target selection",
                "prevented the target from running.",
            ),
            _text(
                "`conflict` means Prescribe refused to proceed because ownership or file content",
                "did not match recorded state.",
            ),
            "`error` means the target or command failed.",
            "`rolled-back` means rollback applied recorded undo history.",
            "`restored` means a displaced asset-mirror extra file was restored from its recorded backup.",
            _text(
                "JSON output may use internal statuses such as `noop` or `dry-run`; human output",
                "renders those as `in sync` or `would change` where appropriate.",
            ),
        ],
        "examples": [
            "prescribe status dotfiles.toml --diff",
            "prescribe validate-dir specs --plan --explain-skips",
            "prescribe rollback ~/.gitconfig --json",
        ],
    },
    "validate": {
        "title": "Validate",
        "summary": "Validate parses specs and reports target selection without touching managed state.",
        "body": [
            "`validate SPEC` checks one spec. `validate-dir DIRECTORY` checks every direct child spec in a directory.",
            _text(
                "`--plan` adds a read-only plan showing whether matching targets would create, update,",
                "or remain unchanged.",
            ),
            _text(
                "`validate --plan` uses a temporary state database, so it is useful for spec correctness",
                "and previews. Use `status` when you want the current managed-state view.",
            ),
            _text(
                "Spec-level selectors and CLI filters both apply. A target must pass platform, architecture,",
                "machine, command, env, tag, and skip-tag checks to be processed.",
            ),
            _text(
                "`status` is the live-state read-only check. `validate --plan` is a spec-oriented preview",
                "that intentionally avoids mutating or depending on the managed state database.",
            ),
        ],
        "examples": [
            "prescribe validate dotfiles.toml",
            "prescribe validate dotfiles.toml --plan --diff",
            "prescribe validate-dir specs --plan --explain-skips",
        ],
    },
    "rollback": {
        "title": "Rollback",
        "summary": _text(
            "Rollback applies Prescribe's recorded undo history for one managed file,",
            "asset, displaced file, or mirror root.",
        ),
        "body": [
            _text(
                "`TARGET` is a filesystem path. It can be absolute or relative;",
                "Prescribe normalizes it before matching recorded state.",
            ),
            _text(
                "Default rollback undoes Prescribe-managed changes while preserving unrelated edits where possible.",
                "It is not a repair-to-current-spec command.",
            ),
            _text(
                "For structured files, unrelated edits are keys outside Prescribe's recorded managed keys.",
                "For shell/text blocks, unrelated edits are content outside the managed block.",
            ),
            _text(
                "`--original` restores managed content to the state from before Prescribe first managed it.",
                "If Prescribe created the file, it removes that file.",
            ),
            _text(
                "`--on-conflict` applies when rollback finds outside edits inside managed content.",
                "`prompt` asks per entry in a TTY, `revert` applies the recorded undo anyway,",
                "and `ignore` leaves the edited entry alone.",
            ),
            _text(
                "For asset mirrors, pass the mirror root to restore all displaced extra files,",
                "or pass one displaced extra file path to restore just that file.",
            ),
            "Accepted `--on-conflict` values are `prompt`, `revert`, and `ignore`.",
            "An asset mirror root is the destination directory for a mirror asset.",
            "A displaced extra file is a destination file moved aside because it was not in the mirror source.",
            _text(
                "If rollback cannot safely distinguish managed content from outside edits,",
                "it skips or errors rather than guessing.",
            ),
        ],
        "examples": [
            "prescribe rollback ~/.gitconfig --dry-run",
            "prescribe rollback ~/.gitconfig",
            "prescribe rollback ~/.gitconfig --on-conflict revert",
            "prescribe rollback ~/.config/mytool --original",
        ],
    },
    "backups": {
        "title": "Recovery Backups",
        "summary": _text(
            "Recovery backups are byte-for-byte copies captured before Prescribe overwrites",
            "or deletes existing files.",
        ),
        "body": [
            "`backups` is an audit command. It lists recovery backups, not every internal asset-displacement backup.",
            _text(
                "`TARGET` is optional. When provided, it is a filesystem path filter;",
                "Prescribe normalizes the path before matching the recorded target path.",
            ),
            _text(
                "Recovery backups are broader than rollback history. They capture the bytes that existed",
                "immediately before apply, asset writes, rollback writes, and rollback-original deletes.",
            ),
            _text(
                "If Prescribe cannot safely back up existing content before a destructive write,",
                "the operation should fail instead of silently discarding that content.",
            ),
            "Recovery backups are byte copies for recovery/audit; rollback uses recorded undo history.",
            _text(
                "Common backup blockers are unsafe path redirection, file size or binary guards for displacement,",
                "unsupported text encodings for text-aware operations, or inability to write backup storage.",
            ),
            "Current backup commands are for audit and recovery inspection; rollback handles recorded undo operations.",
        ],
        "examples": [
            "prescribe backups",
            "prescribe backups ~/.gitconfig",
            "prescribe backups --json",
        ],
    },
    "claims": {
        "title": "Claims And Conflicts",
        "summary": "Claims record which spec owns a managed file/key/block/asset address.",
        "body": [
            "Claims prevent two specs from silently managing the same address.",
            _text(
                "`--on-claim-conflict fail` refuses to continue. `prompt` asks in a TTY.",
                "`take` updates the durable ownership record to the current spec.",
            ),
            "`take` does not merge specs and does not force overwrites of externally changed file content.",
            _text(
                "`take` means future runs consider the current spec the owner of the same address.",
                "Use status or apply conflict output to handle file drift separately.",
            ),
            _text(
                "Claim conflicts are different from rollback content conflicts.",
                "Claim conflicts are about ownership records; rollback content conflicts are about outside edits",
                "inside managed content.",
            ),
        ],
        "examples": [
            "prescribe apply specs/base.toml --on-claim-conflict fail",
            "prescribe apply specs/work.toml --on-claim-conflict take",
            "prescribe validate-dir specs --explain-skips",
        ],
    },
    "safety": {
        "title": "Safety Model",
        "summary": "Prescribe favors recoverable writes and explicit refusal over destructive filesystem behavior.",
        "body": [
            "Mutating commands use a SQLite run lock and durable ownership claims.",
            "Prescribe refuses unsafe symlink/junction-like paths for managed writes and rollback operations.",
            "Before overwriting or deleting existing files, Prescribe records recovery backups when possible.",
            _text(
                "SQLite state is rejected on network filesystems unless `--allow-network-state`",
                "or `PRESCRIBE_ALLOW_NETWORK_STATE=1` is used.",
            ),
            _text(
                "The state database is created and migrated automatically. It stores runs, claims, snapshots,",
                "rollback history, recovery backups, and asset-displacement records.",
            ),
            _text(
                "When Prescribe says it records backups when possible, unsafe paths, unsupported encodings,",
                "or failed backup writes should stop the operation instead of becoming unrecoverable deletes.",
            ),
        ],
        "examples": [
            "prescribe doctor",
            "prescribe apply dotfiles.toml --dry-run",
            "prescribe backups --json",
        ],
    },
    "doctor": {
        "title": "Doctor",
        "summary": "Doctor reports platform, path, shell, tool, and persistent environment diagnostics.",
        "body": [
            "`doctor` is read-only and does not apply specs.",
            _text(
                "It reports the Prescribe version, detected platform, materialization backend,",
                "state path, data dir, config dir, path separator, available shells, and common tools.",
            ),
            "`--json` returns the same diagnostics in a machine-readable shape.",
            "Use doctor when platform behavior, persistent env support, or state paths look surprising.",
        ],
        "examples": [
            "prescribe doctor",
            "prescribe doctor --json",
            "prescribe doctor --state .prescribe/state.db",
        ],
    },
    "state": {
        "title": "State Database",
        "summary": _text(
            "The SQLite state database stores Prescribe's run history, ownership,",
            "rollback, and backup metadata.",
        ),
        "body": [
            "`--state PATH` selects the SQLite database path. `PRESCRIBE_STATE` provides the same setting by env var.",
            "Prescribe creates and migrates the database automatically when a command needs state.",
            _text(
                "State includes runs, target results, durable ownership claims, file snapshots,",
                "undo history, and backup records.",
            ),
            _text(
                "Commands that only parse specs, such as `validate` without live state, do not need to",
                "create durable managed records.",
            ),
            _text(
                "Network filesystem state paths are refused by default. Use `--allow-network-state`",
                "or `PRESCRIBE_ALLOW_NETWORK_STATE=1` only when you accept that risk.",
            ),
        ],
        "examples": [
            "prescribe list --state .prescribe/state.db",
            "PRESCRIBE_STATE=.prescribe/state.db prescribe status dotfiles.toml",
            "prescribe doctor --json",
        ],
    },
    "materialization": {
        "title": "Materialization",
        "summary": "Materialization means persisting environment values outside the current Prescribe process.",
        "body": [
            "Shell rendering writes managed startup blocks that set environment variables when a shell starts.",
            _text(
                "OS-level materialization writes supported environment variables",
                "into a platform-specific persistent store.",
            ),
            _text(
                "OS-level materialization happens only for env targets that request it and only",
                "when the current platform has a supported backend.",
            ),
            _text(
                "Supported OS-level materialization is platform dependent. Windows uses the user environment;",
                "systemd-based Linux can use user environment.d files;",
                "unsupported platforms should rely on shell blocks.",
            ),
            "`doctor` reports the detected materialization backend and related platform diagnostics.",
            "When materialization is unsupported, specs can still render shell blocks and manage files/assets.",
        ],
        "examples": [
            "prescribe doctor",
            "prescribe docs safety",
            "prescribe docs apply",
        ],
    },
}

_DOC_ALIASES = {
    "command": "overview",
    "commands": "overview",
    "spec": "specs",
    "spec-file": "specs",
    "spec-path": "specs",
    "directory": "dirs",
    "directories": "dirs",
    "dir": "dirs",
    "ownership": "claims",
    "conflict": "claims",
    "conflicts": "claims",
    "recovery": "backups",
    "backup": "backups",
    "target": "target",
    "targets": "target",
    "env": "specs",
    "environment-persistence": "materialization",
    "environment persistence": "materialization",
    "environment": "materialization",
    "materialize": "materialization",
    "materialization": "materialization",
    "behavior": "apply",
    "merge": "specs",
    "selector": "selectors",
    "selectors": "selectors",
    "filter": "selectors",
    "filters": "selectors",
    "result": "statuses",
    "results": "statuses",
    "result-states": "statuses",
    "result states": "statuses",
    "statuses": "statuses",
    "listing": "list",
    "managed": "list",
    "diagnostic": "doctor",
    "diagnostics": "doctor",
    "doctor": "doctor",
    "ordering": "dirs",
    "order": "dirs",
    "database": "state",
    "db": "state",
    "sqlite": "state",
}

STATE_HELP = "Path to SQLite state database. Created and migrated automatically."


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


def _should_prompt_recovery(*, output_json: bool) -> bool:
    return not output_json and sys.stdin.isatty() and sys.stdout.isatty()


def _prompt_recover_interrupted(orchestrator: Orchestrator, *, output_json: bool) -> bool:
    if not _should_prompt_recovery(output_json=output_json):
        return True

    inspection = orchestrator.recover_interrupted(dry_run=True)
    if inspection.status == "noop":
        return True

    typer.echo(_status_line(inspection.status, inspection.changed, "interrupted state", inspection.error), err=True)
    if not typer.confirm("Run recovery now?"):
        return False

    recovered = orchestrator.recover_interrupted(force=True)
    typer.echo(_status_line(recovered.status, recovered.changed, "interrupted state", recovered.error), err=True)
    return recovered.status != "error"


@app.command()
def apply(
    spec: Path = typer.Argument(..., metavar="SPEC_PATH", help="Path to the spec TOML file."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan changes without writing."),
    output_json: bool = typer.Option(False, "--json", help="Output results as JSON."),
    state: Path | None = typer.Option(None, "--state", envvar="PRESCRIBE_STATE", help=STATE_HELP),
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
        help=(
            "Ownership policy when another spec owns the same file/key/block: "
            "prompt, fail, or take. Defaults: prompt in TTY, fail otherwise."
        ),
    ),
) -> None:
    """Write desired state from one TOML spec and record ownership/history."""
    try:
        spec_obj = SpecLoader().load(spec)
    except SpecError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None

    tag_set = _parse_tags(tags)
    skip_set = _parse_tags(skip_tags)
    explain = _option_bool(explain_skips)
    orchestrator = Orchestrator(_make_store(state, allow_network_state=allow_network_state))
    if not dry_run and not _prompt_recover_interrupted(orchestrator, output_json=output_json):
        raise typer.Exit(1)
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
    spec: Path = typer.Argument(..., metavar="SPEC_PATH", help="Path to the spec TOML file."),
    output_json: bool = typer.Option(False, "--json", help="Output results as JSON."),
    state: Path | None = typer.Option(None, "--state", envvar="PRESCRIBE_STATE", help=STATE_HELP),
    tags: str | None = typer.Option(None, "--tags", help="Only show targets with these tags (comma-separated)."),
    skip_tags: str | None = typer.Option(None, "--skip-tags", help="Skip targets with these tags (comma-separated)."),
    diff: bool = typer.Option(False, "--diff", help="Show unified diffs for files that would change."),
    explain_skips: bool = typer.Option(False, "--explain-skips", help="Show why targets were skipped."),
) -> None:
    """Compare the current system to one spec using the current state database."""
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
    spec: Path = typer.Argument(..., metavar="SPEC_PATH", help="Path to the spec TOML file."),
    output_json: bool = typer.Option(False, "--json", help="Output validation summary as JSON."),
    tags: str | None = typer.Option(None, "--tags", help="Only consider targets with these tags (comma-separated)."),
    skip_tags: str | None = typer.Option(None, "--skip-tags", help="Skip targets with these tags (comma-separated)."),
    explain_skips: bool = typer.Option(False, "--explain-skips", help="Show why targets were skipped."),
    plan: bool = typer.Option(
        False,
        "--plan",
        help="Show each matching target and whether it would create, update, or leave unchanged.",
    ),
    diff: bool = typer.Option(False, "--diff", help="With --plan, include unified diffs for changed file targets."),
) -> None:
    """Check one spec without live managed state; use status for the current system view."""
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
    directory: Path = typer.Argument(..., help=DIRECT_CHILD_SPEC_HELP),
    output_json: bool = typer.Option(False, "--json", help="Output discovered specs as JSON."),
) -> None:
    """List direct child *.toml spec files only; nested directories are ignored."""
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
    directory: Path = typer.Argument(..., help=DIRECT_CHILD_SPEC_HELP),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan changes without writing."),
    output_json: bool = typer.Option(False, "--json", help="Output results as JSON."),
    state: Path | None = typer.Option(None, "--state", envvar="PRESCRIBE_STATE", help=STATE_HELP),
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
        help=(
            "Ownership policy when another spec owns the same file/key/block: "
            "prompt, fail, or take. Defaults: prompt in TTY, fail otherwise."
        ),
    ),
) -> None:
    """Apply every direct child *.toml spec in a directory; nested directories are ignored."""
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
    directory: Path = typer.Argument(..., help=DIRECT_CHILD_SPEC_HELP),
    output_json: bool = typer.Option(False, "--json", help="Output results as JSON."),
    state: Path | None = typer.Option(None, "--state", envvar="PRESCRIBE_STATE", help=STATE_HELP),
    tags: str | None = typer.Option(None, "--tags", help="Only show targets with these tags (comma-separated)."),
    skip_tags: str | None = typer.Option(None, "--skip-tags", help="Skip targets with these tags (comma-separated)."),
    diff: bool = typer.Option(False, "--diff", help="Show unified diffs for files that would change."),
    explain_skips: bool = typer.Option(False, "--explain-skips", help="Show why targets were skipped."),
) -> None:
    """Show status for every direct child *.toml spec; nested directories are ignored."""
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
    directory: Path = typer.Argument(..., help=DIRECT_CHILD_SPEC_HELP),
    output_json: bool = typer.Option(False, "--json", help="Output validation summary as JSON."),
    tags: str | None = typer.Option(None, "--tags", help="Only consider targets with these tags (comma-separated)."),
    skip_tags: str | None = typer.Option(None, "--skip-tags", help="Skip targets with these tags (comma-separated)."),
    explain_skips: bool = typer.Option(False, "--explain-skips", help="Show why targets were skipped."),
    plan: bool = typer.Option(
        False,
        "--plan",
        help="Show each matching target and whether it would create, update, or leave unchanged.",
    ),
    diff: bool = typer.Option(False, "--diff", help="With --plan, include unified diffs for changed file targets."),
) -> None:
    """Validate every direct child *.toml spec in a directory; nested directories are ignored."""
    tag_set = _parse_tags(tags)
    skip_set = _parse_tags(skip_tags)
    explain = _option_bool(explain_skips)
    specs = Presets().list_specs(directory)
    payload: list[dict[str, object]] = []
    had_error = False
    all_claims = []
    loaded_specs: list[tuple[Path, Spec]] = []
    for spec_path in specs:
        try:
            spec_obj = SpecLoader().load(spec_path)
            loaded_specs.append((spec_path, spec_obj))
        except SpecError as exc:
            had_error = True
            payload.append({"spec": str(spec_path), "valid": False, "error": str(exc), "targets": []})
    file_skip_reasons = _directory_file_skip_reasons(loaded_specs, tags=tag_set, skip_tags=skip_set)
    for spec_path, spec_obj in loaded_specs:
        try:
            all_claims.extend(
                compute_claims(
                    spec_obj,
                    include=lambda target: _claim_target_matches(
                        target,
                        tags=tag_set,
                        skip_tags=skip_set,
                        file_skip_reasons=file_skip_reasons,
                    ),
                )
            )
            targets = _validation_targets(
                spec_obj,
                tags=tag_set,
                skip_tags=skip_set,
                explain_skips=explain,
                plan=_option_bool(plan),
                diff=_option_bool(diff),
                file_skip_reasons=file_skip_reasons,
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
    state: Path | None = typer.Option(None, "--state", envvar="PRESCRIBE_STATE", help=STATE_HELP),
) -> None:
    """List managed paths with surviving Prescribe records in state."""
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


@app.command("backups")
def list_backups(
    target: Path | None = typer.Argument(
        None,
        metavar="TARGET",
        help="Optional filesystem target path to filter recovery backups; normalized before matching.",
    ),
    output_json: bool = typer.Option(False, "--json", help="Output results as JSON."),
    state: Path | None = typer.Option(None, "--state", envvar="PRESCRIBE_STATE", help=STATE_HELP),
) -> None:
    """List recovery backups only, not rollback undo history."""
    store = _make_store(state)
    if not store.path.exists():
        if output_json:
            typer.echo("[]")
        else:
            typer.echo("no recovery backups", err=True)
        return

    store.initialize()
    records = store.recovery_backups(target)

    if output_json:
        typer.echo(
            json.dumps(
                [
                    {
                        "id": r.id,
                        "run_id": r.run_id,
                        "target_path": str(r.target_path),
                        "target_kind": r.target_kind,
                        "operation": r.operation,
                        "backup_path": str(r.backup_path),
                        "created_at": r.created_at.isoformat(),
                        "hash_algo": r.hash_algo,
                        "content_hash": r.content_hash.hex(),
                        "size": r.size,
                        "mtime_ns": r.mtime_ns,
                        "has_text": r.content_text is not None,
                    }
                    for r in records
                ],
                indent=2,
            )
        )
        return

    if not records:
        typer.echo("no recovery backups")
        return

    for r in records:
        kind = typer.style(f"{r.target_kind:<8}", fg=typer.colors.BRIGHT_BLACK)
        date = r.created_at.strftime("%Y-%m-%d %H:%M")
        size = f"{r.size} bytes"
        typer.echo(f"{kind}  {date}  {r.operation:<24}  {size:<12}  {r.target_path}")


@app.command("recover")
def recover(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show interrupted work without changing recovery state."),
    force: bool = typer.Option(
        False,
        "--force",
        help="Mark interrupted attempts as operator-cleared and expire their claim reservations.",
    ),
    output_json: bool = typer.Option(False, "--json", help="Output result as JSON."),
    state: Path | None = typer.Option(None, "--state", envvar="PRESCRIBE_STATE", help=STATE_HELP),
    allow_network_state: bool = typer.Option(
        False,
        "--allow-network-state",
        help="Allow the SQLite state database on a network filesystem.",
    ),
) -> None:
    """Inspect or clear interrupted apply attempts before running new writes."""
    orchestrator = Orchestrator(_make_store(state, allow_network_state=allow_network_state))
    if force or dry_run or output_json or not _should_prompt_recovery(output_json=output_json):
        result = orchestrator.recover_interrupted(dry_run=dry_run, force=force)
    else:
        result = orchestrator.recover_interrupted(dry_run=True)
        if result.status != "noop":
            typer.echo(_status_line(result.status, result.changed, "interrupted state", result.error))
            if not typer.confirm("Clear interrupted recovery state now?"):
                raise typer.Exit(1)
            result = orchestrator.recover_interrupted(force=True)
    if output_json:
        data: dict[str, object] = {
            "status": result.status,
            "applied": result.applied,
            "changed": result.changed,
            "dry_run": result.dry_run,
        }
        if result.error:
            data["error"] = result.error
        typer.echo(json.dumps(data, indent=2))
    else:
        typer.echo(_status_line(result.status, result.changed, "interrupted state", result.error))
    if result.status == "error":
        raise typer.Exit(1)


@app.command("docs")
def docs(
    topic: str | None = typer.Argument(None, metavar="TOPIC", help=DOCS_TOPIC_HELP),
    output_json: bool = typer.Option(False, "--json", help="Output docs topic as JSON."),
) -> None:
    """Show explanations and examples. Try overview, specs, apply, rollback, or safety."""
    topic_key = _resolve_doc_topic(topic)
    if topic_key is None:
        if output_json:
            typer.echo(json.dumps({"topics": _doc_topic_index(), "aliases": _doc_alias_index()}, indent=2))
            return
        typer.echo(_render_doc_index())
        return
    if topic_key not in _DOC_TOPICS:
        message = f"unknown docs topic: {topic}"
        if output_json:
            typer.echo(json.dumps({"error": message, "topics": _doc_topic_index()}, indent=2))
        else:
            typer.echo(f"error: {message}", err=True)
            typer.echo("Run `prescribe docs` to list topics.", err=True)
        raise typer.Exit(1)

    payload = _DOC_TOPICS[topic_key]
    if output_json:
        typer.echo(json.dumps({"topic": topic_key, **payload}, indent=2))
        return
    typer.echo(_render_doc_topic(topic_key, payload))


@app.command()
def doctor(
    output_json: bool = typer.Option(False, "--json", help="Output diagnostics as JSON."),
    state: Path | None = typer.Option(None, "--state", envvar="PRESCRIBE_STATE", help=STATE_HELP),
) -> None:
    """Report platform, path, shell, and persistent environment diagnostics."""
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
    target: Path = typer.Argument(
        ...,
        metavar="TARGET",
        help=(
            "Managed file, mirror root, or displaced file path. A displaced file was moved aside "
            "because it was not part of the mirror source."
        ),
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what managed changes would be undone."),
    output_json: bool = typer.Option(False, "--json", help="Output result as JSON."),
    state: Path | None = typer.Option(None, "--state", envvar="PRESCRIBE_STATE", help=STATE_HELP),
    on_conflict: str | None = typer.Option(
        None,
        "--on-conflict",
        help=(
            "Rollback policy for outside edits inside managed content: prompt, revert, or ignore. "
            "Defaults: prompt in TTY, ignore otherwise."
        ),
    ),
    original: bool = typer.Option(
        False,
        "--original",
        help=(
            "Restore managed content to the state from before Prescribe first managed it. "
            "If Prescribe created the file, remove it."
        ),
    ),
    allow_network_state: bool = typer.Option(
        False,
        "--allow-network-state",
        help="Allow the SQLite state database on a network filesystem.",
    ),
) -> None:
    """Undo managed changes for TARGET while preserving unrelated edits where possible."""
    orchestrator = Orchestrator(_make_store(state, allow_network_state=allow_network_state))
    if not dry_run and not _prompt_recover_interrupted(orchestrator, output_json=output_json):
        raise typer.Exit(1)
    result = orchestrator.rollback(
        target, dry_run=dry_run, original=original, conflict_resolver=_make_conflict_resolver(on_conflict)
    )

    if output_json:
        data: dict[str, object] = {
            "path": str(target),
            "status": result.status,
            "applied": result.applied,
            "changed": result.changed,
        }
        if result.error:
            data["error"] = result.error
        typer.echo(json.dumps(data, indent=2))
    else:
        typer.echo(_status_line(result.status, result.changed, str(target), result.error))

    if result.status == "error":
        raise typer.Exit(1)


def _results_to_json(
    spec_obj: Spec,
    results: list[OrchestrationResult],
    *,
    display_status: bool = False,
    explain_skips: bool = False,
) -> list[dict[str, object]]:
    if _is_run_level_error(results):
        result = results[0]
        return [
            {
                "type": "error",
                "status": result.status,
                "applied": result.applied,
                "changed": result.changed,
                "error": result.error or "",
            }
        ]

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
    if spec_obj.env and not spec_obj.files and not spec_obj.shell and not spec_obj.assets and idx < len(results):
        r = results[idx]
        idx += 1
        env_entry: dict[str, object] = {
            "type": "env",
            "status": _display_status(r.status, r.changed) if display_status else r.status,
            "applied": r.applied,
            "changed": r.changed,
            "vars": r.env_vars,
        }
        if r.error:
            env_entry["error"] = r.error
        if r.materialize_errors:
            env_entry["materialize_errors"] = r.materialize_errors
        data.append(env_entry)

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
    if _is_run_level_error(results):
        result = results[0]
        typer.echo(_status_line(result.status, result.changed, "run", result.error))
        return

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
    active_env = _resolved_env_count(results)
    skipped_env = 0
    if spec_obj.env and not spec_obj.files and not spec_obj.shell and not spec_obj.assets:
        r = results[idx] if idx < len(results) else None
        if r is not None:
            idx += 1
            if r.skipped:
                skipped_env += 1

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


def _resolved_env_count(results: list[OrchestrationResult]) -> int:
    for result in results:
        if result.env_vars:
            return len(result.env_vars)
    return 0


def _is_run_level_error(results: list[OrchestrationResult]) -> bool:
    if len(results) != 1:
        return False
    result = results[0]
    if result.status != "error" or result.error is None:
        return False
    if result.env_vars or result.materialize_errors:
        return False
    run_level_prefixes = (
        "another prescribe write is active:",
        "claim conflict:",
        "claim reservation conflict:",
        "global lock was lost",
        "global lock heartbeat failed:",
        "interrupted prescribe operation",
        "materialize failed:",
        "portable path collision:",
    )
    return result.error.startswith(run_level_prefixes)


def _resolve_doc_topic(topic: str | None) -> str | None:
    if topic is None:
        return None
    normalized = topic.strip().lower()
    if normalized in _DOC_TOPICS:
        return normalized
    return _DOC_ALIASES.get(normalized, normalized)


def _doc_topic_index() -> list[dict[str, str]]:
    return [
        {
            "topic": key,
            "title": str(value["title"]),
            "summary": str(value["summary"]),
        }
        for key, value in _DOC_TOPICS.items()
    ]


def _doc_alias_index() -> list[dict[str, str]]:
    return [{"alias": alias, "topic": topic} for alias, topic in sorted(_DOC_ALIASES.items()) if alias != topic]


def _render_doc_index() -> str:
    lines = [
        "Prescribe docs",
        "",
        "Run `prescribe docs TOPIC` for explanations and examples.",
        "",
        "Topics:",
    ]
    topic_width = max(len(item["topic"]) for item in _doc_topic_index())
    for item in _doc_topic_index():
        lines.append(f"  {item['topic']:<{topic_width}} {item['summary']}")
    aliases = _doc_alias_index()
    if aliases:
        lines.extend(["", "Aliases:"])
        lines.extend(f"  {item['alias']:<12} -> {item['topic']}" for item in aliases)
    lines.extend(
        [
            "",
            "Common starting points:",
            "  prescribe docs specs",
            "  prescribe docs apply",
            "  prescribe docs rollback",
            "  prescribe docs safety",
        ]
    )
    return "\n".join(lines)


def _render_doc_topic(topic: str, payload: dict[str, object]) -> str:
    lines = [str(payload["title"]), "", str(payload["summary"]), ""]
    body = payload.get("body")
    if isinstance(body, list):
        lines.append("Details:")
        lines.extend(f"  - {item}" for item in body)
        lines.append("")
    examples = payload.get("examples")
    if isinstance(examples, list):
        lines.append("Examples:")
        lines.extend(f"  {example}" for example in examples)
        lines.append("")
    lines.append(f"Topic: {topic}")
    return "\n".join(lines)


def main() -> None:
    _run_app(app)


def _run_app(app_callable: Callable[[], None]) -> None:
    try:
        app_callable()
    except sqlite3.OperationalError as exc:
        message = str(exc)
        if "database is locked" in message.lower():
            typer.echo(f"error: state database is locked: {message}", err=True)
            raise typer.Exit(1) from None
        raise


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
    if not dry_run and not _prompt_recover_interrupted(Orchestrator(store), output_json=output_json):
        raise typer.Exit(1)
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
    file_skip_reasons = _directory_file_skip_reasons(loaded_specs, tags=tag_set, skip_tags=skip_set)
    claim_conflicts = detect_internal_claim_conflicts(
        [
            claim
            for _, spec_obj in loaded_specs
            for claim in compute_claims(
                spec_obj,
                include=lambda target: _claim_target_matches(
                    target,
                    tags=tag_set,
                    skip_tags=skip_set,
                    file_skip_reasons=file_skip_reasons,
                ),
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

    if not had_error and not dry_run:
        preflight_items: list[tuple[Path, Spec, list[OrchestrationResult]]] = []
        for spec_path, spec_obj in loaded_specs:
            results = Orchestrator(store).run(
                spec_obj,
                dry_run=True,
                tags=tag_set,
                skip_tags=skip_set,
                diff=diff,
                explain_skips=explain,
                claim_resolver=claim_resolver,
                file_skip_reasons=file_skip_reasons,
            )
            preflight_items.append((spec_path, spec_obj, results))
            if any(r.status in {"error", "conflict"} for r in results):
                had_error = True

        if had_error:
            for spec_path, spec_obj, results in preflight_items:
                payload.append(
                    {
                        "spec": str(spec_path),
                        "results": _results_to_json(
                            spec_obj,
                            results,
                            display_status=True,
                            explain_skips=explain,
                        ),
                    }
                )
                display_items.append((spec_path, spec_obj, results, None))

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
                file_skip_reasons=file_skip_reasons,
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


def _claim_target_matches(
    target: object,
    *,
    tags: set[str] | None,
    skip_tags: set[str] | None,
    file_skip_reasons: dict[int, str] | None = None,
) -> bool:
    if not isinstance(target, (FileTarget, EnvTarget, ShellTarget, AssetTarget)):
        return False
    if isinstance(target, FileTarget) and id(target) in (file_skip_reasons or {}):
        return False
    return condition_matches(target=target, tags=tags, skip_tags=skip_tags)


def _directory_file_skip_reasons(
    loaded_specs: list[tuple[Path, Spec]],
    *,
    tags: set[str] | None,
    skip_tags: set[str] | None,
) -> dict[int, str]:
    candidates: dict[Path, list[FileTarget]] = {}
    for _, spec_obj in loaded_specs:
        for target in spec_obj.files:
            if condition_matches(target=target, tags=tags, skip_tags=skip_tags):
                candidates.setdefault(target.path, []).append(target)

    skip_reasons: dict[int, str] = {}
    for path_targets in candidates.values():
        if len(path_targets) < 2:
            continue
        best_priority = min(target.priority for target in path_targets)
        winners = [target for target in path_targets if target.priority == best_priority]
        if len(winners) != 1:
            continue
        winner = winners[0]
        for target in path_targets:
            if target is winner:
                continue
            skip_reasons[id(target)] = (
                "lower priority target selected "
                f"(winner path {winner.path}, winner priority {winner.priority}; skipped priority {target.priority})"
            )
    return skip_reasons


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
    file_skip_reasons: dict[int, str] | None = None,
) -> list[dict[str, object]]:
    plan_results = (
        _validation_plan_results(
            spec_obj,
            tags=tags,
            skip_tags=skip_tags,
            diff=diff,
            explain_skips=explain_skips,
            file_skip_reasons=file_skip_reasons,
        )
        if plan
        else []
    )
    result_idx = 0
    targets: list[dict[str, object]] = []
    for file_target in spec_obj.files:
        directory_skip_reason = (file_skip_reasons or {}).get(id(file_target))
        active = directory_skip_reason is None and condition_matches(target=file_target, tags=tags, skip_tags=skip_tags)
        entry: dict[str, object] = {
            "type": "file",
            "path": str(file_target.path),
            "format": file_target.format,
            "active": active,
        }
        if explain_skips and not active:
            entry["skip_reason"] = directory_skip_reason or condition_skip_reason(
                target=file_target, tags=tags, skip_tags=skip_tags
            )
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
    file_skip_reasons: dict[int, str] | None = None,
) -> list[OrchestrationResult]:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        store = StateStore(Path(tmpdir) / "state.db")
        return Orchestrator(store).run(
            spec_obj,
            dry_run=True,
            tags=tags,
            skip_tags=skip_tags,
            diff=diff,
            explain_skips=explain_skips,
            file_skip_reasons=file_skip_reasons,
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
