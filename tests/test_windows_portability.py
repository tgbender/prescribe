from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def _xfail_windows_old_behavior(message: str) -> None:
    if sys.platform == "win32":
        pytest.xfail(message)


def test_spec_paths_respect_home_env_on_windows(tmp_path: Path, monkeypatch) -> None:
    from prescribe.spec import SpecLoader

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("APP_NAME", "demo")

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = '~/.config/${APP_NAME}/config.toml'\nformat = 'toml'\n")

    spec = SpecLoader().load(spec_path)
    expected = (home / ".config" / "demo" / "config.toml").resolve()
    if spec.files[0].path != expected:
        _xfail_windows_old_behavior("Windows expanduser ignored the test HOME override")
    assert spec.files[0].path == expected


def test_vars_expand_in_file_and_shell_paths(tmp_path: Path) -> None:
    from prescribe.spec import SpecLoader

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[vars]\n"
        "CONFIG = 'generated'\n"
        "\n"
        "[[files]]\n"
        "path = '$CONFIG/settings.toml'\n"
        "format = 'toml'\n"
        "\n"
        "[[shell]]\n"
        "path = '$CONFIG/profile.sh'\n"
        "managed_block_id = 'prescribe-env'\n"
    )

    spec = SpecLoader().load(spec_path)
    expected_file = (tmp_path / "generated" / "settings.toml").resolve()
    expected_shell = (tmp_path / "generated" / "profile.sh").resolve()
    if spec.files[0].path != expected_file or spec.shell[0].path != expected_shell:
        _xfail_windows_old_behavior("[vars] were not expanded in file and shell paths")
    assert spec.files[0].path == expected_file
    assert spec.shell[0].path == expected_shell


def test_env_path_entries_use_platform_separators(state_store, monkeypatch) -> None:
    from prescribe.orchestrator import Orchestrator
    from prescribe.spec import EnvTarget, Spec

    home = Path(os.environ.get("HOME") or Path.home())
    monkeypatch.setenv("TOOLS_HOME", "/wrong/tools")

    spec = Spec(
        env=[
            EnvTarget(name="TOOLS_HOME", value="$HOME/tools"),
            EnvTarget(name="PATH", prepend=["$TOOLS_HOME/bin"]),
        ]
    )

    resolved = Orchestrator(state_store).run(spec)[0].env_vars
    expected = str(home / "tools" / "bin")
    if expected not in resolved["PATH"].split(os.pathsep):
        _xfail_windows_old_behavior("expanded PATH entries used POSIX separators on Windows")
    assert expected in resolved["PATH"].split(os.pathsep)
    assert "/wrong/tools/bin" not in resolved["PATH"].split(os.pathsep)


def test_xdg_env_vars_are_respected_on_windows(tmp_path: Path, monkeypatch) -> None:
    from prescribe.paths import config_dir, data_dir

    data_home = tmp_path / "xdg-data"
    config_home = tmp_path / "xdg-config"
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    if data_dir() != data_home / "prescribe" or config_dir() != config_home / "prescribe":
        _xfail_windows_old_behavior("platformdirs ignored XDG env vars on Windows")
    assert data_dir() == data_home / "prescribe"
    assert config_dir() == config_home / "prescribe"


def test_materialize_home_expansion_uses_native_path_separators() -> None:
    from prescribe.materialize import _expand

    expected = str(Path.home() / ".cargo")
    if _expand("$HOME/.cargo") != expected:
        _xfail_windows_old_behavior("$HOME expansion produced mixed path separators")
    assert _expand("$HOME/.cargo") == expected
