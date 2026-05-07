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
prescribe list-specs specs             # List top-level specs in sorted apply order
prescribe apply-dir specs              # Apply every top-level *.toml spec in a directory
prescribe status-dir specs --diff      # Preview a whole spec directory
prescribe validate-dir specs --plan    # Validate and plan a whole spec directory
prescribe list                         # List all managed files
prescribe rollback path/to/file        # Roll back changes to a file
```

All commands accept `--json` for machine-readable output and `--state` (or `PRESCRIBE_STATE` env var) to set the database path.

`prescribe rollback` accepts `--on-conflict` (prompt/revert/ignore) and `--original` (restore pre-prescribe state).

Mutating commands use a short-lived SQLite run lock to prevent concurrent writes. Prescribe also records durable ownership claims for managed keys, blocks, env vars, and assets; overlapping claims fail before writes unless ownership is explicitly taken with `--on-claim-conflict take` or approved interactively with `--on-claim-conflict prompt`.

By default, prescribe refuses to place its SQLite state database on a network filesystem such as UNC, mapped network drives, SMB, SSHFS, or NFS. Keep state on a local disk for reliable locking. If you intentionally accept the risk, set `PRESCRIBE_ALLOW_NETWORK_STATE=1` or pass `--allow-network-state` to mutating commands.

---

## Spec format

A spec has an optional `[vars]` section and four optional target sections: `[[files]]`, `[[assets]]`, `[[env]]`, and `[[shell]]`.

### `[vars]` — reusable variables

Define once, use anywhere in env values and paths.

```toml
[vars]
LOCAL_BIN = "$HOME/.local/bin"
TOOLS_BIN = "~/tools/bin"
```

### `[locations]` — reusable path resolution

Use named locations when several targets need the same fallback path logic.

```toml
[locations.codex_home]
kind = "dir"
mode = "first_existing_parent"
candidates = [
  { path = "$USERPROFILE/.codex", platforms = ["windows"] },
  { path = "~/.codex", platforms = ["linux", "macos", "wsl"] },
]

[locations.cursor_mcp]
kind = "file"
candidates = [
  { path = "$APPDATA/Cursor/User/mcp.json", platforms = ["windows"] },
  { path = "~/Library/Application Support/Cursor/User/mcp.json", platforms = ["macos"] },
  { path = "~/.config/Cursor/User/mcp.json", platforms = ["linux", "wsl"] },
]
```

- `candidates` — non-empty list of paths, or tables with `path`, `platforms`, `machine`, and `if_command_exists`
- `mode` — `first_existing_parent` (default), `first_existing`, `required`, `first`, or `create_parent`
- `kind` — `file`, `dir`, or `any`; existing candidates with the wrong kind are rejected

Targets can reference a location instead of repeating paths:

```toml
[[files]]
location = "cursor_mcp"
format = "jsonc"

[files.data]
"mcpServers.local.command" = "uvx"

[[assets]]
source = "codex/skills/**/*.md"
dest_location = "codex_home"
dest_append = "skills"
mode = "mirror"
replace = true
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
- `location` / `path_append` — use a named `[locations]` entry, optionally with a relative child path
- `paths` — fallback list if `path` doesn't exist
- `text` / `text_from` — for `format = "line"` managed blocks, define block content inline or from a file

### `[[assets]]` — repo-owned file materialization

Use assets when your dotfiles repo should own a whole file or tree at the destination.
Unlike `[[files]]`, an asset replaces the destination file with the source content.
Prescribe still records checkpoints, detects external edits on later applies, and can roll back created or overwritten files.

```toml
[[assets]]
source = "codex/mcp.json"
dest = "~/.codex/mcp.json"
tags = ["agent"]

[[assets]]
source = "codex/skills/**/*.md"
dest = "~/.codex/skills"
mode = "mirror"
replace = true
tags = ["agent"]
```

- `source` — file path or glob pattern, relative to the spec file unless absolute
- `dest` — destination file for `mode = "file"` or destination directory for `mode = "mirror"`
- `dest_location` / `dest_append` — use a named `[locations]` entry, optionally with a relative child path
- `paths` — fallback destination list; the first existing path wins, then first existing parent
- `mode` — `file` or `mirror`; glob sources default to `mirror`, otherwise `file`
- `delete_extra` — reserved for future mirror pruning; currently must stay `false`
- `replace` — for `mode = "mirror"`, move extra destination files into Prescribe backup storage instead of leaving them active
- `max_displace_bytes` — maximum size of an extra file that `replace` may move; defaults to 10 MiB
- `allow_binary` — allow `replace` to move binary-looking extra files; defaults to `false`

Asset destinations are replaced at the path itself. If the destination is a symlink or hardlink, Prescribe avoids mutating the linked target content; rollback restores previous file content and restores symlink destinations when possible.
When `replace = true`, Prescribe refuses broad destinations such as the home directory or filesystem roots, refuses directories, and moves extra files/symlinks to managed backup storage before recording a restore manifest.

For shell snippets or other file fragments that should not own the whole file, use `format = "line"` managed blocks instead:

```toml
[[files]]
path = "~/.config/powershell/Microsoft.PowerShell_profile.ps1"
format = "line"
managed_block_id = "aliases"
text = """
Set-Alias ll Get-ChildItem
function gs { git status @args }
"""

[[files]]
path = "~/.ssh/config"
format = "line"
managed_block_id = "work-hosts"
text_from = "blocks/ssh-work-hosts.txt"
```

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

Prescribe writes a managed block with env var exports. Supported shells: `xonsh`, `bash`, `zsh`, `fish`, `nu`, `pwsh`, and `cmd`. The block is idempotent — re-applying replaces the entire block atomically. Existing content outside the block is never touched.

### Conditions

All three section types support gating:

| Field               | Type        | Behaviour                                                                                  |
| ------------------- | ----------- | ------------------------------------------------------------------------------------------ |
| `platforms`         | `list[str]` | One of `linux`, `macos`, `windows`. Empty = all.                                           |
| `machine`           | `list[str]` | Hostname match or `PRESCRIBE_MACHINE` override.                                            |
| `tags`              | `list[str]` | Runtime filter via `--tags` / `--skip-tags`. Empty = always.                               |
| `if_command_exists` | `list[str]` | Skip if the binary isn't installed.                                                        |
| `shells`            | `list[str]` | For `[[env]]` and `[[shell]]`: one of `xonsh`, `bash`, `zsh`, `fish`, `nu`, `pwsh`, `cmd`. |

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

# Read existing shell files without applying or mutating anything
from prescribe import extract_shell_env_file
extraction = extract_shell_env_file(Path("~/.zshrc").expanduser())
print(extraction.updates)
print(extraction.issues)

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
