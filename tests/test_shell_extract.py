"""Tests for read-only shell environment extraction."""

from pathlib import Path

import pytest

from prescribe import ShellEnvUpdate, extract_shell_env, extract_shell_env_file, infer_shell_type


def test_extract_posix_static_exports_and_path_prepend() -> None:
    result = extract_shell_env(
        """
# editor
export EDITOR=nvim
PATH="$HOME/.local/bin:$PATH"
export TOOL_HOME="$HOME/tools"
""",
        shell_type="bash",
    )

    assert result.issues == ()
    assert result.updates == (
        ShellEnvUpdate(
            name="EDITOR",
            operation="set",
            value="nvim",
            shell_type="bash",
            line_number=3,
            raw="export EDITOR=nvim",
            exported=True,
        ),
        ShellEnvUpdate(
            name="PATH",
            operation="path_prepend",
            entries=("$HOME/.local/bin",),
            shell_type="bash",
            line_number=4,
            raw='PATH="$HOME/.local/bin:$PATH"',
        ),
        ShellEnvUpdate(
            name="TOOL_HOME",
            operation="set",
            value="$HOME/tools",
            shell_type="bash",
            line_number=5,
            raw='export TOOL_HOME="$HOME/tools"',
            exported=True,
        ),
    )


def test_extract_posix_path_append_and_set() -> None:
    result = extract_shell_env(
        """
MANPATH="$MANPATH:$HOME/man"
PYTHONPATH="$HOME/src:$HOME/lib"
""",
        shell_type="zsh",
    )

    assert [(update.name, update.operation, update.entries) for update in result.updates] == [
        ("MANPATH", "path_append", ("$HOME/man",)),
        ("PYTHONPATH", "path_set", ("$HOME/src", "$HOME/lib")),
    ]


def test_extract_posix_reports_dynamic_assignments_without_guessing() -> None:
    result = extract_shell_env(
        """
export STATIC=yes
export TOKEN="$(op read token)"
""",
        shell_type="bash",
    )

    assert [update.name for update in result.updates] == ["STATIC"]
    assert len(result.issues) == 1
    assert result.issues[0].line_number == 3
    assert result.issues[0].reason == "dynamic or unsupported assignment"


def test_extract_posix_strips_comments_outside_quotes_only() -> None:
    result = extract_shell_env(
        """
export PROMPT="keep # literal" # outside
export EDITOR=nvim # outside
""",
        shell_type="bash",
    )

    assert [(update.name, update.value) for update in result.updates] == [
        ("PROMPT", "keep # literal"),
        ("EDITOR", "nvim"),
    ]


def test_extract_xonsh_env_and_path_list() -> None:
    result = extract_shell_env(
        """
$EDITOR = 'nvim'
$PATH = ['/opt/bin', '/usr/local/bin']
""",
        shell_type="xonsh",
    )

    assert [(update.name, update.operation, update.value, update.entries) for update in result.updates] == [
        ("EDITOR", "set", "nvim", ()),
        ("PATH", "path_set", None, ("/opt/bin", "/usr/local/bin")),
    ]


def test_extract_fish_values() -> None:
    result = extract_shell_env(
        """
set -gx EDITOR nvim
set -gx PATH /opt/bin /usr/local/bin
""",
        shell_type="fish",
    )

    extracted = [
        (update.name, update.operation, update.value, update.entries, update.exported) for update in result.updates
    ]
    assert extracted == [
        ("EDITOR", "set", "nvim", (), True),
        ("PATH", "path_set", None, ("/opt/bin", "/usr/local/bin"), True),
    ]


def test_extract_pwsh_and_cmd_path_mutations() -> None:
    pwsh = extract_shell_env("$env:PATH = '$env:PATH;C:\\Tools\\bin'\n", shell_type="pwsh")
    cmd = extract_shell_env('set "PATH=%PATH%;C:\\Tools\\bin"\n', shell_type="cmd")

    assert pwsh.updates[0].operation == "path_append"
    assert pwsh.updates[0].entries == ("C:\\Tools\\bin",)
    assert cmd.updates[0].operation == "path_append"
    assert cmd.updates[0].entries == ("C:\\Tools\\bin",)


def test_extract_shell_env_file_infers_shell_type(tmp_path: Path) -> None:
    shell_file = tmp_path / ".zshrc"
    shell_file.write_text("export EDITOR=nvim\n", encoding="utf-8")

    result = extract_shell_env_file(shell_file)

    assert result.updates[0].shell_type == "zsh"
    assert result.updates[0].name == "EDITOR"


@pytest.mark.parametrize(
    ("filename", "shell_type"),
    [
        (".bashrc", "bash"),
        (".zshrc", "zsh"),
        ("config.fish", "fish"),
        (".xonshrc", "xonsh"),
        ("profile.ps1", "pwsh"),
        ("profile.cmd", "cmd"),
    ],
)
def test_infer_shell_type(filename: str, shell_type: str) -> None:
    assert infer_shell_type(filename) == shell_type


def test_unknown_shell_type_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported shell type"):
        extract_shell_env("setenv EDITOR vim", shell_type="csh")
