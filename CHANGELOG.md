# Changelog

## 0.3.1 (Unreleased)

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
