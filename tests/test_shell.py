"""Tests for shell block rendering."""

from prescribe.shell import render_shell_block


def test_render_xonsh_does_not_mutate_input_dict() -> None:
    env = {"PATH": "/usr/local/bin:/usr/bin", "EDITOR": "nvim"}
    render_shell_block(shell_type="xonsh", env_vars=env, managed_block_id="test")
    assert "PATH" in env


def test_render_fish_does_not_mutate_input_dict() -> None:
    env = {"PATH": "/usr/local/bin:/usr/bin", "EDITOR": "nvim"}
    render_shell_block(shell_type="fish", env_vars=env, managed_block_id="test")
    assert "PATH" in env


def test_render_xonsh_idempotent_on_same_dict() -> None:
    env = {"PATH": "/usr/local/bin:/usr/bin", "EDITOR": "nvim"}
    lines1 = render_shell_block(shell_type="xonsh", env_vars=env, managed_block_id="test")
    lines2 = render_shell_block(shell_type="xonsh", env_vars=env, managed_block_id="test")
    assert lines1 == lines2


def test_render_posix_does_not_mutate_input_dict() -> None:
    env = {"PATH": "/usr/local/bin:/usr/bin", "EDITOR": "nvim"}
    render_shell_block(shell_type="bash", env_vars=env, managed_block_id="test")
    assert "PATH" in env


def test_render_nushell_does_not_mutate_input_dict() -> None:
    env = {"PATH": "/usr/local/bin:/usr/bin", "EDITOR": "nvim"}
    render_shell_block(shell_type="nu", env_vars=env, managed_block_id="test")
    assert "PATH" in env
