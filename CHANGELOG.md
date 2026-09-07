# Changelog

## 0.5.1

### Bug Fixes

- Reject overlapping ownership claims and reservations before changing files,
  including parent-key deletions and whole-file replacements.
- Preserve literal dotted JSON keys and newly created parent metadata during
  rollback, and retain independent backups for identical displaced files.
- Refuse forced recovery while another writer holds the live run lock.
- Preserve the runtime PATH when sourcing managed shell prepend/append blocks.
- Require safe-fs-ops 0.1.1 for durable capture identity, native path handling,
  and released-lease recovery fixes.

### Development

- Create release tags at the commit used by the publishing workflow.

## 0.5.0

### Features

- Prescribe now depends on the published `safe-fs-ops` package instead of carrying
  the side-by-side package implementation in this repository.
- Added durable interrupted-work recovery, including `prescribe recover` and
  automatic interactive recovery prompts before mutating commands.
- Added recoverable claim reservations and target attempt records to the state
  ledger so interrupted runs can be inspected and reconciled more safely.
- Added lock heartbeats and token-based lock freshness checks around state
  mutation, rollback, and recovery work.

### Bug Fixes

- Hardened asset recovery and rollback paths against race windows, stale locks,
  inconsistent state transitions, and post-mutation failures.
- Improved run-level error reporting and JSON result alignment for interrupted
  work, materialize failures, and state database lock failures.
- Fixed spaced managed line block IDs after rollback.
- Tightened state migrations to preserve new ledger records and added stress
  coverage for lock contention.

### Development

- Removed the vendored `safe-fs-ops/` source and tests from this repository.
- Added `safe-fs-ops>=0.1.0,<0.2.0` as a normal package dependency.
- Updated local agent guidance to treat `safe-fs-ops` as an external published
  package.

## 0.3.2

### Features

- Recovery backups now capture byte-for-byte copies before Prescribe overwrites or deletes existing files during apply, asset writes, and rollback operations.
- `prescribe backups` lists recovery backup records for audit and recovery inspection.
- `prescribe docs` now includes richer built-in reference topics and aliases for CLI terminology such as targets, selectors, result statuses, ownership, backups, state, and materialization.

### Documentation

- Clarified rollback behavior, `TARGET` path semantics, `--original`, and rollback conflict policies.
- Clarified the difference between `status`, `validate --plan`, and `apply --dry-run`.
- Refined CLI help text for specs, managed paths, recovery backups, directory commands, and docs discovery.

## 0.3.1

### Bug Fixes

- `validate-dir`, `status-dir`, and `apply-dir` now resolve file `priority` winners before checking ownership claims, so lower-priority alternatives across specs are skipped instead of reported as claim conflicts.
- `validate-dir --json --explain-skips` now reports priority alternatives once per spec, with the lower-priority target marked inactive and annotated with the winning target.
- `apply-dir` now preflights every spec before writing, so a later conflict or error cannot leave earlier specs partially applied.
- Rolling back an asset mirror destination directory now restores displaced child backups created by `replace = true`.

## 0.3.0

### Features

- **Directory specs** — `list-specs`, `apply-dir`, `status-dir`, and `validate-dir` manage top-level spec directories in deterministic order.
- **Validation and planning** — `validate`, `validate-dir`, `--plan`, `--diff`, and `--explain-skips` show active/skipped targets and planned changes without touching state.
- **Additional shell support** — shell rendering supports `pwsh` and `cmd` in addition to `xonsh`, `bash`, `zsh`, `fish`, and `nu`.
- **Asset materialization** — `[[assets]]` manages whole files or mirrored trees from repo-owned source files, with dry-run diffs and rollback.
- **Guarded asset replacement** — mirror assets can use `replace = true` to move extra files into managed backup storage instead of deleting them.
- **Safety checks** — Prescribe rejects unsafe portable paths, reserved Windows path names, case-insensitive collisions, broad asset replacement roots, symlink/junction/reparse-point destinations, unsafe rollback paths, oversized displaced files, and binary-looking displaced files by default.
- **State ledger** — SQLite now records runs, target runs, durable ownership claims, asset backups, and short-lived run locks using WAL mode and `BEGIN IMMEDIATE`.
- **Network state guard** — SQLite state paths on network filesystems are rejected unless explicitly allowed.
- **Named locations** — `[locations]` defines reusable path resolution with platform, machine, architecture, and command-existence filters.
- **Architecture selectors** — targets and location candidates can be gated by `arch`.
- **Line text helpers** — `format = "line"` targets can use `text` or `text_from` for managed block content.
- **Shell extraction helpers** — library helpers can read shell files and extract simple environment updates without mutating files.
- **Tool inspection API** — read-only helpers inspect intentionally installed tools and manager-owned executable paths for `uv`, `mise`, `scoop`, `brew`, `bun`, and `pnpm`.
- **Doctor diagnostics** — `prescribe doctor` reports platform, state path, shell support, materialization backend, and selected tool paths.
- **WSL check runner** — `scripts/check_wsl.py` copies the checkout to WSL-native storage and runs Linux-side checks.

### Bug Fixes

- Windows path expansion and path separators are handled consistently for files, env paths, and shell rendering.
- Shell targets now receive only matching shell-specific environment variables.
- Shell block diffs are shown for shell targets.
- Shell PATH rendering avoids serializing the current machine PATH into managed blocks.
- New line files diff correctly before creation.
- Original rollback preserves unrelated edits.
- Newline conventions are preserved when updating existing files.
- File permission bits are preserved when replacing files where the platform supports them.
- Rollback recovery paths are hardened against unsafe filesystem redirection.
- Unknown `mise` install folders are no longer assigned guessed versions.
- Homebrew prefixes are not guessed on Windows.

## 0.2.0

### Features

- **v2 spec format** — `[[files]]`, `[[env]]`, `[[shell]]` sections replace the removed `[[targets]]` format from v0.1.x.
- **Environment variables** — `[[env]]` supports `value`, `prepend`, `append`, `path_prepend`, `path_append`, `materialize`, and `if_env_missing`.
- **OS-level materialize** — `materialize = true` persists env vars to macOS LaunchAgents, Linux systemd, or Windows registry.
- **Shell block injection** — `[[shell]]` writes managed blocks with env exports for `bash`, `zsh`, `fish`, `xonsh`, and `nu`.
- **`[vars]` section** — reusable variables, referenced as `$NAME`, with `$HOME` and `~` expansion.
- **File priority** — `priority` field disambiguates same-path entries (lower number wins).
- **Unified diffs** — `prescribe status --diff` shows what would change before applying.
- **Presets API** — `Presets` class and `prescribe.presets` module for applying multiple spec files from a directory.
- **Spec as first-class object** — build specs programmatically or load from TOML via `SpecLoader`.
- **Tags filtering** — `--tags` / `--skip-tags` on CLI, `tags` param on `Orchestrator.run()`.
- **Platform + machine gating** — `platforms`, `machine`, `if_command_exists` conditions on all target types.
- **Automatic schema migration** — SQLite schema auto-reconciles from SQLAlchemy ORM models on startup.

### Bug Fixes

- CLI result/spec alignment when tag filtering skips files.
- Shell block stopped rendering twice in `_handle_shell`.
- Removed `import re` from hot-path `_expandvars`.
- `Presets.list_managed()` added for API parity with CLI `list`.
- Materialize errors surfaced to CLI stderr/JSON instead of being silently swallowed.
- `migrate.py:_rebuild` now preserves shared-column data instead of dropping it.
- `expand_spec_vars` longest-keys-first prevents prefix corruption, such as `$A` corrupting `$AB`.
- JSONC comment-only files no longer crash, fall back to `{}` and standard JSON serialization.
- Replaced production `assert` statements with explicit `RuntimeError` checks.
- Removed remaining broad exception swallows in materialize backends.
- `shell.py:_split_path` uses `os.pathsep` instead of hardcoded `:` for Windows compatibility.

### Breaking Changes

- Removed `[[targets]]` TOML section — use `[[files]]`, `[[env]]`, `[[shell]]` instead.

## 0.1.x

- Initial prototype with rollback, conflict detection, and `[[targets]]`-based spec format.
