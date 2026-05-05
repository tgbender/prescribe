# Changelog

## 0.2.0 (Unreleased)

### Features

- **v2 spec format** — `[[files]]`, `[[env]]`, `[[shell]]` sections replace the removed `[[targets]]` format from v0.1.x
- **Environment variables** — `[[env]]` supports `value`, `prepend`, `append`, `path_prepend`, `path_append`, `materialize`, and `if_env_missing`
- **OS-level materialize** — `materialize = true` persists env vars to macOS LaunchAgents, Linux systemd, or Windows registry
- **Shell block injection** — `[[shell]]` writes managed blocks with env exports for `bash`, `zsh`, `fish`, `xonsh`, and `nu`
- **`[vars]` section** — reusable variables, referenced as `$NAME`, with `$HOME` and `~` expansion
- **File priority** — `priority` field disambiguates same-path entries (lower number wins)
- **Unified diffs** — `prescribe status --diff` shows what would change before applying
- **Presets API** — `Presets` class and `prescribe.presets` module for applying multiple spec files from a directory
- **Spec as first-class object** — build specs programmatically or load from TOML via `SpecLoader`
- **Tags filtering** — `--tags` / `--skip-tags` on CLI, `tags` param on `Orchestrator.run()`
- **Platform + machine gating** — `platforms`, `machine`, `if_command_exists` conditions on all target types
- **Automatic schema migration** — SQLite schema auto-reconciles from SQLAlchemy ORM models on startup

### Bug Fixes

- CLI result/spec alignment when tag filtering skips files
- Shell block stopped rendering twice in `_handle_shell`
- Removed `import re` from hot-path `_expandvars`
- `Presets.list_managed()` added for API parity with CLI `list`
- Materialize errors surfaced to CLI stderr/JSON instead of being silently swallowed
- `migrate.py:_rebuild` now preserves shared-column data instead of dropping it
- `expand_spec_vars` longest-keys-first prevents prefix corruption (e.g. `$A` corrupting `$AB`)
- JSONC comment-only files no longer crash, fall back to `{}` and standard JSON serialization
- Replaced 9 production `assert` statements with explicit `RuntimeError` checks
- Removed remaining `contextlib.suppress(Exception)` swallows in materialize backends
- `shell.py:_split_path` uses `os.pathsep` instead of hardcoded `:` for Windows compatibility

### Breaking Changes

- Removed `[[targets]]` TOML section — use `[[files]]`, `[[env]]`, `[[shell]]` instead

## 0.1.x

- Initial prototype with rollback, conflict detection, and `[[targets]]`-based spec format
