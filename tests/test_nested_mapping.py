from __future__ import annotations

from pathlib import Path

from prescribe._util import delete_mapping_value, set_mapping_value
from prescribe.adapters.toml import TomlAdapter
from prescribe.adapters.yaml import YamlAdapter
from prescribe.core import DesiredState, Planner
from prescribe.core.apply import apply_operations


def test_planner_detects_nested_set_and_update(tmp_path: Path) -> None:
    source = tmp_path / "config.toml"
    source.write_text("title = 'hello'\n[tool]\n[tool.demo]\ncount = 1\n")

    document = TomlAdapter().load(source)
    plan = Planner().plan(
        document,
        DesiredState(
            path=source,
            format="toml",
            data={"tool": {"demo": {"count": 2, "extra": True}}},
        ),
    )

    assert plan.changed is True
    by_key = {op.key: op for op in plan.operations}
    assert by_key["tool.demo.count"].kind == "update"
    assert by_key["tool.demo.extra"].kind == "set"


def test_apply_nested_operations(tmp_path: Path) -> None:
    source = tmp_path / "config.toml"
    source.write_text("title = 'hello'\n[tool]\n[tool.demo]\ncount = 1\n")

    document = TomlAdapter().load(source)
    plan = Planner().plan(
        document,
        DesiredState(
            path=source,
            format="toml",
            data={"tool": {"demo": {"count": 2, "extra": True}}},
        ),
    )

    apply_operations(document, plan.operations)
    assert document.root["tool"]["demo"]["count"] == 2
    assert document.root["tool"]["demo"]["extra"] is True


def test_planner_no_changes_when_nested_matches(tmp_path: Path) -> None:
    source = tmp_path / "config.toml"
    source.write_text("title = 'hello'\n[tool]\n[tool.demo]\ncount = 1\n")

    document = TomlAdapter().load(source)
    plan = Planner().plan(
        document,
        DesiredState(path=source, format="toml", data={"tool": {"demo": {"count": 1}}}),
    )

    assert plan.changed is False
    assert plan.operations == []


def test_apply_creates_intermediate_dicts(tmp_path: Path) -> None:
    source = tmp_path / "config.toml"
    source.write_text("title = 'hello'\n")

    document = TomlAdapter().load(source)
    plan = Planner().plan(
        document,
        DesiredState(path=source, format="toml", data={"tool": {"new_section": {"key": "val"}}}),
    )

    assert plan.changed is True
    apply_operations(document, plan.operations)
    assert document.root["tool"]["new_section"]["key"] == "val"


def test_nested_yaml_planning(tmp_path: Path) -> None:
    source = tmp_path / "config.yaml"
    source.write_text("title: hello\ntool:\n  demo:\n    count: 1\n")

    document = YamlAdapter().load(source)
    plan = Planner().plan(
        document,
        DesiredState(path=source, format="yaml", data={"tool": {"demo": {"count": 2}}}),
    )

    assert plan.changed is True
    assert len(plan.operations) == 1
    assert plan.operations[0].key == "tool.demo.count"

    apply_operations(document, plan.operations)
    assert document.root["tool"]["demo"]["count"] == 2


def test_planner_generates_delete_ops(tmp_path: Path) -> None:
    source = tmp_path / "config.toml"
    source.write_text("title = 'hello'\ncount = 1\nextra = true\n")

    document = TomlAdapter().load(source)
    plan = Planner().plan(
        document,
        DesiredState(path=source, format="toml", delete=["extra"]),
    )

    assert plan.changed is True
    assert len(plan.operations) == 1
    assert plan.operations[0].kind == "delete"
    assert plan.operations[0].key == "extra"


def test_apply_delete_operations(tmp_path: Path) -> None:
    source = tmp_path / "config.toml"
    source.write_text("title = 'hello'\ncount = 1\nextra = true\n")

    document = TomlAdapter().load(source)
    plan = Planner().plan(
        document,
        DesiredState(path=source, format="toml", delete=["extra"]),
    )

    apply_operations(document, plan.operations)
    assert "extra" not in document.root
    assert document.root["count"] == 1


def test_planner_delete_nested_key(tmp_path: Path) -> None:
    source = tmp_path / "config.toml"
    source.write_text("title = 'hello'\n[tool]\n[tool.demo]\ncount = 1\nextra = true\n")

    document = TomlAdapter().load(source)
    plan = Planner().plan(
        document,
        DesiredState(path=source, format="toml", delete=["tool.demo.extra"]),
    )

    assert plan.changed is True
    assert plan.operations[0].kind == "delete"
    assert plan.operations[0].key == "tool.demo.extra"

    apply_operations(document, plan.operations)
    assert "extra" not in document.root["tool"]["demo"]
    assert document.root["tool"]["demo"]["count"] == 1


def test_planner_delete_nonexistent_key_is_noop(tmp_path: Path) -> None:
    source = tmp_path / "config.toml"
    source.write_text("title = 'hello'\n")

    document = TomlAdapter().load(source)
    plan = Planner().plan(
        document,
        DesiredState(path=source, format="toml", delete=["nonexistent"]),
    )

    assert plan.changed is False
    assert plan.operations == []


def test_set_update_and_delete_combined(tmp_path: Path) -> None:
    source = tmp_path / "config.toml"
    source.write_text("title = 'hello'\ncount = 1\nextra = true\n")

    document = TomlAdapter().load(source)
    plan = Planner().plan(
        document,
        DesiredState(
            path=source,
            format="toml",
            data={"count": 2, "new_key": "val"},
            delete=["extra"],
        ),
    )

    apply_operations(document, plan.operations)
    assert document.root["count"] == 2
    assert document.root["new_key"] == "val"
    assert "extra" not in document.root


# ── regression tests ──────────────────────────────────────────────


def test_planner_dotted_key_detects_update(tmp_path: Path) -> None:
    """Dotted keys like ``"core.editor"`` should navigate nested dicts to detect updates."""
    source = tmp_path / "config.toml"
    source.write_text("[core]\neditor = 'vim'\nautocrlf = true\n")

    document = TomlAdapter().load(source)
    plan = Planner().plan(
        document,
        DesiredState(path=source, format="toml", data={"core.editor": "nvim"}),
    )

    assert plan.changed is True
    assert len(plan.operations) == 1
    assert plan.operations[0].kind == "update"
    assert plan.operations[0].key == "core.editor"
    assert plan.operations[0].value == "nvim"
    assert plan.operations[0].before_value == "vim"
    assert plan.operations[0].before_exists is True


def test_planner_dotted_key_detects_set_when_missing(tmp_path: Path) -> None:
    """Dotted keys should produce ``set`` ops when the nested key is missing."""
    source = tmp_path / "config.toml"
    source.write_text("[core]\neditor = 'vim'\n")

    document = TomlAdapter().load(source)
    plan = Planner().plan(
        document,
        DesiredState(path=source, format="toml", data={"core.timeout": 30}),
    )

    assert plan.changed is True
    assert plan.operations[0].kind == "set"
    assert plan.operations[0].key == "core.timeout"
    assert plan.operations[0].before_exists is False


def test_planner_dotted_key_noop_when_matches(tmp_path: Path) -> None:
    """Dotted keys that already match should produce no operations."""
    source = tmp_path / "config.toml"
    source.write_text("[core]\neditor = 'nvim'\n")

    document = TomlAdapter().load(source)
    plan = Planner().plan(
        document,
        DesiredState(path=source, format="toml", data={"core.editor": "nvim"}),
    )

    assert plan.changed is False
    assert plan.operations == []


def test_delete_mapping_value_missing_key_is_noop() -> None:
    """delete_mapping_value on a missing key should not raise."""
    root: dict[str, object] = {"a": 1}
    delete_mapping_value(root, "nonexistent")  # must not raise
    delete_mapping_value(root, "a.b.c")  # missing intermediate path
    assert root == {"a": 1}


def test_delete_mapping_value_missing_intermediate_is_noop() -> None:
    """delete_mapping_value with missing intermediate dicts must not create empty dicts."""
    root: dict[str, object] = {"x": 1}
    delete_mapping_value(root, "a.b.c")
    # must not create phantom empty dicts
    assert "a" not in root
    assert root == {"x": 1}


def test_delete_mapping_value_removes_existing_nested_key() -> None:
    """delete_mapping_value should still remove keys that do exist."""
    root: dict[str, object] = {"a": {"b": {"c": 42}}}
    delete_mapping_value(root, "a.b.c")
    assert root == {"a": {"b": {}}}


def test_set_mapping_value_still_creates_intermediate_dicts() -> None:
    """set_mapping_value must still create intermediate dicts (existing behaviour)."""
    root: dict[str, object] = {}
    set_mapping_value(root, "a.b.c", 1)
    assert root == {"a": {"b": {"c": 1}}}
