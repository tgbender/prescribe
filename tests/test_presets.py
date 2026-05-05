"""Tests for the presets module."""

from pathlib import Path

from prescribe.presets import Presets, apply_all, discover_specs, list_specs


def test_discover_specs_empty_directory(tmp_path: Path) -> None:
    assert discover_specs(tmp_path) == []


def test_discover_specs_sorts_alphabetically(tmp_path: Path) -> None:
    (tmp_path / "z.toml").write_text("")
    (tmp_path / "a.toml").write_text("")
    (tmp_path / "m.toml").write_text("")
    (tmp_path / "other.txt").write_text("")  # not a spec
    specs = discover_specs(tmp_path)
    assert [p.name for p in specs] == ["a.toml", "m.toml", "z.toml"]


def test_discover_specs_nonexistent_directory() -> None:
    assert discover_specs(Path("/does/not/exist")) == []


def test_presets_list_specs(tmp_path: Path) -> None:
    (tmp_path / "base.toml").write_text("[vars]\nX = '1'\n")
    presets = Presets()
    specs = presets.list_specs(tmp_path)
    assert len(specs) == 1
    assert specs[0].name == "base.toml"


def test_presets_apply_all_dry_run(tmp_path: Path, memory_state_store) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    config = config_dir / "config.toml"
    config.write_text("title = 'hello'\n")

    spec_dir = tmp_path / "specs"
    spec_dir.mkdir()
    spec = spec_dir / "spec.toml"
    spec.write_text(f"[[files]]\npath = '{config}'\nformat = 'toml'\n[files.data]\ntitle = 'world'\n")

    presets = Presets(state_store=memory_state_store)
    results = presets.apply_all(spec_dir, dry_run=True)

    assert len(results) == 1
    spec_path, spec_results = results[0]
    assert spec_path.name == "spec.toml"
    assert any(r.changed for r in spec_results)
    assert config.read_text() == "title = 'hello'\n"  # dry run: no write


def test_presets_apply_all_applies_changes(tmp_path: Path, memory_state_store) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    config = config_dir / "config.toml"
    config.write_text("title = 'hello'\n")

    spec_dir = tmp_path / "specs"
    spec_dir.mkdir()
    spec = spec_dir / "spec.toml"
    spec.write_text(f"[[files]]\npath = '{config}'\nformat = 'toml'\n[files.data]\ntitle = 'world'\n")

    presets = Presets(state_store=memory_state_store)
    results = presets.apply_all(spec_dir)

    assert len(results) == 1
    assert config.read_text().strip() == 'title = "world"'


def test_presets_status_is_dry_run(tmp_path: Path, memory_state_store) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    config = config_dir / "config.toml"
    config.write_text("title = 'hello'\n")

    spec_dir = tmp_path / "specs"
    spec_dir.mkdir()
    spec = spec_dir / "spec.toml"
    spec.write_text(f"[[files]]\npath = '{config}'\nformat = 'toml'\n[files.data]\ntitle = 'world'\n")

    presets = Presets(state_store=memory_state_store)
    results = presets.status(spec_dir)

    assert len(results) == 1
    assert config.read_text() == "title = 'hello'\n"  # no write


def test_presets_apply_all_skips_non_toml(tmp_path: Path, memory_state_store) -> None:
    (tmp_path / "readme.txt").write_text("not a spec")
    (tmp_path / "data.json").write_text('{"x": 1}')

    presets = Presets(state_store=memory_state_store)
    results = presets.apply_all(tmp_path)
    assert results == []


def test_module_level_apply_all(tmp_path: Path, memory_state_store) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    config = config_dir / "config.toml"
    config.write_text("x = 1\n")

    spec_dir = tmp_path / "specs"
    spec_dir.mkdir()
    spec = spec_dir / "spec.toml"
    spec.write_text(f"[[files]]\npath = '{config}'\nformat = 'toml'\n[files.data]\nx = 2\n")

    results = apply_all(spec_dir, dry_run=True, state_store=memory_state_store)
    assert len(results) == 1


def test_module_level_list_specs(tmp_path: Path) -> None:
    (tmp_path / "a.toml").write_text("")
    specs = list_specs(tmp_path)
    assert [p.name for p in specs] == ["a.toml"]
