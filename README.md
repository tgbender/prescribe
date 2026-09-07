# prescribe

Manage selected configuration keys, whole files, and shell blocks from a TOML
spec. Preview changes, apply them repeatedly, and undo recorded changes while
preserving unrelated edits where possible.

Prescribe supports TOML, YAML, JSON/JSONC, and managed text blocks on Windows,
macOS, Linux, and WSL. It records ownership and history in a local SQLite database
and uses [safe-fs-ops](https://pypi.org/project/safe-fs-ops/) for filesystem and
recovery primitives. Python 3.12 or newer is required.

```sh
uv tool install prescribe
prescribe --version
```

For use as a Python library, install it into your project's environment instead,
for example with `uv add prescribe`.

## Try it in a scratch directory

Create an empty directory and work inside it. Save this as `app.toml`:

```toml
[editor]
theme = "light"

[personal]
note = "Keep this setting"
```

Save this as `spec.toml` beside it:

```toml
id = "readme-demo"

[[files]]
path = "app.toml"
format = "toml"

[files.data]
"editor.theme" = "dark"
"editor.tab_width" = 4
```

Run these commands in that directory. The explicit state path keeps the demo's
history separate from other Prescribe configurations:

```sh
prescribe validate spec.toml --plan
prescribe status spec.toml --diff --state state.sqlite
prescribe apply spec.toml --dry-run --state state.sqlite
prescribe apply spec.toml --state state.sqlite
prescribe apply spec.toml --state state.sqlite
prescribe rollback app.toml --dry-run --state state.sqlite
prescribe rollback app.toml --state state.sqlite
```

The first apply changes the theme and adds `tab_width`, leaving `personal.note`
alone. The second apply reports `in sync` (`noop` in JSON output). Rollback
restores the light theme and removes the added key. Inspect `app.toml` after each step.

Relative paths in a spec resolve against the spec's directory. Spec paths support
`~` and environment-variable expansion. A relative `--state` path resolves against
your current working directory.

## Commands

| Command | Purpose |
| --- | --- |
| `prescribe validate spec.toml --plan` | Validate a spec and show planned targets. |
| `prescribe status spec.toml --diff` | Preview changes and conflicts against current files and recorded state. |
| `prescribe apply spec.toml --dry-run` | Preview an apply without changing managed files or state. |
| `prescribe apply spec.toml` | Apply desired state and record ownership and history. |
| `prescribe list-specs specs` | List direct child `*.toml` specs in sorted order. |
| `prescribe validate-dir specs --plan` | Validate a directory of specs together. |
| `prescribe status-dir specs --diff` | Preview a directory of specs. |
| `prescribe apply-dir specs` | Preflight the directory, then apply its specs in order. |
| `prescribe list` | List managed paths in the selected state database. |
| `prescribe backups` | List recorded recovery backups. |
| `prescribe rollback TARGET` | Undo recorded changes for a managed file, asset, or mirror root. |
| `prescribe recover --dry-run` | Inspect interrupted attempts without clearing them. |
| `prescribe doctor` | Show platform, state, shell, and tool diagnostics. |
| `prescribe docs rollback` | Read built-in help for a command or concept. |

These commands support `--json`. Commands that use managed state accept `--state`
or `PRESCRIBE_STATE`; otherwise Prescribe uses its platform-specific data directory.
Use the same state database for apply, status, backups, recovery, and rollback.
`prescribe COMMAND --help` lists each command's options.

Apply, status, and validation commands support `--tags`, `--skip-tags`, and
`--explain-skips`. For example:

```sh
prescribe apply spec.toml --tags editor --explain-skips
```

## Writing specs

A spec can have an `id`, reusable `[vars]` and `[locations]`, and four target
sections: `[[files]]`, `[[assets]]`, `[[env]]`, and `[[shell]]`.

### Selected keys: `[[files]]`

Use `format = "toml"`, `"yaml"`, or `"jsonc"` to manage mapping keys. Use `jsonc`
for ordinary JSON as well. The quick start above is a complete TOML example.

- `[files.data]` supplies desired values. Dotted names address nested keys, but
  an existing literal key with the full dotted name takes precedence.
- `delete = ["editor.old_option"]` removes managed keys.
- `path` selects a file; optional `paths` adds fallback candidates. Prescribe
  prefers an existing candidate, then one with an existing parent, then the first.
- `location` and optional `path_append` reference a named location instead of `path`.
- `priority` selects among active entries for the same file; the lowest number
  wins (default `0`). **Prescribe selects one whole target; it does not merge the
  entries' data.** Put all desired settings in the winning target.

Match the format to the actual file. Git's `.gitconfig` uses Git configuration
syntax, not TOML; Prescribe has no structured Git/INI adapter. Use a managed text
block for a fragment, or an asset when your repository owns the whole file.

Adapters aim to retain unmanaged values and surrounding comments/formatting.
Edited content may be reformatted; rollback does not promise to reproduce the
entire original file byte for byte. Inspect `status --diff` for the actual change.

### Text fragments: `format = "line"`

Use a stable block ID to manage a fragment in an otherwise manually maintained
text file. This example writes a separate PowerShell snippet; source it from your
profile if you want to use it in your shell:

```toml
[[files]]
path = "profiles/aliases.ps1"
format = "line"
managed_block_id = "aliases"
text = """
Set-Alias ll Get-ChildItem
function gs { git status @args }
"""
```

Use `text_from = "blocks/aliases.ps1"` to read a fragment relative to the spec,
or `lines = ["..."]` to supply individual lines. Choose one of `text`,
`text_from`, and `lines`. Prescribe replaces its marked block on later applies
and retains surrounding text; it does not automatically source the file.

### Whole files and trees: `[[assets]]`

Assets copy repository-owned content to destinations. Create the source files
before validating or applying these examples:

```toml
[[assets]]
source = "assets/guide.md"
dest = "~/.config/example-app/guide.md"
tags = ["editor"]

[[assets]]
source = "assets/snippets/**/*.md"
dest = "~/.config/example-app/snippets"
mode = "mirror"
tags = ["editor"]
```

- `source` is a path or glob relative to the spec.
- `mode = "file"` owns a whole destination file; `"mirror"` materializes source
  files under a directory. Glob sources default to mirror mode.
- `dest_location` and optional `dest_append` reference a named location.
- `paths` adds fallback destinations using the existing-path/parent preference.
- Mirrors leave extra destination files alone by default. With `replace = true`,
  eligible extras are moved into managed backup storage.
- Replacement refuses broad roots such as your home directory and refuses to
  displace directories. The default per-file limit is 10 MiB
  (`max_displace_bytes`); binary-looking extras require `allow_binary = true`.
- `delete_extra` is reserved and must remain `false`.

Asset writes replace destination entries rather than writing through a leaf
symlink or hardlink. Path checks can still reject unsafe targets. Roll back a
managed asset path to undo its file changes, or a mirror destination directory
to restore displaced extras:

```sh
prescribe rollback ~/.config/example-app/snippets
```

### Environment variables and shell exports

`[[env]]` resolves values. Add `[[shell]]` to write those values into a managed
shell block. This complete example generates a PowerShell snippet:

```toml
[vars]
LOCAL_BIN = "~/.local/bin"

[[env]]
name = "EDITOR"
value = "nvim"

[[env]]
name = "CARGO_HOME"
value = "~/.cargo"

[[env]]
name = "PATH"
prepend = ["$LOCAL_BIN", "$CARGO_HOME/bin"]

[[shell]]
path = "profiles/environment.ps1"
managed_block_id = "prescribe-env"
shells = ["pwsh"]
```

Source `profiles/environment.ps1` from PowerShell to use it. For another shell,
choose the appropriate path and shell type: `xonsh`, `bash`, `zsh`, `fish`, `nu`,
`pwsh`, or `cmd`.

- For duplicate environment names, the last active `value` wins. PATH
  `prepend`/`append` entries accumulate in spec order and are deduplicated.
- `path_prepend`/`path_append` on an env target also add entries to PATH.
- Later environment values can reference earlier ones. `[vars]` supplies reusable
  substitutions for paths and values in the spec.
- `if_env_missing = true` skips a variable already set in the process environment.
- Managed PATH additions preserve the shell's runtime PATH, moving managed entries
  to the requested beginning or end without accumulating duplicates. An explicit
  PATH `value` replaces PATH instead.

Without a shell target or `materialize = true`, resolved variables are returned
in results but do not change your shell environment.

`materialize = true` additionally writes selected variables to the platform's
environment store: a LaunchAgent and script on macOS, `environment.d` on
Linux/WSL, or the user registry on Windows. Linux consumption requires systemd
user-environment support; writing a file in WSL alone does not guarantee it is
loaded. Existing processes do not automatically inherit new values.

OS environment materialization is best-effort and is separate from file rollback.
Values can be stored as plain text; it is not a secret store.

### Named locations

Locations share fallback path logic across targets. This example chooses a
platform-specific directory for a hypothetical application's TOML settings:

```toml
[locations.app_config]
kind = "dir"
mode = "create_parent"
candidates = [
  { path = "$APPDATA/example-app", platforms = ["windows"] },
  { path = "~/Library/Application Support/example-app", platforms = ["macos"] },
  { path = "~/.config/example-app", platforms = ["linux", "wsl"] },
]

[[files]]
location = "app_config"
path_append = "settings.toml"
format = "toml"

[files.data]
"editor.theme" = "dark"
```

`candidates` is a non-empty list of paths or tables with `path` and optional
`platforms`, `arch`, `machine`, and `if_command_exists` selectors. `kind` is
`file`, `dir`, or `any`; existing candidates of the wrong kind are rejected.
`first_existing_parent` (default) prefers an existing candidate, then one with an
existing parent, then the first candidate. `first_existing` and `required` require
an existing candidate. `first` and `create_parent` select the first active candidate;
parent directories are created when applying a target, not while loading the spec.

### Conditions

| Field | Applies to | Meaning |
| --- | --- | --- |
| `platforms` | Targets and location candidates | `linux`, `macos`, `windows`, or `wsl`; empty means all. |
| `arch` | Targets and location candidates | `x86_64`, `arm64`, `x86`, `armv7`, or `all`; empty means all. |
| `machine` | Targets and location candidates | Hostname or `PRESCRIBE_MACHINE` override. |
| `if_command_exists` | Targets and location candidates | Require the listed executables. |
| `tags` | Targets | Filter with `--tags`/`--skip-tags`; untagged targets remain eligible. |
| `shells` | Env and shell targets | Filter env entries by shell and select the shell block renderer. |

## Ownership, rollback, and interrupted work

Prescribe records ownership claims for keys, blocks, environment variables, and
assets. Overlap includes parent/child keys, whole-file ownership, and mirror
descendants. A claim conflict defaults to a prompt in a TTY and failure otherwise.
`apply --on-claim-conflict fail` makes that explicit; `take` transfers conflicting
ownership. Taking a claim is distinct from overwriting externally edited content.

If a managed value changes outside Prescribe, apply reports a conflict rather than
silently overwriting it. Default rollback undoes recorded operations while
preserving unrelated edits where possible. On conflicting managed content,
rollback prompts in a TTY and ignores the conflicting content otherwise.

```sh
prescribe rollback app.toml --dry-run
prescribe rollback app.toml --on-conflict revert
prescribe rollback app.toml --original
```

`--on-conflict revert` authorizes undoing conflicting managed changes.
`--original` restores managed content to its baseline before Prescribe first
managed it; for a Prescribe-created file, it removes the file, including later
unrelated additions. Default rollback can retain those additions instead.

Recovery backups capture existing file bytes before supported managed writes and
deletes. They are separate from the operation history used by normal rollback.
Keep the state database and associated backup storage together; a spec alone
does not contain the history needed to undo earlier changes.

Runs use a writer lock with heartbeats. Directory preflight catches validation
and ownership problems before applying specs, but an entire run is not an
all-or-nothing transaction across every file and OS environment store.

After interruption, inspect `prescribe recover --dry-run` and the affected files.
`recover --force` marks unfinished attempts as operator-cleared and expires their
reservations; **it does not restore file contents or replay interrupted work**.
It refuses to clear attempts while another writer holds the live lock. Recovery
commands may initialize or migrate the state database, including during inspection.

Keep state on a local filesystem. Network state paths are rejected by default;
`--allow-network-state` on mutating commands or `PRESCRIBE_ALLOW_NETWORK_STATE=1`
opts out of that check. See `prescribe docs safety`, `docs backups`, and
`docs rollback` for more detail.

## Python API

This self-contained example creates a scratch directory and prints its path so
you can inspect the files afterward. It does not touch your real configuration:

```python
from pathlib import Path
from tempfile import mkdtemp

from prescribe import FileTarget, Orchestrator, Spec, StateStore

root = Path(mkdtemp(prefix="prescribe-demo-"))
target = root / "app.toml"
target.write_text('[editor]\ntheme = "light"\n', encoding="utf-8")
spec = Spec(
    id="python-demo",
    path=root / "spec.toml",
    files=[FileTarget(target, "toml", data={"editor.theme": "dark"})],
)
orchestrator = Orchestrator(StateStore(root / "state.sqlite"))

preview = orchestrator.run(spec, dry_run=True, diff=True)
print(preview[0].diff)
assert orchestrator.run(spec)[0].status == "applied"
assert orchestrator.run(spec)[0].status == "noop"
assert orchestrator.rollback(target).status == "rolled-back"
assert 'theme = "light"' in target.read_text(encoding="utf-8")
print(f"Inspect the example files in {root}")
```

Load a TOML spec with `SpecLoader().load(Path("spec.toml"))`, or pass its path
directly to `Orchestrator.run`. Programmatic targets should use resolved paths;
unlike spec loading, constructing `Path("~/...")` does not expand your home.
Use `.expanduser()` and `.resolve()` when appropriate.

Known Windows library limitation in 0.5.1: an existing-file apply can retain a
SQLite connection until garbage collection or process exit, so deleting its
state directory immediately in the same process can fail with a file-in-use
error. The example leaves its directory for inspection; remove it after the
Python process exits.

Other exported helpers include `render_shell_block`, `extract_shell_env_file`,
`inspect_installed_tools`, and `inspect_tool_paths`. Tool inspection reports
installed tools and manager-owned paths; it does not install packages.

See [the changelog](CHANGELOG.md) for version history and
[release instructions](docs/RELEASING.md) for publishing and dependency updates.
