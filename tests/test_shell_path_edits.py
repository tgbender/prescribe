import os
import shutil
import subprocess
from pathlib import Path

import pytest

from prescribe.orchestrator import Orchestrator
from prescribe.shell import render_shell_block
from prescribe.spec import EnvTarget
from prescribe.state import StateStore


def test_explicit_path_value_still_replaces_inherited_path(tmp_path: Path) -> None:
    edits: dict[str, list[str]] = {}
    resolved, _ = Orchestrator(StateStore(tmp_path / "state.db"))._resolve_env(
        [EnvTarget(name="PATH", value="explicit", prepend=["before"], append=["after"])],
        include_current_path=False,
        path_edits=edits,
    )
    assert resolved["PATH"].split(os.pathsep) == ["before", "explicit", "after"]
    assert edits == {}


@pytest.mark.parametrize("shell", ["cmd", "pwsh", "nu", "xonsh", "bash", "zsh", "fish"])
@pytest.mark.parametrize("edits", ["prepend", "append", "both"])
def test_runtime_path_edits_relocate_entries_and_preserve_special_characters(
    tmp_path: Path, shell: str, edits: str
) -> None:
    executable = shutil.which(shell)
    if shell == "cmd":
        if os.name != "nt":
            pytest.skip("requires Windows cmd")
        executable = shutil.which("cmd.exe")
    if shell in {"bash", "zsh", "fish"} and os.name == "nt":
        pytest.skip("requires a native POSIX shell")
    if executable is None:
        pytest.skip(f"{shell} is not installed")
    pre = str(tmp_path / "pre space & bang!")
    post = str(tmp_path / "post space (tools)")
    middle = str(tmp_path / "inherited")
    prepend = [] if edits == "append" else [pre]
    append = [] if edits == "prepend" else [post]
    # Managed entries already occur in a different order in the incoming PATH.
    inherited = [*append, middle, *prepend]
    lines = render_shell_block(
        shell_type=shell,
        env_vars={},
        managed_block_id="test",
        path_prepend=prepend,
        path_append=append,
    )
    profile = tmp_path / ("profile.cmd" if shell == "cmd" else "profile.txt")
    env = dict(os.environ)
    env.update(
        HOME=str(tmp_path),
        USERPROFILE=str(tmp_path),
        XDG_CONFIG_HOME=str(tmp_path / ".config"),
        PATH=os.pathsep.join(inherited),
        PRESCRIBE_TEST_PATH=os.pathsep.join(inherited),
        PRESCRIBE_TEST_OUTPUT=str(tmp_path / "runtime-path.txt"),
    )
    block = "\n".join(lines) + "\n"
    if shell == "cmd":
        content = "@echo off\n" + block * 2 + 'echo "%PATH%"\n'
        command = [executable, "/d", "/c", str(profile)]
    elif shell == "pwsh":
        profile = profile.with_suffix(".ps1")
        content = (
            "[Console]::OutputEncoding = [Text.UTF8Encoding]::new()\n"
            "$env:PATH = $env:PRESCRIBE_TEST_PATH\n" + block * 2 + "[Console]::Write($env:PATH)\n"
        )
        command = [executable, "-NoProfile", "-File", str(profile)]
    elif shell == "nu":
        content = (
            "$env.PATH = ($env.PRESCRIBE_TEST_PATH | split row (char esep))\n"
            + block * 2
            + "print --no-newline ($env.PATH | str join (char esep))\n"
        )
        command = [executable, "--no-config-file", str(profile)]
    elif shell == "xonsh":
        content = (
            "$PATH = list($PRESCRIBE_TEST_PATH)\n"
            + block * 2
            + '__import__("pathlib").Path($PRESCRIBE_TEST_OUTPUT).write_text('
            '__import__("os").pathsep.join($PATH), encoding="utf-8")\n'
        )
        command = [executable, "--no-rc", str(profile)]
    elif shell == "fish":
        content = block * 2 + "string join : $PATH\n"
        command = [executable, "--no-config", str(profile)]
    else:
        content = block * 2 + 'printf "%s" "$PATH"\n'
        command = (
            [executable, "-f", str(profile)] if shell == "zsh" else [executable, "--noprofile", "--norc", str(profile)]
        )
        env.pop("BASH_ENV", None)
        env.pop("ENV", None)
    profile.write_text(content, encoding="utf-8")
    output = tmp_path / "output.txt"
    errors = tmp_path / "stderr.txt"
    with output.open("wb") as handle, errors.open("wb") as error_handle:
        result = subprocess.run(command, env=env, stdout=handle, stderr=error_handle, timeout=30)
    text = output.read_text(encoding="utf-8-sig").rstrip("\r\n")
    assert result.returncode == 0, errors.read_text(encoding="utf-8", errors="replace")
    if shell == "cmd":
        text = text[1:-1]
    elif shell == "xonsh":
        # Optional readline backends can print startup notices to stdout.
        text = (tmp_path / "runtime-path.txt").read_text(encoding="utf-8")
    assert text.split(os.pathsep) == [*prepend, middle, *append]
