"""Tests for shell block rendering."""

import os

from prescribe.shell import render_shell_block


def test_render_xonsh_does_not_mutate_input_dict() -> None:
    env = {"PATH": "/usr/local/bin:/usr/bin", "EDITOR": "nvim"}
    render_shell_block(shell_type="xonsh", env_vars=env, managed_block_id="test")
    assert "PATH" in env


def test_render_fish_does_not_mutate_input_dict() -> None:
    env = {"PATH": "/usr/local/bin:/usr/bin", "EDITOR": "nvim"}
    render_shell_block(shell_type="fish", env_vars=env, managed_block_id="test")
    assert "PATH" in env


def test_render_fish_path_uses_single_list_assignment() -> None:
    env = {"PATH": os.pathsep.join(["/usr/local/bin", "/usr/bin"]), "EDITOR": "nvim"}
    lines = render_shell_block(shell_type="fish", env_vars=env, managed_block_id="test")

    path_lines = [line for line in lines if "PATH" in line or line.startswith("fish_add_path")]
    assert path_lines == ["set -gx PATH '/usr/local/bin' '/usr/bin'"]


def test_render_xonsh_idempotent_on_same_dict() -> None:
    env = {"PATH": "/usr/local/bin:/usr/bin", "EDITOR": "nvim"}
    lines1 = render_shell_block(shell_type="xonsh", env_vars=env, managed_block_id="test")
    lines2 = render_shell_block(shell_type="xonsh", env_vars=env, managed_block_id="test")
    assert lines1 == lines2


def test_render_xonsh_path_uses_single_list_assignment() -> None:
    env = {"PATH": os.pathsep.join(["/usr/local/bin", "/usr/bin"]), "EDITOR": "nvim"}
    lines = render_shell_block(shell_type="xonsh", env_vars=env, managed_block_id="test")

    path_lines = [line for line in lines if line.startswith("$PATH")]
    assert path_lines == ["$PATH = ['/usr/local/bin', '/usr/bin']"]


def test_render_posix_does_not_mutate_input_dict() -> None:
    env = {"PATH": "/usr/local/bin:/usr/bin", "EDITOR": "nvim"}
    render_shell_block(shell_type="bash", env_vars=env, managed_block_id="test")
    assert "PATH" in env


def test_render_nushell_does_not_mutate_input_dict() -> None:
    env = {"PATH": "/usr/local/bin:/usr/bin", "EDITOR": "nvim"}
    render_shell_block(shell_type="nu", env_vars=env, managed_block_id="test")
    assert "PATH" in env


def test_render_pwsh_uses_env_assignments() -> None:
    env = {"PATH": os.pathsep.join([r"C:\Tools\bin", r"C:\Apps\bin"]), "EDITOR": "nvim"}
    lines = render_shell_block(shell_type="pwsh", env_vars=env, managed_block_id="test")

    assert "$env:EDITOR = 'nvim'" in lines
    assert "$env:PATH = @('C:\\Tools\\bin', 'C:\\Apps\\bin') -join [IO.Path]::PathSeparator" in lines


def test_render_pwsh_escapes_single_quotes() -> None:
    lines = render_shell_block(shell_type="pwsh", env_vars={"NAME": "Bob's"}, managed_block_id="test")

    assert lines == ["$env:NAME = 'Bob''s'"]


def test_render_cmd_uses_set_assignments() -> None:
    env = {"PATH": os.pathsep.join([r"C:\Tools\bin", r"C:\Apps\bin"]), "EDITOR": "nvim"}
    lines = render_shell_block(shell_type="cmd", env_vars=env, managed_block_id="test")

    assert "set \"EDITOR=nvim\"" in lines
    assert f'set "PATH={r"C:\Tools\bin"}{os.pathsep}{r"C:\Apps\bin"}"' in lines


def test_render_cmd_escapes_percent_signs() -> None:
    lines = render_shell_block(shell_type="cmd", env_vars={"PROMPT_TEXT": "%USERPROFILE%"}, managed_block_id="test")

    assert lines == ['set "PROMPT_TEXT=%%USERPROFILE%%"']


def test_shell_target_only_receives_matching_shell_env(tmp_path, state_store) -> None:
    from prescribe.orchestrator import Orchestrator
    from prescribe.spec import EnvTarget, ShellTarget, Spec

    bashrc = tmp_path / ".bashrc"
    spec = Spec(
        env=[
            EnvTarget(name="GLOBAL_EDITOR", value="nvim"),
            EnvTarget(name="FISH_ONLY", value="fish", shells=["fish"]),
            EnvTarget(name="BASH_ONLY", value="bash", shells=["bash"]),
        ],
        shell=[ShellTarget(path=bashrc, managed_block_id="prescribe-env", shells=["bash"])],
    )

    result = Orchestrator(state_store).run(spec)[0]

    assert result.status == "applied"
    content = bashrc.read_text(encoding="utf-8")
    assert "GLOBAL_EDITOR" in content
    assert "BASH_ONLY" in content
    assert "FISH_ONLY" not in content


def test_shell_path_block_uses_only_spec_path_entries(tmp_path, state_store, monkeypatch) -> None:
    from prescribe.orchestrator import Orchestrator
    from prescribe.spec import EnvTarget, ShellTarget, Spec

    monkeypatch.setenv("PATH", os.pathsep.join(["/system/bin", "/other/bin"]))
    bashrc = tmp_path / ".bashrc"
    spec = Spec(
        env=[
            EnvTarget(name="TOOLS_HOME", value="~/tools", path_prepend=["$TOOLS_HOME/bin"]),
            EnvTarget(name="PATH", append=["~/apps/bin"]),
        ],
        shell=[ShellTarget(path=bashrc, managed_block_id="prescribe-env", shells=["bash"])],
    )

    result = Orchestrator(state_store).run(spec)[0]

    assert result.status == "applied"
    content = bashrc.read_text(encoding="utf-8")
    assert "TOOLS_HOME" in content
    assert "tools" in content
    assert "apps" in content
    assert "/system/bin" not in content
    assert "/other/bin" not in content
