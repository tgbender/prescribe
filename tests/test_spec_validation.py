from pathlib import Path

import pytest

from prescribe.spec import SpecError, SpecLoader


def test_spec_targets_key_removed(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[targets]]\npath = 'config.toml'\nformat = 'toml'\n")

    with pytest.raises(SpecError, match=r"\[\[targets\]\] is removed"):
        SpecLoader().load(spec_path)


def test_spec_missing_required_sections(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("title = 'hello'\n")
    spec = SpecLoader().load(spec_path)
    assert len(spec.files) == 0
    assert len(spec.env) == 0
    assert len(spec.shell) == 0


def test_spec_files_must_be_a_list(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("files = { path = 'config.toml', format = 'toml' }\n")

    with pytest.raises(SpecError, match="'files' must be an array"):
        SpecLoader().load(spec_path)


def test_spec_file_target_missing_path(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\nformat = 'toml'\n")

    with pytest.raises(SpecError, match="missing required key 'path'"):
        SpecLoader().load(spec_path)


def test_spec_file_target_missing_format(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'config.toml'\n")

    with pytest.raises(SpecError, match="missing required key 'format'"):
        SpecLoader().load(spec_path)


def test_spec_file_target_unknown_format(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'config.toml'\nformat = 'csv'\n")

    with pytest.raises(SpecError, match="unknown format"):
        SpecLoader().load(spec_path)


def test_spec_file_target_data_must_be_table(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\ndata = ['not', 'a', 'table']\n")

    with pytest.raises(SpecError, match="key 'data' must be a table/object"):
        SpecLoader().load(spec_path)


def test_spec_file_target_list_fields_must_be_string_lists(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\ndelete = ['ok', 1]\n")

    with pytest.raises(SpecError, match="key 'delete' item #1 must be a non-empty string"):
        SpecLoader().load(spec_path)


def test_spec_file_target_unknown_platform_rejected(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\nplatforms = ['solaris']\n")

    with pytest.raises(SpecError, match="unknown platform"):
        SpecLoader().load(spec_path)


def test_spec_duplicate_file_path_allowed_when_conditions_do_not_overlap(
    tmp_path: Path,
) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[files]]\n"
        "path = 'config.toml'\n"
        "format = 'toml'\n"
        "platforms = ['linux']\n"
        "\n"
        "[[files]]\n"
        "path = 'config.toml'\n"
        "format = 'yaml'\n"
        "platforms = ['windows']\n"
    )

    spec = SpecLoader().load(spec_path)
    assert len(spec.files) == 2


def test_spec_duplicate_file_path_rejected_when_conditions_overlap(
    tmp_path: Path,
) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[files]]\n"
        "path = 'config.toml'\n"
        "format = 'toml'\n"
        "machine = ['workstation']\n"
        "\n"
        "[[files]]\n"
        "path = 'config.toml'\n"
        "format = 'yaml'\n"
        "machine = ['workstation']\n"
    )

    with pytest.raises(SpecError, match="overlaps with files"):
        SpecLoader().load(spec_path)


def test_spec_line_format_requires_managed_block_id(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = '.env'\nformat = 'line'\n")

    with pytest.raises(SpecError, match="requires 'managed_block_id'"):
        SpecLoader().load(spec_path)


def test_spec_invalid_toml(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("{{{{not valid toml")

    with pytest.raises(SpecError, match="failed to parse spec"):
        SpecLoader().load(spec_path)


def test_spec_valid_minimal_toml_file(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'config.toml'\nformat = 'toml'\n")

    spec = SpecLoader().load(spec_path)
    assert len(spec.files) == 1
    assert spec.files[0].format == "toml"


def test_spec_valid_jsonc_file(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = 'settings.json'\nformat = 'jsonc'\n")

    spec = SpecLoader().load(spec_path)
    assert len(spec.files) == 1
    assert spec.files[0].format == "jsonc"


def test_spec_expands_home_and_env_vars_in_file_path(tmp_path: Path, monkeypatch) -> None:
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("APP_NAME", "myapp")

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = '~/.config/${APP_NAME}/config.toml'\nformat = 'toml'\n")

    spec = SpecLoader().load(spec_path)
    assert spec.files[0].path == (home_dir / ".config/myapp/config.toml").resolve()


def test_spec_uses_first_existing_path_from_fallback_list(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    existing = tmp_path / "existing.toml"
    existing.write_text("title = 'hello'\n")
    spec_path.write_text(
        "[[files]]\npath = 'missing.toml'\npaths = ['also-missing.toml', 'existing.toml']\nformat = 'toml'\n"
    )

    spec = SpecLoader().load(spec_path)
    assert spec.files[0].path == existing.resolve()


def test_spec_file_target_uses_named_location(tmp_path: Path) -> None:
    existing = tmp_path / "existing" / "config.toml"
    existing.parent.mkdir()
    existing.write_text("title = 'hello'\n")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[locations.config]\n"
        "candidates = ['missing/config.toml', 'existing/config.toml']\n"
        "\n"
        "[[files]]\n"
        "location = 'config'\n"
        "format = 'toml'\n"
    )

    spec = SpecLoader().load(spec_path)

    assert spec.locations["config"].name == "config"
    assert spec.files[0].path == existing.resolve()


def test_spec_location_uses_first_existing_parent_when_no_file_exists(tmp_path: Path) -> None:
    parent = tmp_path / "present"
    parent.mkdir()
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[locations.config]\n"
        "candidates = ['missing/config.toml', 'present/config.toml']\n"
        "\n"
        "[[files]]\n"
        "location = 'config'\n"
        "format = 'toml'\n"
    )

    spec = SpecLoader().load(spec_path)

    assert spec.files[0].path == (parent / "config.toml").resolve()


def test_spec_location_can_append_relative_child_path(tmp_path: Path) -> None:
    root = tmp_path / "tool"
    root.mkdir()
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[locations.tool]\n"
        "kind = 'dir'\n"
        "candidates = ['tool']\n"
        "\n"
        "[[files]]\n"
        "location = 'tool'\n"
        "path_append = 'config.toml'\n"
        "format = 'toml'\n"
    )

    spec = SpecLoader().load(spec_path)

    assert spec.files[0].path == (root / "config.toml").resolve()


def test_spec_location_candidate_platform_filter(tmp_path: Path) -> None:
    import sys

    current = "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else "linux"
    inactive = "linux" if current == "windows" else "windows"
    active = tmp_path / "active.toml"
    active.write_text("title = 'hello'\n")
    inactive_path = tmp_path / "inactive.toml"
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[locations.config]\n"
        "candidates = [\n"
        f"  {{ path = '{inactive_path.as_posix()}', platforms = ['{inactive}'] }},\n"
        f"  {{ path = '{active.as_posix()}', platforms = ['{current}'] }},\n"
        "]\n"
        "\n"
        "[[files]]\n"
        "location = 'config'\n"
        "format = 'toml'\n"
    )

    spec = SpecLoader().load(spec_path)

    assert spec.files[0].path == active.resolve()


def test_spec_location_first_existing_mode_requires_existing_path(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[locations.config]\n"
        "mode = 'first_existing'\n"
        "candidates = ['missing/config.toml']\n"
        "\n"
        "[[files]]\n"
        "location = 'config'\n"
        "format = 'toml'\n"
    )

    with pytest.raises(SpecError, match="did not match an existing path"):
        SpecLoader().load(spec_path)


def test_spec_location_rejects_absolute_append(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[locations.tool]\n"
        "candidates = ['tool']\n"
        "\n"
        "[[files]]\n"
        "location = 'tool'\n"
        f"path_append = '{(tmp_path / 'config.toml').as_posix()}'\n"
        "format = 'toml'\n"
    )

    with pytest.raises(SpecError, match="must be a relative path"):
        SpecLoader().load(spec_path)


def test_spec_location_unknown_reference_rejected(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\nlocation = 'missing'\nformat = 'toml'\n")

    with pytest.raises(SpecError, match="unknown location"):
        SpecLoader().load(spec_path)


def test_spec_shell_target_uses_named_location(tmp_path: Path) -> None:
    profile = tmp_path / "profile.ps1"
    profile.write_text("")
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[locations.profile]\n"
        "candidates = ['profile.ps1']\n"
        "\n"
        "[[shell]]\n"
        "location = 'profile'\n"
        "managed_block_id = 'prescribe-env'\n"
        "shells = ['pwsh']\n"
    )

    spec = SpecLoader().load(spec_path)

    assert spec.shell[0].path == profile.resolve()


def test_spec_valid_line_file_with_block_id(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[files]]\npath = '.env'\nformat = 'line'\nmanaged_block_id = 'managed'\n")

    spec = SpecLoader().load(spec_path)
    assert spec.files[0].managed_block_id == "managed"


# ── env target tests ──────────────────────────────────────


def test_spec_valid_env_target(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[env]]\nname = 'EDITOR'\nvalue = 'nvim'\nmaterialize = true\nplatforms = ['macos']\n")

    spec = SpecLoader().load(spec_path)
    assert len(spec.env) == 1
    assert spec.env[0].name == "EDITOR"
    assert spec.env[0].value == "nvim"
    assert spec.env[0].materialize is True
    assert spec.env[0].platforms == ["macos"]


def test_spec_env_target_with_path_prepend(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[env]]\nname = 'CARGO_HOME'\nvalue = '$HOME/.cargo'\npath_prepend = ['$CARGO_HOME/bin']\n")

    spec = SpecLoader().load(spec_path)
    assert spec.env[0].path_prepend == ["$CARGO_HOME/bin"]


def test_spec_env_target_with_prepend_append(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[env]]\nname = 'PATH'\nprepend = ['$HOME/.local/bin']\nappend = ['/usr/local/sbin']\n")

    spec = SpecLoader().load(spec_path)
    assert spec.env[0].value is None
    assert spec.env[0].prepend == ["$HOME/.local/bin"]
    assert spec.env[0].append == ["/usr/local/sbin"]


def test_spec_env_target_requires_name(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[env]]\nvalue = 'nvim'\n")

    with pytest.raises(SpecError, match="key 'name' must be a non-empty string"):
        SpecLoader().load(spec_path)


def test_spec_env_target_unknown_platform_rejected(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[env]]\nname = 'VAR'\nvalue = '1'\nplatforms = ['solaris']\n")

    with pytest.raises(SpecError, match="unknown platform"):
        SpecLoader().load(spec_path)


def test_spec_env_target_accepts_pwsh_shell(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[env]]\nname = 'VAR'\nvalue = '1'\nshells = ['pwsh']\n")

    spec = SpecLoader().load(spec_path)
    assert spec.env[0].shells == ["pwsh"]


def test_spec_env_target_unknown_shell_rejected(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[env]]\nname = 'VAR'\nvalue = '1'\nshells = ['tcsh']\n")

    with pytest.raises(SpecError, match="unknown shell"):
        SpecLoader().load(spec_path)


# ── shell target tests ────────────────────────────────────


def test_spec_valid_shell_target(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    rc_path = tmp_path / ".xonshrc"
    rc_path.touch()
    spec_path.write_text(f"[[shell]]\npath = '{rc_path}'\nmanaged_block_id = 'prescribe-env'\nshells = ['xonsh']\n")

    spec = SpecLoader().load(spec_path)
    assert len(spec.shell) == 1
    assert spec.shell[0].shells == ["xonsh"]
    assert spec.shell[0].managed_block_id == "prescribe-env"


def test_spec_valid_pwsh_shell_target(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    profile_path = tmp_path / "profile.ps1"
    spec_path.write_text(f"[[shell]]\npath = '{profile_path}'\nmanaged_block_id = 'prescribe-env'\nshells = ['pwsh']\n")

    spec = SpecLoader().load(spec_path)
    assert spec.shell[0].shells == ["pwsh"]


def test_spec_valid_cmd_shell_target(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    profile_path = tmp_path / "profile.cmd"
    spec_path.write_text(f"[[shell]]\npath = '{profile_path}'\nmanaged_block_id = 'prescribe-env'\nshells = ['cmd']\n")

    spec = SpecLoader().load(spec_path)
    assert spec.shell[0].shells == ["cmd"]


def test_spec_shell_target_requires_path(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[shell]]\nmanaged_block_id = 'block'\n")

    with pytest.raises(SpecError, match="missing required key 'path'"):
        SpecLoader().load(spec_path)


def test_spec_shell_target_requires_managed_block_id(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[shell]]\npath = '~/.xonshrc'\n")

    with pytest.raises(SpecError, match="key 'managed_block_id' must be a non-empty string"):
        SpecLoader().load(spec_path)
