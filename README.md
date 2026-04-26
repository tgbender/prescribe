# prescribe

Declarative, idempotent config file management with rollback and conflict detection.

Write a TOML spec describing how your config files should look. Run `prescribe apply`. Done.

```sh
pip install prescribe
```

---

## Commands

```sh
prescribe apply spec.toml            # Apply changes
prescribe apply spec.toml --dry-run  # Preview without writing
prescribe status spec.toml           # Show sync status of all targets
prescribe list                       # List all managed files
prescribe rollback path/to/file      # Roll back changes to a file
```

All commands accept `--json` for machine-readable output and `--state` (or `PRESCRIBE_STATE` env var) to set the state database path.

`prescribe rollback` accepts `--on-conflict` to control behaviour when a managed key was externally modified: `prompt` (default when stdin is a TTY), `revert` (always force-revert), or `ignore` (skip silently).

---

## Spec format

A spec is a TOML file with one or more `[[targets]]` blocks.

```toml
[[targets]]
path = "~/.config/nvim/settings.json"
format = "jsonc"

[targets.data]
"editor.tabSize" = 2
"editor.formatOnSave" = true

[[targets]]
path = "~/.gitconfig"
format = "toml"
delete = ["core.autocrlf"]

[targets.data]
"core.editor" = "nvim"

[[targets]]
path = "~/.zshrc"
format = "line"
managed_block_id = "prescribe"
lines = [
  "export EDITOR=nvim",
  "alias ll='ls -lah'",
]
```

### Target fields

| Field              | Required | Description                                                                   |
| ------------------ | -------- | ----------------------------------------------------------------------------- |
| `path`             | yes      | Path to the config file (supports `~`, env vars, and a `paths` fallback list) |
| `format`           | yes      | `toml`, `yaml`, `jsonc`, `json5`, or `line`                                   |
| `data`             | no       | Key/value pairs to set (dot-notation keys for nested values)                  |
| `delete`           | no       | Keys to remove                                                                |
| `lines`            | no       | Lines to manage (requires `format = "line"`)                                  |
| `managed_block_id` | no\*     | Block identifier for line-format files (\*required for `line`)                |
| `platforms`        | no       | Limit to `linux`, `macos`, and/or `windows`                                   |
| `machine`          | no       | Limit to specific hostnames (overridable via `PRESCRIBE_MACHINE` env var)     |

---

## JSONC

prescribe includes a pure-Python JSONC round-trip parser that preserves comments and formatting. It is validated against Microsoft's [`node-jsonc-parser`](https://github.com/microsoft/node-jsonc-parser) — the same parser used internally by VS Code — via an oracle test suite against real VS Code settings files.

---

## To do

- **CLI integration tests** — `CliRunner` tests for `apply`, `rollback` (including `--on-conflict`), and `status` commands
- **Schema migration tests** — exercise `migrate()` against a real DB with a prior schema version
- **Symlink awareness** — detect and handle symlinked target paths correctly
- **File permission preservation** — restore `chmod` bits on rollback (important for `~/.ssh/config` etc.)
- **Spec composition** — `include` one spec from another for large multi-machine dotfile repos
- **Pre/post hooks** — run shell commands before/after apply (e.g. `source ~/.bashrc`, package installs)
- **Windows validation** — CRLF handling, path resolution, and atomic writes need real Windows testing
