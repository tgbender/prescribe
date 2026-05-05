# prescribe

Declarative, idempotent config management with rollback, diff preview, and OS-level environment persistence.

Write a TOML spec describing what your config files, env vars, and shell blocks should look like. Run `prescribe apply`. Done. `prescribe status --diff` shows exactly what would change before you commit.

```sh
pip install prescribe
```

---

## Commands

```sh
prescribe apply spec.toml              # Apply all changes
prescribe apply spec.toml --dry-run    # Preview without writing
prescribe apply spec.toml --tags agent # Only targets tagged "agent"
prescribe status spec.toml             # Show sync status
prescribe status --diff spec.toml      # Show unified diffs of what would change
prescribe list                         # List all managed files
prescribe rollback path/to/file        # Roll back changes to a file
```

All commands accept `--json` for machine-readable output and `--state` (or `PRESCRIBE_STATE` env var) to set the database path.

`prescribe rollback` accepts `--on-conflict` (prompt/revert/ignore) and `--original` (restore pre-prescribe state).

---

## Spec format

A spec has three optional sections: `[vars]`, `[[files]]`, `[[env]]`, and `[[shell]]`.

### `[vars]` — reusable variables

Define once, use anywhere in env values and paths.

```toml
[vars]
LOCAL_BIN = "$HOME/.local/bin"
TOOLS_BIN = "~/tools/bin"
```

### `[[files]]` — config file management

```toml
[[files]]
path = "~/.gitconfig"
format = "toml"
tags = ["base", "git"]

[files.data]
"core.editor" = "nvim"
"core.autocrlf" = "input"
"init.defaultBranch" = "main"

[[files]]
path = "~/.gitconfig"
format = "toml"
priority = 10
tags = ["work"]

[files.data]
"user.name" = "Work Name"
"user.email" = "work@example.com"
```

- `priority` — lower wins when multiple entries target the same file (default 0)
- `delete` — list of dotted keys to remove
- `format` — `toml`, `yaml`, `jsonc`, or `line`
- `paths` — fallback list if `path` doesn't exist

### `[[env]]` — environment variables

```toml
[[env]]
name = "EDITOR"
value = "nvim"
materialize = true          # persist to OS (macOS launchd, Linux systemd, Windows registry)

[[env]]
name = "CARGO_HOME"
value = "~/.cargo"
path_prepend = ["$CARGO_HOME/bin"]   # set the var AND add its bin to PATH

[[env]]
name = "PATH"
prepend = ["$LOCAL_BIN"]
append = ["/usr/local/sbin"]

[[env]]
name = "SECRET_TOKEN"
value = "!fnox get SECRET_TOKEN"     # !command values kept as literal strings
tags = ["secrets"]
```

- `value` — last one wins for duplicate names
- `prepend` / `append` — accumulated in spec order, deduplicated
- `path_prepend` / `path_append` — forwarded to PATH construction
- `materialize` — persist to OS-level store (best-effort, no secrets check)
- `!command` values pass through as literal strings (shell block resolves at source time)
- `if_env_missing = true` — skip if already set in process env

### `[[shell]]` — managed block injection

```toml
[[shell]]
path = "~/.xonshrc"
managed_block_id = "prescribe-env"
shells = ["xonsh"]
```

Prescribe writes a managed block with env var exports. Supported shells: `xonsh`, `bash`, `zsh`, `fish`, `nu`. The block is idempotent — re-applying replaces the entire block atomically. Existing content outside the block is never touched.

### Conditions

All three section types support gating:

| Field               | Type        | Behaviour                                                                   |
| ------------------- | ----------- | --------------------------------------------------------------------------- |
| `platforms`         | `list[str]` | One of `linux`, `macos`, `windows`. Empty = all.                            |
| `machine`           | `list[str]` | Hostname match or `PRESCRIBE_MACHINE` override.                             |
| `tags`              | `list[str]` | Runtime filter via `--tags` / `--skip-tags`. Empty = always.                |
| `if_command_exists` | `list[str]` | Skip if the binary isn't installed.                                         |
| `shells`            | `list[str]` | For `[[env]]` and `[[shell]]`: one of `xonsh`, `bash`, `zsh`, `fish`, `nu`. |

---

## Rollback and conflict detection

Prescribe tracks every change in a SQLite state database. Managed keys are fingerprinted before and after every apply. If someone edits a managed key outside of prescribe, the next `apply` detects the conflict and refuses to overwrite.

```sh
prescribe rollback ~/.gitconfig                    # Roll back last change
prescribe rollback ~/.gitconfig --original         # Restore to pre-prescribe state
prescribe rollback ~/.gitconfig --on-conflict revert  # Force-revert external edits
```

---

## Library API

```python
import prescribe

# Parse a spec
spec = prescribe.SpecLoader().load(Path("spec.toml"))

# Or build one programmatically
spec = prescribe.Spec(
    id="my-spec",
    files=[prescribe.FileTarget(
        path=Path("~/.gitconfig"),
        format="toml",
        data={"core.editor": "nvim"},
    )],
    env=[prescribe.EnvTarget(
        name="EDITOR",
        value="nvim",
    )],
)

# Apply
store = prescribe.StateStore("~/.prescribe/state.db")
store.initialize()
orch = prescribe.Orchestrator(state_store=store)

results = orch.run(spec=spec)                      # Apply
results = orch.run(spec=spec, dry_run=True)        # Status check
results = orch.run(spec=spec, tags={"agent"})      # Filter by tags
results = orch.run(spec=spec, diff=True)           # Include diffs

for r in results:
    print(f"{r.status:12} {r.diff or ''}")

# Resolved env vars
print(results[0].env_vars)  # {"EDITOR": "nvim", "PATH": "..."}

# Rollback
orch.rollback(Path("~/.gitconfig"))

# Conditions as standalone functions
from prescribe import platform_matches, machine_matches, condition_matches

# Shell block rendering
from prescribe import render_shell_block
render_shell_block(shell_type="xonsh", env_vars={"EDITOR": "nvim"}, managed_block_id="prescribe-env")

# Materialize env vars to OS
from prescribe import materialize
materialize(env_vars={"EDITOR": "nvim"})
```

---

## Principles

- **Declarative** — spec says what the state should be, not how to get there
- **Idempotent** — re-running produce zero changes if everything is in sync
- **Rollback** — every change is recorded; undo to any previous state
- **Round-trip safe** — unmanaged keys, comments, and formatting are preserved
- **Cross-platform** — macOS, Linux, Windows. File paths and env persistence work everywhere
- **Pure function design** — conditions and shell rendering are standalone functions, no hidden state
