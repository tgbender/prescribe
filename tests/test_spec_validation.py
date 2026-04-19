from pathlib import Path

import pytest

from prescribe.spec import SpecError, SpecLoader


def test_spec_missing_targets_key(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("title = 'hello'\n")

    with pytest.raises(SpecError, match="missing required key 'targets'"):
        SpecLoader().load(spec_path)


def test_spec_targets_must_be_a_list(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("targets = { path = 'config.toml', format = 'toml' }\n")

    with pytest.raises(SpecError, match="key 'targets' must be a list"):
        SpecLoader().load(spec_path)


def test_spec_target_missing_path(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[targets]]\nformat = 'toml'\n")

    with pytest.raises(SpecError, match="missing required key 'path'"):
        SpecLoader().load(spec_path)


def test_spec_target_missing_format(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[targets]]\npath = 'config.toml'\n")

    with pytest.raises(SpecError, match="missing required key 'format'"):
        SpecLoader().load(spec_path)


def test_spec_target_unknown_format(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[targets]]\npath = 'config.toml'\nformat = 'csv'\n")

    with pytest.raises(SpecError, match="unknown format"):
        SpecLoader().load(spec_path)


def test_spec_target_data_must_be_table(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[targets]]\n"
        "path = 'config.toml'\n"
        "format = 'toml'\n"
        "data = ['not', 'a', 'table']\n"
    )

    with pytest.raises(SpecError, match="key 'data' must be a table/object"):
        SpecLoader().load(spec_path)


def test_spec_target_list_fields_must_be_string_lists(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[targets]]\npath = 'config.toml'\nformat = 'toml'\ndelete = ['ok', 1]\n"
    )

    with pytest.raises(
        SpecError, match="key 'delete' item #1 must be a non-empty string"
    ):
        SpecLoader().load(spec_path)


def test_spec_target_unknown_platform_rejected(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[targets]]\npath = 'config.toml'\nformat = 'toml'\nplatforms = ['solaris']\n"
    )

    with pytest.raises(SpecError, match="unknown platform"):
        SpecLoader().load(spec_path)


def test_spec_duplicate_target_path_allowed_when_conditions_do_not_overlap(
    tmp_path: Path,
) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[targets]]\n"
        "path = 'config.toml'\n"
        "format = 'toml'\n"
        "platforms = ['linux']\n"
        "\n"
        "[[targets]]\n"
        "path = 'config.toml'\n"
        "format = 'yaml'\n"
        "platforms = ['windows']\n"
    )

    spec = SpecLoader().load(spec_path)
    assert len(spec.targets) == 2


def test_spec_duplicate_target_path_rejected_when_conditions_overlap(
    tmp_path: Path,
) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[targets]]\n"
        "path = 'config.toml'\n"
        "format = 'toml'\n"
        "machine = ['workstation']\n"
        "\n"
        "[[targets]]\n"
        "path = 'config.toml'\n"
        "format = 'yaml'\n"
        "machine = ['workstation']\n"
    )

    with pytest.raises(SpecError, match="overlaps with target"):
        SpecLoader().load(spec_path)


def test_spec_line_format_requires_managed_block_id(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[targets]]\npath = '.env'\nformat = 'line'\n")

    with pytest.raises(SpecError, match="requires 'managed_block_id'"):
        SpecLoader().load(spec_path)


def test_spec_invalid_toml(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("{{{{not valid toml")

    with pytest.raises(SpecError, match="failed to parse spec"):
        SpecLoader().load(spec_path)


def test_spec_valid_minimal_toml_target(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[targets]]\npath = 'config.toml'\nformat = 'toml'\n")

    spec = SpecLoader().load(spec_path)
    assert len(spec.targets) == 1
    assert spec.targets[0].format == "toml"


def test_spec_valid_jsonc_target(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text("[[targets]]\npath = 'settings.json'\nformat = 'jsonc'\n")

    spec = SpecLoader().load(spec_path)
    assert len(spec.targets) == 1
    assert spec.targets[0].format == "jsonc"


def test_spec_expands_home_and_env_vars_in_path(tmp_path: Path, monkeypatch) -> None:
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("APP_NAME", "myapp")

    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[targets]]\npath = '~/.config/${APP_NAME}/config.toml'\nformat = 'toml'\n"
    )

    spec = SpecLoader().load(spec_path)
    assert spec.targets[0].path == (home_dir / ".config/myapp/config.toml").resolve()


def test_spec_uses_first_existing_path_from_fallback_list(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    existing = tmp_path / "existing.toml"
    existing.write_text("title = 'hello'\n")
    spec_path.write_text(
        "[[targets]]\n"
        "path = 'missing.toml'\n"
        "paths = ['also-missing.toml', 'existing.toml']\n"
        "format = 'toml'\n"
    )

    spec = SpecLoader().load(spec_path)
    assert spec.targets[0].path == existing.resolve()


def test_spec_valid_line_target_with_block_id(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    spec_path.write_text(
        "[[targets]]\npath = '.env'\nformat = 'line'\nmanaged_block_id = 'managed'\n"
    )

    spec = SpecLoader().load(spec_path)
    assert spec.targets[0].managed_block_id == "managed"
